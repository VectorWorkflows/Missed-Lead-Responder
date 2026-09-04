# app/services/leads.py
"""
Lead persistence and the single fan-out point for "something happened on this
lead" -> save it, text them, alert the owner, write the sheet row.

Every write attempts Mongo directly first (fast path) and falls back to the
outbox if Mongo is unreachable, so a database outage delays the record but
never loses it.
"""

from datetime import datetime, timezone
from typing import Any, Optional

from app import outbox
from app.database import leads_collection
from app.logging_config import get_logger, redact_phone

log = get_logger("leads")


def new_lead_document(
    client_config: dict,
    call_sid: str,
    caller_phone: str,
    call_status: str = "IN_PROGRESS",
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    return {
        "client_id": client_config.get("_id"),
        "caller_phone": caller_phone,
        "twilio_number": client_config.get("twilio_number"),
        "call_sid": call_sid,
        "call_time": now,
        "call_status": call_status,
        "ivr_selection": None,
        "initial_sms_sent": False,
        "followup_sms_sent": False,
        "customer_replied": False,
        "reply_text": "",
        "messages": [],
        "owner_status": "NEW",
        "owner_reminder_sent": False,
        "alerted": False,
        "created_at": now,
        "updated_at": now,
    }


async def upsert_lead(call_sid: str, fields: dict[str, Any],
                      set_on_insert: Optional[dict[str, Any]] = None) -> bool:
    """
    Merge fields into a lead. Returns True if it hit Mongo, False if it was
    spooled to the outbox instead.

    $setOnInsert carries the "birth" fields so a later event never resets
    owner_status back to NEW - a real bug in the previous version.
    """
    fields = {**fields, "updated_at": datetime.now(timezone.utc)}
    update: dict[str, Any] = {"$set": fields}
    if set_on_insert:
        update["$setOnInsert"] = {k: v for k, v in set_on_insert.items() if k not in fields}

    try:
        await leads_collection.update_one({"call_sid": call_sid}, update, upsert=True)
        return True
    except Exception as exc:
        log.error("Mongo write failed for %s (%s) - spooling to outbox.", call_sid, exc)
        await outbox.enqueue(
            "lead_upsert",
            {"call_sid": call_sid, "fields": fields, "set_on_insert": set_on_insert or {}},
        )
        return False


async def get_lead(call_sid: str) -> Optional[dict[str, Any]]:
    try:
        return await leads_collection.find_one({"call_sid": call_sid})
    except Exception as exc:
        log.error("Could not read lead %s: %s", call_sid, exc)
        return None


# --------------------------------------------------------------- fan-out
async def dispatch_lead_events(
    client_config: dict,
    lead: dict[str, Any],
    *,
    sms_body: Optional[str] = None,
    alert: bool = True,
    sheet: bool = True,
    event: str = "ivr",
) -> None:
    """
    Queue every side effect for a lead. Returns in about a millisecond -
    all real work happens in the outbox worker.
    """
    call_sid = lead.get("call_sid", "")
    caller = lead.get("caller_phone", "")
    slim_client = _slim(client_config)

    if sms_body:
        await outbox.enqueue(
            "sms",
            {
                "from_number": client_config.get("twilio_number"),
                "to_number": caller,
                "body": sms_body,
                "call_sid": call_sid,
                "lead_type": event,
            },
            key=f"sms:{call_sid}:{event}",
        )

    if alert:
        await outbox.enqueue(
            "telegram_alert",
            {"client": slim_client, "lead": _serialisable(lead)},
            key=f"alert:{call_sid}:{event}",
        )

    if sheet:
        await outbox.enqueue(
            "sheets_upsert",
            {"client": slim_client, "lead": _serialisable(lead)},
            key=f"sheet:{call_sid}:{event}",
        )

    log.info("Dispatched '%s' events for %s (caller %s)", event, call_sid, redact_phone(caller))


def _slim(client_config: dict) -> dict[str, Any]:
    """Only the fields the handlers need - keeps the queue rows small."""
    keys = (
        "_id", "business_name", "twilio_number", "owner_telegram_chat_id",
        "google_sheet_id", "booking_url", "intake_form_url", "timezone",
        "followup_sms_template", "website_url", "sms_enabled",
    )
    return {k: client_config.get(k) for k in keys}


def _serialisable(lead: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for k, v in lead.items():
        if k == "_id":
            continue
        out[k] = v.isoformat() if isinstance(v, datetime) else v
    return out
