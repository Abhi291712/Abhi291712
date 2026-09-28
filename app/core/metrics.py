"""Prometheus metrics: counters and histograms describing how the service is behaving.

Logs answer "what happened to this request?"; metrics answer "how is the system doing overall?"
(request rate, error rate, latency percentiles, how often tools blow their latency budget).
Prometheus scrapes ``GET /metrics`` periodically and dashboards/alerts are built on top.

Metrics are defined once at module level because Prometheus requires each metric name to be
registered only once per process. Labels are kept low-cardinality: routes are recorded as
templates such as ``/calls/{call_id}``, never as raw paths containing IDs.
"""

from prometheus_client import Counter, Histogram

HTTP_REQUESTS = Counter(
    "http_requests_total",
    "HTTP requests handled, by method, route template and status code.",
    ["method", "route", "status"],
)

HTTP_LATENCY = Histogram(
    "http_request_duration_seconds",
    "Time from request start to the last byte of the response.",
    ["method", "route"],
    # Fine-grained buckets below one second, where voice tool latency matters most.
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 0.8, 1.0, 2.5, 5.0, 10.0),
)

TOOL_LATENCY_BUDGET_EXCEEDED = Counter(
    "tool_latency_budget_exceeded_total",
    "Agent tool responses slower than TOOL_LATENCY_BUDGET_MS (a caller was left waiting).",
    ["route"],
)

WEBHOOK_EVENTS = Counter(
    "webhook_events_total",
    "Webhook events by type and outcome (received, duplicate, processed, failed).",
    ["event_type", "result"],
)

LLM_ANALYSES = Counter(
    "llm_call_analyses_total",
    "LLM transcript analyses by outcome (success, unusable, error).",
    ["result"],
)

CALENDAR_SYNCS = Counter(
    "calendar_syncs_total",
    "Appointments pushed to the external calendar, by outcome.",
    ["result"],
)

RATE_LIMITED = Counter("rate_limited_requests_total", "Requests rejected with 429.")
