"""
BizOps Events database models.

This is the SECOND on-prem database — the append-only audit log.
Completely separate from CRDB/ERDB (the operational database in db_models.py).

Why separate?
  - BizOps / Operations teams query this for reporting and compliance without
    touching the operational database.
  - Different backup and retention policies (audit logs kept longer).
  - If CRDB/ERDB has an issue, audit logging continues independently.
  - Matches the architecture design: two distinct on-prem SQL databases.

Who writes here?
  EventProcessor (app/on_prem/event_processor.py) — one row per IntegrationEvent,
  written before the WorkflowEngine runs so nothing is lost even if processing fails.

Who reads here?
  - BizOps / Ops teams via SQL queries or BI tools.
  - Grafana dashboards (event volumes, error rates, latency KPIs).
  - GET /api/v1/bizops/events  (future read-only API endpoint).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Index, Integer, String, Text
from sqlalchemy import JSON
from sqlalchemy import Uuid
from sqlalchemy.orm import DeclarativeBase


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class BizOpsBase(DeclarativeBase):
    """
    Separate DeclarativeBase keeps BizOps tables isolated in their own database.
    Never mix BizOpsBase and the operational Base — they point to different engines.
    """


class BizOpsEventModel(BizOpsBase):
    """
    Immutable audit record of every IntegrationEvent received and processed.

    Written once by EventProcessor when an event enters the on-prem platform.
    Never updated after creation — strictly append-only.

    Sources:
      event_source = "service_bus"  — arrived via Azure Service Bus
                   = "polling"      — discovered by on-prem PollingService
                   = "manual"       — triggered manually via API
    """
    __tablename__ = "bizops_events"

    id            = Column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id      = Column(String(64),  unique=True, nullable=False, index=True)
    event_type    = Column(String(128), nullable=False, index=True)
    service_name  = Column(String(64),  nullable=False, index=True)
    trigger_name  = Column(String(128), nullable=False)
    payload       = Column(JSON, nullable=False, default=dict)
    metadata_     = Column("metadata", JSON, nullable=False, default=dict)
    correlation_id   = Column(String(64),  index=True)
    source_record_id = Column(String(128), index=True)
    # Where this event originated within the on-prem platform
    event_source  = Column(String(32), nullable=False, default="service_bus")
    schema_version = Column(String(16), default="1.0")
    received_at   = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    __table_args__ = (
        # Time-range queries per service (most common BizOps access pattern)
        Index("ix_biz_service_received",  "service_name", "received_at"),
        # Filter by event type over time (e.g. "show me all initiative-close events this week")
        Index("ix_biz_type_received",     "event_type",   "received_at"),
        # Trace a single business transaction end-to-end
        Index("ix_biz_correlation",       "correlation_id"),
    )


class BizOpsWorkflowRunModel(BizOpsBase):
    """
    Audit record of every workflow recipe execution.

    Mirrors WorkflowRunModel from db_models.py (CRDB/ERDB) but lives here
    so BizOps teams can correlate: which event → which workflow → which outcome.
    Written by EventProcessor after WorkflowEngine completes a run.
    """
    __tablename__ = "bizops_workflow_runs"

    id           = Column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id       = Column(String(64),  unique=True, nullable=False, index=True)
    workflow_id  = Column(String(64),  nullable=False, index=True)
    workflow_name = Column(String(256), nullable=False, default="")
    trigger_event_id = Column(String(64), index=True)   # links back to BizOpsEventModel
    status       = Column(String(16),  nullable=False)  # success | failed | skipped
    error        = Column(Text)
    step_count   = Column(Integer, default=0)
    started_at   = Column(DateTime(timezone=True), nullable=False)
    finished_at  = Column(DateTime(timezone=True))
    recorded_at  = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    __table_args__ = (
        Index("ix_biz_run_workflow_started", "workflow_id", "started_at"),
        Index("ix_biz_run_status",           "status", "recorded_at"),
    )
