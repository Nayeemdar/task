"""
Integration Actions Router.

Single dynamic entry point for every integration and every action:
  POST   /api/v1/integrations/{service}/actions/{action}
  GET    /api/v1/integrations/
  GET    /api/v1/integrations/{service}
"""
from __future__ import annotations

import logging
import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Path

from app.core.event_bus import IntegrationEvent, get_event_bus
from app.core.registry import IntegrationRegistry, get_registry
from app.models.schemas import (
    ActionRequest,
    ActionResponse,
    APIResponse,
    ServiceManifest,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/integrations", tags=["Integrations"])


def _get_registry() -> IntegrationRegistry:
    return get_registry()


# ── List all registered services ──────────────────────────────────────────────

@router.get("/", response_model=APIResponse, summary="List all registered integration services")
async def list_integrations(registry: IntegrationRegistry = Depends(_get_registry)):
    return APIResponse(data={"services": registry.list_services()})


# ── Get service manifest ───────────────────────────────────────────────────────

@router.get(
    "/{service_name}",
    response_model=APIResponse,
    summary="Get manifest (actions + triggers) for an integration service",
)
async def get_integration(
    service_name: str = Path(..., description="e.g. salesforce, jira, ukg"),
    registry: IntegrationRegistry = Depends(_get_registry),
):
    try:
        manifest = registry.get_manifest(service_name)
        return APIResponse(data=manifest)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


# ── Execute an action ─────────────────────────────────────────────────────────

@router.post(
    "/{service_name}/actions/{action_name}",
    response_model=ActionResponse,
    summary="Execute an action on an integration service",
    description="""
Execute any action registered on the given service.

**How to add a new action**: simply add a method decorated with `@action` to the
integration class. It will be available at this endpoint immediately — no router
changes required.

**Dry-run mode**: set `dry_run=true` to validate the request without executing.
""",
)
async def execute_action(
    service_name: str = Path(..., description="Integration service name e.g. salesforce"),
    action_name: str = Path(..., description="Action method name e.g. create_lead"),
    request: ActionRequest = ...,
    registry: IntegrationRegistry = Depends(_get_registry),
):
    if request.dry_run:
        return ActionResponse(
            success=True,
            data={"dry_run": True, "service": service_name, "action": action_name, "payload": request.payload},
            correlation_id=request.correlation_id,
        )

    try:
        result = await registry.execute_action(service_name, action_name, request.payload)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        logger.exception("Action execution error: %s.%s", service_name, action_name)
        raise HTTPException(status_code=500, detail=str(exc))

    # Publish action result event to the bus for audit / workflow chaining
    event = IntegrationEvent(
        event_type=f"{service_name}.{action_name}",
        service_name=service_name,
        trigger_name=action_name,
        payload={"input": request.payload, "output": result.data, "success": result.success},
        correlation_id=request.correlation_id,
    )
    await get_event_bus().publish(event)

    return ActionResponse(
        success=result.success,
        data=result.data,
        error=result.error,
        correlation_id=request.correlation_id or event.event_id,
        metadata=result.metadata,
    )


# ── Manually fire a polling trigger ───────────────────────────────────────────

@router.post(
    "/{service_name}/triggers/{trigger_name}/poll",
    response_model=APIResponse,
    summary="Manually poll a trigger (useful for testing)",
)
async def poll_trigger(
    service_name: str,
    trigger_name: str,
    context: dict = None,
    registry: IntegrationRegistry = Depends(_get_registry),
):
    try:
        result = await registry.execute_trigger(service_name, trigger_name, context or {})
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    return APIResponse(
        data={
            "events": result.events,
            "events_count": len(result.events),
            "cursor": result.cursor,
            "has_more": result.has_more,
        }
    )
