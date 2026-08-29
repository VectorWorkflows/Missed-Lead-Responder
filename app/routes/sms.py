# app/routes/sms.py
import asyncio
from datetime import datetime, timezone
from fastapi import APIRouter, Form, Response, BackgroundTasks
from app.database import leads_collection, client_configs_collection
from app.services.sheets_service import log_new_lead_reply
from app.services.telegram_bot import send_telegram_lead_alert

router = APIRouter(prefix="/webhook", tags=["SMS"])

def clean_phone_number(phone: str) -> str:
    """Ensures phone number starts with a '+' and strips spaces."""
    if not phone:
        return ""
    cleaned = phone.strip()
    if not cleaned.startswith("+"):
        cleaned = "+" + cleaned.lstrip("+")
    return cleaned

@router.post("/sms-inbound")
async def handle_inbound_sms(
    background_tasks: BackgroundTasks,
    From: str = Form(...),
    To: str = Form(...),
    Body: str = Form(...)
):
    """Triggered when a customer replies to our automated text."""
    caller = clean_phone_number(From)
    twilio_num = clean_phone_number(To)

    print(f"\n💬 Received SMS from {caller}: '{Body}'")
    
    # Find newest open lead for this caller
    lead = await leads_collection.find_one(
        {"caller_phone": caller, "twilio_number": twilio_num},
        sort=[("call_time", -1)]
    )
    
    if lead:
        # Update MongoDB record
        await leads_collection.update_one(
            {"_id": lead["_id"]},
            {"$set": {
                "customer_replied": True,
                "reply_text": Body,
                "reply_time": datetime.now(timezone.utc)
            }}
        )
        print(f"✅ Lead {lead['call_sid']} updated with customer reply in MongoDB.")

        # Fetch client config
        client_config = await client_configs_collection.find_one({"_id": lead["client_id"]})
        
        if client_config:
            updated_lead = {**lead, "reply_text": Body, "customer_replied": True}
            
            # 1. Sync to Google Sheets
            background_tasks.add_task(
                asyncio.to_thread,
                log_new_lead_reply,
                client_config,
                updated_lead
            )
            
            # 2. Push Instant Alert to Telegram Owner
            background_tasks.add_task(
                send_telegram_lead_alert,
                client_config,
                updated_lead
            )
            
    else:
        print("⚠️ Received SMS, but no matching missed-call lead found.")

    return Response(content="<Response></Response>", media_type="application/xml")