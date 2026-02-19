"""
Event Processor — on-prem event routing hub.

Position in the architecture
─────────────────────────────
                              ┌─────────────────────────────┐
  Azure Service Bus           │         EventBus             │
  (inbound from cloud) ──────►│   (internal asyncio queue)   │
                              │                              │
  PollingService              │  Subscribers (in parallel):  │
  (on-prem polling)  ────────►│  1. EventProcessor  ← HERE   │
                              │     writes to BizOps SQL     │
                              │  2. WorkflowEngine           │
                              │     executes recipe steps    │
                              └─────────────────────────────┘

Both inbound paths (Service Bus and Polling) publish to the same EventBus.
EventProcessor is just another EventBus subscriber — it sees every event,
writes an audit row to the BizOps Events SQL database, and returns.
It does NOT block or gate the WorkflowEngine; both run in parallel via
asyncio.gather inside EventBus._dispatch().

Why audit-first?
  The BizOps row is written before workflow execution results are known.
  This guarantees that even if the workflow fails or the process crashes,
  the raw event is already persisted. The BizOpsWorkflowRunModel row is
  appended separately after the workflow completes.
"""
from __future__ import annotations

import logging
from typing import Optional

from app.core.event_bus import IntegrationEvent, get_event_bus
from app.core.workflow_engine import WorkflowRun
from app.db.bizops_session import BizOpsSessionLocal
from app.models.bizops_models import BizOpsEventModel, BizOpsWorkflowRunModel

logger = logging.getLogger(__name__)


class EventProcessor:
    """
    Subscribes to the internal EventBus and persists every event to BizOps SQL.

    Usage (called once at on-prem startup):
        processor = EventProcessor(event_source="service_bus")
        processor.start()
        ...
        processor.stop()   # at shutdown

    event_source values:
        "service_bus"  — for events arriving via Azure Service Bus
        "polling"      — override when the PollingService is the source
                         (in practice both are published to the same EventBus,
                          so "service_bus" is the safe default)
    """

    def __init__(self, event_source: str = "service_bus"):
        self._event_bus = get_event_bus()
        self._default_source = event_source

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Register as an EventBus subscriber. Call once at startup."""
        self._event_bus.subscribe("*", self._handle_event)
        logger.info("EventProcessor subscribed to EventBus (source=%s)", self._default_source)

    def stop(self) -> None:
        """Unregister from EventBus. Call at shutdown."""
        self._event_bus.unsubscribe("*", self._handle_event)
        logger.info("EventProcessor unsubscribed from EventBus")

    # ── Event handler ─────────────────────────────────────────────────────────

    async def _handle_event(self, event: IntegrationEvent) -> None:
        """
        Called by EventBus for every event (runs in parallel with WorkflowEngine).

        Persists an audit row to BizOps Events SQL.
        Errors here are logged but never re-raised — audit failure must not
        block or crash the workflow execution running in parallel.
        """
        try:
            async with BizOpsSessionLocal() as session:
                record = BizOpsEventModel(
                    event_id=event.event_id,
                    event_type=event.event_type,
                    service_name=event.service_name,
                    trigger_name=event.trigger_name,
                    payload=event.payload,
                    metadata_=event.metadata,
                    correlation_id=event.correlation_id,
                    source_record_id=event.source_record_id,
                    event_source=self._default_source,
                    schema_version=event.schema_version,
                )
                session.add(record)
                await session.commit()
            logger.debug(
                "BizOps audit row written: event_id=%s type=%s",
                event.event_id, event.event_type,
            )
        except Exception as exc:
            # Log and continue — audit logging must NEVER block event processing
            logger.error(
                "EventProcessor failed to persist event %s to BizOps SQL: %s",
                event.event_id, exc,
            )

    # ── Workflow run recorder ─────────────────────────────────────────────────

    async def record_workflow_run(
        self,
        run: WorkflowRun,
        workflow_name: str,
        trigger_event_id: Optional[str] = None,
    ) -> None:
        """
        Persist a BizOpsWorkflowRunModel row after a workflow completes.

        Called by on_prem/main.py after engine.run() returns so BizOps teams
        can correlate: which event → which workflow → success / failure.
        """
        try:
            async with BizOpsSessionLocal() as session:
                record = BizOpsWorkflowRunModel(
                    run_id=run.run_id,
                    workflow_id=run.workflow_id,
                    workflow_name=workflow_name,
                    trigger_event_id=trigger_event_id,
                    status=run.status.value,
                    error=run.error,
                    step_count=len(run.step_results),
                    started_at=run.started_at,
                    finished_at=run.finished_at,
                )
                session.add(record)
                await session.commit()
            logger.debug(
                "BizOps workflow run recorded: run_id=%s status=%s",
                run.run_id, run.status,
            )
        except Exception as exc:
            logger.error(
                "EventProcessor failed to record workflow run %s: %s",
                run.run_id, exc,
            )
