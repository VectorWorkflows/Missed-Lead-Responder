#!/usr/bin/env python3
"""
Client management.

RUN THIS INSIDE THE CONTAINER:

    docker compose exec app python manage_clients.py list
    docker compose exec app python manage_clients.py add

Running it on the host reads your host .env, which may point at a DIFFERENT
database than the app container - that mismatch is what silently broke the
whole system before. The banner below always prints which database you hit.
"""

import asyncio
import os
import sys

from app.config import settings
from app.database import client_configs_collection, describe_target, leads_collection


def banner() -> None:
    where = "INSIDE the container" if os.path.exists("/.dockerenv") else "on the HOST"
    print("\n" + "=" * 70)
    print(f"  Running {where}")
    print(f"  Database: {describe_target()}")
    print("=" * 70)


def ask(prompt: str, default: str = "", required: bool = False) -> str:
    suffix = f" [{default}]" if default else ""
    while True:
        value = input(f"{prompt}{suffix}: ").strip() or default
        if value or not required:
            return value
        print("  ! Required.")


def normalize(phone: str) -> str:
    cleaned = "".join(ch for ch in phone.strip() if ch.isdigit() or ch == "+").lstrip("+")
    return f"+{cleaned}" if cleaned else ""


async def add_client() -> None:
    banner()
    print("\n--- ONBOARD / UPDATE CLIENT ---\n")

    client_id = ask("Unique client ID (e.g. vector_workflows)", required=True)
    existing = await client_configs_collection.find_one({"_id": client_id})
    if existing:
        print(f"  (updating existing client '{client_id}' - Enter keeps current values)\n")

    def cur(field, fallback=""):
        return str(existing.get(field, fallback)) if existing else fallback

    business_name = ask("Business name", cur("business_name"), required=True)
    twilio_number = normalize(ask("Twilio number (+14704706323)", cur("twilio_number"), required=True))
    owner_phone = normalize(ask("Owner's real cell", cur("owner_forwarding_phone")))

    while True:
        raw = ask("Owner's Telegram chat ID", cur("owner_telegram_chat_id"), required=True)
        try:
            telegram_id = int(raw)
            break
        except ValueError:
            print("  ! Numbers only (negative for groups). Get it from @userinfobot.")

    sheet_id = ask("Google Sheet ID", cur("google_sheet_id"))
    booking_url = ask("Booking URL", cur("booking_url"))
    intake_url = ask("Intake form URL", cur("intake_form_url"))
    website = ask("Website URL (spoken in the IVR)", cur("website_url"))
    tz = ask("Timezone", cur("timezone", "Asia/Kolkata"))

    # THE HONESTY SWITCH - see app/config.py
    default_sms = "y" if (existing.get("sms_enabled") if existing else False) else "n"
    sms_raw = ask("Can this number SEND SMS? (A2P/toll-free approved) y/n", default_sms)
    sms_enabled = sms_raw.strip().lower().startswith("y")
    if not sms_enabled:
        print("  -> IVR will offer voicemail + callback + website, and will NOT promise a text.")
    followup = ask("Follow-up SMS template",
                   cur("followup_sms_template",
                       "Just checking in from {business_name} - did you still need a hand?"))

    doc = {
        "_id": client_id,
        "business_name": business_name,
        "twilio_number": twilio_number,
        "owner_forwarding_phone": owner_phone,
        "owner_telegram_chat_id": telegram_id,
        "google_sheet_id": sheet_id,
        "booking_url": booking_url,
        "intake_form_url": intake_url,
        "website_url": website,
        "sms_enabled": sms_enabled,
        "followup_sms_template": followup,
        "timezone": tz,
        "active": True,
    }

    await client_configs_collection.update_one({"_id": client_id}, {"$set": doc}, upsert=True)
    print(f"\n  ✅ Saved '{business_name}' ({twilio_number}).")
    print("  The app picks this up within 60 seconds - no restart needed.\n")


