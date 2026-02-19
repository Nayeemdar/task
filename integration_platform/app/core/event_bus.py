"""
In-process async event bus.

Components publish events to named topics; subscribers receive them.
The Azure Service Bus bridge (infrastructure/service_bus.py) also subscribes
so every internal event is automatically forwarded to the cloud queue.
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine, Dict, List, Optional
import uuid

logger = logging.getLogger(__name__)


@dataclass
class IntegrationEvent:
    """Canonical event envelope used throughout the platform."""

    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    event_type: str = ""                   # e.g. "salesforce.new_lead"
    service_name: str = ""                 # originating integration
    trigger_name: str = ""                 # trigger or action that produced it
    payload: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    correlation_id: Optional[str] = None   # links related events across services
    source_record_id: Optional[str] = None # external record id (e.g. SF opportunity id)
    schema_version: str = "1.0"

    def as_dict(self) -> Dict:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "service_name": self.service_name,
            "trigger_name": self.trigger_name,
            "payload": self.payload,
            "metadata": self.metadata,
            "timestamp": self.timestamp.isoformat(),
            "correlation_id": self.correlation_id,
            "source_record_id": self.source_record_id,
            "schema_version": self.schema_version,
        }


# Handler type alias
EventHandler = Callable[[IntegrationEvent], Coroutine[Any, Any, None]]


class EventBus:
    """
    Lightweight async pub/sub bus.

    Topics support wildcard subscriptions:
      - "salesforce.*"   → all Salesforce events
      - "*"              → every event
    """

    _instance: Optional["EventBus"] = None

    def __init__(self):
        # topic → list of handlers
        self._subscribers: Dict[str, List[EventHandler]] = defaultdict(list)
        self._queue: asyncio.Queue[IntegrationEvent] = asyncio.Queue()
        self._running = False
        self._worker_task: Optional[asyncio.Task] = None

    @classmethod
    def get_instance(cls) -> "EventBus":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        if not self._running:
            self._running = True
            self._worker_task = asyncio.create_task(self._dispatch_loop())
            logger.info("EventBus started")

    async def stop(self) -> None:
        self._running = False
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
        logger.info("EventBus stopped")

    # ── Publish / Subscribe ───────────────────────────────────────────────────

    def subscribe(self, topic: str, handler: EventHandler) -> None:
        """
        Subscribe to a topic.  Use '*' for all events or 'service.*' pattern.
        """
        self._subscribers[topic].append(handler)
        logger.debug("Subscribed handler '%s' to topic '%s'", handler.__name__, topic)

    def unsubscribe(self, topic: str, handler: EventHandler) -> None:
        self._subscribers[topic] = [
            h for h in self._subscribers[topic] if h is not handler
        ]

    async def publish(self, event: IntegrationEvent) -> None:
        """Enqueue an event for async dispatch."""
        logger.info(
            "Event published: %s (id=%s)", event.event_type, event.event_id
        )
        await self._queue.put(event)

    async def publish_sync(self, event: IntegrationEvent) -> None:
        """Publish and wait until all handlers have completed."""
        await self._dispatch(event)

    # ── Internal dispatch ─────────────────────────────────────────────────────

    async def _dispatch_loop(self) -> None:
        while self._running:
            try:
                event = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                await self._dispatch(event)
                self._queue.task_done()
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.exception("EventBus dispatch error: %s", exc)

    async def _dispatch(self, event: IntegrationEvent) -> None:
        matching_handlers = self._get_handlers(event.event_type)
        tasks = [asyncio.create_task(h(event)) for h in matching_handlers]
        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for res in results:
                if isinstance(res, Exception):
                    logger.error("Event handler error: %s", res)

    def _get_handlers(self, event_type: str) -> List[EventHandler]:
        handlers: List[EventHandler] = []
        # Exact match
        handlers.extend(self._subscribers.get(event_type, []))
        # Wildcard: "salesforce.*" matches "salesforce.new_lead"
        service = event_type.split(".")[0] if "." in event_type else event_type
        handlers.extend(self._subscribers.get(f"{service}.*", []))
        # Global wildcard
        handlers.extend(self._subscribers.get("*", []))
        # Deduplicate while preserving order
        seen = set()
        unique = []
        for h in handlers:
            if id(h) not in seen:
                seen.add(id(h))
                unique.append(h)
        return unique


# ── Module-level accessor ──────────────────────────────────────────────────────

def get_event_bus() -> EventBus:
    return EventBus.get_instance()
