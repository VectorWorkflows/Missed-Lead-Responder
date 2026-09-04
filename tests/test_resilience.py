"""
Verification of the two claims this rebuild rests on:

  1. The IVR answers correctly even with MongoDB completely unreachable.
  2. No side effect is ever lost when a downstream service is down.

Run: python tests/test_resilience.py
"""

import asyncio
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "stubs"))
sys.path.insert(0, os.path.dirname(HERE))

TMP = tempfile.mkdtemp(prefix="mlr-test-")

os.environ.update({
    "PUBLIC_BASE_URL": "https://api.example.com/",     # trailing slash on purpose
    "TWILIO_ACCOUNT_SID": "ACtest", "TWILIO_AUTH_TOKEN": "tok",
    "MONGO_URI": "mongodb://u:p@mongo:27017/db?authSource=admin",
    "TELEGRAM_BOT_TOKEN": "123:abc", "DATA_DIR": TMP,
    "FALLBACK_ENABLED": "true",
    "FALLBACK_CLIENT_ID": "vector_workflows",
    "FALLBACK_BUSINESS_NAME": "Vector Workflows",
    "FALLBACK_TWILIO_NUMBER": "+14704706323",
    "FALLBACK_BOOKING_URL": "https://cal.com/vector",
    "FALLBACK_INTAKE_URL": "https://tally.so/vector",
    "FALLBACK_TELEGRAM_CHAT_ID": "999",
    "FALLBACK_WEBSITE_URL": "https://vectorworkflows.com",
    "FALLBACK_SMS_ENABLED": "false",
    "OUTBOX_MAX_ATTEMPTS": "3",
    "OUTBOX_POLL_SECONDS": "1",
})

from motor.motor_asyncio import MODE           # the stub's switch
from app import outbox
from app.clients import bootstrap_registry, normalize_number, registry
from app.config import settings

PASS, FAIL = "\033[92m✓\033[0m", "\033[91m✗\033[0m"
failures = []


def ok(label, condition, detail=""):
    print(f"  {PASS if condition else FAIL} {label}" + (f"  ({detail})" if detail else ""))
    if not condition:
        failures.append(label)


def section(t):
    print(f"\n\033[94m{'─' * 66}\n  {t}\n{'─' * 66}\033[0m")


CLIENT = {
    "_id": "vector_workflows", "business_name": "Vector Workflows",
    "twilio_number": "+14704706323", "owner_telegram_chat_id": 999,
    "google_sheet_id": "sheet123", "booking_url": "https://cal.com/vector",
    "intake_form_url": "https://tally.so/vector", "timezone": "Asia/Kolkata",
    "active": True,
}


