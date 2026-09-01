# app/routes/voice.py
import urllib.parse
import asyncio
import os
from fastapi import APIRouter, Form, Response, BackgroundTasks, Request, Depends, HTTPException
from twilio.request_validator import RequestValidator
from app.database import client_configs_collection, leads_collection
from app.config import settings
from app.services.twilio_service import (
    generate_ivr_twiml,
    generate_voicemail_twiml,
    send_custom_sms
)
from app.services.sheets_service import log_new_lead_reply
from app.services.telegram_bot import send_telegram_lead_alert
from datetime import datetime, timezone
from twilio.twiml.voice_response import VoiceResponse

router = APIRouter(prefix="/webhook", tags=["Voice"])

# --- SECURITY DEPENDENCY ---
TWILIO_AUTH_TOKEN = settings.TWILIO_AUTH_TOKEN
validator = RequestValidator(TWILIO_AUTH_TOKEN)

async def verify_twilio_signature(request: Request):
    signature = request.headers.get("X-Twilio-Signature", "")
    form = await request.form()
    
    url = str(request.url)
    if request.headers.get("X-Forwarded-Proto") == "https":
        url = url.replace("http://", "https://")

    if not validator.validate(url, dict(form), signature):
        raise HTTPException(status_code=403, detail="Access Denied: Invalid Twilio Signature")
# ---------------------------

def clean_phone_number(phone: str) -> str:
    if not phone:
        return ""
    cleaned = phone.strip()
    if not cleaned.startswith("+"):
        cleaned = "+" + cleaned.lstrip("+")
    return cleaned


@router.post("/voice", dependencies=[Depends(verify_twilio_signature)])
async def handle_incoming_call(
    request: Request,
    From: str = Form(...),
    To: str = Form(...)
):
    caller = clean_phone_number(From)
    called_number = clean_phone_number(To)

    # 1. Strict Database Lookup
    client_config = await client_configs_collection.find_one({"twilio_number": called_number})
    
    # 2. Graceful Failure (No more hardcoded Vector Workflows fallback!)
    if not client_config:
        response = VoiceResponse()
        response.say("We are sorry, but this number is currently unavailable. Goodbye.", voice="Polly.Amy")
        return Response(content=str(response), media_type="application/xml")

    client_id = client_config["_id"]
    encoded_caller = urllib.parse.quote_plus(caller)

    # 3. Dynamic Webhook URL using PUBLIC_BASE_URL
    action_url = f"{settings.PUBLIC_BASE_URL}/webhook/ivr-action?client_id={client_id}&caller={encoded_caller}"
    
    # We pass the dynamic business name so the IVR can greet them properly
    business_name = client_config.get("business_name", "our business")
    twiml = generate_ivr_twiml(action_url=action_url, business_name=business_name)
    
    return Response(content=twiml, media_type="application/xml")


