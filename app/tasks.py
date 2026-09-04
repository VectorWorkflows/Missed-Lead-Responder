# app/tasks.py
"""
Outbox job handlers.

Each handler either succeeds or RAISES. Raising is how a job gets retried with
backoff - never swallow an exception in here.
"""

import asyncio
from datetime import datetime, timezone
from typing import Any

from app import outbox
from app.database import leads_collection
from app.logging_config import get_logger
from app.services import sheets_service
from app.services.telegram_bot import send_telegram_lead_alert, send_voicemail_audio
from app.services.twilio_service import send_sms

log = get_logger("tasks")


async def handle_sms(payload: dict[str, Any]) -> None:
    sid = await send_sms(
        from_number=payload["from_number"],
        to_number=payload["to_number"],
        body=payload["body"],
    )
    try:
        await leads_collection.update_one(
            {"call_sid": payload.get("call_sid")},
            {"$set": {
                "initial_sms_sent": True,
                "initial_sms_time": datetime.now(timezone.utc),
                "initial_sms_sid": sid,
                "lead_type": payload.get("lead_type"),
            }},
        )
    except Exception as exc:
        # The SMS is already delivered - do not retry the whole job for this.
        log.warning("SMS sent but could not stamp the lead record: %s", exc)


async def handle_followup_sms(payload: dict[str, Any]) -> None:
    sid = await send_sms(
        from_number=payload["from_number"],
        to_number=payload["to_number"],
        body=payload["body"],
    )
    try:
        await leads_collection.update_one(
            {"call_sid": payload.get("call_sid")},
            {"$set": {
                "followup_sms_sent": True,
                "followup_sms_time": datetime.now(timezone.utc),
                "followup_sms_sid": sid,
            }},
        )
    except Exception as exc:
        log.warning("Follow-up sent but could not stamp the lead: %s", exc)


async def handle_telegram_alert(payload: dict[str, Any]) -> None:
    await send_telegram_lead_alert(payload["client"], payload["lead"])
    try:
        await leads_collection.update_one(
            {"call_sid": payload["lead"].get("call_sid")},
            {"$set": {"alerted": True, "alerted_at": datetime.now(timezone.utc)}},
        )
    except Exception:
        pass


async def handle_voicemail_audio(payload: dict[str, Any]) -> None:
    await send_voicemail_audio(payload["client"], payload["lead"])


async def handle_sheets_upsert(payload: dict[str, Any]) -> None:
    await asyncio.to_thread(sheets_service.upsert_lead_row, payload["client"], payload["lead"])


async def handle_sheets_status(payload: dict[str, Any]) -> None:
    await asyncio.to_thread(
        sheets_service.update_row_status,
        payload["client"], payload["call_sid"], payload["status"],
    )


async def handle_lead_upsert(payload: dict[str, Any]) -> None:
    """Replay a lead write that failed while Mongo was down."""
    fields = dict(payload.get("fields") or {})
    for key in ("call_time", "created_at", "updated_at", "reply_time"):
        if isinstance(fields.get(key), str):
            try:
                fields[key] = datetime.fromisoformat(fields[key])
            except ValueError:
                pass

    update: dict[str, Any] = {"$set": fields}
    on_insert = payload.get("set_on_insert") or {}
    if on_insert:
        update["$setOnInsert"] = {k: v for k, v in on_insert.items() if k not in fields}

    await leads_collection.update_one({"call_sid": payload["call_sid"]}, update, upsert=True)
    log.info("Replayed lead write for %s", payload["call_sid"])


async def handle_owner_reminder(payload: dict[str, Any]) -> None:
    from app.services.notify import notify_ops
    await notify_ops(payload["message"], dedupe_key=payload.get("dedupe_key"), cooldown=0)


def register_all() -> None:
    outbox.register_handler("sms", handle_sms)
    outbox.register_handler("followup_sms", handle_followup_sms)
    outbox.register_handler("telegram_alert", handle_telegram_alert)
    outbox.register_handler("voicemail_audio", handle_voicemail_audio)
    outbox.register_handler("sheets_upsert", handle_sheets_upsert)
    outbox.register_handler("sheets_status", handle_sheets_status)
    outbox.register_handler("lead_upsert", handle_lead_upsert)
    outbox.register_handler("owner_reminder", handle_owner_reminder)
    log.info("Registered %d outbox handlers.", len(outbox._HANDLERS))