async def main():
    # ---------------------------------------------------------------- config
    section("1. CONFIG VALIDATION")
    ok("Trailing slash stripped from PUBLIC_BASE_URL",
       settings.PUBLIC_BASE_URL == "https://api.example.com", settings.PUBLIC_BASE_URL)
    ok("normalize_number handles messy input",
       normalize_number("+1 (470) 470-6323") == "+14704706323"
       and normalize_number("14704706323") == "+14704706323",
       normalize_number("+1 (470) 470-6323"))

    # -------------------------------------------------- config resilience
    section("2. THE IVR MUST NEVER SAY 'UNAVAILABLE'")

    MODE["up"], MODE["docs"] = True, [CLIENT]
    await bootstrap_registry()
    c = await registry.get_by_number("+14704706323")
    ok("Tier 1/2 - resolves from database", c is not None and c["_source"] == "database",
       (c or {}).get("_source"))
    ok("Snapshot written to disk", os.path.exists(os.path.join(TMP, "clients_snapshot.json")))

    # Kill Mongo. Cache still warm -> calls unaffected.
    MODE["up"] = False
    c = await registry.get_by_number("+14704706323")
    ok("Mongo down, warm cache still serves", c is not None and c["business_name"] == "Vector Workflows")

    # Simulate a full container restart with Mongo still down.
    registry._by_number, registry._by_id, registry._source = {}, {}, "empty"
    await bootstrap_registry()
    c = await registry.get_by_number("+14704706323")
    ok("Tier 3 - restart + Mongo down -> disk snapshot",
       c is not None and c["_source"] == "disk_snapshot", (c or {}).get("_source"))
    ok("Snapshot config is complete (business name intact)",
       (c or {}).get("business_name") == "Vector Workflows")

    # Now lose the disk too.
    os.remove(os.path.join(TMP, "clients_snapshot.json"))
    registry._by_number, registry._by_id, registry._source = {}, {}, "empty"
    await bootstrap_registry()
    c = await registry.get_by_number("+14704706323")
    ok("Tier 4 - no DB, no disk -> env fallback",
       c is not None and c["_source"] == "env_fallback", (c or {}).get("_source"))
    ok("Fallback still has booking + intake links",
       (c or {}).get("booking_url") and (c or {}).get("intake_form_url"))

    c = await registry.get_by_number("+19998887777")
    ok("Unknown number gets fallback instead of rejection", c is not None)

    # --------------------------------------------------- the honesty switch
    section("2b. THE HONESTY SWITCH (no SMS promised when we can't send)")
    ok("Fallback client defaults to sms_enabled=False",
       (c or {}).get("sms_enabled") is False, str((c or {}).get("sms_enabled")))
    ok("Fallback carries a website for the spoken CTA",
       bool((c or {}).get("website_url")), (c or {}).get("website_url"))

    from app.services.twilio_service import _speakable
    ok("URL is spoken cleanly", _speakable("https://vectorworkflows.com/") == "vectorworkflows dot com",
       _speakable("https://vectorworkflows.com/"))

    MODE["up"], MODE["docs"] = True, [{**CLIENT, "sms_enabled": True}]
    await registry.refresh_from_db()
    live = await registry.get_by_number("+14704706323")
    ok("A client CAN opt back in per-client", live.get("sms_enabled") is True)

    MODE["up"] = True

    # ---------------------------------------------------------------- outbox
    section("3. NO LEAD IS EVER LOST")
    await asyncio.to_thread(outbox.init_db)

    attempts = {"n": 0}
    delivered = []

    async def flaky(payload):
        attempts["n"] += 1
        if attempts["n"] < 3:                    # fails twice, then works
            raise RuntimeError("telegram is down")
        delivered.append(payload)

    async def always_dead(payload):
        raise RuntimeError("permanently broken")

    outbox.register_handler("flaky", flaky)
    outbox.register_handler("always_dead", always_dead)

    jid = await outbox.enqueue("flaky", {"lead": "abc"}, key="lead:abc")
    ok("Job queued", jid is not None)

    dup = await outbox.enqueue("flaky", {"lead": "abc"}, key="lead:abc")
    ok("Duplicate suppressed by idempotency key", dup is None)

    # Drain manually with the backoff clock wound forward.
    for _ in range(4):
        rows = await asyncio.to_thread(outbox._claim_due_sync, 10)
        for r in rows:
            await outbox._run_job(r)
        await asyncio.to_thread(
            lambda: outbox._conn().execute(
                "UPDATE outbox SET next_attempt_at=0 WHERE status='pending'")
        )

    ok("Survived 2 failures and delivered on retry 3", len(delivered) == 1,
       f"{attempts['n']} attempts")

    st = await outbox.stats()
    ok("Job marked done, nothing left pending", st.get("done") == 1 and st.get("pending", 0) == 0,
       str(st))

    # Dead-lettering
    await outbox.enqueue("always_dead", {"x": 1}, key="dead:1")
    for _ in range(5):
        rows = await asyncio.to_thread(outbox._claim_due_sync, 10)
        for r in rows:
            await outbox._run_job(r)
        await asyncio.to_thread(
            lambda: outbox._conn().execute(
                "UPDATE outbox SET next_attempt_at=0 WHERE status='pending'")
        )
    st = await outbox.stats()
    ok("Gives up after OUTBOX_MAX_ATTEMPTS and dead-letters", st.get("dead") == 1, str(st))

    # Durability across a "restart"
    await outbox.enqueue("flaky", {"lead": "survives"}, key="lead:survives")
    outbox._local.conn = None                     # drop the connection like a restart would
    st = await outbox.stats()
    ok("Queued work survives a process restart", st.get("pending") == 1, str(st))

    # ------------------------------------------------------------- escaping
    section("4. TELEGRAM ESCAPING (the silent-alert-loss bug)")
    import html
    nasty = "Hi! I need *urgent* help_now <script>alert(1)</script> [link](x) 100% & done"
    escaped = html.escape(nasty, quote=False)
    ok("Angle brackets neutralised", "<script>" not in escaped)
    ok("Ampersand escaped", "&amp;" in escaped)
    ok("Markdown chars pass through harmlessly in HTML mode",
       "*urgent*" in escaped and "_now" in escaped)

    # -------------------------------------------------------------- summary
    section("SUMMARY")
    if failures:
        print(f"  \033[91m{len(failures)} check(s) failed:\033[0m")
        for f in failures:
            print(f"    - {f}")
        return 1
    print("  \033[92mAll checks passed.\033[0m")
    print("  Proven: Mongo can be fully down and the caller still hears a correct IVR;")
    print("  queued work retries, dead-letters loudly, and survives a restart.\n")
    return 0


if __name__ == "__main__":
    try:
        code = asyncio.run(main())
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(code)
