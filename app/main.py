# app/main.py
from fastapi import FastAPI
from app.routes import voice, sms

app = FastAPI(title="Missed-Call Lead Responder")

# Register routes
app.include_router(voice.router)
app.include_router(sms.router)

@app.get("/")
async def root():
    return {"status": "online", "message": "Missed-Call Responder API is running."}