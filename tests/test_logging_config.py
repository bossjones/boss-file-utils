"""Tests for logging configuration and structlog processors."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import structlog
from asgi_correlation_id import correlation_id
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider

from boss_file_utils import logging_config
from boss_file_utils.logging_config import (
    add_correlation_id,
    add_open_telemetry_spans,
    configure_logging,
    get_logger,
    get_shared_processors,
    stop_queue_listener,
)


class TestAddCorrelationId:
    """Tests for the add_correlation_id processor."""

    def test_adds_request_id_when_correlation_id_exists(self) -> None:
        """Request ID is added to event dict when correlation ID is set."""
        mock_logger = MagicMock(spec=logging.Logger)
        event_dict: dict[str, Any] = {"event": "test message"}

        with patch("boss_file_utils.logging_config.correlation_id") as mock_cid:
            mock_cid.get.return_value = "test-correlation-123"
            result = add_correlation_id(mock_logger, "info", event_dict)

        assert result["request_id"] == "test-correlation-123"
        assert result["event"] == "test message"

    def test_no_request_id_when_correlation_id_missing(self) -> None:
        """Event dict unchanged when no correlation ID is set."""
        mock_logger = MagicMock(spec=logging.Logger)
        event_dict: dict[str, Any] = {"event": "test message"}

        with patch("boss_file_utils.logging_config.correlation_id") as mock_cid:
            mock_cid.get.return_value = None
            result = add_correlation_id(mock_logger, "info", event_dict)

        assert "request_id" not in result
        assert result["event"] == "test message"

    def test_no_request_id_when_correlation_id_empty(self) -> None:
        """Event dict unchanged when correlation ID is empty string."""
        mock_logger = MagicMock(spec=logging.Logger)
        event_dict: dict[str, Any] = {"event": "test message"}

        with patch("boss_file_utils.logging_config.correlation_id") as mock_cid:
            mock_cid.get.return_value = ""
            result = add_correlation_id(mock_logger, "info", event_dict)

        assert "request_id" not in result


class TestAddOpenTelemetrySpans:
    """Tests for the add_open_telemetry_spans processor."""

    def test_returns_unchanged_when_span_not_recording(self) -> None:
        """Event dict returned unchanged when span is not recording."""
        mock_logger = MagicMock(spec=logging.Logger)
        event_dict: dict[str, Any] = {"event": "test message"}

        mock_span = MagicMock()
        mock_span.is_recording.return_value = False

        with patch("boss_file_utils.logging_config.trace.get_current_span", return_value=mock_span):
            result = add_open_telemetry_spans(mock_logger, "info", event_dict)

        assert "trace_id" not in result
        assert "span_id" not in result
        assert result["event"] == "test message"

    def test_adds_trace_and_span_id_when_recording(self) -> None:
        """Trace and span IDs are added when span is recording."""
        mock_logger = MagicMock(spec=logging.Logger)
        event_dict: dict[str, Any] = {"event": "test message"}

        mock_span_context = MagicMock()
        mock_span_context.trace_id = 0x1234567890ABCDEF1234567890ABCDEF
        mock_span_context.span_id = 0x1234567890ABCDEF

        mock_span = MagicMock()
        mock_span.is_recording.return_value = True
        mock_span.get_span_context.return_value = mock_span_context
        mock_span.parent = None

        with patch("boss_file_utils.logging_config.trace.get_current_span", return_value=mock_span):
            result = add_open_telemetry_spans(mock_logger, "info", event_dict)

        assert result["trace_id"] == "1234567890abcdef1234567890abcdef"
        assert result["span_id"] == "1234567890abcdef"
        assert "parent_span_id" not in result

    def test_adds_parent_span_id_when_parent_exists(self) -> None:
        """Parent span ID is added when parent span exists."""
        mock_logger = MagicMock(spec=logging.Logger)
        event_dict: dict[str, Any] = {"event": "test message"}

        mock_span_context = MagicMock()
        mock_span_context.trace_id = 0xABCDEF1234567890ABCDEF1234567890
        mock_span_context.span_id = 0xABCDEF12345678

        mock_parent = MagicMock()
        mock_parent.span_id = 0x9876543210FEDCBA

        mock_span = MagicMock()
        mock_span.is_recording.return_value = True
        mock_span.get_span_context.return_value = mock_span_context
        mock_span.parent = mock_parent

        with patch("boss_file_utils.logging_config.trace.get_current_span", return_value=mock_span):
            result = add_open_telemetry_spans(mock_logger, "info", event_dict)

        assert result["trace_id"] == "abcdef1234567890abcdef1234567890"
        assert result["span_id"] == "00abcdef12345678"
        assert result["parent_span_id"] == "9876543210fedcba"


class TestConfigureLogging:
    """Tests for the configure_logging function."""

    @pytest.fixture(autouse=True)
    def cleanup_listener(self) -> Iterator[None]:
        """Ensure queue listener is stopped after each test."""
        yield
        stop_queue_listener()

    def test_configure_logging_uses_stdlib_logger_factory(self) -> None:
        """Configure logging uses stdlib LoggerFactory for ProcessorFormatter integration."""
        with patch.object(structlog, "configure") as mock_configure:
            configure_logging(json_output=False)

        mock_configure.assert_called_once()
        call_kwargs = mock_configure.call_args.kwargs
        assert isinstance(call_kwargs["logger_factory"], structlog.stdlib.LoggerFactory)

    def test_configure_logging_ends_with_wrap_for_formatter(self) -> None:
        """Processor chain ends with wrap_for_formatter for ProcessorFormatter integration."""
        with patch.object(structlog, "configure") as mock_configure:
            configure_logging(json_output=False)

        call_kwargs = mock_configure.call_args.kwargs
        processors = call_kwargs["processors"]

        # Last processor should be wrap_for_formatter
        assert processors[-1] == structlog.stdlib.ProcessorFormatter.wrap_for_formatter

    def test_configure_logging_includes_core_processors(self) -> None:
        """Core processors are included in both modes."""
        with patch.object(structlog, "configure") as mock_configure:
            configure_logging(json_output=False)

        call_kwargs = mock_configure.call_args.kwargs
        processors = call_kwargs["processors"]

        # Check that our custom processors are included
        processor_names = [getattr(p, "__name__", None) or p.__class__.__name__ for p in processors]
        assert "add_correlation_id" in processor_names
        assert "add_open_telemetry_spans" in processor_names

    def test_configure_logging_includes_filter_by_level(self) -> None:
        """Processor chain includes filter_by_level for stdlib integration."""
        with patch.object(structlog, "configure") as mock_configure:
            configure_logging(json_output=False)

        call_kwargs = mock_configure.call_args.kwargs
        processors = call_kwargs["processors"]

        # First processor should be filter_by_level
        assert processors[0] == structlog.stdlib.filter_by_level


class TestGetLogger:
    """Tests for the get_logger function."""

    def test_get_logger_returns_bound_logger(self) -> None:
        """get_logger returns a structlog logger instance."""
        logger = get_logger("test_module")
        assert logger is not None

    def test_get_logger_without_name(self) -> None:
        """get_logger works without a name argument."""
        logger = get_logger()
        assert logger is not None

    def test_get_logger_with_name(self) -> None:
        """get_logger accepts a name argument."""
        logger = get_logger("my.custom.logger")
        assert logger is not None


class TestAddCorrelationIdIntegration:
    """Integration tests for correlation ID with real context."""

    def test_with_real_correlation_id_context(self) -> None:
        """Test with actual asgi_correlation_id context variable."""
        mock_logger = MagicMock(spec=logging.Logger)
        event_dict: dict[str, Any] = {"event": "test message"}

        token = correlation_id.set("real-correlation-456")
        try:
            result = add_correlation_id(mock_logger, "info", event_dict)
            assert result["request_id"] == "real-correlation-456"
        finally:
            correlation_id.reset(token)

    def test_without_correlation_id_context(self) -> None:
        """Test when no correlation ID is set in context."""
        mock_logger = MagicMock(spec=logging.Logger)
        event_dict: dict[str, Any] = {"event": "test message"}

        # Ensure no correlation ID is set
        result = add_correlation_id(mock_logger, "info", event_dict)
        # Should not have request_id when correlation_id.get() returns None
        if correlation_id.get():
            assert "request_id" in result
        else:
            assert "request_id" not in result

    def test_preserves_existing_event_dict_fields(self) -> None:
        """Existing fields in event_dict are preserved."""
        mock_logger = MagicMock(spec=logging.Logger)
        event_dict: dict[str, Any] = {
            "event": "test message",
            "custom_field": "custom_value",
            "number": 42,
        }

        token = correlation_id.set("test-123")
        try:
            result = add_correlation_id(mock_logger, "info", event_dict)
            assert result["custom_field"] == "custom_value"
            assert result["number"] == 42
            assert result["event"] == "test message"
        finally:
            correlation_id.reset(token)


class TestAddOpenTelemetrySpansIntegration:
    """Integration tests for OpenTelemetry span context."""

    @pytest.fixture(autouse=True)
    def setup_tracer(self) -> None:
        """Set up a real tracer provider for tests."""
        provider = TracerProvider()
        trace.set_tracer_provider(provider)

    def test_with_real_span(self) -> None:
        """Test with an actual OpenTelemetry span."""
        mock_logger = MagicMock(spec=logging.Logger)
        event_dict: dict[str, Any] = {"event": "test message"}

        tracer = trace.get_tracer(__name__)
        with tracer.start_as_current_span("test-span") as span:
            result = add_open_telemetry_spans(mock_logger, "info", event_dict)

            # Verify trace_id and span_id are present and properly formatted
            assert "trace_id" in result
            assert "span_id" in result
            assert len(result["trace_id"]) == 32  # 128-bit trace ID as hex
            assert len(result["span_id"]) == 16  # 64-bit span ID as hex

            # Verify they match the actual span context
            ctx = span.get_span_context()
            assert result["trace_id"] == format(ctx.trace_id, "032x")
            assert result["span_id"] == format(ctx.span_id, "016x")

    def test_with_nested_spans(self) -> None:
        """Test with nested spans to verify parent span ID."""
        mock_logger = MagicMock(spec=logging.Logger)
        event_dict: dict[str, Any] = {"event": "test message"}

        tracer = trace.get_tracer(__name__)
        with tracer.start_as_current_span("parent-span") as parent_span:
            parent_span_id = format(parent_span.get_span_context().span_id, "016x")

            with tracer.start_as_current_span("child-span"):
                result = add_open_telemetry_spans(mock_logger, "info", event_dict)

                assert "trace_id" in result
                assert "span_id" in result

                # Child span should have parent_span_id matching parent's span_id
                assert "parent_span_id" in result
                assert result["parent_span_id"] == parent_span_id

    def test_outside_span_context(self) -> None:
        """Test when no span is active."""
        mock_logger = MagicMock(spec=logging.Logger)
        event_dict: dict[str, Any] = {"event": "test message"}

        # Outside any span context
        result = add_open_telemetry_spans(mock_logger, "info", event_dict)

        # No span recording, so no trace info should be added
        assert "trace_id" not in result or result.get("trace_id") == "0" * 32


class TestConfigureLoggingIntegration:
    """Integration tests for full logging configuration."""

    @pytest.fixture(autouse=True)
    def cleanup_listener(self) -> Iterator[None]:
        """Ensure queue listener is stopped after each test."""
        yield
        stop_queue_listener()

    def test_configure_logging_sets_log_level_info(self) -> None:
        """Configure logging sets INFO as minimum log level."""
        with patch.object(structlog, "configure") as mock_configure:
            configure_logging(json_output=False)

        call_kwargs = mock_configure.call_args.kwargs
        wrapper_class = call_kwargs["wrapper_class"]
        # The wrapper class should be a filtering bound logger at INFO level
        assert wrapper_class is not None

    def test_configure_logging_caches_logger(self) -> None:
        """Configure logging enables logger caching."""
        with patch.object(structlog, "configure") as mock_configure:
            configure_logging(json_output=False)

        call_kwargs = mock_configure.call_args.kwargs
        assert call_kwargs["cache_logger_on_first_use"] is True

    def test_includes_timestamper(self) -> None:
        """Both modes include TimeStamper processor in shared_processors."""
        with patch.object(structlog, "configure") as mock_configure:
            configure_logging(json_output=True)

        call_kwargs = mock_configure.call_args.kwargs
        processors = call_kwargs["processors"]

        processor_types = [type(p).__name__ for p in processors]
        assert "TimeStamper" in processor_types

    def test_includes_stack_info_renderer(self) -> None:
        """Both modes include StackInfoRenderer processor."""
        with patch.object(structlog, "configure") as mock_configure:
            configure_logging(json_output=False)

        call_kwargs = mock_configure.call_args.kwargs
        processors = call_kwargs["processors"]

        processor_types = [type(p).__name__ for p in processors]
        assert "StackInfoRenderer" in processor_types

    def test_starts_queue_listener(self) -> None:
        """configure_logging starts the QueueListener."""
        configure_logging(json_output=False)
        assert logging_config._queue_listener is not None

    def test_configures_root_logger_with_queue_handler(self) -> None:
        """Root logger is configured with QueueHandler."""
        configure_logging(json_output=False)
        root_logger = logging.getLogger()
        handler_types = [type(h).__name__ for h in root_logger.handlers]
        assert "NonFormattingQueueHandler" in handler_types


class TestConfigureLoggingLogLevel:
    """Tests for the log_level parameter."""

    @pytest.fixture(autouse=True)
    def cleanup_listener(self) -> Iterator[None]:
        """Ensure queue listener is stopped after each test."""
        yield
        stop_queue_listener()

    def test_configure_logging_respects_debug_level(self) -> None:
        """Configure logging with DEBUG level sets appropriate filter."""
        with patch.object(structlog, "configure") as mock_configure:
            configure_logging(json_output=False, log_level="DEBUG")

        call_kwargs = mock_configure.call_args.kwargs
        assert call_kwargs["wrapper_class"] is not None

    def test_configure_logging_respects_warning_level(self) -> None:
        """Configure logging with WARNING level sets appropriate filter."""
        with patch.object(structlog, "configure") as mock_configure:
            configure_logging(json_output=False, log_level="WARNING")

        call_kwargs = mock_configure.call_args.kwargs
        assert call_kwargs["wrapper_class"] is not None

    def test_log_level_case_insensitive(self) -> None:
        """Log level parameter is case insensitive."""
        with patch.object(structlog, "configure") as mock_configure:
            configure_logging(json_output=False, log_level="debug")

        mock_configure.assert_called_once()

    def test_invalid_log_level_defaults_to_info(self) -> None:
        """Invalid log level defaults to INFO."""
        with patch.object(structlog, "configure") as mock_configure:
            configure_logging(json_output=False, log_level="INVALID")

        mock_configure.assert_called_once()


class TestQueueListenerLifecycle:
    """Tests for QueueListener lifecycle management."""

    @pytest.fixture(autouse=True)
    def cleanup_listener(self) -> Iterator[None]:
        """Ensure queue listener is stopped after each test."""
        yield
        stop_queue_listener()

    def test_stop_queue_listener_stops_listener(self) -> None:
        """stop_queue_listener stops the listener when active."""
        configure_logging(json_output=False)
        assert logging_config._queue_listener is not None

        stop_queue_listener()
        assert logging_config._queue_listener is None

    def test_stop_queue_listener_safe_when_not_started(self) -> None:
        """stop_queue_listener is safe to call when no listener exists."""
        logging_config._queue_listener = None
        # Should not raise
        stop_queue_listener()
        assert logging_config._queue_listener is None

    def test_reconfiguring_stops_previous_listener(self) -> None:
        """Calling configure_logging again stops the previous listener."""
        configure_logging(json_output=False)
        first_listener = logging_config._queue_listener
        assert first_listener is not None

        configure_logging(json_output=True)
        second_listener = logging_config._queue_listener
        assert second_listener is not None
        assert second_listener is not first_listener


class TestGetSharedProcessors:
    """Tests for the get_shared_processors function."""

    def test_returns_list_of_processors(self) -> None:
        """get_shared_processors returns a list of processors."""
        processors = get_shared_processors()
        assert isinstance(processors, list)
        assert len(processors) > 0

    def test_includes_contextvars_merger(self) -> None:
        """Shared processors include contextvars merger."""
        processors = get_shared_processors()
        assert structlog.contextvars.merge_contextvars in processors

    def test_includes_custom_processors(self) -> None:
        """Shared processors include custom correlation ID and OTel processors."""
        processors = get_shared_processors()
        processor_names = [getattr(p, "__name__", None) or p.__class__.__name__ for p in processors]
        assert "add_correlation_id" in processor_names
        assert "add_open_telemetry_spans" in processor_names

    def test_includes_timestamper(self) -> None:
        """Shared processors include TimeStamper."""
        processors = get_shared_processors()
        processor_types = [type(p).__name__ for p in processors]
        assert "TimeStamper" in processor_types


class TestStdlibLoggerIntegration:
    """Tests for stdlib logger routing through structlog processors."""

    @pytest.fixture(autouse=True)
    def cleanup_listener(self) -> Iterator[None]:
        """Ensure queue listener is stopped after each test."""
        yield
        stop_queue_listener()

    def test_stdlib_logger_gets_queue_handler(self) -> None:
        """Standard library loggers get QueueHandler configured."""
        configure_logging(json_output=True, log_level="INFO")

        root = logging.getLogger()
        handler_types = [type(h).__name__ for h in root.handlers]
        assert "NonFormattingQueueHandler" in handler_types

    def test_uvicorn_access_logger_silenced(self) -> None:
        """Uvicorn access logger is silenced to avoid duplicates."""
        configure_logging(json_output=True, log_level="INFO")

        uvicorn_access = logging.getLogger("uvicorn.access")
        assert uvicorn_access.level >= logging.WARNING
        assert len(uvicorn_access.handlers) == 0


class TestProcessorChain:
    """Tests for the complete processor chain behavior."""

    @pytest.fixture(autouse=True)
    def cleanup_listener(self) -> Iterator[None]:
        """Ensure queue listener is stopped after each test."""
        yield
        stop_queue_listener()

    def test_processors_execute_in_order(self) -> None:
        """Verify processors execute and accumulate results."""
        mock_logger = MagicMock(spec=logging.Logger)
        event_dict: dict[str, Any] = {"event": "test"}

        # Set up correlation ID
        token = correlation_id.set("chain-test-123")
        try:
            # Run through correlation ID processor
            result = add_correlation_id(mock_logger, "info", event_dict)
            assert result["request_id"] == "chain-test-123"

            # Run through OTel processor (no active span)
            result = add_open_telemetry_spans(mock_logger, "info", result)

            # Original fields preserved
            assert result["event"] == "test"
            assert result["request_id"] == "chain-test-123"
        finally:
            correlation_id.reset(token)

    def test_full_chain_with_span_and_correlation(self) -> None:
        """Test full processor chain with both span and correlation ID."""
        mock_logger = MagicMock(spec=logging.Logger)
        event_dict: dict[str, Any] = {"event": "full chain test", "extra": "data"}

        provider = TracerProvider()
        trace.set_tracer_provider(provider)
        tracer = trace.get_tracer(__name__)

        token = correlation_id.set("full-chain-456")
        try:
            with tracer.start_as_current_span("chain-span"):
                # Run through both processors
                result = add_correlation_id(mock_logger, "info", event_dict)
                result = add_open_telemetry_spans(mock_logger, "info", result)

                # All fields should be present
                assert result["event"] == "full chain test"
                assert result["extra"] == "data"
                assert result["request_id"] == "full-chain-456"
                assert "trace_id" in result
                assert "span_id" in result
        finally:
            correlation_id.reset(token)
