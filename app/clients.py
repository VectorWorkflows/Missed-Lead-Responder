# app/clients.py
"""
Client config resolution - the thing that broke.

THE RULE: answering a phone call must never depend on a live database query.

Four tiers, tried in order:
  1. In-memory cache      (refreshed in the background every 60s)  ~microseconds
  2. Live MongoDB read    (only on a cache miss, e.g. brand new client)
  3. On-disk snapshot     (survives a full Mongo outage AND a container restart)
  4. Env-var fallback     (survives losing the disk too)

Tier 4 is why the caller can never again hear "this number is currently
unavailable" because of an infrastructure problem.
"""

import asyncio
import json
import os
import time
from typing import Any, Optional

from app.config import settings
from app.database import client_configs_collection, safe_find_one
from app.logging_config import get_logger

log = get_logger("clients")

SNAPSHOT_PATH = os.path.join(settings.DATA_DIR, "clients_snapshot.json")


def normalize_number(phone: Optional[str]) -> str:
    """E.164-ish normalisation so '+1 470-470-6323' and '14704706323' match."""
    if not phone:
        return ""
    cleaned = "".join(ch for ch in str(phone).strip() if ch.isdigit() or ch == "+")
    cleaned = cleaned.lstrip("+")
    return f"+{cleaned}" if cleaned else ""


def _fallback_config(dialed_number: str = "") -> Optional[dict[str, Any]]:
    """Tier 4. Built purely from environment variables."""
    if not settings.FALLBACK_ENABLED:
        return None
    number = normalize_number(settings.FALLBACK_TWILIO_NUMBER) or normalize_number(dialed_number)
    return {
        "_id": settings.FALLBACK_CLIENT_ID,
        "business_name": settings.FALLBACK_BUSINESS_NAME,
        "twilio_number": number,
        "owner_telegram_chat_id": settings.FALLBACK_TELEGRAM_CHAT_ID,
        "google_sheet_id": settings.FALLBACK_SHEET_ID,
        "booking_url": settings.FALLBACK_BOOKING_URL,
        "intake_form_url": settings.FALLBACK_INTAKE_URL,
        "timezone": settings.FALLBACK_TIMEZONE,
        "website_url": settings.FALLBACK_WEBSITE_URL,
        "sms_enabled": settings.FALLBACK_SMS_ENABLED,
        "followup_sms_template": (
            "Just checking in from {business_name} - did you still need a hand?"
        ),
        "active": True,
        "_source": "env_fallback",
    }


