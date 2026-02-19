"""
Pydantic v2 request/response schemas for all API endpoints.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional
from enum import Enum

from pydantic import BaseModel, Field, model_validator


# ─── Common ────────────────────────────────────────────────────────────────────

class StatusEnum(str, Enum):
    OK = "ok"
    ERROR = "error"


class APIResponse(BaseModel):
    status: StatusEnum = StatusEnum.OK
    data: Optional[Any] = None
    error: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class PaginatedResponse(APIResponse):
    total: int = 0
    page: int = 1
    page_size: int = 50


# ─── Integration / Action ──────────────────────────────────────────────────────

class ActionRequest(BaseModel):
    """Body sent to POST /integrations/{service}/actions/{action}"""
    payload: Dict[str, Any] = Field(default_factory=dict, description="Action-specific parameters")
    correlation_id: Optional[str] = Field(None, description="Caller-supplied correlation id")
    dry_run: bool = Field(False, description="Validate payload without executing")


class ActionResponse(BaseModel):
    success: bool
    data: Optional[Any] = None
    error: Optional[str] = None
    correlation_id: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ActionMeta(BaseModel):
    name: str
    description: str
    input_schema: Dict[str, Any]
    output_schema: Dict[str, Any]
    idempotent: bool


class TriggerMeta(BaseModel):
    name: str
    description: str
    trigger_type: str
    poll_interval_seconds: int
    output_schema: Dict[str, Any]


class ServiceManifest(BaseModel):
    service_name: str
    service_description: str
    auth_type: str
    actions: List[ActionMeta]
    triggers: List[TriggerMeta]


# ─── Webhook ───────────────────────────────────────────────────────────────────

class WebhookResponse(BaseModel):
    received: bool = True
    event_id: str
    events_count: int = 0


# ─── Workflow ──────────────────────────────────────────────────────────────────

class WorkflowStepSchema(BaseModel):
    step_id: Optional[str] = None
    step_type: str = "action"
    service_name: str
    operation_name: str
    input_mapping: Dict[str, str] = Field(default_factory=dict)
    condition: Optional[str] = None
    on_error: str = "stop"


class WorkflowCreateRequest(BaseModel):
    name: str
    description: str = ""
    trigger_service: str
    trigger_name: str
    steps: List[WorkflowStepSchema]


class WorkflowResponse(BaseModel):
    workflow_id: str
    name: str
    description: str
    status: str
    trigger_service: str
    trigger_name: str
    steps: List[WorkflowStepSchema]
    created_at: datetime
    updated_at: datetime


class WorkflowRunResponse(BaseModel):
    run_id: str
    workflow_id: str
    status: str
    trigger_event: Optional[Dict] = None
    step_results: List[Dict] = Field(default_factory=list)
    error: Optional[str] = None
    started_at: datetime
    finished_at: Optional[datetime] = None


class WorkflowRunRequest(BaseModel):
    """Manually trigger a workflow run via API (test mode)."""
    trigger_data: Dict[str, Any] = Field(default_factory=dict)


# ─── Events ────────────────────────────────────────────────────────────────────

class EventResponse(BaseModel):
    event_id: str
    event_type: str
    service_name: str
    trigger_name: str
    payload: Dict[str, Any]
    metadata: Dict[str, Any]
    timestamp: datetime
    correlation_id: Optional[str]
    source_record_id: Optional[str]
    schema_version: str


# ─── Polling ───────────────────────────────────────────────────────────────────

class PollingJobStatus(BaseModel):
    service: str
    trigger: str
    interval_seconds: int
    last_run: Optional[datetime]
    last_cursor: Optional[str]
    error_count: int
    running: bool


class PollingStatusResponse(BaseModel):
    jobs: Dict[str, PollingJobStatus]


# ─── Health ────────────────────────────────────────────────────────────────────

class ComponentHealth(BaseModel):
    status: str           # healthy | degraded | unhealthy
    details: Optional[str] = None


class HealthResponse(BaseModel):
    status: str
    version: str
    environment: str
    components: Dict[str, ComponentHealth]
    circuit_breakers: Dict[str, str] = Field(default_factory=dict)


# ─── Connection ────────────────────────────────────────────────────────────────

class ConnectionCreateRequest(BaseModel):
    name: str
    service_name: str
    credentials: Dict[str, Any] = Field(description="Will be encrypted before storage")


class ConnectionResponse(BaseModel):
    id: str
    name: str
    service_name: str
    is_active: bool
    created_at: datetime
    updated_at: datetime
    # credentials are never returned
