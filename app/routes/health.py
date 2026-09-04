# app/routes/health.py
"""Health endpoints. /health is what the Docker healthcheck hits."""

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app import outbox
from app.clients import registry
from app.config import settings
from app.database import describe_target, ping_database
from app.services.sheets_service import sheets_configured

router = APIRouter(tags=["Health"])


@router.get("/health")
async def health():
    """Cheap liveness probe - no external calls."""
    clients = registry.status()
    ok = clients["client_count"] > 0
    return JSONResponse(
        status_code=200 if ok else 503,
        content={
            "status": "ok" if ok else "degraded",
            "clients_loaded": clients["client_count"],
            "config_source": clients["source"],
        },
    )


@router.get("/health/deep")
async def health_deep():
    """Full dependency check. Handy to curl after a deploy."""
    mongo_ok = await ping_database()
    queue = await outbox.stats()
    clients = registry.status()

    degraded = (
        not mongo_ok
        or clients["source"] in ("disk_snapshot", "env_fallback")
        or queue.get("dead", 0) > 0
        or (queue.get("oldest_pending_age_seconds") or 0) > 900
    )

    return JSONResponse(
        status_code=200,
        content={
            "status": "degraded" if degraded else "ok",
            "mongo": {"connected": mongo_ok, "target": describe_target()},
            "clients": clients,
            "outbox": queue,
            "sheets_configured": sheets_configured(),
            "public_base_url": settings.PUBLIC_BASE_URL,
            "signature_validation": settings.TWILIO_VALIDATE_SIGNATURE,
        },
    )


@router.get("/")
async def root():
    return {"status": "online", "service": "Missed-Lead Responder"}