class ClientRegistry:
    def __init__(self) -> None:
        self._by_number: dict[str, dict[str, Any]] = {}
        self._by_id: dict[str, dict[str, Any]] = {}
        self._loaded_at: float = 0.0
        self._source: str = "empty"
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------- loading
    async def refresh_from_db(self) -> bool:
        """Pull every active client from Mongo and rewrite the disk snapshot."""
        try:
            cursor = client_configs_collection.find({"active": {"$ne": False}})
            docs = await cursor.to_list(length=1000)
        except Exception as exc:
            log.error("Client cache refresh failed: %s", exc)
            return False

        if not docs:
            log.warning(
                "Client cache refresh returned ZERO active clients. "
                "Either no clients are onboarded, or you are pointed at the wrong database."
            )

        async with self._lock:
            self._by_number = {}
            self._by_id = {}
            for doc in docs:
                doc["_source"] = "database"
                num = normalize_number(doc.get("twilio_number"))
                if num:
                    doc["twilio_number"] = num
                    self._by_number[num] = doc
                self._by_id[str(doc["_id"])] = doc
            self._loaded_at = time.time()
            self._source = "database"

        if docs:
            self._write_snapshot(docs)
        log.info("Client cache loaded: %d active client(s) from database.", len(docs))
        return True

    def _write_snapshot(self, docs: list[dict[str, Any]]) -> None:
        try:
            os.makedirs(settings.DATA_DIR, exist_ok=True)
            payload = {"saved_at": time.time(), "clients": docs}
            tmp = f"{SNAPSHOT_PATH}.tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, default=str, indent=2)
            os.replace(tmp, SNAPSHOT_PATH)  # atomic
            log.debug("Client snapshot written to %s", SNAPSHOT_PATH)
        except Exception as exc:
            log.error("Could not write client snapshot: %s", exc)

    async def load_from_snapshot(self) -> bool:
        """Tier 3. Used when Mongo is unreachable at boot."""
        try:
            with open(SNAPSHOT_PATH, encoding="utf-8") as fh:
                payload = json.load(fh)
        except FileNotFoundError:
            log.warning("No client snapshot on disk yet (%s).", SNAPSHOT_PATH)
            return False
        except Exception as exc:
            log.error("Could not read client snapshot: %s", exc)
            return False

        docs = payload.get("clients", [])
        async with self._lock:
            self._by_number, self._by_id = {}, {}
            for doc in docs:
                doc["_source"] = "disk_snapshot"
                num = normalize_number(doc.get("twilio_number"))
                if num:
                    self._by_number[num] = doc
                self._by_id[str(doc["_id"])] = doc
            self._loaded_at = payload.get("saved_at", 0.0)
            self._source = "disk_snapshot"

        age_h = (time.time() - self._loaded_at) / 3600 if self._loaded_at else -1
        log.warning(
            "DEGRADED: serving %d client(s) from disk snapshot (%.1fh old). Database unreachable.",
            len(docs), age_h,
        )
        return bool(docs)

    # ------------------------------------------------------------- lookups
    async def get_by_number(self, dialed_number: str) -> Optional[dict[str, Any]]:
        number = normalize_number(dialed_number)

        cached = self._by_number.get(number)
        if cached:
            return cached

        # Cache miss: a client may have just been onboarded. Try one live read.
        doc = await safe_find_one(
            client_configs_collection,
            {"twilio_number": number, "active": {"$ne": False}},
        )
        if doc:
            doc["_source"] = "database_live"
            async with self._lock:
                self._by_number[number] = doc
                self._by_id[str(doc["_id"])] = doc
            log.info("Client %s resolved by live lookup and added to cache.", doc["_id"])
            return doc

        if settings.FALLBACK_FOR_UNKNOWN_NUMBERS:
            fb = _fallback_config(number)
            if fb and fb.get("business_name"):
                log.error(
                    "NO CLIENT CONFIG for dialed number %s - serving env fallback IVR. "
                    "Fix your client_configs; the caller heard a working menu anyway.",
                    number,
                )
                return fb

        log.error("NO CLIENT CONFIG and no fallback available for %s.", number)
        return None

    async def get_by_id(self, client_id: str) -> Optional[dict[str, Any]]:
        cached = self._by_id.get(str(client_id))
        if cached:
            return cached

        doc = await safe_find_one(client_configs_collection, {"_id": client_id})
        if doc:
            doc["_source"] = "database_live"
            async with self._lock:
                self._by_id[str(client_id)] = doc
                num = normalize_number(doc.get("twilio_number"))
                if num:
                    self._by_number[num] = doc
            return doc

        if str(client_id) == settings.FALLBACK_CLIENT_ID:
            return _fallback_config()

        log.error("No client config found for id=%s", client_id)
        return None

    def all_clients(self) -> list[dict[str, Any]]:
        return list(self._by_id.values())

    def status(self) -> dict[str, Any]:
        return {
            "source": self._source,
            "client_count": len(self._by_id),
            "numbers": sorted(self._by_number.keys()),
            "loaded_at": self._loaded_at,
            "age_seconds": round(time.time() - self._loaded_at, 1) if self._loaded_at else None,
        }


registry = ClientRegistry()


async def bootstrap_registry() -> None:
    """Boot sequence: database -> snapshot -> env fallback. Never raises."""
    if await registry.refresh_from_db() and registry.all_clients():
        return
    if await registry.load_from_snapshot():
        return
    fb = _fallback_config()
    if fb and fb.get("twilio_number"):
        async with registry._lock:
            registry._by_number[fb["twilio_number"]] = fb
            registry._by_id[fb["_id"]] = fb
            registry._source = "env_fallback"
            registry._loaded_at = time.time()
        log.error("DEGRADED: no database and no snapshot. Serving env fallback client only.")
    else:
        log.critical(
            "NO CLIENT CONFIG AVAILABLE FROM ANY SOURCE. Calls will be rejected. "
            "Set the FALLBACK_* variables in .env to prevent this."
        )


async def refresh_loop() -> None:
    """Background task: keep the cache warm and the snapshot fresh."""
    while True:
        try:
            await asyncio.sleep(settings.CLIENT_CACHE_REFRESH_SECONDS)
            await registry.refresh_from_db()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.error("Client refresh loop error: %s", exc)
