"""
BaseIntegration — the single abstract class every connector extends.

HOW TO ADD A NEW INTEGRATION:
  1. Create `app/integrations/<service>/__init__.py` (empty)
  2. Create `app/integrations/<service>/integration.py`
  3. Subclass BaseIntegration, set `service_name`
  4. Decorate methods with @action or @trigger
  5. Done — routing, retry, telemetry, schema docs are all inherited.

No changes to infrastructure code required.
"""
from __future__ import annotations

import asyncio
import functools
import inspect
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, ClassVar, Dict, List, Optional, Type

import httpx

logger = logging.getLogger(__name__)


# ─── Enums ────────────────────────────────────────────────────────────────────

class AuthType(str, Enum):
    API_KEY = "api_key"
    OAUTH2 = "oauth2"
    BASIC = "basic"
    BEARER = "bearer"
    CUSTOM = "custom"
    NONE = "none"


class TriggerType(str, Enum):
    WEBHOOK = "webhook"           # External system pushes event
    POLLING = "polling"           # Platform polls external system
    SCHEDULED = "scheduled"       # Cron-based
    CDC = "cdc"                   # Change Data Capture
    PLATFORM_EVENT = "platform_event"  # e.g. Salesforce Platform Events


# ─── Result types ─────────────────────────────────────────────────────────────

@dataclass
class ActionResult:
    success: bool
    data: Any = None
    error: Optional[str] = None
    status_code: Optional[int] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def ok(cls, data: Any, metadata: Dict = None) -> "ActionResult":
        return cls(success=True, data=data, metadata=metadata or {})

    @classmethod
    def fail(cls, error: str, status_code: int = None) -> "ActionResult":
        return cls(success=False, error=error, status_code=status_code)


@dataclass
class TriggerResult:
    events: List[Dict[str, Any]]
    cursor: Optional[str] = None   # Opaque pagination cursor for next poll
    has_more: bool = False


# ─── Decorator factories ───────────────────────────────────────────────────────

def action(
    name: str = None,
    description: str = "",
    input_schema: Optional[Dict] = None,
    output_schema: Optional[Dict] = None,
    idempotent: bool = False,
):
    """
    Marks an async method as an integration action (outbound call).

    Usage:
        @action(description="Create a Salesforce lead")
        async def create_lead(self, payload: dict) -> ActionResult:
            ...
    """
    def decorator(func: Callable) -> Callable:
        func._is_action = True
        func._action_name = name or func.__name__
        func._description = description
        func._input_schema = input_schema or {}
        func._output_schema = output_schema or {}
        func._idempotent = idempotent

        @functools.wraps(func)
        async def wrapper(self: "BaseIntegration", payload: dict) -> ActionResult:
            logger.info(
                "Executing action",
                extra={"service": self.service_name, "action": func._action_name},
            )
            return await func(self, payload)

        # Preserve markers after wrapping
        wrapper._is_action = True
        wrapper._action_name = func._action_name
        wrapper._description = func._description
        wrapper._input_schema = func._input_schema
        wrapper._output_schema = func._output_schema
        wrapper._idempotent = func._idempotent
        return wrapper

    return decorator


def trigger(
    name: str = None,
    description: str = "",
    trigger_type: TriggerType = TriggerType.WEBHOOK,
    poll_interval_seconds: int = 300,
    output_schema: Optional[Dict] = None,
):
    """
    Marks an async method as an integration trigger (inbound event producer).

    Usage:
        @trigger(description="New Salesforce opportunity", trigger_type=TriggerType.POLLING)
        async def on_new_opportunity(self, since: str) -> TriggerResult:
            ...
    """
    def decorator(func: Callable) -> Callable:
        func._is_trigger = True
        func._trigger_name = name or func.__name__
        func._description = description
        func._trigger_type = trigger_type
        func._poll_interval_seconds = poll_interval_seconds
        func._output_schema = output_schema or {}

        @functools.wraps(func)
        async def wrapper(self: "BaseIntegration", *args, **kwargs) -> TriggerResult:
            logger.info(
                "Firing trigger",
                extra={"service": self.service_name, "trigger": func._trigger_name},
            )
            return await func(self, *args, **kwargs)

        wrapper._is_trigger = True
        wrapper._trigger_name = func._trigger_name
        wrapper._description = func._description
        wrapper._trigger_type = func._trigger_type
        wrapper._poll_interval_seconds = func._poll_interval_seconds
        wrapper._output_schema = func._output_schema
        return wrapper

    return decorator


# ─── Exceptions ───────────────────────────────────────────────────────────────

class IntegrationError(Exception):
    pass

class AuthenticationError(IntegrationError):
    pass

