"""
On-Premise Integration Platform — standalone process entry point.

Run on Windows Server (or any host) directly:
    python -m app.on_prem.main

Run as a Windows Service (after installation):
    python -m app.on_prem.windows_service start

How this maps to the architecture diagram
──────────────────────────────────────────

  Azure (cloud)                      On-Premise (this process)
  ─────────────────────              ──────────────────────────────────────────────
  Key Vault          ◄── HTTPS ───── step 2: KeyVaultClient.connect()
  Service Bus topic  ──── pull ────► step 7: ServiceBusProcessor
  App Insights       ◄── traces ──── step 1: setup_telemetry()
  Blob Storage       ◄── via AI ──── (App Insights exports to Blob)

  On-prem data flows
  ──────────────────
  ServiceBusProcessor  receives cloud events → publishes to EventBus
                                                      │
                          ┌─────────────────────────── ▼ ──────────────────────────┐
                          │               EventBus (asyncio queue)                  │
                          │  Subscriber A: EventProcessor                           │
                          │    → INSERT into BizOps Events SQL (audit log)          │
                          │  Subscriber B: WorkflowEngine                           │
                          │    → executes workflow recipes                          │
                          │    → calls cloud APIs (Salesforce PATCH, Jira PUT …)   │
                          │    → reads/writes CRDB/ERDB SQL                         │
                          └─────────────────────────────────────────────────────────┘

  PollingService (step 9)
    Every 5 min: polls Jira, Salesforce, UKG … directly
    → publishes to same EventBus → same two subscribers above
    → also writes polling cursors to CRDB/ERDB SQL

  HealthServer (step 10)
    GET /health   → JSON {"status": "ok", "uptime_s": …}
    GET /metrics  → Prometheus text exposition (scraped by Prometheus/Grafana)

Startup sequence (numbered in code below)
──────────────────────────────────────────
  1. Telemetry          — OpenTelemetry → App Insights (traces from this process)
  2. Key Vault          — fetch all secrets (outbound HTTPS to Azure only)
  3. CRDB/ERDB SQL      — create tables if missing (operational DB)
  4. BizOps Events SQL  — create tables if missing (audit DB)
  5. Registry           — auto-discover all connector classes
  6. EventBus           — start the async dispatch worker loop
  7. WorkflowEngine     — instantiate (auto-subscribes to EventBus on __init__)
  8. EventProcessor     — subscribe to EventBus (writes BizOps SQL per event)
  9. ServiceBusProcessor — start consuming Azure Service Bus queue
  10. PollingService    — start per-connector polling loops
  11. HealthServer      — start /health + /metrics HTTP server
  → run until SIGTERM / SIGINT (or Windows Service stop signal)
  → graceful shutdown in reverse order
"""
from __future__ import annotations

import asyncio
import logging
import platform
import signal
import sys
import time
import uuid
from typing import Optional

from app.core.event_bus import IntegrationEvent, get_event_bus
from app.core.polling_service import get_polling_service
from app.core.registry import get_registry
from app.core.workflow_engine import get_workflow_engine
from app.db.bizops_session import bizops_engine
from app.db.session import engine
from app.infrastructure.key_vault import get_key_vault
from app.infrastructure.service_bus import ServiceBusProcessor
from app.infrastructure.telemetry import setup_telemetry
from app.integrations.catalog import IntegrationCatalog
from app.models.bizops_models import BizOpsBase
from app.models.db_models import Base
from app.on_prem.event_processor import EventProcessor
from app.on_prem.health_server import HealthServer
from app.on_prem.metrics import EVENT_BUS_QUEUE_SIZE, PROCESS_UPTIME, SERVICE_BUS_MESSAGES
from config.settings import get_settings

settings = get_settings()

logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG else logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


# ── Service Bus message → EventBus ────────────────────────────────────────────

def _make_service_bus_handler(event_bus):
    """
    Returns the async handler passed to ServiceBusProcessor.

    When a message arrives from Azure Service Bus the handler reconstructs
    the IntegrationEvent from the JSON body and publishes it to the local
    EventBus so both EventProcessor (BizOps SQL) and WorkflowEngine see it.
    """
    async def _handler(body: dict) -> None:
        try:
            event = IntegrationEvent(
                event_id=body.get("event_id") or str(uuid.uuid4()),
                event_type=body.get("event_type", ""),
                service_name=body.get("service_name", ""),
                trigger_name=body.get("trigger_name", ""),
                payload=body.get("payload", {}),
                metadata=body.get("metadata", {}),
                correlation_id=body.get("correlation_id"),
                source_record_id=body.get("source_record_id"),
                schema_version=body.get("schema_version", "1.0"),
            )
            logger.info(
                "Service Bus → EventBus: %s (id=%s)", event.event_type, event.event_id
            )
            SERVICE_BUS_MESSAGES.labels(status="received").inc()
            await event_bus.publish(event)
        except Exception as exc:
            SERVICE_BUS_MESSAGES.labels(status="error").inc()
            logger.error("Failed to process Service Bus message: %s", exc)

    return _handler


# ── Uptime / queue-size updater ────────────────────────────────────────────────

async def _metrics_updater(start_time: float, event_bus) -> None:
    """Background task: refreshes process-level Prometheus gauges every 15 s."""
    while True:
        PROCESS_UPTIME.set(time.monotonic() - start_time)
        try:
            EVENT_BUS_QUEUE_SIZE.set(event_bus._queue.qsize())
        except Exception:
            pass
        await asyncio.sleep(15)


# ── Main entry point ───────────────────────────────────────────────────────────

