"""
On-Premise Integration Platform — standalone process entry point.

Run on Insurity infrastructure (NOT inside Azure Functions):
    python -m app.on_prem.main

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

  PollingService (step 8)
    Every 5 min: polls Jira, Salesforce, UKG … directly
    → publishes to same EventBus → same two subscribers above
    → also writes polling cursors to CRDB/ERDB SQL

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
  → run until SIGTERM / SIGINT
  → graceful shutdown in reverse order
"""
from __future__ import annotations

import asyncio
import logging
import signal
import uuid

from app.core.event_bus import IntegrationEvent, get_event_bus
from app.core.polling_service import get_polling_service
from app.core.registry import get_registry
from app.core.workflow_engine import get_workflow_engine
from app.db.bizops_session import bizops_engine
from app.db.session import engine
from app.infrastructure.key_vault import get_key_vault
from app.infrastructure.service_bus import ServiceBusProcessor
from app.infrastructure.telemetry import setup_telemetry
from app.models.bizops_models import BizOpsBase
from app.models.db_models import Base
from app.on_prem.event_processor import EventProcessor
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
        await event_bus.publish(event)

    return _handler


# ── Main entry point ──────────────────────────────────────────────────────────

async def main() -> None:
    logger.info("=== On-Premise Integration Platform starting ===")

    # ── 1. Telemetry — OpenTelemetry → Azure Application Insights ─────────────
    setup_telemetry()
    logger.info("Telemetry configured")

    # ── 2. Azure Key Vault — fetch credentials (outbound HTTPS to Azure) ──────
    # On-prem never stores secrets locally; all credentials live in Key Vault.
    # This is the ONLY call that goes out to Azure at startup.
    kv = await get_key_vault()
    logger.info("Key Vault ready")

    # ── 3. CRDB/ERDB SQL — operational database ────────────────────────────────
    # Tables: polling_cursors, workflow_runs, workflow_definitions, connections
    # Used by WorkflowEngine and PollingService during processing.
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("CRDB/ERDB tables verified (operational DB)")

    # ── 4. BizOps Events SQL — separate audit database ─────────────────────────
    # Tables: bizops_events, bizops_workflow_runs
    # Append-only audit log written by EventProcessor. Never touched during
    # workflow execution so it never blocks operational processing.
    async with bizops_engine.begin() as conn:
        await conn.run_sync(BizOpsBase.metadata.create_all)
    logger.info("BizOps Events tables verified (audit DB)")

    # ── 5. Integration registry — auto-discover all connector classes ──────────
    # Imports every module under app/integrations/ and registers subclasses
    # of BaseIntegration. No code changes needed when adding new connectors.
    registry = get_registry()
    registry.discover("app.integrations")
    logger.info("Connectors discovered: %s", registry.list_services())

    # ── 6. EventBus — start the async dispatch worker loop ─────────────────────
    # The EventBus holds an asyncio.Queue and a background task that dequeues
    # events and fans them out to all subscribers in parallel.
    event_bus = get_event_bus()
    await event_bus.start()
    logger.info("EventBus started")

    # ── 7. WorkflowEngine — auto-subscribes to EventBus on instantiation ──────
    # Calling get_workflow_engine() creates the singleton and registers its
    # _on_event handler on the EventBus topic "*".
    # From this point every event published to the bus triggers matching recipes.
    workflow_engine = get_workflow_engine()
    logger.info("WorkflowEngine ready")

    # ── 8. EventProcessor — subscribes to EventBus, writes BizOps SQL ─────────
    # Must subscribe AFTER EventBus.start() so the dispatch loop is running.
    # EventProcessor and WorkflowEngine both subscribe to "*" — they run in
    # parallel via asyncio.gather inside EventBus._dispatch().
    event_processor = EventProcessor(event_source="service_bus")
    event_processor.start()
    logger.info("EventProcessor started")

    # ── 9. ServiceBusProcessor — pull messages from Azure Service Bus ──────────
    # Long-running asyncio task.  Reads from queue AZURE_SERVICE_BUS_QUEUE_NAME.
    # Each message is reconstructed into an IntegrationEvent and published to
    # the local EventBus → hits EventProcessor (BizOps log) + WorkflowEngine.
    sb_handler = _make_service_bus_handler(event_bus)
    sb_processor = ServiceBusProcessor(handler=sb_handler)
    sb_task = asyncio.create_task(
        sb_processor.start(), name="service-bus-processor"
    )
    logger.info(
        "ServiceBusProcessor started (queue=%s)", settings.AZURE_SERVICE_BUS_QUEUE_NAME
    )

    # ── 10. PollingService — on-prem polls cloud services directly ─────────────
    # Discovers every @trigger(type=POLLING) across all connectors and starts
    # one asyncio task per trigger at its configured interval.
    # Poll results are published to the EventBus, so EventProcessor (BizOps)
    # and WorkflowEngine see them exactly like Service Bus events.
    polling = get_polling_service()
    await polling.start()
    logger.info("PollingService started (%d job(s))", len(polling._jobs))

    logger.info(
        "=== On-Premise Integration Platform ready | Services: %s ===",
        registry.list_services(),
    )

    # ── Run until SIGTERM / SIGINT ─────────────────────────────────────────────
    stop_event = asyncio.Event()

    def _signal_handler() -> None:
        logger.info("Shutdown signal received")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _signal_handler)

    await stop_event.wait()

    # ── Graceful shutdown (reverse startup order) ─────────────────────────────
    logger.info("=== On-Premise Platform shutting down ===")

    # Stop polling first so no new events are generated
    await polling.stop()
    logger.info("PollingService stopped")

    # Cancel the Service Bus consumer
    sb_task.cancel()
    try:
        await sb_task
    except asyncio.CancelledError:
        pass
    logger.info("ServiceBusProcessor stopped")

    # Unsubscribe EventProcessor (no new BizOps writes after this)
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
