# app/scheduler/jobs.py
"""
Scheduled jobs. This file used to be empty.

  follow_up_unreplied   - text a lead who never answered the first message
  remind_owner          - nudge YOU about a replied lead you never actioned
  watchdog              - shout if the queue is backing up or config is stale
  heartbeat             - a daily "everything is fine" so silence means trouble

The heartbeat matters more than it looks: with it, no news is genuinely good
news. Without it you can never tell "no leads today" apart from "it's been down
since Tuesday" - which is exactly the uncertainty that kills your confidence
pitching this on calls.
"""

from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app import outbox
from app.clients import registry
from app.config import settings
from app.database import leads_collection
from app.logging_config import get_logger
from app.services.notify import notify_ops

log = get_logger("scheduler")


async def follow_up_unreplied() -> None:
    """
    Task A: the first contact went out, they never replied. Nudge once - but
    only during the client's local morning, so a 10pm call is followed up at
    9am rather than at midnight. Nobody wins a lead by texting them at 1am.
    """
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(days=3)
    window_end = now - timedelta(hours=settings.FOLLOWUP_AFTER_HOURS)

    try:
        cursor = leads_collection.find({
            "customer_replied": False,
            "followup_sms_sent": {"$ne": True},
            "owner_status": "NEW",
            "call_time": {"$gte": window_start, "$lte": window_end},
        }).limit(100)
        leads = await cursor.to_list(length=100)
    except Exception as exc:
        log.error("follow_up_unreplied query failed: %s", exc)
        return

    sent = 0
    for lead in leads:
        client_config = await registry.get_by_id(lead.get("client_id", ""))
        if not client_config:
            continue

        # A client whose number cannot send SMS gets no follow-up text.
        if not bool(client_config.get("sms_enabled", settings.FALLBACK_SMS_ENABLED)):
            continue

        if not _is_local_morning(client_config):
            continue

        template = (client_config.get("followup_sms_template")
                    or "Just checking in from {business_name} - did you still need a hand?")
        body = template.replace("{business_name}", client_config.get("business_name", "us"))

        await outbox.enqueue(
            "followup_sms",
            {
                "from_number": client_config.get("twilio_number"),
                "to_number": lead["caller_phone"],
                "body": body,
                "call_sid": lead["call_sid"],
            },
            key=f"followup:{lead['call_sid']}",
        )
        sent += 1

    if sent:
        log.info("Queued %d follow-up SMS.", sent)


def _is_local_morning(client_config: dict) -> bool:
    """True once it is at or past FOLLOWUP_HOUR_LOCAL in the client's timezone."""
    import zoneinfo
    try:
        tz = zoneinfo.ZoneInfo(client_config.get("timezone") or "UTC")
    except Exception:
        tz = timezone.utc
    local_hour = datetime.now(timezone.utc).astimezone(tz).hour
    # Morning window: from the configured hour until early evening. Outside it
    # we simply wait - the job runs every 10 minutes and will catch it.
    return settings.FOLLOWUP_HOUR_LOCAL <= local_hour < 19


async def remind_owner() -> None:
    """Task B: the customer replied hours ago and the lead is still NEW."""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=settings.OWNER_REMINDER_AFTER_HOURS)

    try:
        cursor = leads_collection.find({
            "customer_replied": True,
            "owner_status": "NEW",
            "owner_reminder_sent": {"$ne": True},
            "reply_time": {"$lte": cutoff, "$gte": now - timedelta(days=3)},
        }).limit(50)
        leads = await cursor.to_list(length=50)
    except Exception as exc:
        log.error("remind_owner query failed: %s", exc)
        return

    for lead in leads:
        client_config = await registry.get_by_id(lead.get("client_id", ""))
        chat_id = (client_config or {}).get("owner_telegram_chat_id")
        if not chat_id:
            continue

        import html
        await outbox.enqueue(
            "owner_reminder",
            {
                "message": (
                    "⏳ <b>Lead still untouched</b>\n\n"
                    f"📱 <code>{html.escape(str(lead.get('caller_phone', '')))}</code> "
                    f"replied {settings.OWNER_REMINDER_AFTER_HOURS}h ago and is still marked NEW.\n"
                    f"💬 {html.escape(str(lead.get('reply_text', ''))[:200])}"
                ),
                "dedupe_key": f"reminder:{lead['call_sid']}",
            },
            key=f"reminder:{lead['call_sid']}",
        )
        try:
            await leads_collection.update_one(
                {"_id": lead["_id"]}, {"$set": {"owner_reminder_sent": True}}
            )
        except Exception:
            pass
    if leads:
        log.info("Queued %d owner reminders.", len(leads))


