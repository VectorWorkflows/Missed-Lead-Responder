# app/database.py
"""
MongoDB access.

Design rule: the phone call NEVER waits on this file. Every read has a short
timeout, every write is either spooled to the outbox or fired in the
background. If Mongo is down the IVR still answers perfectly.
"""

from typing import Any, Optional

from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import ASCENDING, DESCENDING
from pymongo.errors import PyMongoError

from app.config import settings
from app.logging_config import get_logger

log = get_logger("database")

# serverSelectionTimeoutMS is deliberately small: we would rather fail fast and
# fall back to cache/snapshot than hold a Twilio webhook open for 30 seconds.
client = AsyncIOMotorClient(
    settings.MONGO_URI,
    serverSelectionTimeoutMS=3000,
    connectTimeoutMS=3000,
    socketTimeoutMS=5000,
    retryWrites=True,
    appname="missed-lead-responder",
)

db = client.get_database(settings.MONGO_DB_NAME)

leads_collection = db.get_collection("leads")
client_configs_collection = db.get_collection("client_configs")


def describe_target() -> str:
    """
    Human-readable description of WHICH database we are actually pointed at.
    This is the single most useful diagnostic in the whole app - it is what
    would have caught the env_file/environment override bug immediately.
    """
    try:
        nodes = getattr(client, "nodes", None) or set()
        hosts = ", ".join(sorted(f"{h}:{p}" for h, p in nodes)) if nodes else "(not yet connected)"
    except Exception:
        hosts = "(unknown)"

    uri = settings.MONGO_URI
    # Never log credentials.
    if "@" in uri:
        scheme, _, rest = uri.partition("://")
        safe = f"{scheme}://<credentials-hidden>@{rest.split('@', 1)[1]}"
    else:
        safe = uri
    return f"db={settings.MONGO_DB_NAME} hosts=[{hosts}] uri={safe}"


async def ping_database() -> bool:
    try:
        await client.admin.command("ping")
        log.info("MongoDB connection OK -> %s", describe_target())
        return True
    except Exception as exc:
        log.error("MongoDB connection FAILED -> %s (%s)", describe_target(), exc)
        return False


async def ensure_indexes() -> bool:
    """
    Indexes are what stop duplicate leads under concurrency and keep the
    inbound-SMS lookup fast. Safe to run on every boot.
    """
    try:
        await leads_collection.create_index(
            [("call_sid", ASCENDING)], unique=True, name="uniq_call_sid"
        )
        await leads_collection.create_index(
            [("caller_phone", ASCENDING), ("twilio_number", ASCENDING), ("call_time", DESCENDING)],
            name="lookup_inbound_sms",
        )
        await leads_collection.create_index(
            [("client_id", ASCENDING), ("call_time", DESCENDING)], name="lookup_by_client"
        )
        await leads_collection.create_index(
            [("customer_replied", ASCENDING), ("initial_sms_sent", ASCENDING), ("call_time", DESCENDING)],
            name="scheduler_followups",
        )
        await leads_collection.create_index(
            [("owner_status", ASCENDING), ("call_time", DESCENDING)], name="scheduler_reminders"
        )
        await client_configs_collection.create_index(
            [("twilio_number", ASCENDING)], name="lookup_by_number"
        )
        log.info("MongoDB indexes ensured.")
        return True
    except PyMongoError as exc:
        # Non-fatal: an existing collection with duplicate call_sids will
        # reject the unique index. Log loudly, keep running.
        log.error("Could not ensure indexes (non-fatal): %s", exc)
        return False


async def safe_find_one(collection, *args, **kwargs) -> Optional[dict[str, Any]]:
    """find_one that returns None instead of raising when Mongo is unreachable."""
    try:
        return await collection.find_one(*args, **kwargs)
    except Exception as exc:
        log.error("Mongo read failed (%s): %s", getattr(collection, "name", "?"), exc)
        return None
