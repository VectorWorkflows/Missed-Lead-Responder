# app/routes/voice.py
from fastapi import APIRouter, Form, Response, BackgroundTasks
from app.database import client_configs_collection, leads_collection
from app.services.twilio_service import generate_dial_twiml, send_missed_call_sms
from datetime import datetime, timezone

router = APIRouter(prefix="/webhook", tags=["Voice"])

@router.post("/voice")
async def handle_incoming_call(
    From: str = Form(...),
    To: str = Form(...),
    CallSid: str = Form(...)
):
    """Triggered the moment a customer calls the Twilio number."""
    # Find the business config based on the dialed number
    client_config = await client_configs_collection.find_one({"twilio_number": To})
    
    if not client_config or not client_config.get("active", True):
        # Reject call if number isn't registered in our database
        return Response(content="<Response><Reject/></Response>", media_type="application/xml")

    # Forward the call to the owner's actual cell phone
    twiml = generate_dial_twiml(
        forwarding_phone=client_config["owner_forwarding_phone"],
        client_id=str(client_config["_id"]),
        caller=From
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
    """Triggered when the forwarded call ends (answered, missed, busy, etc)."""
    missed_statuses = ["no-answer", "busy", "failed", "canceled"]
    
    if DialCallStatus in missed_statuses:
        client_config = await client_configs_collection.find_one({"_id": client_id})
        
        if client_config:
            print(f"🚨 Missed call detected from {caller}. Logging lead...")
            
            new_lead = {
                "client_id": client_id,
                "caller_phone": caller,
                "twilio_number": client_config["twilio_number"],
                "call_sid": CallSid,
                "call_time": datetime.now(timezone.utc),
                "call_status": DialCallStatus,
                "initial_sms_sent": False,
                "followup_sms_sent": False,
                "customer_replied": False,
                "owner_status": "NEW",
                "owner_reminder_sent": False,
                "created_at": datetime.now(timezone.utc)
            }
            await leads_collection.insert_one(new_lead)

            # Fire SMS in the background
            background_tasks.add_task(send_missed_call_sms, client_config, caller, CallSid)

    return Response(content="<Response></Response>", media_type="application/xml")