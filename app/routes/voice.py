# app/routes/voice.py
"""
Voice webhooks.

The critical change from the old version: the lead is created the MOMENT the
phone is answered, before the menu is even spoken. Previously the lead only
existed once the caller finished the IVR - so anyone who hung up during the
greeting (the single most common real-world case) vanished completely: no
record, no Telegram, no sheet row. For a missed-lead product that was the
whole ballgame.
"""

import urllib.parse
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Form, Request, Response

from app import outbox
from app.clients import normalize_number, registry
from app.config import settings
from app.logging_config import get_logger, redact_phone
from app.security import verify_twilio_signature
from app.services.leads import (
    dispatch_lead_events,
    get_lead,
    new_lead_document,
    upsert_lead,
    _slim,
    _serialisable,
)
from app.services.twilio_service import (
    generate_ivr_twiml,
    generate_safe_fallback_twiml,
    generate_thank_you_twiml,
    generate_voicemail_twiml,
)

router = APIRouter(prefix="/webhook", tags=["Voice"])
log = get_logger("routes.voice")

XML = "application/xml"


def _twiml(content: str) -> Response:
    return Response(content=content, media_type=XML)


def _action_url(path: str, client_id: str, caller: str) -> str:
    return (
        f"{settings.PUBLIC_BASE_URL}/webhook/{path}"
        f"?client_id={urllib.parse.quote_plus(str(client_id))}"
        f"&caller={urllib.parse.quote_plus(caller)}"
    )


# ---------------------------------------------------------------- incoming
@router.post("/voice", dependencies=[Depends(verify_twilio_signature)])
async def handle_incoming_call(
    background_tasks: BackgroundTasks,
    From: str = Form(...),
    To: str = Form(...),
    CallSid: str = Form(...),
):
    caller = normalize_number(From)
    dialed = normalize_number(To)

    # Cache lookup - microseconds, no database round trip on the call path.
    client_config = await registry.get_by_number(dialed)

    if not client_config:
        log.critical("Rejecting call to %s - no config from ANY source.", dialed)
        return _twiml(generate_thank_you_twiml(
            "We are sorry, but this number is currently unavailable. Goodbye."
        ))

    if client_config.get("_source") in ("disk_snapshot", "env_fallback"):
        log.warning("Serving call from DEGRADED config source: %s", client_config["_source"])

    # Record the lead immediately, in the background, so TwiML returns instantly.
    lead = new_lead_document(client_config, CallSid, caller, call_status="RINGING")
    background_tasks.add_task(
        upsert_lead, CallSid, {k: v for k, v in lead.items() if k != "created_at"}, lead
    )

    log.info("Incoming call %s from %s to %s (%s)",
             CallSid, redact_phone(caller), dialed, client_config.get("_id"))

    return _twiml(generate_ivr_twiml(
        action_url=_action_url("ivr-action", client_config["_id"], caller),
        business_name=client_config.get("business_name", "our team"),
        sms_enabled=bool(client_config.get("sms_enabled", settings.FALLBACK_SMS_ENABLED)),
        website_url=client_config.get("website_url") or settings.FALLBACK_WEBSITE_URL,
    ))


