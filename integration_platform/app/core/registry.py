"""
Integration Registry — auto-discovers and manages all BaseIntegration subclasses.

At startup the registry scans `app/integrations/` and registers every class
that extends BaseIntegration.  FastAPI routes are generated dynamically from
the registered manifests, so adding a new integration never requires touching
any router or infrastructure file.
"""
from __future__ import annotations

import importlib
import logging
import pkgutil
from typing import Dict, List, Optional, Type

from app.core.base_integration import BaseIntegration, ActionNotFoundError, TriggerNotFoundError
from config.settings import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class IntegrationRegistry:
    """
    Singleton registry for all integration connectors.

    Usage:
        registry = IntegrationRegistry.get_instance()
        result  = await registry.execute_action("salesforce", "create_lead", payload)
    """

    _instance: Optional["IntegrationRegistry"] = None
    _classes: Dict[str, Type[BaseIntegration]] = {}    # service_name → class
    _instances: Dict[str, BaseIntegration] = {}         # service_name → live instance

    # ── Singleton ─────────────────────────────────────────────────────────────

    @classmethod
    def get_instance(cls) -> "IntegrationRegistry":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ── Discovery ─────────────────────────────────────────────────────────────

    def discover(self, package: str = "app.integrations") -> None:
        """
        Walk every sub-package of `package` and import all modules.
        Any BaseIntegration subclass found is registered automatically.
        """
        try:
            pkg = importlib.import_module(package)
        except ModuleNotFoundError:
            logger.warning("Integrations package '%s' not found — skipping discovery", package)
            return

        for finder, name, is_pkg in pkgutil.walk_packages(pkg.__path__, prefix=f"{package}."):
            try:
                importlib.import_module(name)
            except Exception as exc:
                logger.warning("Could not import integration module '%s': %s", name, exc)

        # Register every subclass that has been imported into memory
        self._register_all_subclasses(BaseIntegration)
        logger.info(
            "Integration discovery complete. Registered: %s",
            list(self._classes.keys()),
        )

    def _register_all_subclasses(self, base: Type[BaseIntegration]) -> None:
        for subclass in base.__subclasses__():
            self._register_all_subclasses(subclass)   # recurse for multi-level inheritance
            if subclass.service_name:
                self._classes[subclass.service_name] = subclass
                logger.debug("Registered integration class: %s (%s)", subclass.__name__, subclass.service_name)

    # ── Manual registration (for tests / dynamic loading) ─────────────────────

    def register(self, integration_class: Type[BaseIntegration]) -> None:
        if not integration_class.service_name:
            raise ValueError(f"{integration_class.__name__} must define `service_name`")
        self._classes[integration_class.service_name] = integration_class
        # Invalidate cached instance so it is recreated with fresh credentials
        self._instances.pop(integration_class.service_name, None)
        logger.info("Manually registered integration: %s", integration_class.service_name)

    # ── Instance management ───────────────────────────────────────────────────

    def get_integration(
        self,
        service_name: str,
        credentials: Optional[Dict] = None,
    ) -> BaseIntegration:
        """
        Return (and lazily create) a live integration instance.

        Credentials resolution order:
          1. Explicitly passed `credentials` dict
          2. Environment variables / settings object
        """
        if service_name not in self._classes:
            raise ValueError(
                f"Unknown integration '{service_name}'. "
                f"Available: {list(self._classes.keys())}"
            )

        # Use cached instance if credentials not overridden
        if service_name not in self._instances or credentials:
            cls = self._classes[service_name]
            resolved = credentials or self._resolve_credentials(service_name)
            self._instances[service_name] = cls(resolved)

        return self._instances[service_name]

    def _resolve_credentials(self, service_name: str) -> Dict:
        """
        Build a credentials dict from settings for the given service.
        Extend this method when adding new credential sources (e.g. Key Vault).
        """
        s = settings
        mapping: Dict[str, Dict] = {
            "salesforce": {
                "client_id": s.SALESFORCE_CLIENT_ID,
                "client_secret": s.SALESFORCE_CLIENT_SECRET,
                "username": s.SALESFORCE_USERNAME,
                "password": s.SALESFORCE_PASSWORD,
                "security_token": s.SALESFORCE_SECURITY_TOKEN,
                "instance_url": s.SALESFORCE_INSTANCE_URL,
                "api_version": s.SALESFORCE_API_VERSION,
            },
            "jira": {
                "base_url": s.JIRA_BASE_URL,
                "email": s.JIRA_EMAIL,
                "api_token": s.JIRA_API_TOKEN,
            },
            "ukg": {
                "base_url": s.UKG_BASE_URL,
                "app_key": s.UKG_APP_KEY,
                "username": s.UKG_USERNAME,
                "password": s.UKG_PASSWORD,
            },
            "docebo": {
                "base_url": s.DOCEBO_BASE_URL,
                "client_id": s.DOCEBO_CLIENT_ID,
                "client_secret": s.DOCEBO_CLIENT_SECRET,
            },
            "sage_intact": {
                "company_id": s.SAGE_COMPANY_ID,
                "user_id": s.SAGE_USER_ID,
                "user_password": s.SAGE_USER_PASSWORD,
                "client_id": s.SAGE_CLIENT_ID,
                "client_secret": s.SAGE_CLIENT_SECRET,
            },
        }
        return mapping.get(service_name, {})

    # ── Action / Trigger dispatch ─────────────────────────────────────────────

    async def execute_action(
        self, service_name: str, action_name: str, payload: Dict
    ):
        integration = self.get_integration(service_name)
        return await integration.execute_action(action_name, payload)

    async def execute_trigger(
        self, service_name: str, trigger_name: str, context: Dict = None
    ):
        integration = self.get_integration(service_name)
        return await integration.execute_trigger(trigger_name, context)

    # ── Metadata ──────────────────────────────────────────────────────────────

    def list_services(self) -> List[str]:
        return list(self._classes.keys())

    def get_manifest(self, service_name: str) -> Dict:
        integration = self.get_integration(service_name)
        return integration.get_manifest()

    def get_all_manifests(self) -> List[Dict]:
        return [self.get_manifest(svc) for svc in self._classes]


# ── Module-level singleton accessor ───────────────────────────────────────────

def get_registry() -> IntegrationRegistry:
    return IntegrationRegistry.get_instance()
