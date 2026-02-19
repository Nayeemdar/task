# Integration Platform — Workato Replacement

A production-ready **FastAPI**-based integration platform that replaces Workato.

---

## Architecture Diagram Improvements (vs. Proposed Design)

| Proposed | Implemented / Improved |
|---|---|
| Azure Functions as cloud endpoint | FastAPI app — cheaper, no cold starts, full control |
| No explicit plugin model | **BaseIntegration class** — add one method = new feature, zero infra changes |
| No workflow/recipe engine | **WorkflowEngine** — Workato-style multi-step recipes with data mapping |
| No retry/circuit breaker | **RetryHandler** with exponential backoff + circuit breaker per service |
| No event replay | **EventBus + DB audit log** — any event can be replayed via API |
| Single webhook URL implied | **Per-service + per-trigger webhook routing** |
| No test/dry-run mode | `dry_run=true` on every action call |
| No data mapping layer | **DataMapper** — `{{field | transform}}` template expressions |
| Polling assumed manual | **PollingService** — auto-schedules every `@trigger(type=POLLING)` method |

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

## Quick Start

```bash
cp .env.example .env
# Edit .env with your credentials

docker-compose up -d

# API docs
open http://localhost:8000/docs
```

---

## Project Structure

```
integration_platform/
├── app/
│   ├── main.py                    # FastAPI app + lifespan
│   ├── core/
│   │   ├── base_integration.py    # Abstract base — @action / @trigger decorators
│   │   ├── registry.py            # Auto-discovery + single dispatch entry point
│   │   ├── event_bus.py           # Async pub/sub — wildcard topic support
│   │   ├── workflow_engine.py     # Recipe execution + {{template}} data mapping
│   │   ├── data_mapper.py         # Field-level transforms
│   │   ├── polling_service.py     # Background polling scheduler
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
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
└── .env.example
```
