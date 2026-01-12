"""Tests for access logging middleware."""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from boss_file_utils.logging_config import configure_logging, stop_queue_listener
from boss_file_utils.middleware import AccessLogMiddleware


class TestAccessLogMiddleware:
    """Tests for AccessLogMiddleware."""

    @pytest.fixture(autouse=True)
    def cleanup_listener(self) -> Iterator[None]:
        """Ensure queue listener is stopped after each test."""
        yield
        stop_queue_listener()

    @pytest.fixture
    def app(self) -> FastAPI:
        """Create a test FastAPI app with middleware."""
        test_app = FastAPI()
        test_app.add_middleware(AccessLogMiddleware)

        @test_app.get("/test")
        async def test_endpoint() -> dict[str, str]:
            return {"status": "ok"}

        @test_app.get("/error")
        async def error_endpoint() -> dict[str, str]:
            raise ValueError("test error")

        return test_app

    @pytest.fixture
    def client(self, app: FastAPI) -> TestClient:
        """Create test client."""
        configure_logging(json_output=False)
        return TestClient(app, raise_server_exceptions=False)

    def test_logs_successful_request(self, client: TestClient) -> None:
        """Successful requests are logged with all fields."""
        with patch("boss_file_utils.middleware.get_logger") as mock_get_logger:
            mock_logger = MagicMock()
            mock_get_logger.return_value = mock_logger

            response = client.get("/test")

            assert response.status_code == 200
            mock_logger.info.assert_called_once()
            call_args = mock_logger.info.call_args
            assert call_args[0][0] == "Request completed"
            call_kwargs = call_args.kwargs
            assert call_kwargs["method"] == "GET"
            assert call_kwargs["path"] == "/test"
            assert call_kwargs["status_code"] == 200
            assert "duration_ms" in call_kwargs
            assert "client_ip" in call_kwargs

    def test_logs_client_ip(self, client: TestClient) -> None:
        """Client IP is extracted correctly."""
        with patch("boss_file_utils.middleware.get_logger") as mock_get_logger:
            mock_logger = MagicMock()
            mock_get_logger.return_value = mock_logger

            client.get("/test")

            call_kwargs = mock_logger.info.call_args.kwargs
            assert call_kwargs["client_ip"] == "testclient"

    def test_respects_x_forwarded_for_header(self, client: TestClient) -> None:
        """X-Forwarded-For header is used for client IP when present."""
        with patch("boss_file_utils.middleware.get_logger") as mock_get_logger:
            mock_logger = MagicMock()
            mock_get_logger.return_value = mock_logger

            client.get("/test", headers={"X-Forwarded-For": "192.168.1.1, 10.0.0.1"})

            call_kwargs = mock_logger.info.call_args.kwargs
            assert call_kwargs["client_ip"] == "192.168.1.1"

    def test_logs_exceptions(self, client: TestClient) -> None:
        """Exceptions are logged with exception method."""
        with patch("boss_file_utils.middleware.get_logger") as mock_get_logger:
            mock_logger = MagicMock()
            mock_get_logger.return_value = mock_logger

            client.get("/error")

            mock_logger.exception.assert_called_once()
            call_args = mock_logger.exception.call_args
            assert call_args[0][0] == "Request failed with exception"
            call_kwargs = call_args.kwargs
            assert call_kwargs["method"] == "GET"
            assert call_kwargs["path"] == "/error"
            assert "duration_ms" in call_kwargs
            assert "client_ip" in call_kwargs

    def test_logs_duration_in_milliseconds(self, client: TestClient) -> None:
        """Duration is logged in milliseconds with precision."""
        with patch("boss_file_utils.middleware.get_logger") as mock_get_logger:
            mock_logger = MagicMock()
            mock_get_logger.return_value = mock_logger

            client.get("/test")

            call_kwargs = mock_logger.info.call_args.kwargs
            assert isinstance(call_kwargs["duration_ms"], float)
            assert call_kwargs["duration_ms"] >= 0

    def test_handles_missing_client(self) -> None:
        """Handles case when request.client is None."""
        test_app = FastAPI()
        test_app.add_middleware(AccessLogMiddleware)

        @test_app.get("/test")
        async def test_endpoint() -> dict[str, str]:
            return {"status": "ok"}

        configure_logging(json_output=False)

        with patch("boss_file_utils.middleware.get_logger") as mock_get_logger:
            mock_logger = MagicMock()
            mock_get_logger.return_value = mock_logger

            # TestClient always provides a client, so we patch it
            with patch(
                "starlette.requests.Request.client",
                new_callable=lambda: property(lambda self: None),
            ):
                client = TestClient(test_app)
                client.get("/test")

            call_kwargs = mock_logger.info.call_args.kwargs
            assert call_kwargs["client_ip"] == "unknown"

    def test_logs_different_http_methods(self, app: FastAPI) -> None:
        """Different HTTP methods are logged correctly."""
        configure_logging(json_output=False)

        @app.post("/post-test")
        async def post_endpoint() -> dict[str, str]:
            return {"status": "ok"}

        client = TestClient(app)

        with patch("boss_file_utils.middleware.get_logger") as mock_get_logger:
            mock_logger = MagicMock()
            mock_get_logger.return_value = mock_logger

            client.post("/post-test")

            call_kwargs = mock_logger.info.call_args.kwargs
            assert call_kwargs["method"] == "POST"