@router.post("/ivr-action", dependencies=[Depends(verify_twilio_signature)])
async def handle_ivr_action(
    request: Request,
    client_id: str,
    caller: str,
    background_tasks: BackgroundTasks,
    CallSid: str = Form(...),
    Digits: str = Form(None)
):
    caller = clean_phone_number(caller)
    
    # Strict Lookup
    client_config = await client_configs_collection.find_one({"_id": client_id})
    if not client_config:
        response = VoiceResponse()
        response.say("An error occurred. Goodbye.", voice="Polly.Amy")
        return Response(content=str(response), media_type="application/xml")

    # Pull dynamic client data
    biz_name = client_config.get("business_name", "our team")
    booking_url = client_config.get("booking_url", "our website")
    intake_url = client_config.get("intake_form_url", "our website")

    response = VoiceResponse()

    new_lead = {
        "client_id": client_id,
        "caller_phone": caller,
        "twilio_number": client_config.get("twilio_number"),
        "call_sid": CallSid,
        "call_time": datetime.now(timezone.utc),
        "call_status": "IVR_COMPLETED",
        "initial_sms_sent": False,
        "customer_replied": False,
        "reply_text": "",
        "owner_status": "NEW",
        "created_at": datetime.now(timezone.utc)
    }

    if Digits == "1":
        new_lead["reply_text"] = "🎙️ [Caller Recording Voicemail...]"
        await leads_collection.update_one({"call_sid": CallSid}, {"$set": new_lead}, upsert=True)

        encoded_caller = urllib.parse.quote_plus(caller)
        recording_action = f"{settings.PUBLIC_BASE_URL}/webhook/recording-status?client_id={client_id}&caller={encoded_caller}"
        
        twiml = generate_voicemail_twiml(recording_action_url=recording_action)
        return Response(content=twiml, media_type="application/xml")

    elif Digits == "2":
        # Dynamic SMS Text
        sms_text = f"Hey! Here is the {biz_name} intake form: {intake_url} — fill it out and we will review your request!"
        new_lead["reply_text"] = "📋 [Requested Intake Form Link]"
        await leads_collection.update_one({"call_sid": CallSid}, {"$set": new_lead}, upsert=True)

        background_tasks.add_task(send_custom_sms, client_config, caller, CallSid, sms_text, "INTAKE_LINK")
        background_tasks.add_task(send_telegram_lead_alert, client_config, new_lead)
        background_tasks.add_task(asyncio.to_thread, log_new_lead_reply, client_config, new_lead)

        response.say("We just texted you our intake form link. Feel free to fill it out whenever you are ready. Goodbye!", voice="Polly.Amy")
        response.hangup()

    elif Digits == "3":
        # Dynamic SMS Text
        sms_text = f"Thanks for calling {biz_name}! You can book a meeting directly here: {booking_url}"
        new_lead["reply_text"] = "📅 [Requested Booking Link]"
        await leads_collection.update_one({"call_sid": CallSid}, {"$set": new_lead}, upsert=True)

        background_tasks.add_task(send_custom_sms, client_config, caller, CallSid, sms_text, "CALENDAR_LINK")
        background_tasks.add_task(send_telegram_lead_alert, client_config, new_lead)
        background_tasks.add_task(asyncio.to_thread, log_new_lead_reply, client_config, new_lead)

        response.say("We've texted you our direct booking calendar. Pick a time that works best for you. Goodbye!", voice="Polly.Amy")
        response.hangup()

    else:
        # Dynamic SMS Text
        sms_text = (
            f"Hey from {biz_name}! Here are our direct access links:\n\n"
            f"📅 Book a Meeting: {booking_url}\n"
            f"📋 Project Intake Form: {intake_url}\n\n"
            f"Reply to this text directly if you have any questions!"
        )
        new_lead["reply_text"] = "📦 [Stayed on Line: Dispatched All Links]"
        await leads_collection.update_one({"call_sid": CallSid}, {"$set": new_lead}, upsert=True)

        background_tasks.add_task(send_custom_sms, client_config, caller, CallSid, sms_text, "ALL_LINKS_FALLBACK")
        background_tasks.add_task(send_telegram_lead_alert, client_config, new_lead)
        background_tasks.add_task(asyncio.to_thread, log_new_lead_reply, client_config, new_lead)

        response.hangup()

    return Response(content=str(response), media_type="application/xml")


@router.post("/recording-status", dependencies=[Depends(verify_twilio_signature)])
async def handle_recording_status(
    request: Request,
    client_id: str,
    caller: str,
    background_tasks: BackgroundTasks,
    RecordingUrl: str = Form(None),
    CallSid: str = Form(...)
):
    caller = clean_phone_number(caller)
    
    # Strict Lookup
    client_config = await client_configs_collection.find_one({"_id": client_id})
    if not client_config:
        return Response(content="<Response></Response>", media_type="application/xml")

    audio_link = f"{RecordingUrl}.mp3" if RecordingUrl else "N/A"
    
    update_payload = {
        "reply_text": "🎙️ [Left Voicemail Audio]",
        "recording_url": audio_link
    }
    await leads_collection.update_one({"call_sid": CallSid}, {"$set": update_payload}, upsert=True)

    lead_data = await leads_collection.find_one({"call_sid": CallSid})
    if lead_data:
        background_tasks.add_task(send_telegram_lead_alert, client_config, lead_data)
        background_tasks.add_task(asyncio.to_thread, log_new_lead_reply, client_config, lead_data)

    return Response(content="<Response></Response>", media_type="application/xml")