async def watchdog() -> None:
    """Notice trouble before your customers do."""
    queue = await outbox.stats()
    status = registry.status()

    if queue.get("dead", 0) > 0:
        await notify_ops(
            f"❌ <b>{queue['dead']} job(s) permanently failed</b>\n"
            f"Check <code>/health/deep</code> and the container logs.",
            dedupe_key="dead_letters", cooldown=21600,
        )

    age = queue.get("oldest_pending_age_seconds") or 0
    if age > 900:
        await notify_ops(
            f"⚠️ <b>Outbox backing up</b>\nOldest job is {int(age / 60)} minutes old "
            f"({queue.get('pending', 0)} pending). A downstream service is probably down.",
            dedupe_key="queue_backlog", cooldown=3600,
        )

    if status["source"] in ("disk_snapshot", "env_fallback"):
        await notify_ops(
            f"⚠️ <b>Running on fallback config</b>\nSource: <code>{status['source']}</code>. "
            f"The database is unreachable - calls still work, but new clients won't appear.",
            dedupe_key="degraded_config", cooldown=3600,
        )

    if status["client_count"] == 0:
        await notify_ops(
            "🚨 <b>ZERO clients loaded</b> - calls are being rejected. "
            "Check MONGO_URI and that client_configs is populated.",
            dedupe_key="no_clients", cooldown=1800,
        )


async def heartbeat() -> None:
    """Daily proof of life with yesterday's numbers."""
    if not settings.ENABLE_HEARTBEAT:
        return

    since = datetime.now(timezone.utc) - timedelta(hours=24)
    try:
        total = await leads_collection.count_documents({"call_time": {"$gte": since}})
        replied = await leads_collection.count_documents(
            {"call_time": {"$gte": since}, "customer_replied": True}
        )
        booked = await leads_collection.count_documents(
            {"call_time": {"$gte": since}, "owner_status": "BOOKED"}
        )
    except Exception as exc:
        await notify_ops(f"⚠️ <b>Heartbeat</b>: app is up but the database is unreachable.\n"
                         f"<code>{exc}</code>", dedupe_key="heartbeat_db", cooldown=0)
        return

    queue = await outbox.stats()
    await notify_ops(
        "✅ <b>Daily check: system healthy</b>\n\n"
        f"📞 Leads (24h): <b>{total}</b>\n"
        f"💬 Replied: <b>{replied}</b>\n"
        f"✅ Booked: <b>{booked}</b>\n"
        f"📬 Queue: {queue.get('pending', 0)} pending, {queue.get('dead', 0)} failed\n"
        f"⚙️ Config source: <code>{registry.status()['source']}</code>",
        dedupe_key="heartbeat", cooldown=0,
    )


def build_scheduler() -> AsyncIOScheduler:
    sched = AsyncIOScheduler(timezone="UTC")

    sched.add_job(follow_up_unreplied, IntervalTrigger(minutes=10),
                  id="followups", max_instances=1, coalesce=True)
    sched.add_job(remind_owner, IntervalTrigger(minutes=15),
                  id="reminders", max_instances=1, coalesce=True)
    sched.add_job(watchdog, IntervalTrigger(minutes=5),
                  id="watchdog", max_instances=1, coalesce=True)

    # Heartbeat at the fallback client's local hour.
    try:
        import zoneinfo
        tz = zoneinfo.ZoneInfo(settings.FALLBACK_TIMEZONE)
    except Exception:
        tz = timezone.utc
    sched.add_job(heartbeat, CronTrigger(hour=settings.HEARTBEAT_HOUR_LOCAL, minute=0, timezone=tz),
                  id="heartbeat", max_instances=1, coalesce=True)

    return sched
