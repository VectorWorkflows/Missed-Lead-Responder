# app/services/telegram_bot.py
"""
Telegram owner interface.

Key fix vs. the old version: we use parse_mode="HTML" with every interpolated
value passed through html.escape(). The old code used legacy Markdown with raw
customer text - any SMS containing _ * ` or [ made Telegram reject the whole
message, so the lead card silently never arrived.
"""

import html
import zoneinfo
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import Application, CallbackQueryHandler, ContextTypes

from app.config import settings
from app.logging_config import get_logger, redact_phone

log = get_logger("telegram")

_telegram_app: Optional[Application] = None


def get_telegram_app() -> Application:
    global _telegram_app
    if _telegram_app is None:
        _telegram_app = (
            Application.builder()
            .token(settings.TELEGRAM_BOT_TOKEN)
            .concurrent_updates(True)
            .build()
        )
        _telegram_app.add_handler(CallbackQueryHandler(handle_telegram_callback))
        _telegram_app.add_error_handler(_on_error)
    return _telegram_app


async def _on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.error("Unhandled Telegram error: %s", context.error, exc_info=context.error)


def e(value: Any) -> str:
    """HTML-escape anything before it goes into a Telegram message."""
    return html.escape(str(value if value is not None else ""), quote=False)


def local_time_str(dt: Any, tz_name: str, fmt: str = "%d %b, %I:%M %p %Z") -> str:
    if not isinstance(dt, datetime):
        try:
            dt = datetime.fromisoformat(str(dt))
        except Exception:
            return str(dt or "")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    try:
        return dt.astimezone(zoneinfo.ZoneInfo(tz_name or "UTC")).strftime(fmt)
    except Exception:
        return dt.astimezone(timezone.utc).strftime("%d %b, %I:%M %p UTC")


# --------------------------------------------------------------- lead alerts
async def send_telegram_lead_alert(client_config: dict, lead_data: dict) -> None:
    """
    Push the lead card. RAISES on failure so the outbox retries it - this is
    deliberate. A swallowed exception here is a silently lost lead.
    """
    if not client_config:
        raise ValueError("send_telegram_lead_alert called without a client config")

    chat_id = client_config.get("owner_telegram_chat_id")
    if not chat_id:
        log.warning("Client %s has no Telegram chat id; skipping alert.", client_config.get("_id"))
        return

    app = get_telegram_app()
    call_sid = lead_data.get("call_sid", "")
    caller = lead_data.get("caller_phone", "")
    tz_name = client_config.get("timezone", "UTC")

    headline = lead_data.get("headline") or "NEW LEAD"
    reply = lead_data.get("reply_text") or "(no message)"
    time_str = local_time_str(lead_data.get("call_time"), tz_name)

    lines = [
        f"🚨 <b>{e(headline)}</b>",
        "",
        f"🏢 <b>Business:</b> {e(client_config.get('business_name', 'Business'))}",
        f"📱 <b>Caller:</b> <code>{e(caller)}</code>",
        f"⏰ <b>Time:</b> {e(time_str)}",
        f"💬 <b>Message:</b> {e(reply)}",
    ]
    if lead_data.get("call_duration"):
        lines.append(f"⏱ <b>Call length:</b> {e(lead_data['call_duration'])}s")
    lines += ["", "<i>Tap to update status everywhere:</i>"]

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("📞 Contacted", callback_data=f"status:{call_sid}:CONTACTED"),
        InlineKeyboardButton("✅ Booked", callback_data=f"status:{call_sid}:BOOKED"),
        InlineKeyboardButton("❌ Lost", callback_data=f"status:{call_sid}:LOST"),
    ]])

    await app.bot.send_message(
        chat_id=chat_id,
        text="\n".join(lines),
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )
    log.info("Lead alert delivered to chat %s for caller %s", chat_id, redact_phone(caller))


