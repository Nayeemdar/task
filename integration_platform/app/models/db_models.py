"""
SQLAlchemy ORM models — stored in PostgreSQL (CRDB compatible).

Tables:
  integration_events   — audit log of every inbound/outbound event
  workflow_definitions — persisted workflow recipes
  workflow_runs        — history of every recipe execution
  polling_cursors      — last-seen watermark per polling trigger
  connections          — encrypted credential sets per service (per tenant)
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum,
    Index,
    Integer,
    JSON,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase


def utcnow():
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class IntegrationEventModel(Base):
    __tablename__ = "integration_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id = Column(String(64), unique=True, nullable=False, index=True)
    event_type = Column(String(128), nullable=False, index=True)
    service_name = Column(String(64), nullable=False, index=True)
    trigger_name = Column(String(128), nullable=False)
    payload = Column(JSON, nullable=False, default=dict)
    metadata_ = Column("metadata", JSON, nullable=False, default=dict)
    correlation_id = Column(String(64), index=True)
    source_record_id = Column(String(128), index=True)
    schema_version = Column(String(16), default="1.0")
    direction = Column(String(8), default="inbound")   # inbound | outbound
    status = Column(String(16), default="received")    # received | processed | failed
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    processed_at = Column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_events_service_created", "service_name", "created_at"),
    )


class WorkflowDefinitionModel(Base):
    __tablename__ = "workflow_definitions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workflow_id = Column(String(64), unique=True, nullable=False)
    name = Column(String(256), nullable=False)
    description = Column(Text, default="")
    status = Column(String(16), default="active")
    trigger_service = Column(String(64), nullable=False)
    trigger_name = Column(String(128), nullable=False)
    steps = Column(JSON, nullable=False, default=list)  # serialized WorkflowStep list
    created_at = Column(DateTime(timezone=True), default=utcnow)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class WorkflowRunModel(Base):
    __tablename__ = "workflow_runs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id = Column(String(64), unique=True, nullable=False)
    workflow_id = Column(String(64), nullable=False, index=True)
    status = Column(String(16), nullable=False, default="pending")
    trigger_event = Column(JSON)
    step_results = Column(JSON, default=list)
    error = Column(Text)
    started_at = Column(DateTime(timezone=True), default=utcnow)
    finished_at = Column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_runs_workflow_started", "workflow_id", "started_at"),
    )


class PollingCursorModel(Base):
    """Persists the last-seen watermark for each polling trigger."""

    __tablename__ = "polling_cursors"

    id = Column(Integer, primary_key=True, autoincrement=True)
    service_name = Column(String(64), nullable=False)
    trigger_name = Column(String(128), nullable=False)
    cursor = Column(Text)
    last_run = Column(DateTime(timezone=True))
    error_count = Column(Integer, default=0)

    __table_args__ = (
        Index("ix_cursor_service_trigger", "service_name", "trigger_name", unique=True),
    )


class ConnectionModel(Base):
    """
    Named credential set for an integration service.
    In production, the `encrypted_credentials` column stores AES-encrypted JSON;
    plaintext credentials must never be stored at rest.
    """

    __tablename__ = "connections"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(256), nullable=False)
    service_name = Column(String(64), nullable=False, index=True)
    is_active = Column(Boolean, default=True)
    encrypted_credentials = Column(Text)   # AES-GCM encrypted JSON
    key_vault_secret_name = Column(String(256))  # Alternative: store ref to Key Vault secret
    created_at = Column(DateTime(timezone=True), default=utcnow)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
