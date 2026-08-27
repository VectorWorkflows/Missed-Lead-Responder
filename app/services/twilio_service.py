# app/services/twilio_service.py
from twilio.twiml.voice_response import VoiceResponse, Gather
from twilio.rest import Client
from app.config import settings
from app.database import leads_collection
from datetime import datetime, timezone

twilio_client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)

def generate_ivr_twiml(action_url: str) -> str:
    """Generates the interactive IVR menu with DTMF digit capture."""
    response = VoiceResponse()

    gather = Gather(
        num_digits=1,
        action=action_url,
        method="POST",
        timeout=6
    )
    
    gather.say(
        "Thanks for calling Vector Workflows! We build custom AI and operational automations for modern service businesses. "
        "Press 1 to leave a voice description of your problem. "
        "Press 2 to receive an instant SMS link to our intake form. "
        "Press 3 to get our calendar link and book a 15-minute workflow audit. "
        "If you'd rather not press anything, stay on the line and we'll text you all the links automatically.",
        voice="Polly.Amy",
        language="en-US"
    )
    response.append(gather)

    response.say(
        "Thanks for holding! We just texted you all our links. Have a great day!",
        voice="Polly.Amy",
        language="en-US"
    )
    response.redirect(f"{action_url}?Digits=timeout", method="POST")

    return str(response)

def generate_voicemail_twiml(recording_action_url: str, transcribe_callback_url: str) -> str:
    """Prompts caller to record a voicemail and triggers transcription."""
    response = VoiceResponse()
    response.say(
        "Please describe your project, problem, or workflow bottleneck after the tone. Press pound or hang up when finished.",
        voice="Polly.Amy",
        language="en-US"
    )
    response.record(
        action=recording_action_url,
        transcribe=True,
        transcribe_callback=transcribe_callback_url,
        max_length=120,
        finish_on_key="#",
        play_beep=True
    )
    response.say("Thank you, we received your recording. We will be in touch shortly!", voice="Polly.Amy")
    response.hangup()
    return str(response)

async def send_custom_sms(client_config: dict, caller_phone: str, call_sid: str, message_body: str, lead_type: str = "IVR_INTERACTION"):
    """Sends a specific SMS payload and tracks it in MongoDB."""
    try:
        msg = twilio_client.messages.create(
            body=message_body,
            from_=client_config["twilio_number"],
            to=caller_phone
        )
        await leads_collection.update_one(
            {"call_sid": call_sid},
            {"$set": {
                "initial_sms_sent": True,
                "initial_sms_time": datetime.now(timezone.utc),
                "initial_sms_sid": msg.sid,
                "lead_type": lead_type
            }},
            upsert=True
        )
        print(f"✅ Dispatched {lead_type} SMS to {caller_phone} (SID: {msg.sid})")
    except Exception as e:
        print(f"❌ Failed to send SMS to {caller_phone}: {e}")