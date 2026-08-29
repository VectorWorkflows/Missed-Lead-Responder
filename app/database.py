# app/database.py
from motor.motor_asyncio import AsyncIOMotorClient
from app.config import settings

# Initialize the async MongoDB client
client = AsyncIOMotorClient(settings.MONGO_URI, serverSelectionTimeoutMS=5000)

# Target the specific database for this project
db = client.get_database("missed_call_responder")

# Expose the collections
leads_collection = db.get_collection("leads")
client_configs_collection = db.get_collection("client_configs")

async def ping_database():
    """Helper function to test the database connection on startup."""
    try:
        await client.admin.command('ping')
        print("✅ MongoDB connection successful!")
        return True
    except Exception as e:
        print(f"❌ MongoDB connection failed: {e}")
        return False