# app/security.py
"""
Twilio webhook authentication.

Improved over the original in two ways:

1. It validates against SEVERAL candidate URLs. Signature checks are computed
   over the exact URL Twilio requested; behind a reverse proxy the URL the app
   reconstructs can differ (scheme, host, port). Checking the PUBLIC_BASE_URL
   form as well removes the most common cause of "every call suddenly 403s".

2. On failure it logs exactly which URL it tried and pings you on Telegram,
   because a silent 403 storm is indistinguishable from "the server is down"
   when you are staring at a Twilio error log at midnight.
"""

from typing import Iterable

from fastapi import HTTPException, Request
from twilio.request_validator import RequestValidator

from app.config import settings
from app.logging_config import get_logger

log = get_logger("security")

_validator = RequestValidator(settings.TWILIO_AUTH_TOKEN)


def _candidate_urls(request: Request) -> Iterable[str]:
    raw = str(request.url)
    yield raw

    if raw.startswith("http://"):
        yield "https://" + raw[len("http://"):]

    path_qs = request.url.path
    if request.url.query:
        path_qs = f"{path_qs}?{request.url.query}"
    yield f"{settings.PUBLIC_BASE_URL}{path_qs}"


async def verify_twilio_signature(request: Request) -> None:
    if not settings.TWILIO_VALIDATE_SIGNATURE:
        return

    signature = request.headers.get("X-Twilio-Signature", "")
    form = await request.form()          # cached by Starlette; routes re-read it freely
    params = {k: v for k, v in form.multi_items()}

    tried = []
    for url in _candidate_urls(request):
        tried.append(url)
        if _validator.validate(url, params, signature):
            return

    log.error(
        "TWILIO SIGNATURE REJECTED. path=%s tried=%s has_signature=%s. "
        "If ALL calls are failing, your PUBLIC_BASE_URL or reverse proxy headers are wrong.",
        request.url.path, tried, bool(signature),
    )

    try:
        from app.services.notify import notify_ops
        await notify_ops(
            "⚠️ <b>Twilio signature rejected</b>\n"
            f"Path: <code>{request.url.path}</code>\n"
            "If calls are failing, check <code>PUBLIC_BASE_URL</code> and that your "
            "reverse proxy forwards the original Host and <code>X-Forwarded-Proto</code>.",
            dedupe_key="sig_reject",
        )
    except Exception:
        pass

    raise HTTPException(status_code=403, detail="Invalid Twilio signature")
