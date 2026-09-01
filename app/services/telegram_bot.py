# app/services/telegram_bot.py
import asyncio
import httpx
import zoneinfo
from datetime import datetime, timezone
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, ContextTypes
from app.config import settings
from app.database import leads_collection, client_configs_collection
from app.services.sheets_service import update_sheet_status

telegram_app: Application = None

def get_telegram_app() -> Application:
    """Initializes and returns the global Telegram bot Application instance."""
    global telegram_app
    if telegram_app is None:
        telegram_app = Application.builder().token(settings.TELEGRAM_BOT_TOKEN).build()
        telegram_app.add_handler(CallbackQueryHandler(handle_telegram_callback))
    return telegram_app

async def send_telegram_lead_alert(client_config: dict, lead_data: dict):
    """Sends a rich lead alert card to the owner's Telegram with 1-tap action buttons."""
    if not client_config:
        print("⚠️ No client config found in database. Cannot send Telegram alert.")
        return
        
    chat_id = client_config.get("owner_telegram_chat_id")
    if not chat_id:
        print(f"⚠️ No Telegram chat ID configured for client {client_config.get('_id')}")
        return

    app = get_telegram_app()
    call_sid = lead_data.get("call_sid", "")
    caller = lead_data.get("caller_phone", "")
    business_name = client_config.get("business_name", "Business")
    reply = lead_data.get("reply_text", "")
    recording_url = lead_data.get("recording_url")

    # --- DYNAMIC TIMEZONE FIX ---
    call_time = lead_data.get("call_time")
    client_tz_str = client_config.get("timezone", "UTC")
    
    if isinstance(call_time, datetime):
        try:
            # Convert UTC database time to the client's local timezone
            client_tz = zoneinfo.ZoneInfo(client_tz_str)
            local_time = call_time.astimezone(client_tz)
            time_str = local_time.strftime("%I:%M %p %Z")
        except Exception as e:
            print(f"⚠️ Timezone conversion error: {e}. Falling back to UTC.")
            time_str = call_time.strftime("%I:%M %p UTC")
    else:
        time_str = str(call_time or "")
    # ----------------------------

    text = (
        f"🚨 *NEW MISSED-CALL LEAD*\n\n"
        f"🏢 *Business:* {business_name}\n"
        f"📱 *Caller:* `{caller}`\n"
        f"⏰ *Missed:* {time_str}\n"
        f"💬 *Reply:* \"{reply}\"\n\n"
        f"_Tap to update status in Google Sheets:_"
    )

    keyboard = [
        [
            InlineKeyboardButton("📞 Contacted", callback_data=f"status:{call_sid}:CONTACTED"),
            InlineKeyboardButton("✅ Booked", callback_data=f"status:{call_sid}:BOOKED"),
            InlineKeyboardButton("❌ Lost", callback_data=f"status:{call_sid}:LOST")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    try:
        await app.bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )
        print(f"📲 Telegram lead alert sent to owner (Chat ID: {chat_id})")

        if recording_url and recording_url != "N/A":
            clean_url = recording_url if recording_url.endswith(".mp3") else f"{recording_url}.mp3"
            auth = (settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
            
            async with httpx.AsyncClient(timeout=15.0) as client:
                audio_res = await client.get(clean_url, auth=auth, follow_redirects=True)
                if audio_res.status_code == 200:
                    await app.bot.send_voice(
                        chat_id=chat_id,
                        voice=audio_res.content,
                        caption=f"🎙️ Voicemail from `{caller}`",
                        parse_mode="Markdown"
                    )
    except Exception as e:
        print(f"❌ Failed to send Telegram alert: {e}")

async def handle_telegram_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Triggered when the owner taps one of the inline status buttons."""
    query = update.callback_query
    await query.answer()

    data = query.data
    if not data or not data.startswith("status:"):
        return

    parts = data.split(":")
    if len(parts) != 3:
        return

    _, call_sid, new_status = parts

    lead = await leads_collection.find_one({"call_sid": call_sid})
    if not lead:
        await query.edit_message_text(f"⚠️ Lead `{call_sid}` not found.")
        return

    await leads_collection.update_one(
        {"call_sid": call_sid},
        {"$set": {
            "owner_status": new_status,
            "owner_status_updated_at": datetime.now(timezone.utc)
        }}
    )

    client_config = await client_configs_collection.find_one({"_id": lead["client_id"]})
    if client_config:
        await asyncio.to_thread(update_sheet_status, client_config, call_sid, new_status)

    status_emoji = {
        "CONTACTED": "📞 CONTACTED",
        "BOOKED": "✅ BOOKED",
        "LOST": "❌ LOST"
    }
    label = status_emoji.get(new_status, new_status)

    original_text = query.message.text
    updated_text = f"{original_text}\n\n━━━━━━━━━━━━━━━━━━━━\n📌 *Status Updated:* **{label}**"

    await query.edit_message_text(text=updated_text, parse_mode="Markdown")
    print(f"✅ Lead {call_sid} status updated to '{new_status}' via Telegram button tap.")