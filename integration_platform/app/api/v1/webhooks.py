"""
Inbound Webhook Router.

Single webhook entry point for every service:
  POST /api/v1/webhooks/{service_name}

Validates the signature, wraps the payload in an IntegrationEvent, and
dispatches it to the EventBus (which forwards to Service Bus + triggers workflows).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import uuid
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Path, Request

from app.core.event_bus import IntegrationEvent, get_event_bus
from app.core.registry import get_registry
from app.models.schemas import WebhookResponse
from config.settings import get_settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks", tags=["Webhooks"])
settings = get_settings()


def _verify_signature(body: bytes, signature: str, secret: str) -> bool:
    """Verify GitHub-style HMAC-SHA256 signature."""
    if not signature or not secret:
        return True   # Signature verification optional when secret is not configured
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


async def _dispatch_event(
    service_name: str,
    trigger_name: str,
    payload: dict,
    event_id: str,
    correlation_id: Optional[str],
) -> None:
    """Background task: hand the webhook payload to the service trigger and publish the event."""
    registry = get_registry()
    try:
        result = await registry.execute_trigger(service_name, trigger_name, payload)
        for evt_payload in result.events:
            event = IntegrationEvent(
                event_id=event_id,
                event_type=f"{service_name}.{trigger_name}",
                service_name=service_name,
                trigger_name=trigger_name,
                payload=evt_payload,
                correlation_id=correlation_id,
            )
            await get_event_bus().publish(event)
    except Exception as exc:
        logger.exception("Webhook dispatch error for %s.%s: %s", service_name, trigger_name, exc)


@router.post(
    "/{service_name}",
    response_model=WebhookResponse,
    summary="Receive an inbound webhook from any integrated service",
    description="""
Universal webhook endpoint.  The `service_name` path parameter routes the
payload to the correct integration class.

**Signature verification**: include the `X-Hub-Signature-256` header with an
HMAC-SHA256 signature if `WEBHOOK_SECRET` is configured.

The request is acknowledged immediately (< 1 ms) and processed asynchronously
so the external service does not time out waiting for a response.
""",
)
async def receive_webhook(
    service_name: str = Path(..., description="Integration service name e.g. salesforce"),
    request: Request = ...,
    background_tasks: BackgroundTasks = ...,
    x_hub_signature_256: Optional[str] = Header(None),
    x_correlation_id: Optional[str] = Header(None),
    x_trigger_name: Optional[str] = Header(None, description="Override the trigger to invoke"),
):
    body = await request.body()

    # ── Signature check ────────────────────────────────────────────────────────
    if settings.WEBHOOK_SECRET and settings.WEBHOOK_SECRET != "change-me":
        if not _verify_signature(body, x_hub_signature_256 or "", settings.WEBHOOK_SECRET):
            raise HTTPException(status_code=401, detail="Invalid webhook signature")

    # ── Parse body ─────────────────────────────────────────────────────────────
    try:
        payload = json.loads(body) if body else {}
    except json.JSONDecodeError:
        payload = {"raw": body.decode("utf-8", errors="replace")}

    # ── Determine trigger name ─────────────────────────────────────────────────
    trigger_name = x_trigger_name or "on_webhook"
    event_id = str(uuid.uuid4())

    logger.info(
        "Webhook received: service=%s trigger=%s event_id=%s",
        service_name, trigger_name, event_id,
    )

    # ── Background dispatch (fire-and-forget) ──────────────────────────────────
    background_tasks.add_task(
        _dispatch_event,
        service_name=service_name,
        trigger_name=trigger_name,
        payload=payload,
        event_id=event_id,
        correlation_id=x_correlation_id,
    )

    return WebhookResponse(received=True, event_id=event_id)


@router.post(
    "/{service_name}/{trigger_name}",
    response_model=WebhookResponse,
    summary="Receive webhook routed to a specific trigger method",
)
async def receive_webhook_with_trigger(
    service_name: str,
    trigger_name: str,
    request: Request,
    background_tasks: BackgroundTasks,
    x_hub_signature_256: Optional[str] = Header(None),
    x_correlation_id: Optional[str] = Header(None),
):
    """
    Explicit trigger routing: POST /webhooks/salesforce/on_new_opportunity
    Useful when a service sends different event types to different URLs.
    """
    body = await request.body()
    try:
        payload = json.loads(body) if body else {}
    except json.JSONDecodeError:
        payload = {"raw": body.decode("utf-8", errors="replace")}

    event_id = str(uuid.uuid4())
    background_tasks.add_task(
        _dispatch_event,
        service_name=service_name,
        trigger_name=trigger_name,
        payload=payload,
        event_id=event_id,
        correlation_id=x_correlation_id,
    )

    return WebhookResponse(received=True, event_id=event_id)
