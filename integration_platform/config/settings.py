"""
Central configuration using Pydantic Settings.
All secrets are pulled from environment variables or Azure Key Vault at startup.
"""
from functools import lru_cache
from typing import List, Optional
from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    # ─── App ─────────────────────────────────────────────────────────────────
    APP_NAME: str = "Integration Platform"
    APP_VERSION: str = "1.0.0"
    ENVIRONMENT: str = Field("development", env="ENVIRONMENT")   # development | staging | production
    DEBUG: bool = Field(False, env="DEBUG")
    SECRET_KEY: str = Field("change-me-in-production", env="SECRET_KEY")

    # ─── Database ─────────────────────────────────────────────────────────────
    DATABASE_URL: str = Field(
        "postgresql+asyncpg://postgres:postgres@localhost:5432/integration_platform",
        env="DATABASE_URL"
    )

    # ─── Azure Service Bus ────────────────────────────────────────────────────
    AZURE_SERVICE_BUS_CONNECTION_STRING: Optional[str] = Field(None, env="AZURE_SERVICE_BUS_CONNECTION_STRING")
    AZURE_SERVICE_BUS_QUEUE_NAME: str = Field("integration-events", env="AZURE_SERVICE_BUS_QUEUE_NAME")
    AZURE_SERVICE_BUS_TOPIC_NAME: str = Field("integration-events-topic", env="AZURE_SERVICE_BUS_TOPIC_NAME")

    # ─── Azure Key Vault ─────────────────────────────────────────────────────
    AZURE_KEY_VAULT_URL: Optional[str] = Field(None, env="AZURE_KEY_VAULT_URL")
    AZURE_CLIENT_ID: Optional[str] = Field(None, env="AZURE_CLIENT_ID")
    AZURE_CLIENT_SECRET: Optional[str] = Field(None, env="AZURE_CLIENT_SECRET")
    AZURE_TENANT_ID: Optional[str] = Field(None, env="AZURE_TENANT_ID")

    # ─── Azure Application Insights ───────────────────────────────────────────
    APPLICATIONINSIGHTS_CONNECTION_STRING: Optional[str] = Field(None, env="APPLICATIONINSIGHTS_CONNECTION_STRING")

    # ─── Salesforce ───────────────────────────────────────────────────────────
    SALESFORCE_CLIENT_ID: Optional[str] = Field(None, env="SALESFORCE_CLIENT_ID")
    SALESFORCE_CLIENT_SECRET: Optional[str] = Field(None, env="SALESFORCE_CLIENT_SECRET")
    SALESFORCE_USERNAME: Optional[str] = Field(None, env="SALESFORCE_USERNAME")
    SALESFORCE_PASSWORD: Optional[str] = Field(None, env="SALESFORCE_PASSWORD")
    SALESFORCE_SECURITY_TOKEN: Optional[str] = Field(None, env="SALESFORCE_SECURITY_TOKEN")
    SALESFORCE_INSTANCE_URL: str = Field("https://login.salesforce.com", env="SALESFORCE_INSTANCE_URL")
    SALESFORCE_API_VERSION: str = Field("v59.0", env="SALESFORCE_API_VERSION")

    # ─── Jira ─────────────────────────────────────────────────────────────────
    JIRA_BASE_URL: Optional[str] = Field(None, env="JIRA_BASE_URL")
    JIRA_EMAIL: Optional[str] = Field(None, env="JIRA_EMAIL")
    JIRA_API_TOKEN: Optional[str] = Field(None, env="JIRA_API_TOKEN")

    # ─── UKG (Kronos) ─────────────────────────────────────────────────────────
    UKG_BASE_URL: Optional[str] = Field(None, env="UKG_BASE_URL")
    UKG_APP_KEY: Optional[str] = Field(None, env="UKG_APP_KEY")
    UKG_USERNAME: Optional[str] = Field(None, env="UKG_USERNAME")
    UKG_PASSWORD: Optional[str] = Field(None, env="UKG_PASSWORD")

    # ─── Docebo ───────────────────────────────────────────────────────────────
    DOCEBO_BASE_URL: Optional[str] = Field(None, env="DOCEBO_BASE_URL")
    DOCEBO_CLIENT_ID: Optional[str] = Field(None, env="DOCEBO_CLIENT_ID")
    DOCEBO_CLIENT_SECRET: Optional[str] = Field(None, env="DOCEBO_CLIENT_SECRET")

    # ─── Sage Intacct ─────────────────────────────────────────────────────────
    SAGE_COMPANY_ID: Optional[str] = Field(None, env="SAGE_COMPANY_ID")
    SAGE_USER_ID: Optional[str] = Field(None, env="SAGE_USER_ID")
    SAGE_USER_PASSWORD: Optional[str] = Field(None, env="SAGE_USER_PASSWORD")
    SAGE_CLIENT_ID: Optional[str] = Field(None, env="SAGE_CLIENT_ID")
    SAGE_CLIENT_SECRET: Optional[str] = Field(None, env="SAGE_CLIENT_SECRET")

    # ─── Polling ──────────────────────────────────────────────────────────────
    POLLING_DEFAULT_INTERVAL_SECONDS: int = Field(300, env="POLLING_DEFAULT_INTERVAL_SECONDS")
    POLLING_MAX_WORKERS: int = Field(10, env="POLLING_MAX_WORKERS")

    # ─── Retry / Circuit Breaker ──────────────────────────────────────────────
    RETRY_MAX_ATTEMPTS: int = Field(3, env="RETRY_MAX_ATTEMPTS")
    RETRY_BASE_DELAY_SECONDS: float = Field(1.0, env="RETRY_BASE_DELAY_SECONDS")
    CIRCUIT_BREAKER_FAILURE_THRESHOLD: int = Field(5, env="CIRCUIT_BREAKER_FAILURE_THRESHOLD")
    CIRCUIT_BREAKER_RECOVERY_TIMEOUT: int = Field(60, env="CIRCUIT_BREAKER_RECOVERY_TIMEOUT")

    # ─── Webhook Security ─────────────────────────────────────────────────────
    WEBHOOK_SIGNATURE_HEADER: str = "X-Hub-Signature-256"
    WEBHOOK_SECRET: str = Field("change-me", env="WEBHOOK_SECRET")

    # ─── CORS ─────────────────────────────────────────────────────────────────
    ALLOWED_ORIGINS: List[str] = Field(["*"], env="ALLOWED_ORIGINS")

    class Config:
        env_file = ".env"
        case_sensitive = True


@lru_cache()
def get_settings() -> Settings:
    return Settings()
