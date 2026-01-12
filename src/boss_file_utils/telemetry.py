"""
OpenTelemetry configuration and instrumentation for the boss_file_utils application.

Provides tracer setup and helper functions for span management.
"""

from __future__ import annotations

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from opentelemetry.trace import Status, StatusCode

_tracer: trace.Tracer | None = None


def setup_telemetry(service_name: str = "boss-file-utils") -> trace.Tracer:
    """
    Initialize OpenTelemetry with a console exporter for development.

    In production, replace ConsoleSpanExporter with OTLPSpanExporter.
    """
    global _tracer

    resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(resource=resource)

    # Console exporter for development; swap with OTLP for production
    processor = BatchSpanProcessor(ConsoleSpanExporter())
    provider.add_span_processor(processor)

    trace.set_tracer_provider(provider)
    _tracer = trace.get_tracer(__name__)

    return _tracer


def get_tracer() -> trace.Tracer:
    """Return the configured tracer instance, or a no-op tracer if not initialized."""
    if _tracer is None:
        return trace.get_tracer(__name__)
    return _tracer


def record_exception_on_span(exception: Exception) -> None:
    """
    Record an exception on the current span and set error status.

    Use this in exception handlers to capture error details in traces.
    """
    span = trace.get_current_span()
    if span.is_recording():
        span.record_exception(exception)
        span.set_status(Status(StatusCode.ERROR, str(exception)))
