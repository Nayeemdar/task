"""
Telemetry — OpenTelemetry traces + metrics forwarded to Azure Application Insights.

When APPLICATIONINSIGHTS_CONNECTION_STRING is not set the SDK runs with a
no-op exporter so local dev works without any Azure dependency.
"""
from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from functools import wraps
from typing import Any, Callable, Optional

from config.settings import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

try:
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
    OT_AVAILABLE = True
except ImportError:
    OT_AVAILABLE = False
    logger.warning("opentelemetry-sdk not installed — telemetry disabled")

try:
    from azure.monitor.opentelemetry.exporter import AzureMonitorTraceExporter
    AZURE_MONITOR_AVAILABLE = True
except ImportError:
    AZURE_MONITOR_AVAILABLE = False


def setup_telemetry() -> None:
    """
    Configure the global OpenTelemetry TracerProvider at application startup.
    Call once from lifespan.
    """
    if not OT_AVAILABLE:
        return

    provider = TracerProvider()

    conn_str = settings.APPLICATIONINSIGHTS_CONNECTION_STRING
    if conn_str and AZURE_MONITOR_AVAILABLE:
        exporter = AzureMonitorTraceExporter(connection_string=conn_str)
        logger.info("Telemetry: Azure Application Insights exporter configured")
    else:
        exporter = ConsoleSpanExporter()
        logger.info("Telemetry: using console exporter (no Application Insights connection string)")

    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)


def get_tracer(name: str = "integration_platform"):
    if OT_AVAILABLE:
        return trace.get_tracer(name)
    return _NoOpTracer()


# ─── Decorator ────────────────────────────────────────────────────────────────

def traced(span_name: str = None, service: str = ""):
    """
    Decorator that wraps an async function in an OpenTelemetry span.

    Usage:
        @traced("salesforce.create_lead", service="salesforce")
        async def create_lead(self, payload):
            ...
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            tracer = get_tracer()
            name = span_name or func.__qualname__
            with tracer.start_as_current_span(name) as span:
                if service:
                    span.set_attribute("integration.service", service)
                span.set_attribute("function", func.__name__)
                start = time.perf_counter()
                try:
                    result = await func(*args, **kwargs)
                    span.set_attribute("success", True)
                    return result
                except Exception as exc:
                    span.set_attribute("success", False)
                    span.set_attribute("error", str(exc))
                    span.record_exception(exc)
                    raise
                finally:
                    elapsed = (time.perf_counter() - start) * 1000
                    span.set_attribute("duration_ms", round(elapsed, 2))
        return wrapper
    return decorator


# ─── No-op tracer for when OT SDK is missing ──────────────────────────────────

class _NoOpSpan:
    def set_attribute(self, *a, **kw): pass
    def record_exception(self, *a, **kw): pass
    def __enter__(self): return self
    def __exit__(self, *a): pass


class _NoOpTracer:
    def start_as_current_span(self, name, **kw):
        return _NoOpSpan()
