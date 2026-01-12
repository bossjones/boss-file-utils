"""
FastAPI application with OpenTelemetry instrumentation and structured logging.

Endpoints:
- GET /              : Hello World response
- GET /ping          : Health check
- GET /items/{item_id} : Item lookup with optional query param, simulates I/O delay
- GET /invalid       : Raises ValueError (demonstrates error handling)
- GET /exception     : Demonstrates OpenTelemetry span exception recording
- GET /external-api  : Makes external HTTP call to httpbin.org
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
from asgi_correlation_id import CorrelationIdMiddleware
from fastapi import FastAPI, HTTPException, Request
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.trace import Status, StatusCode

from boss_file_utils.logging_config import configure_logging, get_logger, stop_queue_listener
from boss_file_utils.middleware import AccessLogMiddleware
from boss_file_utils.telemetry import get_tracer, record_exception_on_span, setup_telemetry


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:  # pyright: ignore[reportUnusedParameter]
    """
    Application lifespan handler for startup/shutdown.

    Initializes OpenTelemetry and logging on startup.
    """
    setup_telemetry(service_name="boss-file-utils")
    configure_logging(json_output=False)

    HTTPXClientInstrumentor().instrument()

    log = get_logger(__name__)
    log.info("Application started")

    yield

    log.info("Application shutting down")
    stop_queue_listener()


app = FastAPI(
    title="Logging Lab API",
    description="Demo API with OpenTelemetry and structlog integration",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(CorrelationIdMiddleware)
app.add_middleware(AccessLogMiddleware)

FastAPIInstrumentor.instrument_app(app)


@app.get("/")
async def read_root() -> dict[str, str]:
    """Return Hello World response."""
    log = get_logger(__name__)
    log.info("Root endpoint called")
    return {"Hello": "World"}


@app.get("/ping")
async def health_check() -> str:
    """Health check endpoint."""
    return "pong"


@app.get("/items/{item_id}")
async def read_item(item_id: int, q: str | None = None) -> dict[str, Any]:
    """
    Read an item by ID with optional query parameter.

    Simulates I/O delay for even item IDs to demonstrate async behavior.
    """
    log = get_logger(__name__)
    tracer = get_tracer()

    with tracer.start_as_current_span("process_item") as span:
        span.set_attribute("item.id", item_id)
        if q:
            span.set_attribute("item.query", q)

        log.info("Processing item", item_id=item_id, query=q)

        if item_id % 2 == 0:
            log.debug("Simulating I/O delay for even item_id", item_id=item_id)
            await asyncio.sleep(0.1)

        return {"item_id": item_id, "q": q}


@app.get("/invalid")
async def invalid_endpoint() -> dict[str, str]:
    """
    Endpoint that raises a ValueError.

    Demonstrates error handling and exception propagation.
    """
    log = get_logger(__name__)
    log.warning("About to raise ValueError")
    raise ValueError("This is an intentional error for testing")


@app.get("/exception")
async def exception_demo() -> dict[str, str]:
    """
    Demonstrates OpenTelemetry span recording with exception handling.

    Records the exception on the span, sets error status, but returns
    a successful response after handling the error.
    """
    log = get_logger(__name__)
    tracer = get_tracer()

    with tracer.start_as_current_span("exception_demo_span") as span:
        try:
            log.info("Starting exception demo")
            raise RuntimeError("Simulated error for OpenTelemetry demo")
        except RuntimeError as e:
            span.record_exception(e)
            span.set_status(Status(StatusCode.ERROR, str(e)))
            log.exception("Caught and recorded exception", error=str(e))

            return {"status": "error_handled", "message": str(e)}


@app.get("/external-api")
async def external_api_call() -> dict[str, Any]:
    """
    Makes an external HTTP call to httpbin.org.

    Demonstrates httpx async client with OpenTelemetry instrumentation.
    The HTTP call will be automatically traced via HTTPXClientInstrumentation.
    """
    log = get_logger(__name__)
    tracer = get_tracer()

    with tracer.start_as_current_span("external_api_call") as span:
        span.set_attribute("http.url", "https://httpbin.org/get")

        log.info("Making external API call")

        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                response = await client.get("https://httpbin.org/get")
                response.raise_for_status()

                data = response.json()
                log.info(
                    "External API call successful",
                    status_code=response.status_code,
                )

                return {
                    "source": "httpbin.org",
                    "status_code": response.status_code,
                    "origin": data.get("origin"),
                }
            except httpx.HTTPStatusError as e:
                record_exception_on_span(e)
                log.error("External API returned error", status_code=e.response.status_code)
                raise HTTPException(
                    status_code=502,
                    detail=f"External API error: {e.response.status_code}",
                ) from e
            except httpx.RequestError as e:
                record_exception_on_span(e)
                log.error("External API request failed", error=str(e))
                raise HTTPException(
                    status_code=503,
                    detail="External API unavailable",
                ) from e


@app.exception_handler(ValueError)
async def value_error_handler(
    request: Request,  # pyright: ignore[reportUnusedParameter]
    exc: ValueError,
) -> None:
    """Handle ValueError exceptions by recording to span and returning 400."""
    log = get_logger(__name__)
    record_exception_on_span(exc)
    log.error("ValueError caught", error=str(exc))
    raise HTTPException(status_code=400, detail=str(exc))
