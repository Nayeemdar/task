"""
Azure Functions v2 entry point — Integration Platform.

Architecture:
  HTTP trigger  → AsgiMiddleware → FastAPI app (all existing routes unchanged)
  Timer trigger → PollingService.poll_all_once() every 5 minutes
                  (replaces the in-process asyncio loops used in Docker/uvicorn mode)

All environment variables are set as Azure Function App Application Settings.
They map 1-to-1 with the keys in .env.example / local.settings.json.
"""
from __future__ import annotations

import logging

import azure.functions as func
from azure.functions import AsgiMiddleware

from app.main import app as fastapi_app
from app.core.polling_service import get_polling_service
from app.core.registry import get_registry

logger = logging.getLogger(__name__)

# ── Function App ───────────────────────────────────────────────────────────────

func_app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)


# ── HTTP trigger — serves the full FastAPI / OpenAPI app ──────────────────────

@func_app.route(route="{*route}")
async def http_trigger(
    req: func.HttpRequest,
    context: func.Context,
) -> func.HttpResponse:
    """
    Catch-all HTTP trigger.

    Routes:
      POST /api/v1/integrations/{service}/actions/{action}
      POST /api/v1/webhooks/{service}[/{trigger}]
      POST /api/v1/workflows/
      GET  /api/v1/events/
      GET  /api/v1/health/live
      GET  /docs   (Swagger UI)
    """
    return await AsgiMiddleware(fastapi_app).handle_async(req, context)


# ── Timer trigger — polling service (every 5 minutes) ─────────────────────────

@func_app.timer_trigger(
    schedule="0 */5 * * * *",   # every 5 minutes (NCRONTAB: sec min hr day month dow)
    arg_name="timer",
    run_on_startup=False,
)
async def polling_timer(timer: func.TimerRequest) -> None:
    """
    Azure Functions timer replaces the in-process asyncio polling loops.

    On each invocation:
      1. Ensures integrations are discovered (registry may be cold in this worker).
      2. Calls poll_all_once() — one poll cycle across every @trigger(POLLING) method.

    If the timer fires late (past_due), it still runs immediately.
    """
    if timer.past_due:
        logger.warning("Polling timer is past due — executing now")

    # Registry may be cold in a fresh timer-worker process
    registry = get_registry()
    if not registry.list_services():
        registry.discover("app.integrations")

    polling = get_polling_service()
    await polling.poll_all_once()
    logger.info("Polling cycle complete")
