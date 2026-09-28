"""Tests for Prometheus metrics, the tool latency budget and OpenTelemetry tracing."""

import time

from fastapi.testclient import TestClient
from prometheus_client import REGISTRY

from app.main import create_app
from app.services.appointment_service import AppointmentService


def sample(name: str, labels: dict) -> float:
    """Current value of a metric sample (0 if it has not been recorded yet)."""
    return REGISTRY.get_sample_value(name, labels) or 0.0


def test_metrics_endpoint_exposes_prometheus_format(client):
    client.get("/health")

    response = client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "http_requests_total" in response.text


def test_requests_are_counted_by_route_template(client, auth_headers, create_call):
    labels = {"method": "GET", "route": "/calls/{call_id}", "status": "200"}
    before = sample("http_requests_total", labels)
    call = create_call()

    client.get(f"/calls/{call['id']}", headers=auth_headers)

    # Recorded under the template, not the concrete ID, so label values stay bounded.
    assert sample("http_requests_total", labels) == before + 1


def test_unknown_routes_share_one_label(client):
    labels = {"method": "GET", "route": "unmatched", "status": "404"}
    before = sample("http_requests_total", labels)

    client.get("/does-not-exist-1")
    client.get("/does-not-exist-2")

    assert sample("http_requests_total", labels) == before + 2


def test_webhook_outcomes_are_counted(send_webhook):
    received = {"event_type": "call_started", "result": "received"}
    duplicate = {"event_type": "call_started", "result": "duplicate"}
    before_received, before_duplicate = (
        sample("webhook_events_total", received),
        sample("webhook_events_total", duplicate),
    )

    send_webhook("call_started", "ext-metrics", event_id="evt-metrics")
    send_webhook("call_started", "ext-metrics", event_id="evt-metrics")

    assert sample("webhook_events_total", received) == before_received + 1
    assert sample("webhook_events_total", duplicate) == before_duplicate + 1


def test_metrics_can_be_disabled(settings):
    with TestClient(create_app(settings.model_copy(update={"metrics_enabled": False}))) as c:
        assert c.get("/metrics").status_code == 404


def test_slow_tool_response_exceeds_latency_budget(settings, auth_headers, monkeypatch, caplog):
    original = AppointmentService.check_availability

    def slow_check(self, request):
        time.sleep(0.02)  # 20 ms, well over the 5 ms budget configured below.
        return original(self, request)

    monkeypatch.setattr(AppointmentService, "check_availability", slow_check)
    labels = {"route": "/tools/check-availability"}
    before = sample("tool_latency_budget_exceeded_total", labels)
    strict = settings.model_copy(update={"tool_latency_budget_ms": 5})

    with TestClient(create_app(strict)) as client:
        caplog.set_level("WARNING")
        response = client.post(
            "/tools/check-availability", json={"date": "2030-01-15"}, headers=auth_headers
        )

    assert response.status_code == 200  # Still answered; the budget is monitored, not enforced.
    assert sample("tool_latency_budget_exceeded_total", labels) == before + 1
    assert any("latency budget" in r.getMessage() for r in caplog.records)


def test_fast_tool_response_is_within_budget(client, auth_headers):
    labels = {"route": "/tools/check-availability"}
    before = sample("tool_latency_budget_exceeded_total", labels)

    client.post("/tools/check-availability", json={"date": "2030-01-15"}, headers=auth_headers)

    assert sample("tool_latency_budget_exceeded_total", labels) == before


def test_tracing_records_a_span_per_request(settings, auth_headers):
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from app.core.tracing import setup_tracing

    traced = settings.model_copy(update={"tracing_enabled": False})
    app = create_app(traced)  # Built without tracing, then instrumented with a test exporter.
    exporter = InMemorySpanExporter()
    setup_tracing(app, settings.model_copy(update={"tracing_enabled": True}), exporter=exporter)

    with TestClient(app) as client:
        client.get("/calls", headers=auth_headers)

    names = [span.name for span in exporter.get_finished_spans()]
    assert any("/calls" in name for name in names)
