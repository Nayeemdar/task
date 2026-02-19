"""
Prometheus metrics for the on-premise integration platform process.

All counters/gauges/histograms are defined here so every module can import
them without circular dependencies.  The metrics HTTP server in
health_server.py calls prometheus_client.generate_latest() to expose them
on GET /metrics.

Metric naming follows the Prometheus convention:
    {namespace}_{subsystem}_{name}_{unit}
Namespace: integration_platform
"""
from prometheus_client import Counter, Gauge, Histogram

# ── EventBus / Service Bus ─────────────────────────────────────────────────

EVENTS_RECEIVED = Counter(
    "integration_platform_events_received_total",
    "Total integration events received (all sources)",
    ["service_name", "event_type", "source"],  # source = service_bus | polling
)

SERVICE_BUS_MESSAGES = Counter(
    "integration_platform_service_bus_messages_total",
    "Raw Azure Service Bus messages pulled from the queue",
    ["status"],  # status = received | error
)

# ── BizOps audit writes ────────────────────────────────────────────────────

BIZOPS_WRITES = Counter(
    "integration_platform_bizops_writes_total",
    "Successful BizOps audit rows written to SQL",
)

BIZOPS_WRITE_ERRORS = Counter(
    "integration_platform_bizops_write_errors_total",
    "Failed BizOps SQL write attempts (logged and swallowed)",
)

# ── Workflow engine ────────────────────────────────────────────────────────

WORKFLOW_RUNS = Counter(
    "integration_platform_workflow_runs_total",
    "Workflow executions by final status",
    ["workflow_name", "status"],  # status = success | failure | skipped
)

WORKFLOW_DURATION = Histogram(
    "integration_platform_workflow_duration_seconds",
    "End-to-end workflow execution time in seconds",
    ["workflow_name"],
    buckets=(0.1, 0.5, 1, 5, 10, 30, 60, 120),
)

# ── Polling service ────────────────────────────────────────────────────────

POLLING_RUNS = Counter(
    "integration_platform_polling_runs_total",
    "Polling trigger invocations by result",
    ["service_name", "trigger_name", "status"],  # status = ok | error
)

POLLING_EVENTS_EMITTED = Counter(
    "integration_platform_polling_events_emitted_total",
    "Events published to EventBus as a result of polling",
    ["service_name", "trigger_name"],
)

# ── Circuit breaker ────────────────────────────────────────────────────────

CIRCUIT_BREAKER_STATE = Gauge(
    "integration_platform_circuit_breaker_state",
    "Circuit breaker state per service (0=closed, 1=open, 2=half-open)",
    ["service_name"],
)

# ── Process health ─────────────────────────────────────────────────────────

PROCESS_UPTIME = Gauge(
    "integration_platform_process_uptime_seconds",
    "Seconds since the on-prem process started",
)

EVENT_BUS_QUEUE_SIZE = Gauge(
    "integration_platform_event_bus_queue_size",
    "Current number of events waiting in the internal asyncio EventBus queue",
)
