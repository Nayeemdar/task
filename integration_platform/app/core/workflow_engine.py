"""
Workflow Engine — Workato-style recipe execution.

A Workflow (recipe) is a sequence of steps:
  - One trigger  (what starts the workflow)
  - One or more actions  (what to do)
  - Optional conditions / data-mapping between steps

Workflows are stored in the database and loaded at runtime.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
import uuid

from app.core.event_bus import IntegrationEvent, get_event_bus
from app.core.registry import get_registry
from app.core.retry_handler import RetryHandler

logger = logging.getLogger(__name__)


class StepType(str, Enum):
    TRIGGER = "trigger"
    ACTION = "action"
    CONDITION = "condition"
    DATA_MAP = "data_map"
    STOP = "stop"


class WorkflowStatus(str, Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    ERROR = "error"


class RunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


# ─── Data classes ─────────────────────────────────────────────────────────────

@dataclass
class WorkflowStep:
    step_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    step_type: StepType = StepType.ACTION
    service_name: str = ""                # "salesforce", "jira", …
    operation_name: str = ""              # action or trigger name
    input_mapping: Dict[str, str] = field(default_factory=dict)  # dest_key → "{{trigger.field}}"
    condition: Optional[str] = None       # simple Python expression evaluated against context
    on_error: str = "stop"               # stop | continue | retry


@dataclass
class Workflow:
    workflow_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    description: str = ""
    status: WorkflowStatus = WorkflowStatus.ACTIVE
    trigger_service: str = ""
    trigger_name: str = ""
    steps: List[WorkflowStep] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class WorkflowRun:
    run_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    workflow_id: str = ""
    status: RunStatus = RunStatus.PENDING
    trigger_event: Optional[Dict] = None
    step_results: List[Dict] = field(default_factory=list)
    error: Optional[str] = None
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: Optional[datetime] = None


# ─── Workflow Engine ───────────────────────────────────────────────────────────

class WorkflowEngine:
    """
    Executes workflow recipes end-to-end.

    The engine:
      1. Listens for trigger events on the EventBus
      2. Finds workflows subscribed to that trigger
      3. Executes each step in order, passing data between steps
      4. Records run history
    """

    def __init__(self):
        self._workflows: Dict[str, Workflow] = {}   # workflow_id → Workflow
        self._retry = RetryHandler()
        self._event_bus = get_event_bus()
        self._registry = get_registry()
        # Subscribe to all events so we can match against workflow triggers
        self._event_bus.subscribe("*", self._on_event)

    # ── Workflow CRUD ─────────────────────────────────────────────────────────

    def register_workflow(self, workflow: Workflow) -> None:
        self._workflows[workflow.workflow_id] = workflow
        logger.info("Workflow registered: '%s' (%s)", workflow.name, workflow.workflow_id)

    def get_workflow(self, workflow_id: str) -> Optional[Workflow]:
        return self._workflows.get(workflow_id)

    def list_workflows(self) -> List[Workflow]:
        return list(self._workflows.values())

    def delete_workflow(self, workflow_id: str) -> bool:
        return bool(self._workflows.pop(workflow_id, None))

    # ── Event listener ────────────────────────────────────────────────────────

    async def _on_event(self, event: IntegrationEvent) -> None:
        """Called by EventBus for every published event."""
        matching = [
            wf for wf in self._workflows.values()
            if wf.status == WorkflowStatus.ACTIVE
            and wf.trigger_service == event.service_name
            and wf.trigger_name == event.trigger_name
        ]
        if not matching:
            return

        for workflow in matching:
            asyncio.create_task(self.run(workflow, event.payload))

    # ── Execution ─────────────────────────────────────────────────────────────

    async def run(self, workflow: Workflow, trigger_data: Dict) -> WorkflowRun:
        run = WorkflowRun(
            workflow_id=workflow.workflow_id,
            status=RunStatus.RUNNING,
            trigger_event=trigger_data,
        )
        logger.info("Starting workflow run: '%s' run=%s", workflow.name, run.run_id)

        context: Dict[str, Any] = {"trigger": trigger_data, "steps": {}}

        try:
            for step in workflow.steps:
                step_result = await self._execute_step(step, context, run)
                context["steps"][step.step_id] = step_result

                if step_result.get("status") == RunStatus.FAILED and step.on_error == "stop":
                    run.status = RunStatus.FAILED
                    run.error = step_result.get("error")
                    break
            else:
                run.status = RunStatus.SUCCESS
        except Exception as exc:
            run.status = RunStatus.FAILED
            run.error = str(exc)
            logger.exception("Workflow run failed: %s", exc)

        run.finished_at = datetime.now(timezone.utc)
        logger.info(
            "Workflow run finished: '%s' status=%s", workflow.name, run.status
        )
        return run

    async def _execute_step(
        self, step: WorkflowStep, context: Dict, run: WorkflowRun
    ) -> Dict[str, Any]:
        # Evaluate optional condition
        if step.condition:
            try:
                if not eval(step.condition, {"__builtins__": {}}, context):  # noqa: S307
                    return {"status": RunStatus.SKIPPED, "step_id": step.step_id}
            except Exception as exc:
                return {"status": RunStatus.FAILED, "step_id": step.step_id, "error": f"Condition error: {exc}"}

        # Map input data using template expressions
        mapped_input = self._map_data(step.input_mapping, context)

        if step.step_type == StepType.ACTION:
            try:
                result = await self._registry.execute_action(
                    step.service_name, step.operation_name, mapped_input
                )
                step_result = {
                    "status": RunStatus.SUCCESS if result.success else RunStatus.FAILED,
                    "step_id": step.step_id,
                    "data": result.data,
                    "error": result.error,
                }
            except Exception as exc:
                step_result = {
                    "status": RunStatus.FAILED,
                    "step_id": step.step_id,
                    "error": str(exc),
                }
        else:
            step_result = {
                "status": RunStatus.SKIPPED,
                "step_id": step.step_id,
                "note": f"Step type '{step.step_type}' is handled by the engine, not the executor",
            }

        run.step_results.append(step_result)
        return step_result

    def _map_data(self, mapping: Dict[str, str], context: Dict) -> Dict[str, Any]:
        """
        Resolve template expressions like "{{trigger.email}}" against context.
        Supports nested dot-notation paths.
        """
        result = {}
        for dest_key, template in mapping.items():
            if isinstance(template, str) and template.startswith("{{") and template.endswith("}}"):
                path = template[2:-2].strip().split(".")
                value = context
                for part in path:
                    if isinstance(value, dict):
                        value = value.get(part)
                    else:
                        value = None
                        break
                result[dest_key] = value
            else:
                result[dest_key] = template
        return result


# ── Module-level singleton ─────────────────────────────────────────────────────

_engine: Optional[WorkflowEngine] = None


def get_workflow_engine() -> WorkflowEngine:
    global _engine
    if _engine is None:
        _engine = WorkflowEngine()
    return _engine
