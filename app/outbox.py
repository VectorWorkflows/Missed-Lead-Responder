# app/outbox.py
"""
Durable outbox - the thing that makes "no lead is ever lost" actually true.

Every side effect (send SMS, push Telegram card, write the Sheets row, save the
lead) is written to a local SQLite file FIRST, then dispatched by a background
worker with exponential backoff.

Why this matters:
  * Twilio's webhook gets its TwiML response immediately - never a timeout.
  * If Telegram, Google or Mongo is down for an hour, nothing is lost. The work
    sits on disk and drains automatically when the service returns.
  * A container restart / redeploy does not lose queued work (the DB lives on a
    mounted volume).
  * Duplicate work is impossible: every job carries an idempotency key.
"""

import asyncio
import json
import os
import sqlite3
import threading
import time
from typing import Any, Awaitable, Callable, Optional

from app.config import settings
from app.logging_config import get_logger

log = get_logger("outbox")

DB_PATH = os.path.join(settings.DATA_DIR, "outbox.db")

# Backoff: 5s, 15s, 45s, 2m, 6m, 20m, 1h, 2h, 4h ... capped at 6h.
_BACKOFF_SCHEDULE = [5, 15, 45, 120, 360, 1200, 3600, 7200, 14400, 21600]

Handler = Callable[[dict[str, Any]], Awaitable[None]]
_HANDLERS: dict[str, Handler] = {}

_local = threading.local()
_write_lock = threading.Lock()


def register_handler(kind: str, fn: Handler) -> None:
    _HANDLERS[kind] = fn


