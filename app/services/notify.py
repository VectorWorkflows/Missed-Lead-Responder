# app/services/notify.py
"""
Operator alerts - messages to YOU about the health of the system itself,
as opposed to lead alerts which are about customers.

Deliberately de-duplicated: a broken integration would otherwise spam you
every few seconds and you would start ignoring the bot.
"""

import time
from typing import Optional

from app.config import settings
from app.logging_config import get_logger

log = get_logger("notify")

_last_sent: dict[str, float] = {}
_DEFAULT_COOLDOWN = 1800  # 30 minutes


async def notify_ops(message_html: str, dedupe_key: Optional[str] = None,
                     cooldown: int = _DEFAULT_COOLDOWN) -> None:
    chat_id = settings.ops_chat_id
    if not chat_id:
        log.warning("OPS ALERT (no chat id configured): %s", message_html)
        return

    if dedupe_key:
        last = _last_sent.get(dedupe_key, 0)
        if time.time() - last < cooldown:
            log.debug("Ops alert suppressed by cooldown (%s)", dedupe_key)
            return
        _last_sent[dedupe_key] = time.time()

    try:
        from app.services.telegram_bot import send_plain
        await send_plain(int(chat_id), message_html)
        log.info("Ops alert sent.")
    except Exception as exc:
        log.error("Could not send ops alert: %s", exc)
