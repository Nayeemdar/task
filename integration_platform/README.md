# Integration Platform — Workato Replacement

A production-ready **FastAPI**-based integration platform that replaces Workato.

---

## Architecture

| Concern | Implementation |
|---|---|
| **HTTP entrypoint** | Azure Function App — HTTP trigger wraps FastAPI via `AsgiMiddleware` |
| **Polling scheduler** | Azure Functions Timer Trigger — fires every 5 min, no always-on process needed |
| **Plugin model** | **BaseIntegration class** — add one method = new feature, zero infra changes |
| **Workflow/recipe engine** | **WorkflowEngine** — Workato-style multi-step recipes with data mapping |
| **Retry / circuit breaker** | **RetryHandler** with exponential backoff + circuit breaker per service |
| **Event replay** | **EventBus + DB audit log** — any event can be replayed via API |
| **Webhook routing** | Per-service + per-trigger webhook routing |
| **Test/dry-run mode** | `dry_run=true` on every action call |
| **Data mapping** | **DataMapper** — `{{field \| transform}}` template expressions |
| **Secrets** | Azure Key Vault — loaded at startup, cached in-process |
| **Observability** | OpenTelemetry → Azure Application Insights |

---

## Key Design: How to Add a New Integration

**Zero infrastructure changes required.** Just:

```
1. Create app/integrations/<service>/__init__.py
2. Create app/integrations/<service>/integration.py
3. Write a class extending BaseIntegration
4. Add methods with @action or @trigger decorators
```

The registry auto-discovers it at startup and exposes it through the API.

### Example — Adding a "Glean" integration

```python
# app/integrations/glean/integration.py

from app.core.base_integration import BaseIntegration, action, trigger, ActionResult, TriggerResult, TriggerType

class GleanIntegration(BaseIntegration):
    service_name = "glean"
    service_description = "Glean enterprise search"

    async def authenticate(self) -> bool:
        # set up self._client with auth headers
        return True

    @action(description="Search Glean index")
    async def search(self, payload: dict) -> ActionResult:
        resp = await self._client.get("/search", params={"q": payload["query"]})
        return ActionResult.ok(resp.json())

    @action(description="Index a document")
    async def index_document(self, payload: dict) -> ActionResult:
        resp = await self._client.post("/index", json=payload)
        return ActionResult.ok(resp.json())

    @trigger(description="New documents available", trigger_type=TriggerType.POLLING, poll_interval_seconds=300)
    async def on_new_document(self, context: dict) -> TriggerResult:
        # ... fetch new documents since context["cursor"]
        return TriggerResult(events=[...], cursor="new_cursor")
```

**That's it.** The following are now automatically available:
- `GET  /api/v1/integrations/glean` — manifest with all actions/triggers
- `POST /api/v1/integrations/glean/actions/search`
- `POST /api/v1/integrations/glean/actions/index_document`
- `POST /api/v1/webhooks/glean` — inbound webhook receiver
- Background polling every 5 minutes via PollingService

---

## API Reference

### Single Entry Point Pattern

```
POST /api/v1/integrations/{service}/actions/{action}
```

Body:
```json
{
  "payload": { "...action-specific params..." },
  "correlation_id": "optional-trace-id",
  "dry_run": false
}
```

### Webhooks
```
POST /api/v1/webhooks/{service}                  # → on_webhook trigger
POST /api/v1/webhooks/{service}/{trigger_name}   # → specific trigger method
```

### Workflows (Recipes)
```
POST   /api/v1/workflows/          Create a workflow
GET    /api/v1/workflows/          List all workflows
POST   /api/v1/workflows/{id}/run  Manually run a workflow (test mode)
```

### Events (Audit + Replay)
```
GET  /api/v1/events/                      List events with filters
GET  /api/v1/events/{event_id}            Get single event
POST /api/v1/events/{event_id}/replay     Re-publish event
```

### Health
```
GET /api/v1/health/live    Kubernetes liveness
GET /api/v1/health/ready   Kubernetes readiness
GET /api/v1/health/        Full status + circuit breakers
GET /api/v1/health/polling Polling job status
```

---

## Workato Feature Parity

| Workato Feature | Platform Equivalent |
|---|---|
| Recipes | WorkflowEngine (POST /workflows/) |
| Triggers | `@trigger` decorator on integration class |
| Actions | `@action` decorator on integration class |
| Connections | ConnectionModel + Key Vault |
| Data pills / mapping | DataMapper with `{{field\|transform}}` syntax |
| Polling triggers | PollingService background scheduler |
| Webhook triggers | /webhooks/{service} endpoint |
| Retry on failure | RetryHandler (exponential backoff) |
| Error monitoring | EventBus audit log + App Insights |
| Test mode | `dry_run=true` on any action |
| Event replay | POST /events/{id}/replay |
| Circuit breaker | Per-service circuit breaker in RetryHandler |

---

## Quick Start — Local Development

```bash
# 1. Install Azure Functions Core Tools (v4)
npm install -g azure-functions-core-tools@4

# 2. Copy and fill in credentials
cp local.settings.json.example local.settings.json   # or just edit local.settings.json

# 3. Start a local Postgres (if not already running)
docker-compose up -d db

# 4. Run locally via Azure Functions Core Tools
cd integration_platform
func start

# API docs (served through Azure Functions HTTP trigger)
open http://localhost:7071/docs
```