def _conn() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        os.makedirs(settings.DATA_DIR, exist_ok=True)
        conn = sqlite3.connect(DB_PATH, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=30000")
        _local.conn = conn
    return conn


def init_db() -> None:
    conn = _conn()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS outbox (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            kind             TEXT    NOT NULL,
            payload          TEXT    NOT NULL,
            idempotency_key  TEXT    UNIQUE,
            status           TEXT    NOT NULL DEFAULT 'pending',
            attempts         INTEGER NOT NULL DEFAULT 0,
            next_attempt_at  REAL    NOT NULL DEFAULT 0,
            created_at       REAL    NOT NULL,
            updated_at       REAL    NOT NULL,
            last_error       TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_due ON outbox (status, next_attempt_at);
        CREATE INDEX IF NOT EXISTS idx_kind ON outbox (kind, status);
        """
    )
    log.info("Outbox ready at %s", DB_PATH)


# ------------------------------------------------------------------ writing
def _enqueue_sync(kind: str, payload: dict[str, Any], key: Optional[str]) -> Optional[int]:
    now = time.time()
    body = json.dumps(payload, default=str)
    with _write_lock:
        conn = _conn()
        try:
            cur = conn.execute(
                """INSERT INTO outbox (kind, payload, idempotency_key, status,
                                       attempts, next_attempt_at, created_at, updated_at)
                   VALUES (?, ?, ?, 'pending', 0, ?, ?, ?)""",
                (kind, body, key, now, now, now),
            )
            return cur.lastrowid
        except sqlite3.IntegrityError:
            log.debug("Duplicate job suppressed (key=%s)", key)
            return None


async def enqueue(kind: str, payload: dict[str, Any], key: Optional[str] = None) -> Optional[int]:
    """
    Queue a job. Returns the row id, or None if it was a duplicate.
    Never raises - a queueing failure must not break a phone call.
    """
    if kind not in _HANDLERS:
        log.error("enqueue() called with unknown kind=%r - job dropped.", kind)
        return None
    try:
        job_id = await asyncio.to_thread(_enqueue_sync, kind, payload, key)
        if job_id:
            log.info("Queued %s (job #%s, key=%s)", kind, job_id, key)
        return job_id
    except Exception as exc:
        log.critical("OUTBOX WRITE FAILED for %s: %s", kind, exc)
        return None


# ------------------------------------------------------------------ draining
def _claim_due_sync(limit: int) -> list[sqlite3.Row]:
    conn = _conn()
    rows = conn.execute(
        """SELECT * FROM outbox
           WHERE status = 'pending' AND next_attempt_at <= ?
           ORDER BY next_attempt_at ASC LIMIT ?""",
        (time.time(), limit),
    ).fetchall()
    return list(rows)


def _mark_done_sync(job_id: int) -> None:
    with _write_lock:
        _conn().execute(
            "UPDATE outbox SET status='done', updated_at=?, last_error=NULL WHERE id=?",
            (time.time(), job_id),
        )


def _mark_retry_sync(job_id: int, attempts: int, err: str) -> float:
    delay = _BACKOFF_SCHEDULE[min(attempts, len(_BACKOFF_SCHEDULE) - 1)]
    nxt = time.time() + delay
    with _write_lock:
        _conn().execute(
            """UPDATE outbox SET attempts=?, next_attempt_at=?, updated_at=?,
                                 last_error=? WHERE id=?""",
            (attempts, nxt, time.time(), err[:1000], job_id),
        )
    return delay


def _mark_dead_sync(job_id: int, attempts: int, err: str) -> None:
    with _write_lock:
        _conn().execute(
            """UPDATE outbox SET status='dead', attempts=?, updated_at=?,
                                 last_error=? WHERE id=?""",
            (attempts, time.time(), err[:1000], job_id),
        )


def _purge_sync(days: int) -> int:
    cutoff = time.time() - days * 86400
    with _write_lock:
        cur = _conn().execute(
            "DELETE FROM outbox WHERE status='done' AND updated_at < ?", (cutoff,)
        )
        return cur.rowcount or 0


def stats_sync() -> dict[str, Any]:
    conn = _conn()
    out: dict[str, Any] = {"pending": 0, "done": 0, "dead": 0}
    for row in conn.execute("SELECT status, COUNT(*) c FROM outbox GROUP BY status"):
        out[row["status"]] = row["c"]
    oldest = conn.execute(
        "SELECT MIN(created_at) m FROM outbox WHERE status='pending'"
    ).fetchone()["m"]
    out["oldest_pending_age_seconds"] = round(time.time() - oldest, 1) if oldest else None
    return out


async def stats() -> dict[str, Any]:
    try:
        return await asyncio.to_thread(stats_sync)
    except Exception as exc:
        return {"error": str(exc)}


async def _run_job(row: sqlite3.Row) -> None:
    job_id, kind, attempts = row["id"], row["kind"], row["attempts"]
    handler = _HANDLERS.get(kind)

    if handler is None:
        await asyncio.to_thread(_mark_dead_sync, job_id, attempts, f"no handler for {kind}")
        return

    try:
        payload = json.loads(row["payload"])
        await handler(payload)
        await asyncio.to_thread(_mark_done_sync, job_id)
        log.info("Delivered %s (job #%s) after %d attempt(s).", kind, job_id, attempts + 1)
    except Exception as exc:
        attempts += 1
        err = f"{type(exc).__name__}: {exc}"
        if attempts >= settings.OUTBOX_MAX_ATTEMPTS:
            await asyncio.to_thread(_mark_dead_sync, job_id, attempts, err)
            log.critical("DEAD LETTER: %s job #%s gave up after %d attempts: %s",
                         kind, job_id, attempts, err)
            await _alert_dead_letter(kind, job_id, err)
        else:
            delay = await asyncio.to_thread(_mark_retry_sync, job_id, attempts, err)
            log.warning("Retry %s job #%s in %ss (attempt %d): %s",
                        kind, job_id, delay, attempts, err)


async def _alert_dead_letter(kind: str, job_id: int, err: str) -> None:
    """Tell the operator when something has permanently failed."""
    try:
        from app.services.notify import notify_ops
        await notify_ops(
            f"❌ <b>Job permanently failed</b>\n"
            f"Type: <code>{kind}</code>\nJob: <code>#{job_id}</code>\n"
            f"Error: <code>{err[:300]}</code>\n\n"
            f"This piece of work will NOT be retried automatically."
        )
    except Exception:
        pass


async def worker_loop() -> None:
    """Background drain loop. Started once in the app lifespan."""
    log.info("Outbox worker started (poll every %ss).", settings.OUTBOX_POLL_SECONDS)
    last_purge = 0.0
    while True:
        try:
            rows = await asyncio.to_thread(_claim_due_sync, 25)
            if rows:
                # Run concurrently - a slow Sheets write must not hold up an SMS.
                await asyncio.gather(*(_run_job(r) for r in rows), return_exceptions=True)

            if time.time() - last_purge > 3600:
                removed = await asyncio.to_thread(_purge_sync, settings.OUTBOX_RETENTION_DAYS)
                if removed:
                    log.info("Purged %d completed outbox rows.", removed)
                last_purge = time.time()

            if not rows:
                await asyncio.sleep(settings.OUTBOX_POLL_SECONDS)
        except asyncio.CancelledError:
            log.info("Outbox worker stopping.")
            raise
        except Exception as exc:
            log.error("Outbox worker error: %s", exc)
            await asyncio.sleep(settings.OUTBOX_POLL_SECONDS)
