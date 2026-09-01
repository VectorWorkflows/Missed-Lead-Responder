# manage_clients.py
import asyncio
from app.database import client_configs_collection

async def add_client():
    print("\n--- 🏢 ONBOARD NEW CLIENT ---")
    client_id = input("Unique Client ID (e.g. vector_workflows): ").strip()
    business_name = input("Business Name: ").strip()
    twilio_number = input("Twilio Virtual Number (e.g. +12125550199): ").strip()
    owner_phone = input("Owner's Real Cell Phone (e.g. +919876543210): ").strip()
    
    try:
        telegram_id = int(input("Owner's Telegram Chat ID: ").strip())
    except ValueError:
        print("❌ Invalid Telegram ID. Must be numbers only.")
        return

    sheet_id = input("Google Sheet ID: ").strip()
    
    # --- NEW DYNAMIC FIELDS ---
    booking_url = input("Booking URL (e.g. https://cal.com/...): ").strip()
    intake_url = input("Intake Form URL (e.g. https://tally.so/...): ").strip()
    
    timezone = input("Client Timezone (default 'Asia/Kolkata'): ").strip() or "Asia/Kolkata"
    
    initial_template = (
        input("Initial SMS Template [Press Enter for default]: ").strip() 
        or "Hey, sorry we missed your call at {business_name}! What's the issue and when is a good time for a quick callback?"
    )

    client_doc = {
        "_id": client_id,
        "business_name": business_name,
        "twilio_number": twilio_number,
        "owner_forwarding_phone": owner_phone,
        "owner_telegram_chat_id": telegram_id,
        "google_sheet_id": sheet_id,
        "booking_url": booking_url,
        "intake_form_url": intake_url,
        "initial_sms_template": initial_template,
        "followup_sms_template": "Just checking in from {business_name} — did you still need help?",
        "timezone": timezone,
        "active": True
    }

    await client_configs_collection.update_one(
        {"_id": client_id},
        {"$set": client_doc},
        upsert=True
    )
    print(f"\n✅ Successfully registered {business_name} in MongoDB!")

async def list_clients():
    print("\n--- 📋 REGISTERED CLIENTS ---")
    clients = await client_configs_collection.find({}).to_list(length=100)
    for c in clients:
        status = "🟢 Active" if c.get("active") else "🔴 Inactive"
        print(f"[{status}] ID: {c['_id']} | Name: {c.get('business_name')} | Number: {c.get('twilio_number')}")

if __name__ == "__main__":
    import sys
    action = sys.argv[1] if len(sys.argv) > 1 else "list"
    if action == "add":
        asyncio.run(add_client())
    else:
        asyncio.run(list_clients())