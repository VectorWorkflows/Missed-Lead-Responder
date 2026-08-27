# seed.py
import asyncio
from app.database import client_configs_collection
from app.config import settings

async def seed():
    real_config = {
        "_id": "client_test_001",
        "twilio_number": settings.CLIENT_TWILIO_NUMBER,  
        "owner_forwarding_phone": settings.CLIENT_CELL_PHONE,  
        "owner_telegram_chat_id": settings.CLIENT_TELEGRAM_CHAT_ID,  
        "google_sheet_id": settings.CLIENT_GOOGLE_SHEET_ID,
        "business_name": "Apex Plumbing",
        "initial_sms_template": "Hey, sorry we missed your call at {business_name}! What's the issue and when's a good time for a quick callback?",
        "followup_sms_template": "Just checking in from {business_name} — did you still need help?",
        "timezone": "Asia/Kolkata",
        "active": True
    }
    
    await client_configs_collection.update_one(
        {"_id": real_config["_id"]}, 
        {"$set": real_config}, 
        upsert=True
    )
    print("✅ Database seeded securely from .env!")

if __name__ == "__main__":
    asyncio.run(seed())