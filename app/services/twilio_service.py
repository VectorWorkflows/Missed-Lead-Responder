# app/services/twilio_service.py
from twilio.rest import Client
from twilio.twiml.voice_response import VoiceResponse, Dial
from app.config import settings
from app.database import leads_collection
from datetime import datetime, timezone

# Initialize the official Twilio REST Client
twilio_client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)

def generate_dial_twiml(forwarding_phone: str, client_id: str, caller: str) -> str:
    """Generates TwiML to forward the call and track its status."""
    response = VoiceResponse()
    
    # timeout=20 means if it rings for 20 seconds without answer, it's missed
    dial = Dial(
        timeout=20, 
        action=f"/webhook/voice-status?client_id={client_id}&caller={caller}"
    )
    dial.number(forwarding_phone)
    response.append(dial)
    
    return str(response)

async def send_missed_call_sms(client_config: dict, to_phone: str, call_sid: str):
    """Sends the immediate auto-reply SMS and logs it in MongoDB."""
    message_body = client_config.get("initial_sms_template", "").format(
        business_name=client_config.get("business_name", "your business")
    )

    try:
        # Fire the SMS via Twilio
        message = twilio_client.messages.create(
            body=message_body,
            from_=client_config["twilio_number"],
            to=to_phone
        )

        # Update the MongoDB lead record
        await leads_collection.update_one(
            {"call_sid": call_sid},
            {"$set": {
                "initial_sms_sent": True,
                "initial_sms_time": datetime.now(timezone.utc)
            }}
        )
        print(f"✅ Auto-reply sent to {to_phone} (Message SID: {message.sid})")
        
    except Exception as e:
        print(f"❌ Failed to send SMS to {to_phone}: {str(e)}")