import urllib.parse
import asyncio
from fastapi import APIRouter, Form, Response, BackgroundTasks, Request
from app.database import client_configs_collection, leads_collection
from app.services.twilio_service import generate_dial_twiml, send_missed_call_sms
from app.services.sheets_service import log_new_lead_reply
from app.services.telegram_bot import send_telegram_lead_alert
from datetime import datetime, timezone

router = APIRouter(prefix="/webhook", tags=["Voice"])

def clean_phone_number(phone: str) -> str:
    """Ensures phone number starts with a '+' and strips spaces."""
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

    if not client_config:
        return Response(content="<Response><Reject/></Response>", media_type="application/xml")

    client_id = client_config["_id"]
    business_name = client_config.get("business_name", "Vector Workflows")
    forwarding_number = client_config.get("owner_forwarding_phone")

    # URL encode caller so '+' is preserved across query params
    encoded_caller = urllib.parse.quote_plus(caller)
    
    # Use the live public HTTPS domain
    base_url = "https://lead.vectorworkflows.com"
    status_callback_url = f"{base_url}/webhook/voice-status?client_id={client_id}&caller={encoded_caller}"

    twiml = generate_dial_twiml(
        forwarding_number=forwarding_number,
        status_callback_url=status_callback_url,
        business_name=business_name
    )
    return Response(content=twiml, media_type="application/xml")

@router.post("/voice-status")
async def handle_voice_status(
    client_id: str,
    caller: str,
    background_tasks: BackgroundTasks,
    CallSid: str = Form(...),
    DialCallStatus: str = Form(None)
):
    caller = clean_phone_number(caller)
    missed_statuses = ["no-answer", "busy", "failed", "canceled"]

    if DialCallStatus in missed_statuses:
        client_config = await client_configs_collection.find_one({"_id": client_id})

        if client_config:
            print(f"🚨 Missed call ({DialCallStatus}) detected from {caller}. Processing recovery...")

            new_lead = {
                "client_id": client_id,
                "caller_phone": caller,
                "twilio_number": client_config.get("twilio_number"),
                "call_sid": CallSid,
                "call_time": datetime.now(timezone.utc),
                "call_status": DialCallStatus,
                "initial_sms_sent": False,
                "followup_sms_sent": False,
                "customer_replied": False,
                "reply_text": "",
                "owner_status": "NEW",
                "owner_reminder_sent": False,
                "created_at": datetime.now(timezone.utc)
            }
            await leads_collection.insert_one(new_lead)

            # 1. Fire SMS to customer
            background_tasks.add_task(send_missed_call_sms, client_config, caller, CallSid)

            # 2. Fire Telegram alert to owner
            background_tasks.add_task(send_telegram_lead_alert, client_config, new_lead)

            # 3. Append to Google Sheets
            background_tasks.add_task(asyncio.to_thread, log_new_lead_reply, client_config, new_lead)

    return Response(content="<Response></Response>", media_type="application/xml")