# app/main.py
"""
Application entrypoint.

Boot philosophy: nothing in startup is allowed to prevent the app from
answering the phone. Every dependency is attempted, failures are logged loudly
and reported to Telegram, and the server comes up regardless - degraded is
always better than down when the alternative is a caller hearing an error.
"""

import asyncio
import contextlib
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import outbox, tasks
from app.clients import bootstrap_registry, refresh_loop, registry
from app.config import settings
from app.database import describe_target, ensure_indexes, ping_database
from app.logging_config import get_logger, setup_logging
from app.routes import health, sms, voice
from app.services.twilio_service import generate_safe_fallback_twiml

setup_logging(settings.LOG_LEVEL)
log = get_logger("main")

# Paths where a failure must still return valid TwiML, never a 500.
VOICE_PATHS = (
    "/webhook/voice",
    "/webhook/ivr-action",
    "/webhook/recording-action",
    "/webhook/recording-ready",
    "/webhook/call-status",
)

_background: list[asyncio.Task] = []


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("=" * 72)
    log.info("Missed-Lead Responder starting")
    log.info("Public base URL : %s", settings.PUBLIC_BASE_URL)
    log.info("Mongo target    : %s", describe_target())
    log.info("Data directory  : %s", settings.DATA_DIR)
    log.info("=" * 72)

    # 1. Outbox first - everything else can queue work into it.
    try:
        await asyncio.to_thread(outbox.init_db)
        tasks.register_all()
    except Exception as exc:
        log.critical("OUTBOX INIT FAILED - queued delivery is unavailable: %s", exc)

    # 2. Database (non-fatal).
    mongo_ok = await ping_database()
    if mongo_ok:
        await ensure_indexes()

    # 3. Client config, with full fallback chain.
    await bootstrap_registry()

    # 4. Telegram bot (non-fatal - the phone still works without it).
    tg_started = False
    try:
        from app.services.telegram_bot import get_telegram_app
        tg_app = get_telegram_app()
        await tg_app.initialize()
        await tg_app.start()
        await tg_app.updater.start_polling(drop_pending_updates=True)
        tg_started = True
        log.info("Telegram bot polling for button taps.")
    except Exception as exc:
        log.error("Telegram bot failed to start (continuing anyway): %s", exc)

    # 5. Background workers.
    _background.append(asyncio.create_task(outbox.worker_loop(), name="outbox"))
    _background.append(asyncio.create_task(refresh_loop(), name="client-refresh"))

    scheduler = None
    if settings.ENABLE_SCHEDULER:
        try:
            from app.scheduler.jobs import build_scheduler
            scheduler = build_scheduler()
            scheduler.start()
            log.info("Scheduler started: follow-ups, reminders, watchdog, heartbeat.")
        except Exception as exc:
            log.error("Scheduler failed to start: %s", exc)

    # 6. Tell the operator what state we came up in.
    await _report_boot_state(mongo_ok, tg_started)

    yield

    log.info("Shutting down...")
    for task in _background:
        task.cancel()
    for task in _background:
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task

    if scheduler:
        with contextlib.suppress(Exception):
            scheduler.shutdown(wait=False)

    if tg_started:
        with contextlib.suppress(Exception):
            from app.services.telegram_bot import get_telegram_app
            tg_app = get_telegram_app()
            await tg_app.updater.stop()
            await tg_app.stop()
            await tg_app.shutdown()

    log.info("Shutdown complete.")


async def _report_boot_state(mongo_ok: bool, tg_started: bool) -> None:
    status = registry.status()
    healthy = mongo_ok and status["client_count"] > 0 and status["source"] == "database"
    if healthy:
        log.info("BOOT OK - %d client(s) live from database.", status["client_count"])
        return

    problems = []
    if not mongo_ok:
        problems.append("• MongoDB unreachable")
    if status["client_count"] == 0:
        problems.append("• ZERO client configs loaded - calls will be rejected")
    if status["source"] != "database":
        problems.append(f"• Serving config from <code>{status['source']}</code>")
    if not tg_started:
        problems.append("• Telegram bot did not start")

    log.error("BOOT DEGRADED:\n%s", "\n".join(problems))
    try:
        from app.services.notify import notify_ops
        await notify_ops(
            "🚨 <b>Started in a degraded state</b>\n\n" + "\n".join(problems)
            + f"\n\nMongo target: <code>{describe_target()}</code>",
            dedupe_key="boot_degraded", cooldown=0,
        )
    except Exception:
        pass


app = FastAPI(title="Missed-Lead Responder", version="2.0.0", lifespan=lifespan)

app.include_router(health.router)
app.include_router(voice.router)
app.include_router(sms.router)


# ------------------------------------------------------------ error handling
def _is_voice_path(path: str) -> bool:
    return any(path.startswith(p) for p in VOICE_PATHS)


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    # A 403 from signature validation must stay a 403 - that is the security
    # boundary. Everything else on a voice path degrades into polite TwiML.
    if exc.status_code == 403 or not _is_voice_path(request.url.path):
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    log.error("HTTP %s on voice path %s: %s", exc.status_code, request.url.path, exc.detail)
    return Response(content=generate_safe_fallback_twiml(), media_type="application/xml")


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    log.error("Validation error on %s: %s", request.url.path, exc.errors())
    if _is_voice_path(request.url.path):
        return Response(content=generate_safe_fallback_twiml(), media_type="application/xml")
    return JSONResponse(status_code=422, content={"detail": "invalid request"})


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    log.exception("UNHANDLED ERROR on %s", request.url.path)
    try:
        from app.services.notify import notify_ops
        await notify_ops(
            f"🔥 <b>Unhandled error</b>\nPath: <code>{request.url.path}</code>\n"
            f"<code>{type(exc).__name__}: {str(exc)[:300]}</code>",
            dedupe_key=f"unhandled:{request.url.path}",
        )
    except Exception:
        pass

    if _is_voice_path(request.url.path):
        # The caller hears a polite message instead of Twilio's error tone.
        return Response(content=generate_safe_fallback_twiml(), media_type="application/xml")
    if request.url.path.startswith("/webhook/"):
        return Response(content="<Response></Response>", media_type="application/xml")
    return JSONResponse(status_code=500, content={"detail": "internal error"})


if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=settings.PORT, reload=False)
