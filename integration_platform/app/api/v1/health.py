"""
Health check endpoints.

GET /api/v1/health         Full health status (DB, Service Bus, integrations)
GET /api/v1/health/live    Kubernetes liveness probe (always 200 if process is up)
GET /api/v1/health/ready   Kubernetes readiness probe
GET /api/v1/health/polling Polling job statuses
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Response
from sqlalchemy import text

from app.core.polling_service import get_polling_service
from app.core.registry import get_registry
from app.core.retry_handler import RetryHandler
from app.db.session import AsyncSessionLocal
from app.models.schemas import ComponentHealth, HealthResponse, PollingStatusResponse
from config.settings import get_settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/health", tags=["Health"])
settings = get_settings()

_retry_handler = RetryHandler()


@router.get("/live", summary="Liveness probe")
async def liveness():
    return {"alive": True}


@router.get("/ready", summary="Readiness probe")
async def readiness(response: Response):
    healthy = True
    components = {}

    # DB check
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
        components["database"] = ComponentHealth(status="healthy")
    except Exception as exc:
        components["database"] = ComponentHealth(status="unhealthy", details=str(exc))
        healthy = False

    response.status_code = 200 if healthy else 503
    return {"ready": healthy, "components": {k: v.model_dump() for k, v in components.items()}}


@router.get("/", response_model=HealthResponse, summary="Full health report")
async def full_health():
    components: dict[str, ComponentHealth] = {}

    # DB
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
        components["database"] = ComponentHealth(status="healthy")
    except Exception as exc:
        components["database"] = ComponentHealth(status="unhealthy", details=str(exc))

    # Azure Service Bus
    if settings.AZURE_SERVICE_BUS_CONNECTION_STRING:
        components["service_bus"] = ComponentHealth(
            status="healthy", details="Connection string configured"
        )
    else:
        components["service_bus"] = ComponentHealth(
            status="degraded", details="No connection string — no-op mode"
        )

    # Azure Key Vault
    if settings.AZURE_KEY_VAULT_URL:
        components["key_vault"] = ComponentHealth(
            status="healthy", details=f"URL: {settings.AZURE_KEY_VAULT_URL}"
        )
    else:
        components["key_vault"] = ComponentHealth(
            status="degraded", details="Not configured — reading from env vars"
        )

    # Integrations
    registry = get_registry()
    components["integrations"] = ComponentHealth(
        status="healthy",
        details=f"Registered: {', '.join(registry.list_services())}",
    )

    # Circuit breakers
    circuit_breakers = _retry_handler.get_all_states()

    overall = (
        "healthy"
        if all(c.status == "healthy" for c in components.values())
        else "degraded"
    )

    return HealthResponse(
        status=overall,
        version=settings.APP_VERSION,
        environment=settings.ENVIRONMENT,
        components=components,
        circuit_breakers=circuit_breakers,
    )


@router.get("/polling", response_model=PollingStatusResponse, summary="Polling job statuses")
async def polling_status():
    raw = get_polling_service().get_status()
    from app.models.schemas import PollingJobStatus
    jobs = {
        key: PollingJobStatus(**val)
        for key, val in raw.items()
    }
    return PollingStatusResponse(jobs=jobs)
