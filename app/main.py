# app/main.py
from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.routes import voice, sms
from app.services.telegram_bot import get_telegram_app


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Initialize and run Telegram bot polling
    tg_app = get_telegram_app()
    await tg_app.initialize()
    await tg_app.start()
    await tg_app.updater.start_polling()
    print("🤖 Telegram Bot started and polling for button taps...")
    
    yield
    
    # Shutdown: Cleanly stop Telegram bot
    await tg_app.updater.stop()
    await tg_app.stop()
    await tg_app.shutdown()
    print("🛑 Telegram Bot stopped.")


app = FastAPI(title="Missed-Call Lead Responder", lifespan=lifespan)

# Register routes
app.include_router(voice.router)
app.include_router(sms.router)


@app.get("/")
async def root():
    return {"status": "online", "message": "Missed-Call Responder API is running."}