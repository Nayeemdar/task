"""
Events Router — inspect and replay events.

  GET  /api/v1/events/                     List recent events from DB
  GET  /api/v1/events/{event_id}           Get a single event
  POST /api/v1/events/{event_id}/replay    Re-publish an event to the bus
"""
from __future__ import annotations

import logging
from typing import List, Optional
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.event_bus import IntegrationEvent, get_event_bus
from app.db.session import get_db
from app.models.db_models import IntegrationEventModel
from app.models.schemas import APIResponse, EventResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/events", tags=["Events"])


def _to_schema(m: IntegrationEventModel) -> EventResponse:
    return EventResponse(
        event_id=m.event_id,
        event_type=m.event_type,
        service_name=m.service_name,
        trigger_name=m.trigger_name,
        payload=m.payload,
        metadata=m.metadata_,
        timestamp=m.created_at,
        correlation_id=m.correlation_id,
        source_record_id=m.source_record_id,
        schema_version=m.schema_version,
    )


@router.get("/", response_model=APIResponse, summary="List recent integration events")
async def list_events(
    service_name: Optional[str] = Query(None),
    event_type: Optional[str] = Query(None),
    since: Optional[datetime] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(IntegrationEventModel).order_by(desc(IntegrationEventModel.created_at)).limit(limit)
    if service_name:
        stmt = stmt.where(IntegrationEventModel.service_name == service_name)
    if event_type:
        stmt = stmt.where(IntegrationEventModel.event_type == event_type)
    if since:
        stmt = stmt.where(IntegrationEventModel.created_at >= since)

    result = await db.execute(stmt)
    events = result.scalars().all()
    return APIResponse(data={"events": [_to_schema(e) for e in events], "count": len(events)})


@router.get("/{event_id}", response_model=EventResponse, summary="Get a single event by ID")
async def get_event(event_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(IntegrationEventModel).where(IntegrationEventModel.event_id == event_id)
    )
    model = result.scalar_one_or_none()
    if not model:
        raise HTTPException(status_code=404, detail=f"Event '{event_id}' not found")
    return _to_schema(model)


@router.post("/{event_id}/replay", response_model=APIResponse, summary="Re-publish an event")
async def replay_event(event_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(IntegrationEventModel).where(IntegrationEventModel.event_id == event_id)
    )
    model = result.scalar_one_or_none()
    if not model:
        raise HTTPException(status_code=404, detail=f"Event '{event_id}' not found")

    event = IntegrationEvent(
        event_id=model.event_id,
        event_type=model.event_type,
        service_name=model.service_name,
        trigger_name=model.trigger_name,
        payload=model.payload,
        metadata=model.metadata_,
        correlation_id=model.correlation_id,
        source_record_id=model.source_record_id,
    )
    await get_event_bus().publish(event)
    logger.info("Replayed event %s", event_id)
    return APIResponse(data={"replayed": True, "event_id": event_id})