async def sync_from_env() -> None:
    """
    Non-interactive setup. Reads the FALLBACK_* values already in your .env and
    writes them into Atlas as the real client config - no prompts, no Atlas UI.

    It also deactivates any OTHER client document claiming the same phone
    number, which is what fixes a stale test client hijacking your greeting.
    """
    banner()
    number = normalize(settings.FALLBACK_TWILIO_NUMBER)
    client_id = settings.FALLBACK_CLIENT_ID

    if not number or not settings.FALLBACK_BUSINESS_NAME:
        print("\n  ❌ FALLBACK_TWILIO_NUMBER / FALLBACK_BUSINESS_NAME are not set in .env.\n")
        return

    doc = {
        "_id": client_id,
        "business_name": settings.FALLBACK_BUSINESS_NAME,
        "twilio_number": number,
        "owner_telegram_chat_id": settings.FALLBACK_TELEGRAM_CHAT_ID,
        "google_sheet_id": settings.FALLBACK_SHEET_ID,
        "booking_url": settings.FALLBACK_BOOKING_URL,
        "intake_form_url": settings.FALLBACK_INTAKE_URL,
        "website_url": settings.FALLBACK_WEBSITE_URL,
        "sms_enabled": settings.FALLBACK_SMS_ENABLED,
        "timezone": settings.FALLBACK_TIMEZONE,
        "followup_sms_template":
            "Just checking in from {business_name} - did you still need a hand?",
        "active": True,
    }
    await client_configs_collection.update_one({"_id": client_id}, {"$set": doc}, upsert=True)
    print(f"\n  ✅ '{settings.FALLBACK_BUSINESS_NAME}' saved for {number}")
    print(f"     SMS: {'ENABLED' if doc['sms_enabled'] else 'OFF - IVR offers callback + website'}")

    # Any other document on this number would fight for the greeting. Retire it.
    others = await client_configs_collection.find(
        {"twilio_number": number, "_id": {"$ne": client_id}}
    ).to_list(length=50)
    for other in others:
        await client_configs_collection.update_one(
            {"_id": other["_id"]}, {"$set": {"active": False}}
        )
        print(f"  🔴 Deactivated conflicting client '{other['_id']}' "
              f"({other.get('business_name')}) on the same number")

    if not others:
        print("  (no conflicting clients found)")
    print("\n  Live within 60 seconds. No restart needed.\n")


async def list_clients() -> None:
    banner()
    clients = await client_configs_collection.find({}).to_list(length=200)
    if not clients:
        print("\n  ⚠️  NO CLIENTS IN THIS DATABASE.")
        print("  If you expected some, you are connected to the wrong database.")
        print("  Run this inside the container: docker compose exec app python manage_clients.py list\n")
        return

    print(f"\n  {len(clients)} client(s):\n")
    for c in clients:
        status = "🟢 active" if c.get("active", True) else "🔴 INACTIVE"
        count = await leads_collection.count_documents({"client_id": c["_id"]})
        print(f"  {status}  {c['_id']}")
        print(f"      name    : {c.get('business_name')}")
        print(f"      number  : {c.get('twilio_number')}")
        print(f"      telegram: {c.get('owner_telegram_chat_id')}")
        print(f"      sheet   : {c.get('google_sheet_id') or '(none)'}")
        print(f"      sms     : {'ENABLED' if c.get('sms_enabled') else 'OFF (callback menu)'}")
        print(f"      leads   : {count}\n")


async def set_active(client_id: str, active: bool) -> None:
    banner()
    res = await client_configs_collection.update_one({"_id": client_id}, {"$set": {"active": active}})
    if res.matched_count:
        print(f"\n  ✅ '{client_id}' is now {'ACTIVE' if active else 'INACTIVE'}.\n")
    else:
        print(f"\n  ❌ No client with id '{client_id}'.\n")


async def show_leads(limit: int = 10) -> None:
    banner()
    leads = await leads_collection.find({}).sort("call_time", -1).to_list(length=limit)
    if not leads:
        print("\n  No leads recorded yet.\n")
        return
    print(f"\n  Last {len(leads)} lead(s):\n")
    for l in leads:
        print(f"  {l.get('call_time')}  {l.get('caller_phone')}  "
              f"[{l.get('owner_status')}]  {str(l.get('reply_text'))[:60]}")
    print()


HELP = """
Usage:
  python manage_clients.py sync-from-env        set up the client from .env (no prompts)
  python manage_clients.py list                 show all clients
  python manage_clients.py add                  add or update a client
  python manage_clients.py disable <client_id>  stop taking calls for a client
  python manage_clients.py enable  <client_id>  resume
  python manage_clients.py leads                show recent leads
"""

if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "list"
    arg = sys.argv[2] if len(sys.argv) > 2 else ""

    if action == "add":
        asyncio.run(add_client())
    elif action in ("sync-from-env", "sync"):
        asyncio.run(sync_from_env())
    elif action == "list":
        asyncio.run(list_clients())
    elif action == "leads":
        asyncio.run(show_leads())
    elif action in ("disable", "enable") and arg:
        asyncio.run(set_active(arg, action == "enable"))
    else:
        print(HELP)
