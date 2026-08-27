# app/routes/voice.py
import urllib.parse
import asyncio
from fastapi import APIRouter, Form, Response, BackgroundTasks, Request
from app.database import client_configs_collection, leads_collection
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

def clean_phone_number(phone: str) -> str:
    if not phone:
        return ""
    cleaned = phone.strip()
    if not cleaned.startswith("+"):
        cleaned = "+" + cleaned.lstrip("+")
    return cleaned

@router.post("/voice")
async def handle_incoming_call(
    request: Request,
    From: str = Form(...),
    To: str = Form(...)
):
    caller = clean_phone_number(From)
    called_number = clean_phone_number(To)

    client_config = await client_configs_collection.find_one({"twilio_number": called_number})
    if not client_config:
        client_config = await client_configs_collection.find_one({"_id": "client_test_001"})

    client_id = client_config["_id"] if client_config else "vector_workflows"
    encoded_caller = urllib.parse.quote_plus(caller)

    action_url = f"https://lead.vectorworkflows.com/webhook/ivr-action?client_id={client_id}&caller={encoded_caller}"
    twiml = generate_ivr_twiml(action_url=action_url)
    return Response(content=twiml, media_type="application/xml")

@router.post("/ivr-action")
async def handle_ivr_action(
    client_id: str,
    caller: str,
    background_tasks: BackgroundTasks,
    CallSid: str = Form(...),
    Digits: str = Form(None)
):
    caller = clean_phone_number(caller)
    client_config = await client_configs_collection.find_one({"_id": client_id})
    if not client_config:
        client_config = await client_configs_collection.find_one({})

    response = VoiceResponse()

    # Define your actual links here or in MongoDB
    intake_url = "https://vectorworkflows.com/intake"
    calendar_url = "https://calendly.com/vectorworkflows/audit"

    # Base lead document
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
        # Press 1: Voicemail
        new_lead["reply_text"] = "🎙️ [Caller chose to leave Voicemail]"
        await leads_collection.update_one({"call_sid": CallSid}, {"$set": new_lead}, upsert=True)

        recording_action = f"https://lead.vectorworkflows.com/webhook/recording-status?client_id={client_id}&caller={urllib.parse.quote_plus(caller)}"
        twiml = generate_voicemail_twiml(recording_action_url=recording_action)
        return Response(content=twiml, media_type="application/xml")

    elif Digits == "2":
        # Press 2: Intake Form
        sms_text = f"Hey! Here is the link to our Vector Workflows intake form: {intake_url} — fill it out anytime and we will review your automation scope!"
        new_lead["reply_text"] = "📋 [Requested Intake Form Link]"
        await leads_collection.update_one({"call_sid": CallSid}, {"$set": new_lead}, upsert=True)

        background_tasks.add_task(send_custom_sms, client_config, caller, CallSid, sms_text, "INTAKE_LINK")
        background_tasks.add_task(send_telegram_lead_alert, client_config, new_lead)
        background_tasks.add_task(asyncio.to_thread, log_new_lead_reply, client_config, new_lead)

        response.say("We just sent you a text with our project intake form. Feel free to fill it out whenever you are ready. Goodbye!", voice="Polly.Amy")
        response.hangup()

    elif Digits == "3":
        # Press 3: Calendar Audit Link
        sms_text = f"Thanks for reaching out to Vector Workflows! You can pick a 15-minute workflow audit slot directly on our calendar here: {calendar_url}"
        new_lead["reply_text"] = "📅 [Requested 15-Min Audit Calendar Link]"
        await leads_collection.update_one({"call_sid": CallSid}, {"$set": new_lead}, upsert=True)

        background_tasks.add_task(send_custom_sms, client_config, caller, CallSid, sms_text, "CALENDAR_LINK")
        background_tasks.add_task(send_telegram_lead_alert, client_config, new_lead)
        background_tasks.add_task(asyncio.to_thread, log_new_lead_reply, client_config, new_lead)

        response.say("We've texted you our direct booking link. Pick a time that works best for you. Goodbye!", voice="Polly.Amy")
        response.hangup()

    else:
        # Default / Timeout (No digit pressed)
        sms_text = (
            f"Hey from Vector Workflows! Here are our direct access links:\n\n"
            f"1️⃣ 15-Min Workflow Audit: {calendar_url}\n"
            f"2️⃣ Intake Form: {intake_url}\n\n"
            f"Reply to this text directly if you have any questions!"
        )
        new_lead["reply_text"] = "📦 [Stayed on line: Sent All Agency Links]"
        await leads_collection.update_one({"call_sid": CallSid}, {"$set": new_lead}, upsert=True)

        background_tasks.add_task(send_custom_sms, client_config, caller, CallSid, sms_text, "ALL_LINKS_FALLBACK")
        background_tasks.add_task(send_telegram_lead_alert, client_config, new_lead)
        background_tasks.add_task(asyncio.to_thread, log_new_lead_reply, client_config, new_lead)

        response.hangup()

    return Response(content=str(response), media_type="application/xml")

@router.post("/recording-status")
async def handle_recording_status(
    client_id: str,
    caller: str,
    background_tasks: BackgroundTasks,
    RecordingUrl: str = Form(None),
    CallSid: str = Form(...),
    TranscriptionText: str = Form(None)
):
    """Triggered when a voicemail finishes recording."""
    caller = clean_phone_number(caller)
    client_config = await client_configs_collection.find_one({"_id": client_id})

    rec_display = f"🎙️ Audio: {RecordingUrl}"
    if TranscriptionText:
        rec_display += f"\n📝 Transcript: \"{TranscriptionText}\""

    update_payload = {
        "reply_text": rec_display,
        "recording_url": RecordingUrl
    }
    await leads_collection.update_one({"call_sid": CallSid}, {"$set": update_payload})

    if client_config:
        lead_data = await leads_collection.find_one({"call_sid": CallSid})
        background_tasks.add_task(send_telegram_lead_alert, client_config, lead_data)
        background_tasks.add_task(asyncio.to_thread, log_new_lead_reply, client_config, lead_data)

    return Response(content="<Response></Response>", media_type="application/xml")