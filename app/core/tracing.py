"""Optional distributed tracing with OpenTelemetry.

A trace shows one request as a timeline of spans (the HTTP handler, and anything instrumented
inside it), which makes it easy to see *where* a slow tool call spent its time. Traces are
exported over OTLP to any compatible backend (Jaeger, Grafana Tempo, Honeycomb, Datadog, ...).

Switched on with ``TRACING_ENABLED=true``. The exporter is configured with the standard
OpenTelemetry environment variables, for example::

    OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
    OTEL_SERVICE_NAME=voice-ai-backend
"""

import logging

from fastapi import FastAPI

from app.core.config import Settings

logger = logging.getLogger(__name__)


def setup_tracing(app: FastAPI, settings: Settings, exporter=None) -> None:
    """Instrument the app when tracing is enabled. `exporter` lets tests capture spans."""
    if not settings.tracing_enabled:
        return

    # Imported lazily: the OpenTelemetry packages are only needed when tracing is on.
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor

    provider = TracerProvider(resource=Resource.create({"service.name": settings.app_name}))
    if exporter is None:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        # Batching sends spans in the background so tracing adds almost no request latency.
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    else:
        provider.add_span_processor(SimpleSpanProcessor(exporter))

    # Passing the provider explicitly (instead of setting a process-wide global) keeps each
    # app instance independent, which matters in tests that build several apps.
    FastAPIInstrumentor.instrument_app(
        app, tracer_provider=provider, excluded_urls="health,metrics"
    )
    app.state.tracer_provider = provider
    logger.info("Tracing enabled")
