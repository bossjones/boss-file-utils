"""
Structlog configuration with OpenTelemetry trace context integration.

Configures structured logging with correlation IDs, trace context, and
non-blocking I/O via QueueHandler/QueueListener for production use.

Architecture:
- Structlog as frontend API with context propagation
- Python stdlib logging as backend with QueueHandler for non-blocking I/O
- ProcessorFormatter unifies both structlog and library logs (Uvicorn, etc.)
"""

from __future__ import annotations

import atexit
import logging
import logging.config
import sys
from logging.handlers import QueueHandler, QueueListener
from queue import Queue
from typing import Any

import structlog
from asgi_correlation_id import correlation_id
from opentelemetry import trace
from typing_extensions import override

# Module-level state for QueueListener lifecycle management
_queue_listener: QueueListener | None = None
_log_queue: Queue[logging.LogRecord] | None = None


class NonFormattingQueueHandler(QueueHandler):
    """
    QueueHandler that preserves the original LogRecord without pre-formatting.

    The standard QueueHandler.prepare() calls format() and sets record.msg to
    the formatted string, which breaks structlog's ProcessorFormatter that
    expects record.msg to be the original event dict. This subclass skips
    the formatting step to preserve the dict through the queue.
    """

    @override
    def prepare(self, record: logging.LogRecord) -> logging.LogRecord:
        """Prepare record for queuing without formatting."""
        # Do NOT call format() or modify record.msg/record.args
        # Just return the record as-is so ProcessorFormatter can handle it
        return record


def add_correlation_id(
    logger: logging.Logger,  # pyright: ignore[reportUnusedParameter]
    method_name: str,  # pyright: ignore[reportUnusedParameter]
    event_dict: dict[str, Any],
) -> dict[str, Any]:
    """Add request correlation ID to log entries."""
    if request_id := correlation_id.get():
        event_dict["request_id"] = request_id
    return event_dict


def add_open_telemetry_spans(
    logger: logging.Logger,  # pyright: ignore[reportUnusedParameter]
    method_name: str,  # pyright: ignore[reportUnusedParameter]
    event_dict: dict[str, Any],
) -> dict[str, Any]:
    """
    Add OpenTelemetry span context to log entries.

    Enables correlation between logs and traces in observability platforms.
    """
    span = trace.get_current_span()
    if not span.is_recording():
        return event_dict

    ctx = span.get_span_context()
    parent = getattr(span, "parent", None)

    event_dict["trace_id"] = format(ctx.trace_id, "032x")
    event_dict["span_id"] = format(ctx.span_id, "016x")
    if parent:
        event_dict["parent_span_id"] = format(parent.span_id, "016x")

    return event_dict


def get_shared_processors() -> list[structlog.types.Processor]:
    """
    Build the shared processor chain used by both structlog loggers
    and the ProcessorFormatter for stdlib loggers.

    This ensures unified log formatting across all log sources.
    """
    return [  # pyright: ignore[reportReturnType]
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.PositionalArgumentsFormatter(),
        add_correlation_id,
        add_open_telemetry_spans,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
    ]


def configure_logging(json_output: bool = False, log_level: str = "INFO") -> None:
    """
    Configure structlog with non-blocking I/O for production use.

    Uses QueueHandler/QueueListener to offload log I/O to a dedicated thread,
    ensuring async endpoints are never blocked by slow disk/terminal writes.

    Args:
        json_output: If True, output JSON format (for production).
                     If False, use colored console output (for development).
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL).
    """
    global _queue_listener, _log_queue

    # Stop any existing listener before reconfiguring
    stop_queue_listener()

    # Validate and parse log level
    level = getattr(logging, log_level.upper(), None)
    if level is None:
        level = logging.INFO
    level_name = logging.getLevelName(level)

    # Build shared processors
    shared_processors = get_shared_processors()

    # Build renderer based on output mode
    if json_output:
        renderer: structlog.types.Processor = structlog.processors.JSONRenderer()
        exc_processor: structlog.types.Processor = structlog.processors.format_exc_info
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)
        exc_processor = structlog.dev.set_exc_info

    # Create the log queue for non-blocking I/O
    _log_queue = Queue(-1)  # Unbounded queue

    # Create the ProcessorFormatter for stdlib integration
    # Handles both structlog logs (via wrap_for_formatter) and foreign logs (Uvicorn, etc.)
    processor_formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            exc_processor,
            renderer,
        ],
        foreign_pre_chain=shared_processors,
    )

    # Create console handler that will be used by the QueueListener
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(processor_formatter)
    console_handler.setLevel(level)

    # Create the non-formatting queue handler to preserve structlog's event dict
    queue_handler = NonFormattingQueueHandler(_log_queue)

    # Configure stdlib logging via dictConfig
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "handlers": {
                "queue": {
                    "()": lambda: queue_handler,  # Use our pre-created handler
                },
            },
            "loggers": {
                "": {  # Root logger
                    "handlers": ["queue"],
                    "level": level_name,
                    "propagate": True,
                },
                "uvicorn": {
                    "handlers": ["queue"],
                    "level": level_name,
                    "propagate": False,
                },
                "uvicorn.error": {
                    "handlers": ["queue"],
                    "level": level_name,
                    "propagate": False,
                },
                "uvicorn.access": {
                    # Silence uvicorn.access - custom middleware handles access logs
                    "handlers": [],
                    "level": "WARNING",
                    "propagate": False,
                },
            },
        }
    )

    # Create and start the QueueListener
    _queue_listener = QueueListener(
        _log_queue,
        console_handler,
        respect_handler_level=True,
    )
    _queue_listener.start()

    # Register atexit handler as safety net
    atexit.register(stop_queue_listener)

    # Configure structlog to use stdlib integration
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def stop_queue_listener() -> None:
    """
    Stop the log queue listener gracefully.

    Should be called during application shutdown to ensure all pending
    log messages are flushed before the process exits.
    """
    global _queue_listener, _log_queue
    if _queue_listener is not None:
        _queue_listener.stop()
        _queue_listener = None
    _log_queue = None


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Get a configured structlog logger instance."""
    return structlog.get_logger(name)
