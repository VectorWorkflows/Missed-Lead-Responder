from twilio.twiml.voice_response import VoiceResponse, Dial
from twilio.rest import Client
from app.config import settings
from app.database import leads_collection
from datetime import datetime, timezone

twilio_client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)

def generate_dial_twiml(forwarding_number: str, status_callback_url: str, business_name: str = "Vector Workflows") -> str:
    response = VoiceResponse()

    # Neural voice greeting
    response.say(
        f"Thank you for calling {business_name}. Please hold while we connect your call.",
        voice="Polly.Amy",
        language="en-US"
    )

    dial = Dial(
        action=status_callback_url,
        method="POST",
        timeout=20
    )
    dial.number(
        forwarding_number,
        status_callback_event="initiated ringing answered completed",
        status_callback=status_callback_url,
        status_callback_method="POST"
    )
    response.append(dial)

    return str(response)

async def send_missed_call_sms(client_config: dict, caller_phone: str, call_sid: str):
    template = client_config.get(
        "initial_sms_template", 
        "Hey, sorry we missed your call at {business_name}! What's the issue and when's a good time for a quick callback?"
    )
    business_name = client_config.get("business_name", "our team")
    body = template.format(business_name=business_name)

    try:
        msg = twilio_client.messages.create(
            body=body,
            from_=client_config["twilio_number"],
            to=caller_phone
        )
        await leads_collection.update_one(
            {"call_sid": call_sid},
            {"$set": {
                "initial_sms_sent": True,
                "initial_sms_time": datetime.now(timezone.utc),
                "initial_sms_sid": msg.sid
            }}
        )
        print(f"✅ Missed call recovery SMS sent to {caller_phone} (SID: {msg.sid})")
    except Exception as e:
        print(f"❌ Failed to send SMS to {caller_phone}: {e}")