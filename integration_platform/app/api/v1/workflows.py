"""
Workflow (Recipe) Router — CRUD for workflow definitions + manual run trigger.

Endpoints:
  POST   /api/v1/workflows/               Create a new workflow
  GET    /api/v1/workflows/               List all workflows
  GET    /api/v1/workflows/{id}           Get a workflow
  PUT    /api/v1/workflows/{id}           Update a workflow
  DELETE /api/v1/workflows/{id}           Delete a workflow
  POST   /api/v1/workflows/{id}/run       Manually trigger a workflow run
  GET    /api/v1/workflows/{id}/runs      Get run history for a workflow
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, HTTPException

from app.core.workflow_engine import (
    Workflow,
    WorkflowEngine,
    WorkflowStatus,
    WorkflowStep,
    get_workflow_engine,
)
from app.models.schemas import (
    APIResponse,
    WorkflowCreateRequest,
    WorkflowResponse,
    WorkflowRunRequest,
    WorkflowRunResponse,
    WorkflowStepSchema,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/workflows", tags=["Workflows"])


def _engine() -> WorkflowEngine:
    return get_workflow_engine()


def _to_response(wf: Workflow) -> WorkflowResponse:
    return WorkflowResponse(
        workflow_id=wf.workflow_id,
        name=wf.name,
        description=wf.description,
        status=wf.status,
        trigger_service=wf.trigger_service,
        trigger_name=wf.trigger_name,
        steps=[
            WorkflowStepSchema(
                step_id=s.step_id,
                step_type=s.step_type,
                service_name=s.service_name,
                operation_name=s.operation_name,
                input_mapping=s.input_mapping,
                condition=s.condition,
                on_error=s.on_error,
            )
            for s in wf.steps
        ],
        created_at=wf.created_at,
        updated_at=wf.updated_at,
    )


@router.post("/", response_model=WorkflowResponse, status_code=201)
async def create_workflow(body: WorkflowCreateRequest):
    steps = [
        WorkflowStep(
            step_type=s.step_type,
            service_name=s.service_name,
            operation_name=s.operation_name,
            input_mapping=s.input_mapping,
            condition=s.condition,
            on_error=s.on_error,
        )
        for s in body.steps
    ]
    workflow = Workflow(
        name=body.name,
        description=body.description,
        trigger_service=body.trigger_service,
        trigger_name=body.trigger_name,
        steps=steps,
    )
    _engine().register_workflow(workflow)
    return _to_response(workflow)


@router.get("/", response_model=List[WorkflowResponse])
async def list_workflows():
    return [_to_response(wf) for wf in _engine().list_workflows()]


@router.get("/{workflow_id}", response_model=WorkflowResponse)
async def get_workflow(workflow_id: str):
    wf = _engine().get_workflow(workflow_id)
    if not wf:
        raise HTTPException(status_code=404, detail=f"Workflow '{workflow_id}' not found")
    return _to_response(wf)


@router.put("/{workflow_id}", response_model=WorkflowResponse)
async def update_workflow(workflow_id: str, body: WorkflowCreateRequest):
    wf = _engine().get_workflow(workflow_id)
    if not wf:
        raise HTTPException(status_code=404, detail=f"Workflow '{workflow_id}' not found")
    wf.name = body.name
    wf.description = body.description
    wf.trigger_service = body.trigger_service
    wf.trigger_name = body.trigger_name
    wf.steps = [
        WorkflowStep(
            step_type=s.step_type,
            service_name=s.service_name,
            operation_name=s.operation_name,
            input_mapping=s.input_mapping,
            condition=s.condition,
            on_error=s.on_error,
        )
        for s in body.steps
    ]
    wf.updated_at = datetime.now(timezone.utc)
    return _to_response(wf)


@router.patch("/{workflow_id}/status", response_model=WorkflowResponse)
async def set_workflow_status(workflow_id: str, status: str):
    wf = _engine().get_workflow(workflow_id)
    if not wf:
        raise HTTPException(status_code=404, detail=f"Workflow '{workflow_id}' not found")
    try:
        wf.status = WorkflowStatus(status)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid status '{status}'")
    return _to_response(wf)


@router.delete("/{workflow_id}", response_model=APIResponse)
async def delete_workflow(workflow_id: str):
    deleted = _engine().delete_workflow(workflow_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Workflow '{workflow_id}' not found")
    return APIResponse(data={"deleted": True, "workflow_id": workflow_id})


@router.post("/{workflow_id}/run", response_model=WorkflowRunResponse)
async def manual_run(workflow_id: str, body: WorkflowRunRequest):
    """Manually trigger a workflow (useful for testing)."""
    wf = _engine().get_workflow(workflow_id)
    if not wf:
        raise HTTPException(status_code=404, detail=f"Workflow '{workflow_id}' not found")
    run = await _engine().run(wf, body.trigger_data)
    return WorkflowRunResponse(
        run_id=run.run_id,
        workflow_id=run.workflow_id,
        status=run.status,
        trigger_event=run.trigger_event,
        step_results=run.step_results,
        error=run.error,
        started_at=run.started_at,
        finished_at=run.finished_at,
    )
