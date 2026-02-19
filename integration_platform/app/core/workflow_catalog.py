"""
WorkflowCatalog — single-class, single-method-per-integration framework.

Problem it solves
─────────────────
Previously, adding a new cross-system integration required touching 3–4 files:
  • The source connector  (add a @trigger)
  • The target connector  (add an @action)
  • A workflow recipe file (wire them together)
  • Both startup files    (register the recipe)

With WorkflowCatalog, the only file you ever touch for a new integration is
app/integrations/catalog.py — add one @workflow method, done.

How it works
────────────
1. @workflow marks an async method as an integration handler.
   It stores the trigger binding (e.g. "salesforce.on_contact_created")
   on the function as metadata.

2. WorkflowCatalog.__getattr__ provides transparent service proxies:
       await self.docebo.create_user_and_assign_plans({...})
   is translated at run time to:
       registry.execute_action("docebo", "create_user_and_assign_plans", {...})
   Any service registered in the IntegrationRegistry is callable this way.

3. catalog.start() scans the subclass for @workflow methods, subscribes
   each one as an EventBus listener filtered to its declared trigger.
   catalog.stop() unsubscribes everything cleanly on shutdown.

Relationship to WorkflowEngine
───────────────────────────────
WorkflowEngine still exists and handles workflows defined dynamically via
the REST API (POST /api/v1/workflows).  WorkflowCatalog is a complementary
code-first layer that sits alongside it — both subscribe to the EventBus
independently and neither blocks the other.

  EventBus
    ├── WorkflowEngine subscriber    ← handles REST-API-defined recipes
    └── WorkflowCatalog subscriber   ← handles code-defined catalog methods

Usage in catalog.py
────────────────────

    from app.core.workflow_catalog import WorkflowCatalog, workflow

    class IntegrationCatalog(WorkflowCatalog):

        @workflow(trigger="salesforce.on_contact_created")
        async def contact_created_to_docebo(self, event: dict):
            return await self.docebo.create_user_and_assign_plans({
                "email":             event["email"],
                "first_name":        event["first_name"],
                "last_name":         event["last_name"],
                "learning_plan_ids": [101, 202],
            })

        # ← add new integrations here, nothing else changes

Usage at startup
────────────────
    from app.integrations.catalog import IntegrationCatalog

    catalog = IntegrationCatalog()
    catalog.start()   # registers all @workflow handlers with EventBus
    ...
    catalog.stop()    # clean unsubscribe on shutdown
"""
from __future__ import annotations

import inspect
import logging
from typing import Any, Callable, Dict

from app.core.event_bus import IntegrationEvent, get_event_bus
from app.core.registry import get_registry

logger = logging.getLogger(__name__)


# ── @workflow decorator ────────────────────────────────────────────────────────

def workflow(
    trigger: str,
    name: str = "",
    description: str = "",
    enabled: bool = True,
):
    """
    Mark an async method on a WorkflowCatalog subclass as an integration handler.

    Parameters
    ----------
    trigger : str
        ``"service_name.trigger_name"`` — e.g. ``"salesforce.on_contact_created"``.
        Must match the service_name of a registered integration and the method
        name of one of its @trigger-decorated methods.
    name : str, optional
        Human-readable workflow name shown in logs.
        Defaults to the method name converted to title case.
    description : str, optional
        Free-text description for documentation.
    enabled : bool, optional
        Set to False to disable without deleting the method.
    """
    def decorator(fn: Callable) -> Callable:
        fn._is_workflow = True
        fn._workflow_trigger = trigger
        fn._workflow_name = name or fn.__name__.replace("_", " ").title()
        fn._workflow_description = description
        fn._workflow_enabled = enabled
        return fn
    return decorator


# ── Service proxy ──────────────────────────────────────────────────────────────

class _ServiceProxy:
    """
    Transparent proxy that turns attribute access into registry.execute_action calls.

        await self.docebo.create_user_and_assign_plans(payload)
        # → await registry.execute_action("docebo", "create_user_and_assign_plans", payload)

    The action must exist on the connector class (decorated with @action).
    Credentials are resolved from settings / Key Vault by the registry.
    """

    __slots__ = ("_service_name", "_registry")

    def __init__(self, service_name: str, registry) -> None:
        self._service_name = service_name
        self._registry = registry

    def __getattr__(self, action_name: str) -> Callable:
        """Return an async callable for the named action on this service."""
        service_name = self._service_name
        registry = self._registry

        async def _call(payload: Dict = None):
            return await registry.execute_action(
                service_name, action_name, payload or {}
            )

        _call.__name__ = f"{service_name}.{action_name}"
        _call.__qualname__ = f"_ServiceProxy.{service_name}.{action_name}"
        return _call