# -------------------------------------------------------------- IVR result
@router.post("/ivr-action", dependencies=[Depends(verify_twilio_signature)])
async def handle_ivr_action(
    client_id: str,
    caller: str,
    CallSid: str = Form(...),
    Digits: Optional[str] = Form(None),
):
    caller = normalize_number(caller)
    client_config = await registry.get_by_id(client_id)

    if not client_config:
        log.error("IVR action for unknown client_id=%s", client_id)
        return _twiml(generate_safe_fallback_twiml())

    biz = client_config.get("business_name", "our team")
    booking_url = client_config.get("booking_url") or "our website"
    intake_url = client_config.get("intake_form_url") or "our website"

    base_fields = {
        "client_id": client_id,
        "caller_phone": caller,
        "twilio_number": client_config.get("twilio_number"),
        "call_sid": CallSid,
        "call_status": "IVR_COMPLETED",
        "ivr_selection": Digits or "none",
    }
    birth = new_lead_document(client_config, CallSid, caller, call_status="IVR_COMPLETED")

    # ---------------------------------------------------------- press 1
    if Digits == "1":
        fields = {**base_fields,
                  "reply_text": "🎙️ [Recording voicemail...]",
                  "call_status": "VOICEMAIL_STARTED"}
        await upsert_lead(CallSid, fields, birth)

        log.info("Call %s -> voicemail", CallSid)
        return _twiml(generate_voicemail_twiml(
            recording_action_url=_action_url("recording-action", client_id, caller),
            recording_status_url=_action_url("recording-ready", client_id, caller),
        ))

    # ------------------------------------------------- press 2 / 3 / other
    sms_enabled = bool(client_config.get("sms_enabled", settings.FALLBACK_SMS_ENABLED))

    if not sms_enabled:
        # Honest mode: this number cannot text, so the menu never promised one.
        # Anything that is not "1" is a callback request. You still get the
        # Telegram card and the sheet row - only the SMS leg is absent.
        sms_body = None
        reply_text = ("📞 [Requested a callback]" if Digits == "2"
                      else "📞 [Stayed on line - wants a callback]")
        spoken = ("Perfect. We've got your number and someone will get back to you "
                  "today. Thanks for calling. Goodbye!")
        event = "callback_request"
        headline = "CALLBACK REQUESTED"

    elif Digits == "2":
        sms_body = (f"Hey! Here is the {biz} intake form: {intake_url} "
                    f"- fill it out and we'll review your request!")
        reply_text = "📋 [Requested intake form link]"
        spoken = ("We just texted you our intake form link. "
                  "Fill it out whenever you're ready. Goodbye!")
        event = "intake_link"
        headline = "NEW LEAD FROM CALL"

    elif Digits == "3":
        sms_body = f"Thanks for calling {biz}! You can book a meeting directly here: {booking_url}"
        reply_text = "📅 [Requested booking link]"
        spoken = ("We've texted you our direct booking calendar. "
                  "Pick a time that works best for you. Goodbye!")
        event = "booking_link"
        headline = "NEW LEAD FROM CALL"

    else:
        # Gather timeout, or any stray key (4-9, 0, *, #).
        sms_body = (
            f"Hey from {biz}! Here are our direct access links:\n\n"
            f"📅 Book a meeting: {booking_url}\n"
            f"📋 Project intake form: {intake_url}\n\n"
            f"Reply to this text directly if you have any questions!"
        )
        reply_text = ("📦 [Stayed on line: sent all links]" if Digits in (None, "timeout")
                      else f"📦 [Pressed {Digits}: sent all links]")
        spoken = "No problem! We've just texted you all of our links. Have a great day. Goodbye!"
        event = "all_links"
        headline = "NEW LEAD FROM CALL"

    fields = {**base_fields, "reply_text": reply_text, "headline": headline}
    await upsert_lead(CallSid, fields, birth)

    await dispatch_lead_events(
        client_config,
        {**birth, **fields},
        sms_body=sms_body,
        event=event,
    )

    log.info("Call %s -> %s", CallSid, event)
    return _twiml(generate_thank_you_twiml(spoken))


# ------------------------------------------------------------- voicemail
@router.post("/recording-action", dependencies=[Depends(verify_twilio_signature)])
async def handle_recording_action(
    client_id: str,
    caller: str,
    CallSid: str = Form(...),
    RecordingUrl: Optional[str] = Form(None),
    RecordingDuration: Optional[str] = Form(None),
):
    """
    Fired when <Record> finishes normally (caller pressed #).

    This must return real TwiML - the old version returned an empty <Response/>,
    so the caller got silence and a hangup with no confirmation.
    """
    await _process_voicemail(client_id, normalize_number(caller), CallSid,
                             RecordingUrl, RecordingDuration, source="action")
    return _twiml(generate_thank_you_twiml(
        "Thank you, we've received your message. Someone will be in touch shortly. Goodbye!"
    ))


@router.post("/recording-ready", dependencies=[Depends(verify_twilio_signature)])
async def handle_recording_ready(
    client_id: str,
    caller: str,
    CallSid: str = Form(...),
    RecordingUrl: Optional[str] = Form(None),
    RecordingDuration: Optional[str] = Form(None),
    RecordingStatus: Optional[str] = Form(None),
):
    """
    recordingStatusCallback. Fires when the media is genuinely available -
    including when the caller HUNG UP instead of pressing #, which the action
    URL does not reliably cover. This is what stops hang-up voicemails from
    disappearing.
    """
    if RecordingStatus and RecordingStatus != "completed":
        return _twiml("<Response></Response>")

    await _process_voicemail(client_id, normalize_number(caller), CallSid,
                             RecordingUrl, RecordingDuration, source="status_callback")
    return _twiml("<Response></Response>")


