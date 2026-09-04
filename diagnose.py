#!/usr/bin/env python3
"""
diagnose.py - run this whenever you doubt the system.

    docker compose exec app python diagnose.py     # what the APP sees
    python diagnose.py                             # what your HOST sees

Run it BOTH ways. If the two disagree about the Mongo host or the client list,
you have found your bug - that mismatch is exactly what made the IVR say
"this number is currently unavailable" while manage_clients.py showed the
client as happily registered.
"""

import asyncio
import os
import sys

GREEN, RED, YELLOW, BLUE, DIM, RESET = (
    "\033[92m", "\033[91m", "\033[93m", "\033[94m", "\033[2m", "\033[0m"
)

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    icon = f"{GREEN}PASS{RESET}" if ok else f"{RED}FAIL{RESET}"
    print(f"  [{icon}] {name}")
    if detail:
        for line in str(detail).splitlines():
            print(f"         {DIM}{line}{RESET}")
    results.append((name, ok, detail))


def section(title: str) -> None:
    print(f"\n{BLUE}{'─' * 68}{RESET}")
    print(f"{BLUE}  {title}{RESET}")
    print(f"{BLUE}{'─' * 68}{RESET}")


async def main() -> int:
    where = "INSIDE the container" if os.path.exists("/.dockerenv") else "on the HOST machine"
    print(f"\n{BLUE}╔{'═' * 66}╗{RESET}")
    print(f"{BLUE}║  MISSED-LEAD RESPONDER - DIAGNOSTICS{' ' * 30}║{RESET}")
    print(f"{BLUE}╚{'═' * 66}╝{RESET}")
    print(f"  Running {YELLOW}{where}{RESET}")

    # ---------------------------------------------------------- 1. config
    section("1. CONFIGURATION")
    try:
        from app.config import settings
    except Exception as exc:
        check("Load settings", False, f"{exc}\n\nA required variable is missing from .env")
        return 1

    check("Load settings", True)
    check("PUBLIC_BASE_URL", settings.PUBLIC_BASE_URL.startswith("https://"),
          f"{settings.PUBLIC_BASE_URL}"
          + ("" if settings.PUBLIC_BASE_URL.startswith("https://")
             else "\nTwilio requires https for production webhooks."))
    check("Signature validation enabled", settings.TWILIO_VALIDATE_SIGNATURE,
          "" if settings.TWILIO_VALIDATE_SIGNATURE
          else "Webhooks are UNAUTHENTICATED. Anyone can forge calls. Turn this back on.")

    # ---------------------------------------------------------- 2. mongo
    section("2. DATABASE  <-- the usual suspect")
    from app.database import client_configs_collection, describe_target, leads_collection

    uri = settings.MONGO_URI
    kind = ("MongoDB Atlas (cloud)" if "mongodb+srv" in uri or "mongodb.net" in uri
            else "local docker container" if "@mongo:" in uri or uri.startswith("mongodb://mongo:")
            else "localhost" if "localhost" in uri or "127.0.0.1" in uri
            else "custom host")
    has_auth = "@" in uri
    print(f"  {YELLOW}This process is pointed at: {kind}{RESET}")
    print(f"  {DIM}{describe_target()}{RESET}")
    check("Connection string includes credentials", has_auth,
          "" if has_auth else "No username/password in MONGO_URI. If this database is "
                              "reachable from the internet, it is wide open.")

    try:
        from app.database import client as motor_client
        await motor_client.admin.command("ping")
        check("Mongo reachable", True)
        mongo_ok = True
    except Exception as exc:
        check("Mongo reachable", False, str(exc))
        mongo_ok = False

    clients = []
    if mongo_ok:
        try:
            clients = await client_configs_collection.find({}).to_list(length=100)
            active = [c for c in clients if c.get("active", True)]
            check(f"client_configs contains {len(clients)} document(s)", bool(clients),
                  "" if clients else
                  "THE DATABASE IS EMPTY.\n"
                  "If manage_clients.py DID show your client, you are running that script\n"
                  "against a different database than this process. That is the bug.")
            for c in clients:
                flag = "" if c.get("active", True) else "  (INACTIVE - calls rejected)"
                print(f"         {DIM}- {c.get('_id')}: {c.get('twilio_number')} "
                      f"/ {c.get('business_name')}{flag}{RESET}")
            check("At least one ACTIVE client", bool(active))
            total_leads = await leads_collection.count_documents({})
            print(f"  {DIM}  leads collection: {total_leads} document(s){RESET}")
        except Exception as exc:
            check("Read client_configs", False, str(exc))

    # ---------------------------------------------------------- 3. fallback
    section("3. SAFETY NET")
    fb_ready = bool(settings.FALLBACK_ENABLED and settings.FALLBACK_TWILIO_NUMBER
                    and settings.FALLBACK_BUSINESS_NAME)
    check("Env fallback client configured", fb_ready,
          f"{settings.FALLBACK_BUSINESS_NAME} / {settings.FALLBACK_TWILIO_NUMBER}" if fb_ready
          else "Set FALLBACK_* in .env. Without it, a database outage means callers\n"
               "hear 'this number is currently unavailable' again.")

    snapshot = os.path.join(settings.DATA_DIR, "clients_snapshot.json")
    check("Disk snapshot present", os.path.exists(snapshot),
          snapshot if os.path.exists(snapshot) else "Written automatically after the first successful DB read.")

    check("DATA_DIR writable", os.access(settings.DATA_DIR, os.W_OK) if os.path.isdir(settings.DATA_DIR) else False,
          f"{settings.DATA_DIR}" if os.path.isdir(settings.DATA_DIR)
          else f"{settings.DATA_DIR} does not exist. Queued jobs cannot survive a restart.")

    # ---------------------------------------------------------- 4. twilio
    section("4. TWILIO")
    from app.services.twilio_service import account_healthcheck, list_incoming_numbers
    ok, detail = await account_healthcheck()
    check("Twilio credentials", ok, detail)

    if ok:
        numbers = await list_incoming_numbers()
        print(f"  {DIM}  Numbers on this account: {', '.join(numbers) or '(none)'}{RESET}")
        configured = {str(c.get("twilio_number")) for c in clients}
        for num in numbers:
            if num in configured:
                print(f"         {GREEN}✓ {num} has a client config{RESET}")
            else:
                print(f"         {YELLOW}! {num} has NO client config - "
                      f"calls fall back or get rejected{RESET}")

    print(f"\n  {YELLOW}Set these in the Twilio console for your number:{RESET}")
    for label, path in [
        ("A CALL COMES IN (POST)", "/webhook/voice"),
        ("CALL STATUS CHANGES (POST)", "/webhook/call-status"),
        ("A MESSAGE COMES IN (POST)", "/webhook/sms-inbound"),
        ("CALL PRIMARY HANDLER FAILS", "/webhook/voice  (or a static TwiML Bin)"),
    ]:
        print(f"    {DIM}{label:<32}{RESET} {settings.PUBLIC_BASE_URL}{path}")

    # ---------------------------------------------------------- 5. telegram
    section("5. TELEGRAM")
    try:
        from telegram import Bot
        bot = Bot(settings.TELEGRAM_BOT_TOKEN)
        me = await bot.get_me()
        check("Bot token", True, f"@{me.username}")

        chat_id = settings.ops_chat_id
        if chat_id:
            try:
                await bot.send_message(
                    chat_id=int(chat_id),
                    text="🔧 <b>Diagnostics</b>\nIf you can read this, alerts work.",
                    parse_mode="HTML",
                )
                check("Test message delivered", True, f"chat {chat_id}")
            except Exception as exc:
                check("Test message delivered", False,
                      f"{exc}\nSend /start to @{me.username} from that account first.")
        else:
            check("Ops chat id configured", False,
                  "Set OPS_TELEGRAM_CHAT_ID or FALLBACK_TELEGRAM_CHAT_ID in .env")
    except Exception as exc:
        check("Bot token", False, str(exc))

    # ---------------------------------------------------------- 6. sheets
    section("6. GOOGLE SHEETS")
    from app.services.sheets_service import healthcheck as sheet_health, sheets_configured
    if not sheets_configured():
        check("Credentials configured", False,
              "GOOGLE_SERVICE_ACCOUNT_JSON is empty. Everything else still works.")
    else:
        checked_any = False
        for c in clients or []:
            sid = c.get("google_sheet_id")
            if not sid or sid == "placeholder_sheet_id":
                continue
            checked_any = True
            ok, detail = await asyncio.to_thread(sheet_health, sid)
            check(f"Sheet for {c.get('_id')}", ok,
                  detail + ("" if ok else
                            "\nShare the sheet with your service account's client_email as Editor."))
        if not checked_any and settings.FALLBACK_SHEET_ID:
            ok, detail = await asyncio.to_thread(sheet_health, settings.FALLBACK_SHEET_ID)
            check("Fallback sheet", ok, detail)
        elif not checked_any:
            check("A sheet to test", False, "No client has a google_sheet_id set.")

    # ---------------------------------------------------------- 7. outbox
    section("7. OUTBOX QUEUE")
    try:
        from app import outbox
        await asyncio.to_thread(outbox.init_db)
        st = await asyncio.to_thread(outbox.stats_sync)
        healthy = st.get("dead", 0) == 0
        check("Queue readable", True,
              f"pending={st.get('pending', 0)} done={st.get('done', 0)} failed={st.get('dead', 0)}")
        if not healthy:
            check("No dead-lettered jobs", False,
                  f"{st['dead']} job(s) gave up permanently. Inspect outbox.db.")
    except Exception as exc:
        check("Queue readable", False, str(exc))

    # ---------------------------------------------------------- summary
    section("SUMMARY")
    failed = [n for n, ok, _ in results if not ok]
    if not failed:
        print(f"  {GREEN}Everything passed. The system is production ready.{RESET}\n")
        return 0
    print(f"  {RED}{len(failed)} check(s) failed:{RESET}")
    for name in failed:
        print(f"    {RED}✗{RESET} {name}")
    print(f"\n  {YELLOW}Reminder: run this both inside the container and on the host.{RESET}")
    print(f"  {YELLOW}If they disagree about the database, that IS the problem.{RESET}\n")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
