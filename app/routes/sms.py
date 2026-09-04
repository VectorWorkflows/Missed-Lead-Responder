# app/routes/sms.py
"""
Inbound SMS.

Three fixes over the old version:
  * Body is optional - an MMS with no text used to 422 the whole webhook.
  * The lead lookup is bounded to SMS_LOOKBACK_DAYS, so a text months later
    no longer attaches itself to an ancient lead.
  * A text from a number with NO matching call still alerts you. Previously it
    printed a warning and vanished, which is the exact opposite of what a lead
    responder should do.
Replies are appended to a messages[] array instead of overwriting reply_text,
so a multi-message conversation is preserved.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Form, Response

from app.clients import normalize_number, registry
from app.config import settings
from app.database import leads_collection
from app.logging_config import get_logger, redact_phone
from app.security import verify_twilio_signature
from app.services.leads import dispatch_lead_events, upsert_lead

router = APIRouter(prefix="/webhook", tags=["SMS"])
log = get_logger("routes.sms")


@router.post("/sms-inbound", dependencies=[Depends(verify_twilio_signature)])
async def handle_inbound_sms(
    From: str = Form(...),
    To: str = Form(...),
    Body: str = Form(""),
    MessageSid: str = Form(""),
    NumMedia: str = Form("0"),
):
    caller = normalize_number(From)
    dialed = normalize_number(To)

    body = (Body or "").strip()
    try:
        media_count = int(NumMedia or "0")
    except ValueError:
        media_count = 0
    if not body and media_count:
        body = f"[{media_count} media attachment(s), no text]"
    elif not body:
        body = "[empty message]"

    log.info("Inbound SMS from %s to %s", redact_phone(caller), dialed)

    cutoff = datetime.now(timezone.utc) - timedelta(days=settings.SMS_LOOKBACK_DAYS)
    lead = None
    try:
        lead = await leads_collection.find_one(
            {"caller_phone": caller, "twilio_number": dialed, "call_time": {"$gte": cutoff}},
            sort=[("call_time", -1)],
        )
    except Exception as exc:
        log.error("Lead lookup failed: %s", exc)

    client_config = None
    if lead:
        client_config = await registry.get_by_id(lead.get("client_id", ""))
    if not client_config:
        client_config = await registry.get_by_number(dialed)

    if not client_config:
        log.error("Inbound SMS to unconfigured number %s - cannot alert anyone.", dialed)
        return Response(content="<Response></Response>", media_type="application/xml")

    now = datetime.now(timezone.utc)
    message_entry = {"direction": "inbound", "body": body, "at": now, "sid": MessageSid}

    if lead:
        call_sid = lead["call_sid"]
        try:
            await leads_collection.update_one(
                {"_id": lead["_id"]},
                {
                    "$set": {
                        "customer_replied": True,
                        "reply_text": body,
                        "reply_time": now,
                        "headline": "CUSTOMER REPLIED",
                        "updated_at": now,
                    },
                    "$push": {"messages": message_entry},
                },
            )
        except Exception as exc:
            log.error("Could not record reply on lead %s: %s", call_sid, exc)

        merged = {**lead, "reply_text": body, "customer_replied": True,
                  "headline": "CUSTOMER REPLIED"}
        event = f"reply:{MessageSid or now.timestamp()}"
    else:
        # Cold inbound text - no prior call. Still a lead. Create one.
        call_sid = f"SMS-{MessageSid}" if MessageSid else f"SMS-{int(now.timestamp())}"
        merged = {
            "client_id": client_config.get("_id"),
            "caller_phone": caller,
            "twilio_number": dialed,
            "call_sid": call_sid,
            "call_time": now,
            "call_status": "SMS_ONLY",
            "customer_replied": True,
            "reply_text": body,
            "owner_status": "NEW",
            "headline": "NEW TEXT (no prior call)",
            "messages": [message_entry],
            "created_at": now,
        }
        await upsert_lead(call_sid, merged, merged)
        log.info("Created SMS-only lead %s", call_sid)
        event = f"sms_only:{MessageSid or now.timestamp()}"

    await dispatch_lead_events(client_config, merged, alert=True, sheet=True, event=event)
    return Response(content="<Response></Response>", media_type="application/xml")


@router.post("/sms-status", dependencies=[Depends(verify_twilio_signature)])
async def handle_sms_status(
    MessageSid: str = Form(...),
    MessageStatus: str = Form(...),
    ErrorCode: Optional[str] = Form(None),
):
    """
    Outbound delivery receipts. Set this as the number's messaging status
    callback. Without it, a blocked SMS (unregistered A2P 10DLC is the usual
    culprit on US numbers) looks like a success in your logs while the customer
    receives nothing.
    """
    if MessageStatus in ("failed", "undelivered"):
        log.error("SMS %s %s (error %s)", MessageSid, MessageStatus, ErrorCode)
        try:
            from app.services.notify import notify_ops
            await notify_ops(
                f"⚠️ <b>SMS not delivered</b>\n"
                f"Status: <code>{MessageStatus}</code>\n"
                f"Twilio error: <code>{ErrorCode or 'unknown'}</code>\n\n"
                f"Error 30034 = A2P 10DLC registration missing.",
                dedupe_key=f"sms_fail:{ErrorCode}",
            )
        except Exception:
            pass
        try:
            await leads_collection.update_one(
                {"initial_sms_sid": MessageSid},
                {"$set": {"sms_delivery_status": MessageStatus, "sms_error_code": ErrorCode}},
            )
        except Exception:
            pass
    return Response(content="<Response></Response>", media_type="application/xml")
