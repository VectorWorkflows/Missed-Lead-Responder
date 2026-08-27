# test_scheduler.py
import asyncio
from datetime import datetime, timezone, timedelta
from app.database import leads_collection, client_configs_collection
from app.scheduler.jobs import run_all_scheduler_jobs
from app.services.telegram_bot import get_telegram_app


async def test_scheduler():
    config = await client_configs_collection.find_one({"_id": "client_test_001"})
    if not config:
        print("❌ Please run 'python seed.py' first.")
        return

    now = datetime.now(timezone.utc)
    
    # Clean up test records
    await leads_collection.delete_many({"call_sid": {"$in": ["TEST_TASK_A_LEAD", "TEST_TASK_B_LEAD"]}})

    # 1. Mock Lead A: Missed call 2.5 hours ago, customer never replied
    lead_a = {
        "client_id": "client_test_001",
        "caller_phone": config["owner_forwarding_phone"],  # sends to your test phone
        "twilio_number": config["twilio_number"],
        "call_sid": "TEST_TASK_A_LEAD",
        "call_time": now - timedelta(hours=2, minutes=30),
        "call_status": "no-answer",
        "initial_sms_sent": True,
        "initial_sms_time": now - timedelta(hours=2, minutes=30),
        "followup_sms_sent": False,
        "customer_replied": False,
        "owner_status": "NEW",
        "owner_reminder_sent": False,
        "created_at": now - timedelta(hours=2, minutes=30)
    }

    # 2. Mock Lead B: Customer replied 4.5 hours ago, owner left it as NEW
    lead_b = {
        "client_id": "client_test_001",
        "caller_phone": config["owner_forwarding_phone"],
        "twilio_number": config["twilio_number"],
        "call_sid": "TEST_TASK_B_LEAD",
        "call_time": now - timedelta(hours=5),
        "call_status": "no-answer",
        "initial_sms_sent": True,
        "initial_sms_time": now - timedelta(hours=5),
        "followup_sms_sent": True,
        "customer_replied": True,
        "reply_text": "Need urgent AC maintenance.",
        "reply_time": now - timedelta(hours=4, minutes=30),
        "owner_status": "NEW",
        "owner_reminder_sent": False,
        "created_at": now - timedelta(hours=5)
    }

    await leads_collection.insert_many([lead_a, lead_b])
    print("📥 Seeded aged test leads for Task A and Task B.")

    # Initialize telegram bot context for the test
    tg_app = get_telegram_app()
    await tg_app.initialize()

    print("🚀 Triggering scheduler jobs...")
    await run_all_scheduler_jobs()

    # Verify DB states
    doc_a = await leads_collection.find_one({"call_sid": "TEST_TASK_A_LEAD"})
    doc_b = await leads_collection.find_one({"call_sid": "TEST_TASK_B_LEAD"})

    print("\n--- SCHEDULER VERIFICATION RESULTS ---")
    print(f"Task A (Follow-up SMS Sent): {doc_a.get('followup_sms_sent') == True}")
    print(f"Task B (Owner Reminder Sent): {doc_b.get('owner_reminder_sent') == True}")
    print("--------------------------------------\n")

    # Cleanup
    await leads_collection.delete_many({"call_sid": {"$in": ["TEST_TASK_A_LEAD", "TEST_TASK_B_LEAD"]}})
    await tg_app.shutdown()


if __name__ == "__main__":
    asyncio.run(test_scheduler())