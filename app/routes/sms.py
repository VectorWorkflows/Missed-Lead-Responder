# app/routes/sms.py
from fastapi import APIRouter, Form, Response
from app.database import leads_collection
from datetime import datetime, timezone

# We will import Sheets and Telegram services here in Phases 3 & 4

router = APIRouter(prefix="/webhook", tags=["SMS"])

@router.post("/sms-inbound")
async def handle_inbound_sms(
    From: str = Form(...),
    To: str = Form(...),
    Body: str = Form(...)
):
    """Triggered when a customer replies to our automated text."""
    print(f"\n💬 Received SMS from {From}: '{Body}'")
    
    # Find the most recent open lead for this exact caller and business number
    lead = await leads_collection.find_one(
        {"caller_phone": From, "twilio_number": To},
        sort=[("call_time", -1)]  # Get the newest one
    )
    
    if lead:
        # Update the lead to show they replied
        await leads_collection.update_one(
            {"_id": lead["_id"]},
            {"$set": {
                "customer_replied": True,
                "reply_text": Body,
                "reply_time": datetime.now(timezone.utc)
            }}
        )
        print(f"✅ Lead {lead['call_sid']} updated with customer reply in MongoDB.")
        
        # TODO: Trigger Google Sheets append (Phase 3)
        # TODO: Trigger Telegram Owner Alert (Phase 4)
        
    else:
        print("⚠️ Received SMS, but no matching missed-call lead found.")

    # Return empty TwiML so Twilio knows we successfully received the message
    return Response(content="<Response></Response>", media_type="application/xml")