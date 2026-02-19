"""
Integration Platform — FastAPI application entry point.

Architecture highlights:
  • Single entry point per service: POST /api/v1/integrations/{service}/actions/{action}
  • Single webhook receiver:        POST /api/v1/webhooks/{service}
  • Plugin-based integrations:      Add @action/@trigger to a class — zero infra changes
  • Event-driven:                   Every trigger publishes to EventBus → Service Bus
  • Workato-style workflows:        POST /api/v1/workflows/ to define recipes
  • Full observability:             OpenTelemetry → Application Insights
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

# Azure Functions sets FUNCTIONS_WORKER_RUNTIME in every worker process.
# When running there, the Timer Trigger in function_app.py owns the polling
# schedule — starting the in-process asyncio loops here would duplicate work.
_RUNNING_IN_AZURE_FUNCTIONS = bool(os.environ.get("FUNCTIONS_WORKER_RUNTIME"))

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1 import events, health, integrations, webhooks, workflows
from app.core.event_bus import get_event_bus
from app.core.polling_service import get_polling_service
from app.core.registry import get_registry
from app.infrastructure.key_vault import get_key_vault
from app.infrastructure.service_bus import get_bridge
from app.infrastructure.telemetry import setup_telemetry
from app.models.db_models import Base
from app.db.session import engine
from config.settings import get_settings

settings = get_settings()

logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG else logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


# ─── Lifespan (startup / shutdown) ────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown lifecycle."""

    # 1. Telemetry
    setup_telemetry()
    logger.info("=== Integration Platform starting up ===")

    # 2. Database — create tables if not exist (use Alembic for migrations in prod)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables verified")

    # 3. Key Vault
    kv = await get_key_vault()
    logger.info("Key Vault ready")

    # 4. Integration registry — auto-discover all integration classes
    registry = get_registry()
    registry.discover("app.integrations")

    # 5. Service Bus bridge — subscribe EventBus → Azure Service Bus
    bridge = await get_bridge()
    event_bus = get_event_bus()

    async def _forward_to_service_bus(event):
        await bridge.send_event(event.as_dict())

    event_bus.subscribe("*", _forward_to_service_bus)

    # 6. EventBus worker loop
    await event_bus.start()
    logger.info("EventBus started")

    # 7. Polling service — skip in Azure Functions (Timer Trigger owns the schedule)
    polling = get_polling_service()
    if _RUNNING_IN_AZURE_FUNCTIONS:
        logger.info("Azure Functions detected — polling handled by Timer Trigger, skipping in-process loops")
    else:
        await polling.start()
        logger.info("Polling service started")

    logger.info(
        "=== Integration Platform ready | Services: %s ===",
        registry.list_services(),
    )

    yield   # ← application running

    # ── Shutdown ──────────────────────────────────────────────────────────────
    logger.info("=== Integration Platform shutting down ===")
    if not _RUNNING_IN_AZURE_FUNCTIONS:
        await polling.stop()
    await event_bus.stop()
    await bridge.close()
    await kv.close()
    await engine.dispose()
    logger.info("=== Shutdown complete ===")


# ─── App factory ──────────────────────────────────────────────────────────────

def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        description=__doc__,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.ALLOWED_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Global exception handler
    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        logger.exception("Unhandled exception: %s", exc)
        return JSONResponse(
            status_code=500,
            content={"status": "error", "error": str(exc)},
        )

    # ── Include routers ────────────────────────────────────────────────────────
    PREFIX = "/api/v1"
    app.include_router(health.router, prefix=PREFIX)
    app.include_router(integrations.router, prefix=PREFIX)
    app.include_router(webhooks.router, prefix=PREFIX)
    app.include_router(workflows.router, prefix=PREFIX)
    app.include_router(events.router, prefix=PREFIX)

    # Root redirect to docs
    @app.get("/", include_in_schema=False)
    async def root():
        return {"message": f"{settings.APP_NAME} v{settings.APP_VERSION}", "docs": "/docs"}

    return app


app = create_app()