The `func start` command loads `local.settings.json` as environment variables,
starts the Azure Functions host, and serves both the HTTP trigger and the
Timer Trigger locally.

---

## Deploy to Azure Function App

### Prerequisites

- Azure Function App (Linux, Python 3.12)
- Azure Container Registry (for custom container deployments) OR
  zip-deploy via `func azure functionapp publish`

### Option A — Zip deploy (simplest)

```bash
cd integration_platform

# Publish directly from local source
func azure functionapp publish <YOUR_FUNCTION_APP_NAME> --python
```

### Option B — Container deploy

```bash
# Build and push to ACR
az acr build \
  --registry <YOUR_ACR_NAME> \
  --image integration-platform:latest \
  .

# Configure the Function App to use the container
az functionapp config container set \
  --name <YOUR_FUNCTION_APP_NAME> \
  --resource-group <YOUR_RG> \
  --docker-custom-image-name <YOUR_ACR_NAME>.azurecr.io/integration-platform:latest
```

### Application Settings (replace `.env` values)

Set all keys from `local.settings.json → Values` as **Application Settings**
in the Azure Portal (Function App → Configuration → Application settings), or
via CLI:

```bash
az functionapp config appsettings set \
  --name <YOUR_FUNCTION_APP_NAME> \
  --resource-group <YOUR_RG> \
  --settings \
    DATABASE_URL="postgresql+asyncpg://..." \
    AZURE_SERVICE_BUS_CONNECTION_STRING="Endpoint=sb://..." \
    AZURE_KEY_VAULT_URL="https://your-vault.vault.azure.net/" \
    SALESFORCE_CLIENT_ID="..." \
    # ... (see local.settings.json for full list)
```

> **Note:** `FUNCTIONS_WORKER_RUNTIME=python` and `AzureWebJobsStorage` are
> set automatically by Azure when you create a Python Function App.

---

## Azure Functions — How It Works

```
HTTP request
  └─► http_trigger (function_app.py)
        └─► AsgiMiddleware(fastapi_app)
              └─► FastAPI router → integration action / webhook / workflow

Timer (every 5 min)
  └─► polling_timer (function_app.py)
        └─► PollingService.poll_all_once()
              └─► calls every @trigger(type=POLLING) across all integrations
                    └─► publishes events → EventBus → Azure Service Bus
```

The FastAPI `lifespan` still runs on first HTTP request (initialises DB,
Key Vault, registry, EventBus, Service Bus bridge). When `FUNCTIONS_WORKER_RUNTIME`
is detected, the **in-process asyncio polling loops are skipped** — the Timer
Trigger drives polling instead.

---

## Project Structure

```
integration_platform/
├── function_app.py            # Azure Functions v2 entry point
│                              #   http_trigger  — ASGI wrapper for FastAPI
│                              #   polling_timer — Timer Trigger (every 5 min)
├── host.json                  # Azure Functions host configuration
├── local.settings.json        # Local dev settings (never commit real secrets)
├── app/
│   ├── main.py                # FastAPI app + lifespan (Azure-aware)
│   ├── core/
│   │   ├── base_integration.py    # Abstract base — @action / @trigger decorators
│   │   ├── registry.py            # Auto-discovery + single dispatch entry point
│   │   ├── event_bus.py           # Async pub/sub — wildcard topic support
│   │   ├── workflow_engine.py     # Recipe execution + {{template}} data mapping
│   │   ├── data_mapper.py         # Field-level transforms
│   │   ├── polling_service.py     # poll_all_once() used by Timer Trigger
│   │   └── retry_handler.py       # Exponential backoff + circuit breaker
│   ├── infrastructure/
│   │   ├── service_bus.py         # Azure Service Bus bridge
│   │   ├── key_vault.py           # Azure Key Vault secret loader
│   │   └── telemetry.py           # OpenTelemetry → App Insights
│   ├── integrations/
│   │   ├── salesforce/            # 9 actions + 2 triggers
│   │   ├── jira/                  # 9 actions + 2 triggers
│   │   ├── ukg/                   # 7 actions + 2 triggers
│   │   ├── docebo/                # 7 actions + 2 triggers
│   │   └── sage_intact/           # 4 actions + 1 trigger
│   ├── api/v1/
│   │   ├── integrations.py        # Dynamic action endpoint
│   │   ├── webhooks.py            # Universal webhook receiver
│   │   ├── workflows.py           # Recipe CRUD + manual run
│   │   ├── events.py              # Event log + replay
│   │   └── health.py              # Liveness / readiness / polling status
│   ├── models/
│   │   ├── db_models.py           # SQLAlchemy ORM (Postgres / CRDB compatible)
│   │   └── schemas.py             # Pydantic v2 request/response schemas
│   └── db/session.py              # Async session factory
├── config/settings.py             # Pydantic Settings — all env vars
├── requirements.txt               # Includes azure-functions>=1.21
├── Dockerfile                     # Azure Functions Python 4 runtime image
└── docker-compose.yml             # Local DB only (functions run via func start)
```
