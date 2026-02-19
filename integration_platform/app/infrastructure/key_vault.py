"""
Azure Key Vault connector — retrieves secrets at startup and caches them.

The registry calls `key_vault.get_secret(name)` when building credential dicts.
Falls back to environment variables / settings when Key Vault is not configured.
"""
from __future__ import annotations

import logging
from functools import lru_cache
from typing import Dict, Optional

from config.settings import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

try:
    from azure.identity.aio import ClientSecretCredential, DefaultAzureCredential
    from azure.keyvault.secrets.aio import SecretClient
    AZURE_AVAILABLE = True
except ImportError:
    AZURE_AVAILABLE = False
    logger.warning("azure-identity / azure-keyvault-secrets not installed — Key Vault disabled")


class KeyVaultClient:
    """
    Async wrapper around Azure Key Vault.

    Secrets are fetched on first access and cached in-process.
    Set AZURE_KEY_VAULT_URL to enable.
    """

    def __init__(self):
        self._vault_url = settings.AZURE_KEY_VAULT_URL
        self._enabled = bool(self._vault_url and AZURE_AVAILABLE)
        self._cache: Dict[str, str] = {}
        self._client: Optional[object] = None

    async def connect(self) -> None:
        if not self._enabled:
            logger.info("KeyVaultClient: running without Key Vault (no URL or SDK)")
            return

        if settings.AZURE_CLIENT_ID and settings.AZURE_CLIENT_SECRET:
            credential = ClientSecretCredential(
                tenant_id=settings.AZURE_TENANT_ID,
                client_id=settings.AZURE_CLIENT_ID,
                client_secret=settings.AZURE_CLIENT_SECRET,
            )
        else:
            credential = DefaultAzureCredential()

        self._client = SecretClient(vault_url=self._vault_url, credential=credential)
        logger.info("KeyVaultClient connected to %s", self._vault_url)

    async def close(self) -> None:
        if self._client:
            await self._client.close()

    async def get_secret(self, name: str) -> Optional[str]:
        """Retrieve a secret by name. Returns None if not found."""
        if name in self._cache:
            return self._cache[name]

        if not self._enabled or not self._client:
            logger.debug("KeyVault disabled — secret '%s' not fetched", name)
            return None

        try:
            secret = await self._client.get_secret(name)
            self._cache[name] = secret.value
            logger.debug("Secret '%s' fetched from Key Vault", name)
            return secret.value
        except Exception as exc:
            logger.warning("Could not retrieve secret '%s': %s", name, exc)
            return None

    async def set_secret(self, name: str, value: str) -> None:
        """Store a secret in Key Vault and update local cache."""
        if not self._enabled or not self._client:
            logger.warning("KeyVault disabled — secret '%s' not persisted", name)
            self._cache[name] = value
            return

        try:
            await self._client.set_secret(name, value)
            self._cache[name] = value
            logger.info("Secret '%s' stored in Key Vault", name)
        except Exception as exc:
            logger.error("Failed to store secret '%s': %s", name, exc)

    def get_cached_secret(self, name: str) -> Optional[str]:
        """Synchronous access to already-fetched secrets."""
        return self._cache.get(name)

    async def preload_secrets(self, names: list[str]) -> None:
        """Pre-fetch multiple secrets at startup."""
        for name in names:
            await self.get_secret(name)
        logger.info("Pre-loaded %d secret(s) from Key Vault", len(names))


# ── Module-level singleton ─────────────────────────────────────────────────────

_kv: Optional[KeyVaultClient] = None


async def get_key_vault() -> KeyVaultClient:
    global _kv
    if _kv is None:
        _kv = KeyVaultClient()
        await _kv.connect()
    return _kv