async def send_voicemail_audio(client_config: dict, lead_data: dict) -> None:
    """
    Download the Twilio recording and push it as a Telegram voice note.

    Raises on a non-200 so the outbox retries - Twilio's media is often not
    ready at the instant the webhook fires, which is why the old code silently
    attached nothing.
    """
    chat_id = client_config.get("owner_telegram_chat_id")
    url = lead_data.get("recording_url")
    if not chat_id or not url or url == "N/A":
        return

    clean_url = url if url.endswith(".mp3") else f"{url}.mp3"
    auth = (settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)

    async with httpx.AsyncClient(timeout=30.0) as http:
        res = await http.get(clean_url, auth=auth, follow_redirects=True)

    if res.status_code != 200:
        raise RuntimeError(
            f"Recording not ready yet (HTTP {res.status_code}) for {clean_url}"
        )

    caller = lead_data.get("caller_phone", "")
    await get_telegram_app().bot.send_voice(
        chat_id=chat_id,
        voice=res.content,
        caption=f"🎙️ Voicemail from <code>{e(caller)}</code>",
        parse_mode=ParseMode.HTML,
    )
    log.info("Voicemail audio delivered to chat %s", chat_id)


async def send_plain(chat_id: int, text_html: str) -> None:
    await get_telegram_app().bot.send_message(
        chat_id=chat_id,
        text=text_html,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


# ------------------------------------------------------------ button handler
async def handle_telegram_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return
    await query.answer()

    data = query.data or ""
    parts = data.split(":")
    if len(parts) != 3 or parts[0] != "status":
        return
    _, call_sid, new_status = parts

    if new_status not in {"CONTACTED", "BOOKED", "LOST"}:
        return

    # Imported here to avoid a circular import at module load.
    from app.clients import registry
    from app.database import leads_collection
    from app import outbox

    try:
        lead = await leads_collection.find_one({"call_sid": call_sid})
    except Exception as exc:
        log.error("Mongo unavailable during button tap: %s", exc)
        await _safe_edit(query, "\n\n⚠️ <i>Database unreachable - status not saved. Try again shortly.</i>")
        return

    if not lead:
        await _safe_edit(query, f"\n\n⚠️ <i>Lead {e(call_sid)} not found.</i>")
        return

    client_config = await registry.get_by_id(lead.get("client_id", ""))

    # Authorisation: only the owner of THIS lead's business may change it.
    tapper_chat_id = query.message.chat_id if query.message else None
    expected = (client_config or {}).get("owner_telegram_chat_id")
    if expected and tapper_chat_id and int(expected) != int(tapper_chat_id):
        log.warning("Rejected status tap from unauthorised chat %s", tapper_chat_id)
        return

    try:
        await leads_collection.update_one(
            {"call_sid": call_sid},
            {"$set": {
                "owner_status": new_status,
                "owner_status_updated_at": datetime.now(timezone.utc),
            }},
        )
    except Exception as exc:
        log.error("Could not persist status: %s", exc)

    # Sheets update goes through the outbox so a Google outage cannot lose it.
    if client_config:
        await outbox.enqueue(
            "sheets_status",
            {"client": client_config, "call_sid": call_sid, "status": new_status},
            key=f"sheets_status:{call_sid}:{new_status}",
        )

    label = {"CONTACTED": "📞 CONTACTED", "BOOKED": "✅ BOOKED", "LOST": "❌ LOST"}[new_status]
    await _safe_edit(query, f"\n\n━━━━━━━━━━━━━━━━━━\n📌 <b>Status:</b> {e(label)}")
    log.info("Lead %s marked %s via Telegram.", call_sid, new_status)


async def _safe_edit(query, suffix_html: str) -> None:
    """
    Re-render the original card as HTML plus a suffix.

    We rebuild from message.text_html (not .text) so the original bold/code
    formatting survives the edit - re-sending .text as HTML would strip it.
    """
    try:
        original = query.message.text_html if query.message else ""
        await query.edit_message_text(
            text=f"{original}{suffix_html}",
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except TelegramError as exc:
        # "message is not modified" is harmless; anything else we log.
        if "not modified" not in str(exc).lower():
            log.error("Could not edit Telegram message: %s", exc)
