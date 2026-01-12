"""
OpenTelemetry configuration and instrumentation for the boss_file_utils application.

Provides tracer setup and helper functions for span management.
"""

from __future__ import annotations

from opentelemetry import trace
from opentelemetry.instrumentation.asyncio import AsyncioInstrumentor
from opentelemetry.instrumentation.logging import LoggingInstrumentor
from opentelemetry.instrumentation.sqlite3 import SQLite3Instrumentor
from opentelemetry.instrumentation.system_metrics import SystemMetricsInstrumentor
from opentelemetry.instrumentation.threading import ThreadingInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from opentelemetry.trace import Status, StatusCode

_tracer: trace.Tracer | None = None
_instrumented: bool = False


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


def instrument_all() -> None:
    """
    Activate all OpenTelemetry instrumentations for CLI usage.

    Instruments: sqlite3, asyncio, threading, logging, system-metrics.
    Safe to call multiple times - only instruments once.
    """
    global _instrumented

    if _instrumented:
        return

    SQLite3Instrumentor().instrument()
    AsyncioInstrumentor().instrument()
    ThreadingInstrumentor().instrument()
    LoggingInstrumentor().instrument(set_logging_format=True)
    SystemMetricsInstrumentor().instrument()

    _instrumented = True


def uninstrument_all() -> None:
    """
    Deactivate all OpenTelemetry instrumentations.

    Useful for testing or cleanup.
    """
    global _instrumented

    if not _instrumented:
        return

    SQLite3Instrumentor().uninstrument()
    AsyncioInstrumentor().uninstrument()
    ThreadingInstrumentor().uninstrument()
    LoggingInstrumentor().uninstrument()
    SystemMetricsInstrumentor().uninstrument()

    _instrumented = False