# ── WorkflowCatalog base class ─────────────────────────────────────────────────

class WorkflowCatalog:
    """
    Base class for the application-wide integration workflow catalog.

    Subclass once in ``app/integrations/catalog.py``.
    Add one ``@workflow`` method per business integration.
    Call ``catalog.start()`` at process startup.

    Service access
    ──────────────
    Any integration registered in IntegrationRegistry is reachable via
    ``self.<service_name>.<action_name>(payload)`` inside workflow methods:

        await self.salesforce.update_record({...})
        await self.docebo.create_user_and_assign_plans({...})
        await self.jira.get_issue({...})
        await self.ukg.get_employee({...})

    The proxy is created lazily on first access and cached for the lifetime
    of the catalog instance.
    """

    def __init__(self) -> None:
        self._registry = get_registry()
        self._event_bus = get_event_bus()
        self._proxies: Dict[str, _ServiceProxy] = {}
        self._handlers: list = []  # list of (handler_fn, workflow_name) tuples

    # ── Service proxy access ───────────────────────────────────────────────────

    def __getattr__(self, name: str) -> Any:
        # Guard: private/dunder names should raise AttributeError normally
        if name.startswith("_"):
            raise AttributeError(f"'{type(self).__name__}' has no attribute '{name}'")
        # Return (and cache) a service proxy for this integration name
        if name not in self._proxies:
            self._proxies[name] = _ServiceProxy(name, self._registry)
        return self._proxies[name]

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    def start(self) -> None:
        """
        Discover all @workflow methods on this instance and register them as
        EventBus subscribers.  Safe to call multiple times (re-registration
        is idempotent after a stop()).
        """
        count = 0
        for _attr_name, method in inspect.getmembers(self, predicate=inspect.ismethod):
            if not getattr(method, "_is_workflow", False):
                continue

            if not method._workflow_enabled:
                logger.info(
                    "WorkflowCatalog: '%s' is disabled — skipping",
                    method._workflow_name,
                )
                continue

            trigger_str: str = method._workflow_trigger
            try:
                trigger_service, trigger_name = trigger_str.split(".", 1)
            except ValueError:
                logger.error(
                    "WorkflowCatalog: invalid trigger '%s' on '%s' — "
                    "must be 'service_name.trigger_name'",
                    trigger_str, method._workflow_name,
                )
                continue

            handler = self._make_handler(method, trigger_service, trigger_name)
            self._event_bus.subscribe("*", handler)
            self._handlers.append((handler, method._workflow_name))
            count += 1
            logger.info(
                "WorkflowCatalog: '%s' registered ← %s",
                method._workflow_name, trigger_str,
            )

        logger.info("WorkflowCatalog started — %d workflow(s) active", count)

    def stop(self) -> None:
        """Unsubscribe all handlers from the EventBus."""
        for handler, name in self._handlers:
            self._event_bus.unsubscribe("*", handler)
            logger.debug("WorkflowCatalog: unsubscribed '%s'", name)
        self._handlers.clear()
        logger.info("WorkflowCatalog stopped")

    # ── Internal: handler factory ──────────────────────────────────────────────

    def _make_handler(
        self,
        method: Callable,
        trigger_service: str,
        trigger_name: str,
    ) -> Callable:
        """
        Wrap a @workflow method in an EventBus-compatible async handler.

        The wrapper:
        • Filters: ignores events that don't match the declared trigger.
        • Calls the method with ``event.payload`` as the only argument.
        • Catches all exceptions and logs them — never re-raises so that
          one failing workflow never blocks other EventBus subscribers.
        """
        wf_name = method._workflow_name

        async def _handler(event: IntegrationEvent) -> None:
            # Filter: only react to the declared trigger
            if event.service_name != trigger_service or event.trigger_name != trigger_name:
                return

            logger.info(
                "WorkflowCatalog: '%s' triggered by %s.%s (event_id=%s)",
                wf_name, trigger_service, trigger_name, event.event_id,
            )
            try:
                await method(event.payload)
                logger.info(
                    "WorkflowCatalog: '%s' completed (event_id=%s)",
                    wf_name, event.event_id,
                )
            except Exception as exc:
                logger.exception(
                    "WorkflowCatalog: '%s' failed (event_id=%s): %s",
                    wf_name, event.event_id, exc,
                )

        # Give the handler a recognisable name for debugging
        _handler.__name__ = f"catalog__{method.__name__}"
        return _handler