class ActionNotFoundError(IntegrationError):
    pass

class TriggerNotFoundError(IntegrationError):
    pass

class RateLimitError(IntegrationError):
    def __init__(self, retry_after: int = 60):
        self.retry_after = retry_after
        super().__init__(f"Rate limited. Retry after {retry_after}s")


# ─── Base Integration ──────────────────────────────────────────────────────────

class BaseIntegration(ABC):
    """
    Abstract base for every integration connector.

    Subclasses only need to:
      - Set `service_name` class variable
      - Implement `authenticate()`
      - Add @action / @trigger decorated methods

    Everything else (routing, retry, docs, telemetry) is automatic.
    """

    service_name: ClassVar[str] = ""
    service_description: ClassVar[str] = ""
    auth_type: ClassVar[AuthType] = AuthType.API_KEY

    def __init__(self, credentials: Dict[str, Any]):
        if not self.service_name:
            raise ValueError(f"{self.__class__.__name__} must define `service_name`")
        self.credentials = credentials
        self._client: Optional[httpx.AsyncClient] = None
        self._authenticated = False

    # ── Authentication ────────────────────────────────────────────────────────

    @abstractmethod
    async def authenticate(self) -> bool:
        """
        Validate credentials and prepare an authenticated HTTP client.
        Called automatically before the first action/trigger call.
        """

    async def ensure_authenticated(self) -> None:
        if not self._authenticated:
            self._authenticated = await self.authenticate()
            if not self._authenticated:
                raise AuthenticationError(f"Failed to authenticate with {self.service_name}")

    # ── Action dispatch ───────────────────────────────────────────────────────

    async def execute_action(self, action_name: str, payload: Dict[str, Any]) -> ActionResult:
        """
        Single entry point for all outbound actions.
        Callers never invoke individual methods directly.
        """
        await self.ensure_authenticated()
        method = self._resolve_action(action_name)
        try:
            return await method(payload)
        except RateLimitError:
            raise
        except Exception as exc:
            logger.exception("Action failed: %s.%s", self.service_name, action_name)
            return ActionResult.fail(str(exc))

    def _resolve_action(self, name: str) -> Callable:
        for _, method in inspect.getmembers(self, predicate=inspect.ismethod):
            if getattr(method, "_is_action", False) and method._action_name == name:
                return method
        raise ActionNotFoundError(
            f"Action '{name}' not found on {self.service_name}. "
            f"Available: {[m._action_name for _, m in self._iter_actions()]}"
        )

    # ── Trigger dispatch ──────────────────────────────────────────────────────

    async def execute_trigger(self, trigger_name: str, context: Dict[str, Any] = None) -> TriggerResult:
        """Single entry point for all inbound trigger processing."""
        await self.ensure_authenticated()
        method = self._resolve_trigger(trigger_name)
        try:
            return await method(context or {})
        except Exception as exc:
            logger.exception("Trigger failed: %s.%s", self.service_name, trigger_name)
            return TriggerResult(events=[], has_more=False)

    def _resolve_trigger(self, name: str) -> Callable:
        for _, method in inspect.getmembers(self, predicate=inspect.ismethod):
            if getattr(method, "_is_trigger", False) and method._trigger_name == name:
                return method
        raise TriggerNotFoundError(f"Trigger '{name}' not found on {self.service_name}")

    # ── Introspection ─────────────────────────────────────────────────────────

    def _iter_actions(self):
        for name, method in inspect.getmembers(self, predicate=inspect.ismethod):
            if getattr(method, "_is_action", False):
                yield name, method

    def _iter_triggers(self):
        for name, method in inspect.getmembers(self, predicate=inspect.ismethod):
            if getattr(method, "_is_trigger", False):
                yield name, method

    def get_manifest(self) -> Dict[str, Any]:
        """Returns a full descriptor of this integration — actions, triggers, auth."""
        return {
            "service_name": self.service_name,
            "service_description": self.service_description,
            "auth_type": self.auth_type,
            "actions": [
                {
                    "name": m._action_name,
                    "description": m._description,
                    "input_schema": m._input_schema,
                    "output_schema": m._output_schema,
                    "idempotent": m._idempotent,
                }
                for _, m in self._iter_actions()
            ],
            "triggers": [
                {
                    "name": m._trigger_name,
                    "description": m._description,
                    "trigger_type": m._trigger_type,
                    "poll_interval_seconds": m._poll_interval_seconds,
                    "output_schema": m._output_schema,
                }
                for _, m in self._iter_triggers()
            ],
        }

    # ── HTTP helper ───────────────────────────────────────────────────────────

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def __aenter__(self):
        await self.authenticate()
        return self

    async def __aexit__(self, *args):
        await self.close()
