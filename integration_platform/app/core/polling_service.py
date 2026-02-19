"""
Polling Service — background scheduler that calls @trigger(type=POLLING) methods
on every registered integration at their configured interval.

Events produced by polling triggers are published onto the EventBus so workflows
and the Service Bus bridge receive them automatically.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Optional

from app.core.base_integration import TriggerType
from app.core.event_bus import IntegrationEvent, get_event_bus
from app.core.registry import get_registry

logger = logging.getLogger(__name__)


@dataclass
class PollingJob:
    service_name: str
    trigger_name: str
    interval_seconds: int
    last_cursor: Optional[str] = None
    last_run: Optional[datetime] = None
    error_count: int = 0
    _task: Optional[asyncio.Task] = field(default=None, repr=False)


class PollingService:
    """
    Starts one asyncio task per polling trigger.

    Lifecycle:
        await polling_service.start()   # called by lifespan
        await polling_service.stop()    # called by lifespan
    """

    _instance: Optional["PollingService"] = None

    def __init__(self):
        self._jobs: Dict[str, PollingJob] = {}   # "service.trigger" → job
        self._running = False
        self._event_bus = get_event_bus()
        self._registry = get_registry()

    @classmethod
    def get_instance(cls) -> "PollingService":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        self._running = True
        self._discover_polling_triggers()
        for job in self._jobs.values():
            job._task = asyncio.create_task(self._run_job(job))
        logger.info("PollingService started with %d job(s)", len(self._jobs))

    async def stop(self) -> None:
        self._running = False
        for job in self._jobs.values():
            if job._task:
                job._task.cancel()
                try:
                    await job._task
                except asyncio.CancelledError:
                    pass
        logger.info("PollingService stopped")

    # ── Discovery ─────────────────────────────────────────────────────────────

    def _discover_polling_triggers(self) -> None:
        registry = self._registry
        for service_name in registry.list_services():
            try:
                integration = registry.get_integration(service_name)
                for _, method in integration._iter_triggers():
                    if method._trigger_type in (TriggerType.POLLING, TriggerType.SCHEDULED):
                        key = f"{service_name}.{method._trigger_name}"
                        self._jobs[key] = PollingJob(
                            service_name=service_name,
                            trigger_name=method._trigger_name,
                            interval_seconds=method._poll_interval_seconds,
                        )
                        logger.debug("Registered polling job: %s", key)
            except Exception as exc:
                logger.warning("Could not inspect triggers for '%s': %s", service_name, exc)

    # ── Per-job loop ──────────────────────────────────────────────────────────

    async def _run_job(self, job: PollingJob) -> None:
        logger.info(
            "Polling job started: %s.%s every %ds",
            job.service_name, job.trigger_name, job.interval_seconds,
        )
        while self._running:
            try:
                await self._poll_once(job)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                job.error_count += 1
                logger.error("Polling error (%s.%s): %s", job.service_name, job.trigger_name, exc)

            await asyncio.sleep(job.interval_seconds)

    async def _poll_once(self, job: PollingJob) -> None:
        context = {"cursor": job.last_cursor}
        result = await self._registry.execute_trigger(
            job.service_name, job.trigger_name, context
        )
        job.last_run = datetime.now(timezone.utc)

        if result.events:
            logger.info(
                "Poll %s.%s produced %d event(s)",
                job.service_name, job.trigger_name, len(result.events),
            )
            for evt_payload in result.events:
                event = IntegrationEvent(
                    event_type=f"{job.service_name}.{job.trigger_name}",
                    service_name=job.service_name,
                    trigger_name=job.trigger_name,
                    payload=evt_payload,
                )
                await self._event_bus.publish(event)

        if result.cursor:
            job.last_cursor = result.cursor

    # ── Status ────────────────────────────────────────────────────────────────

    def get_status(self) -> Dict:
        return {
            key: {
                "service": job.service_name,
                "trigger": job.trigger_name,
                "interval_seconds": job.interval_seconds,
                "last_run": job.last_run.isoformat() if job.last_run else None,
                "last_cursor": job.last_cursor,
                "error_count": job.error_count,
                "running": job._task and not job._task.done() if job._task else False,
            }
            for key, job in self._jobs.items()
        }


def get_polling_service() -> PollingService:
    return PollingService.get_instance()