async def _process_voicemail(client_id: str, caller: str, call_sid: str,
                             recording_url: Optional[str], duration: Optional[str],
                             source: str) -> None:
    client_config = await registry.get_by_id(client_id)
    if not client_config:
        log.error("Voicemail for unknown client_id=%s", client_id)
        return

    audio = f"{recording_url}.mp3" if recording_url else None
    fields = {
        "reply_text": "🎙️ [Left a voicemail]" if audio else "🎙️ [Voicemail - no audio captured]",
        "recording_url": audio or "N/A",
        "recording_duration": duration,
        "call_status": "VOICEMAIL_RECEIVED",
        "headline": "NEW VOICEMAIL",
    }
    await upsert_lead(call_sid, fields)

    lead = await get_lead(call_sid) or {
        "call_sid": call_sid, "caller_phone": caller,
        "call_time": datetime.now(timezone.utc), "owner_status": "NEW", **fields,
    }
    lead = {**lead, **fields}

    # Idempotency keys mean the action URL and the status callback firing for
    # the same recording produce exactly ONE alert, not two.
    await dispatch_lead_events(client_config, lead, alert=True, sheet=True, event="voicemail")

    if audio:
        await outbox.enqueue(
            "voicemail_audio",
            {"client": _slim(client_config), "lead": _serialisable(lead)},
            key=f"vm_audio:{call_sid}",
        )
    log.info("Voicemail processed for %s via %s", call_sid, source)


# ----------------------------------------------------------- call status
@router.post("/call-status", dependencies=[Depends(verify_twilio_signature)])
async def handle_call_status(
    CallSid: str = Form(...),
    CallStatus: str = Form(...),
    From: str = Form(None),
    To: str = Form(None),
    CallDuration: Optional[str] = Form(None),
):
    """
    Configure this as "Call Status Changes" on your Twilio number.

    It is how an ABANDONED call becomes a lead: someone rings, hears two
    seconds of the greeting, hangs up. No digits, no voicemail, nothing else in
    the system would ever notice - but that is a real person who wanted you.
    """
    if CallStatus not in ("completed", "no-answer", "busy", "failed", "canceled"):
        return _twiml("<Response></Response>")

    lead = await get_lead(CallSid)
    if not lead:
        log.debug("Call status %s for unknown lead %s", CallStatus, CallSid)
        return _twiml("<Response></Response>")

    await upsert_lead(CallSid, {
        "final_call_status": CallStatus,
        "call_duration": CallDuration,
        "call_ended_at": datetime.now(timezone.utc),
    })

    # Did we already tell the owner about this call? If yes, we're done.
    already_handled = (
        lead.get("alerted")
        or lead.get("ivr_selection") not in (None, "", "none")
        or lead.get("call_status") in ("VOICEMAIL_RECEIVED", "VOICEMAIL_STARTED")
    )
    if already_handled:
        return _twiml("<Response></Response>")

    client_config = await registry.get_by_id(lead.get("client_id", ""))
    if not client_config:
        return _twiml("<Response></Response>")

    caller = lead.get("caller_phone") or normalize_number(From or "")
    biz = client_config.get("business_name", "our team")
    booking_url = client_config.get("booking_url") or "our website"
    intake_url = client_config.get("intake_form_url") or "our website"

    fields = {
        "reply_text": f"📵 [Hung up during greeting after {CallDuration or '?'}s - never made a selection]",
        "call_status": "ABANDONED",
        "headline": "MISSED LEAD (caller hung up)",
    }
    await upsert_lead(CallSid, fields)

    sms_body = None
    if bool(client_config.get("sms_enabled", settings.FALLBACK_SMS_ENABLED)):
        sms_body = (
            f"Hey, sorry we missed you at {biz}! Here's everything you need:\n\n"
            f"📅 Book a meeting: {booking_url}\n"
            f"📋 Tell us what you need: {intake_url}\n\n"
            f"Or just reply to this text and we'll get straight back to you."
        )

    await dispatch_lead_events(
        client_config,
        {**lead, **fields, "call_duration": CallDuration},
        sms_body=sms_body,
        event="abandoned",
    )
    log.info("Abandoned call %s converted into a lead.", CallSid)
    return _twiml("<Response></Response>")
