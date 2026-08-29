# app/main.py
from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.routes import voice, sms
from app.services.telegram_bot import get_telegram_app
from app.database import ping_database

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Test MongoDB connection
    await ping_database()
    
    # Startup: Initialize and run Telegram bot polling
    tg_app = get_telegram_app()
    await tg_app.initialize()
    await tg_app.start()
    # Drop pending updates so it doesn't process old taps if the server was offline
    await tg_app.updater.start_polling(drop_pending_updates=True)
    print("🤖 Telegram Bot started and polling for button taps...")
    
    yield
    
    # Shutdown: Cleanly stop Telegram bot
    print("🛑 Shutting down Telegram Bot...")
    await tg_app.updater.stop()
    await tg_app.stop()
    await tg_app.shutdown()
    print("🛑 Telegram Bot stopped cleanly.")

app = FastAPI(title="Missed-Call Lead Responder", lifespan=lifespan)

# Register routes
app.include_router(voice.router)
app.include_router(sms.router)

@app.get("/")
async def root():
    return {"status": "online", "message": "Missed-Call Responder API is running."}