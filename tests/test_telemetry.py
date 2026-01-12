"""Tests for OpenTelemetry configuration and instrumentation."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from opentelemetry.trace import StatusCode

import boss_file_utils.telemetry as telemetry_module
from boss_file_utils.telemetry import (
    get_tracer,
    record_exception_on_span,
    setup_telemetry,
)


@pytest.fixture(autouse=True)
def reset_tracer() -> None:
    """Reset the global _tracer before each test."""
    telemetry_module._tracer = None


class TestSetupTelemetry:
    """Tests for the setup_telemetry function."""

    def test_returns_tracer_instance(self) -> None:
        """setup_telemetry returns a Tracer instance."""
        tracer = setup_telemetry()
        assert tracer is not None

    def test_uses_default_service_name(self) -> None:
        """setup_telemetry uses 'boss-file-utils' as default service name."""
        with patch("boss_file_utils.telemetry.Resource.create") as mock_resource:
            mock_resource.return_value = MagicMock()
            setup_telemetry()

        mock_resource.assert_called_once()
        call_args = mock_resource.call_args[0][0]
        assert call_args["service.name"] == "boss-file-utils"

    def test_uses_custom_service_name(self) -> None:
        """setup_telemetry accepts custom service name."""
        with patch("boss_file_utils.telemetry.Resource.create") as mock_resource:
            mock_resource.return_value = MagicMock()
            setup_telemetry(service_name="my-custom-service")

        mock_resource.assert_called_once()
        call_args = mock_resource.call_args[0][0]
        assert call_args["service.name"] == "my-custom-service"

    def test_sets_global_tracer_provider(self) -> None:
        """setup_telemetry sets the global tracer provider."""
        with patch("boss_file_utils.telemetry.trace.set_tracer_provider") as mock_set:
            setup_telemetry()

        mock_set.assert_called_once()

    def test_adds_batch_span_processor(self) -> None:
        """setup_telemetry adds BatchSpanProcessor to provider."""
        with patch("boss_file_utils.telemetry.TracerProvider") as mock_provider_class:
            mock_provider = MagicMock()
            mock_provider_class.return_value = mock_provider
            setup_telemetry()

        mock_provider.add_span_processor.assert_called_once()


class TestGetTracer:
    """Tests for the get_tracer function."""

    def test_returns_noop_tracer_when_not_initialized(self) -> None:
        """get_tracer returns a tracer even when not initialized."""
        tracer = get_tracer()
        assert tracer is not None

    def test_returns_configured_tracer_after_setup(self) -> None:
        """get_tracer returns the configured tracer after setup."""
        configured_tracer = setup_telemetry()
        retrieved_tracer = get_tracer()
        assert retrieved_tracer is configured_tracer

    def test_returns_same_tracer_on_multiple_calls(self) -> None:
        """get_tracer returns same tracer instance on multiple calls."""
        setup_telemetry()
        tracer1 = get_tracer()
        tracer2 = get_tracer()
        assert tracer1 is tracer2


class TestRecordExceptionOnSpan:
    """Tests for the record_exception_on_span function."""

    def test_records_exception_when_span_is_recording(self) -> None:
        """Exception is recorded when span is actively recording."""
        mock_span = MagicMock()
        mock_span.is_recording.return_value = True

        test_exception = ValueError("Test error message")

        with patch("boss_file_utils.telemetry.trace.get_current_span", return_value=mock_span):
            record_exception_on_span(test_exception)

        mock_span.record_exception.assert_called_once_with(test_exception)
        mock_span.set_status.assert_called_once()

        status_call = mock_span.set_status.call_args[0][0]
        assert status_call.status_code == StatusCode.ERROR
        assert status_call.description == "Test error message"

    def test_no_action_when_span_not_recording(self) -> None:
        """No action taken when span is not recording."""
        mock_span = MagicMock()
        mock_span.is_recording.return_value = False

        test_exception = ValueError("Test error")

        with patch("boss_file_utils.telemetry.trace.get_current_span", return_value=mock_span):
            record_exception_on_span(test_exception)

        mock_span.record_exception.assert_not_called()
        mock_span.set_status.assert_not_called()

    def test_handles_different_exception_types(self) -> None:
        """Works with various exception types."""
        mock_span = MagicMock()
        mock_span.is_recording.return_value = True

        exceptions = [
            RuntimeError("Runtime issue"),
            TypeError("Type issue"),
            KeyError("missing_key"),
        ]

        with patch("boss_file_utils.telemetry.trace.get_current_span", return_value=mock_span):
            for exc in exceptions:
                mock_span.reset_mock()
                record_exception_on_span(exc)
                mock_span.record_exception.assert_called_once_with(exc)

    def test_exception_message_in_status(self) -> None:
        """Exception message is captured in span status."""
        mock_span = MagicMock()
        mock_span.is_recording.return_value = True

        error_msg = "Something went wrong with the database connection"
        test_exception = ConnectionError(error_msg)

        with patch("boss_file_utils.telemetry.trace.get_current_span", return_value=mock_span):
            record_exception_on_span(test_exception)

        status_call = mock_span.set_status.call_args[0][0]
        assert error_msg in status_call.description
