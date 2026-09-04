# app/services/twilio_service.py
"""TwiML generation and outbound SMS."""

import asyncio
from typing import Optional

from twilio.rest import Client
from twilio.twiml.voice_response import Gather, VoiceResponse

from app.config import settings
from app.logging_config import get_logger, redact_phone

log = get_logger("twilio")

twilio_client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)


# ------------------------------------------------------------------- TwiML
def _speakable(url: str) -> str:
    """Turn https://vectorworkflows.com into something Polly reads cleanly."""
    if not url:
        return ""
    clean = url.replace("https://", "").replace("http://", "").rstrip("/")
    return clean.replace(".", " dot ").replace("/", " slash ")


def generate_ivr_twiml(
    action_url: str,
    business_name: str = "our team",
    sms_enabled: bool = True,
    website_url: str = "",
) -> str:
    """
    Two menus, one switch.

    sms_enabled=False is the honest mode for a number that cannot currently
    send SMS (A2P / toll-free verification incomplete). Promising a text that
    never arrives is worse than not offering one - the caller decides you are
    unreliable within about ninety seconds.
    """
    response = VoiceResponse()

    gather = Gather(
        num_digits=1,
        action=action_url,
        method="POST",
        timeout=settings.IVR_GATHER_TIMEOUT,
        input="dtmf",
        action_on_empty_result=False,
    )

    if sms_enabled:
        prompt = (
            f"Thanks for calling {business_name}! "
            "Press 1 to leave a brief voicemail about how we can help you. "
            "Press 2 to receive a text with a link to our intake form. "
            "Press 3 to receive a text with a link to book a meeting on our calendar. "
            "If you'd rather not press anything, stay on the line "
            "and we will text you all of our links automatically."
        )
        holding = "Thanks for holding! We just texted you all our links. Have a great day!"
    else:
        site = _speakable(website_url)
        visit = f" Or visit {site} to book a time instantly on our calendar." if site else ""
        prompt = (
            f"Thanks for calling {business_name}! "
            "Press 1 to leave a brief voicemail about what you need, "
            "and we'll call you back. "
            "Press 2 to request a callback, and we'll get straight back to you today."
            f"{visit}"
        )
        holding = ("Thanks for holding! We've got your number and "
                   "someone will get back to you shortly. Have a great day!")

    gather.say(prompt, voice="Polly.Amy", language="en-US")
    response.append(gather)

    response.say(holding, voice="Polly.Amy", language="en-US")
    # '&' not '?': action_url already carries a query string.
    response.redirect(f"{action_url}&Digits=timeout", method="POST")
    return str(response)


def generate_voicemail_twiml(recording_action_url: str, recording_status_url: str) -> str:
    """
    NOTE: anything after <Record action="..."> is unreachable - Twilio follows
    the action URL's TwiML instead. The old code's "thank you" and <Hangup>
    were dead, so callers got dead air. The thank-you now lives in the
    /recording-action response.

    recordingStatusCallback is the RELIABLE trigger: if the caller hangs up
    instead of pressing #, Twilio may never hit the action URL, but the status
    callback always fires once the media exists.
    """
    response = VoiceResponse()
    response.say(
        "Please leave your message after the tone. "
        "Press pound or hang up when you are finished.",
        voice="Polly.Amy",
        language="en-US",
    )
    response.record(
        action=recording_action_url,
        method="POST",
        max_length=settings.VOICEMAIL_MAX_SECONDS,
        timeout=5,
        finish_on_key="#",
        play_beep=True,
        recording_status_callback=recording_status_url,
        recording_status_callback_method="POST",
        recording_status_callback_event="completed",
    )
    # Only reached if <Record> fails to start at all.
    response.say("Sorry, we could not record your message. "
                 "Please call back or reach us through our website. Goodbye.",
                 voice="Polly.Amy")
    response.hangup()
    return str(response)


def generate_thank_you_twiml(message: str) -> str:
    response = VoiceResponse()
    response.say(message, voice="Polly.Amy", language="en-US")
    response.hangup()
    return str(response)


def generate_safe_fallback_twiml() -> str:
    """
    Last-resort TwiML returned if an unhandled exception reaches the top of a
    voice route. A caller hears something polite rather than Twilio's robotic
    "an application error has occurred".
    """
    response = VoiceResponse()
    response.say(
        "Thanks for calling! We are having a brief technical issue, "
        "but we have your number and will get back to you shortly. Goodbye.",
        voice="Polly.Amy",
        language="en-US",
    )
    response.hangup()
    return str(response)


# --------------------------------------------------------------------- SMS
async def send_sms(from_number: str, to_number: str, body: str) -> str:
    """
    Send an SMS. Returns the message SID. RAISES on failure so the outbox retries.

    Uses the Messaging Service when one is configured - that is the correct
    pattern once a number is attached to a service, and it is what carries the
    A2P campaign registration.

    messages.create is blocking HTTP, so it runs in a thread; calling it
    directly inside async code stalls the whole event loop, which on a busy
    line means delayed call answers.
    """
    kwargs = {"body": body, "to": to_number}

    if settings.TWILIO_MESSAGING_SERVICE_SID:
        kwargs["messaging_service_sid"] = settings.TWILIO_MESSAGING_SERVICE_SID
    elif from_number:
        kwargs["from_"] = from_number
    else:
        raise ValueError("No messaging service SID and no from_number configured")

    msg = await asyncio.to_thread(twilio_client.messages.create, **kwargs)
    log.info("SMS %s dispatched to %s", msg.sid, redact_phone(to_number))
    return msg.sid


async def account_healthcheck() -> tuple[bool, str]:
    try:
        acct = await asyncio.to_thread(
            twilio_client.api.accounts(settings.TWILIO_ACCOUNT_SID).fetch
        )
        return True, f"OK ({acct.friendly_name}, status={acct.status})"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


async def list_incoming_numbers() -> list[str]:
    try:
        numbers = await asyncio.to_thread(twilio_client.incoming_phone_numbers.list, limit=50)
        return [n.phone_number for n in numbers]
    except Exception as exc:
        log.error("Could not list Twilio numbers: %s", exc)
        return []