async def main(_external_stop_event: Optional[asyncio.Event] = None) -> None:
    """
    Async entry point for both direct execution and Windows Service mode.

    Parameters
    ----------
    _external_stop_event : asyncio.Event, optional
        When provided (Windows Service mode) the function uses this event
        instead of registering OS signal handlers.  The Windows Service
        wrapper sets this event when the SCM requests a stop.
    """
    start_time = time.monotonic()
    logger.info("=== On-Premise Integration Platform starting ===")

    # ── 1. Telemetry — OpenTelemetry → Azure Application Insights ─────────────
    setup_telemetry()
    logger.info("Telemetry configured")

    # ── 2. Azure Key Vault — fetch credentials (outbound HTTPS to Azure) ──────
    kv = await get_key_vault()
    logger.info("Key Vault ready")

    # ── 3. CRDB/ERDB SQL — operational database ────────────────────────────────
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("CRDB/ERDB tables verified (operational DB)")

    # ── 4. BizOps Events SQL — separate audit database ─────────────────────────
    async with bizops_engine.begin() as conn:
        await conn.run_sync(BizOpsBase.metadata.create_all)
    logger.info("BizOps Events tables verified (audit DB)")

    # ── 5. Integration registry — auto-discover all connector classes ──────────
    registry = get_registry()
    registry.discover("app.integrations")
    logger.info("Connectors discovered: %s", registry.list_services())

    # ── 6. EventBus — start the async dispatch worker loop ─────────────────────
    event_bus = get_event_bus()
    await event_bus.start()
    logger.info("EventBus started")

    # ── 7. WorkflowEngine — handles workflows defined via the REST API ─────────
    workflow_engine = get_workflow_engine()
    logger.info("WorkflowEngine ready")

    # ── 8. Workflow catalog — code-first handler (add new integration = add one method)
    catalog = IntegrationCatalog()
    catalog.start()

    # ── 9. EventProcessor — subscribes to EventBus, writes BizOps SQL ─────────
    event_processor = EventProcessor(event_source="service_bus")
    event_processor.start()
    logger.info("EventProcessor started")

    # ── 10. ServiceBusProcessor — pull messages from Azure Service Bus ─────────
    sb_handler = _make_service_bus_handler(event_bus)
    sb_processor = ServiceBusProcessor(handler=sb_handler)
    sb_task = asyncio.create_task(
        sb_processor.start(), name="service-bus-processor"
    )
    logger.info(
        "ServiceBusProcessor started (queue=%s)", settings.AZURE_SERVICE_BUS_QUEUE_NAME
    )

    # ── 11. PollingService — on-prem polls cloud services directly ─────────────
    polling = get_polling_service()
    await polling.start()
    logger.info("PollingService started (%d job(s))", len(polling._jobs))

    # ── 12. HealthServer — /health + /metrics ─────────────────────────────────
    health_server = HealthServer(
        host=getattr(settings, "METRICS_HOST", "0.0.0.0"),
        port=getattr(settings, "METRICS_PORT", 9090),
    )
    health_task = asyncio.create_task(health_server.serve(), name="health-server")

    # ── 13. Background metrics updater ────────────────────────────────────────
    metrics_task = asyncio.create_task(
        _metrics_updater(start_time, event_bus), name="metrics-updater"
    )

    # Mark process as ready AFTER all services are up
    health_server.mark_ready()

    logger.info(
        "=== On-Premise Integration Platform ready | Services: %s ===",
        registry.list_services(),
    )

    # ── Wait for stop signal ───────────────────────────────────────────────────
    stop_event = _external_stop_event or asyncio.Event()

    if _external_stop_event is None:
        # Register OS signal handlers.
        # Windows (win32): loop.add_signal_handler() raises NotImplementedError.
        # Use signal.signal() instead — it works on all platforms but delivers
        # the callback in the main thread (which is fine here).
        def _signal_handler(*_) -> None:
            logger.info("Shutdown signal received")
            # Schedule stop_event.set() on the running loop from any thread
            loop = asyncio.get_event_loop()
            loop.call_soon_threadsafe(stop_event.set)

        if platform.system() == "Windows":
            signal.signal(signal.SIGTERM, _signal_handler)
            signal.signal(signal.SIGINT, _signal_handler)
        else:
            # Linux / macOS: prefer loop.add_signal_handler (async-safe)
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(sig, stop_event.set)

    await stop_event.wait()

    # ── Graceful shutdown (reverse startup order) ─────────────────────────────
    logger.info("=== On-Premise Platform shutting down ===")

    # Stop background tasks first
    metrics_task.cancel()
    health_task.cancel()
    try:
        await asyncio.gather(metrics_task, health_task, return_exceptions=True)
    except Exception:
        pass
    logger.info("HealthServer stopped")

    # Stop polling so no new events are generated
    await polling.stop()
    logger.info("PollingService stopped")

    # Cancel the Service Bus consumer
    sb_task.cancel()
    try:
        await sb_task
    except asyncio.CancelledError:
        pass
    logger.info("ServiceBusProcessor stopped")

    # Unsubscribe catalog handlers and EventProcessor
    catalog.stop()
    event_processor.stop()

    # Drain and stop the EventBus (allows in-flight events to complete)
    await event_bus.stop()
    logger.info("EventBus stopped")

    # Close infrastructure connections
    await kv.close()
    await engine.dispose()
    await bizops_engine.dispose()

    logger.info("=== On-Premise shutdown complete ===")


if __name__ == "__main__":
    asyncio.run(main())
