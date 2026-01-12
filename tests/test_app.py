"""Tests for the FastAPI application endpoints."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from boss_file_utils.app import app


@pytest.fixture
def client() -> TestClient:
    """Create a test client for the FastAPI app."""
    return TestClient(app, raise_server_exceptions=False)


def test_read_root(client: TestClient) -> None:
    """Test the root endpoint returns Hello World."""
    response = client.get("/")
    assert response.status_code == 200
    assert response.json() == {"Hello": "World"}


def test_health_check(client: TestClient) -> None:
    """Test the ping endpoint returns pong."""
    response = client.get("/ping")
    assert response.status_code == 200
    assert response.json() == "pong"


def test_read_item_without_query(client: TestClient) -> None:
    """Test reading an item without query parameter."""
    response = client.get("/items/42")
    assert response.status_code == 200
    assert response.json() == {"item_id": 42, "q": None}


def test_read_item_with_query(client: TestClient) -> None:
    """Test reading an item with query parameter."""
    response = client.get("/items/1?q=test")
    assert response.status_code == 200
    assert response.json() == {"item_id": 1, "q": "test"}


def test_invalid_endpoint_raises_error(client: TestClient) -> None:
    """Test that /invalid returns 400 with error message."""
    response = client.get("/invalid")
    assert response.status_code == 400
    assert "intentional error" in response.json()["detail"]


def test_exception_demo(client: TestClient) -> None:
    """Test that /exception handles and returns error info."""
    response = client.get("/exception")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "error_handled"
    assert "Simulated error" in data["message"]


@pytest.mark.slow
def test_external_api_call(client: TestClient) -> None:
    """Test external API call to httpbin.org."""
    response = client.get("/external-api")
    assert response.status_code == 200
    data = response.json()
    assert data["source"] == "httpbin.org"
    assert data["status_code"] == 200
    assert "origin" in data


def test_external_api_call_mocked(client: TestClient) -> None:
    """Test external API call with mocked httpx."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"origin": "1.2.3.4"}
    mock_response.raise_for_status.return_value = None

    with patch("boss_file_utils.app.httpx.AsyncClient") as mock_client:
        mock_instance = AsyncMock()
        mock_instance.get.return_value = mock_response
        mock_instance.__aenter__.return_value = mock_instance
        mock_instance.__aexit__.return_value = None
        mock_client.return_value = mock_instance

        response = client.get("/external-api")
        assert response.status_code == 200
        assert response.json()["origin"] == "1.2.3.4"


class TestReadItemEndpoint:
    """Additional tests for the /items/{item_id} endpoint."""

    def test_read_item_odd_id(self, client: TestClient) -> None:
        """Odd item IDs skip the simulated delay."""
        response = client.get("/items/3")
        assert response.status_code == 200
        assert response.json() == {"item_id": 3, "q": None}

    def test_read_item_even_id(self, client: TestClient) -> None:
        """Even item IDs trigger the simulated I/O delay."""
        response = client.get("/items/4")
        assert response.status_code == 200
        assert response.json() == {"item_id": 4, "q": None}

    def test_read_item_zero(self, client: TestClient) -> None:
        """Zero is a valid even item ID."""
        response = client.get("/items/0")
        assert response.status_code == 200
        assert response.json() == {"item_id": 0, "q": None}

    def test_read_item_negative_id(self, client: TestClient) -> None:
        """Negative item IDs are accepted."""
        response = client.get("/items/-1")
        assert response.status_code == 200
        assert response.json() == {"item_id": -1, "q": None}

    def test_read_item_large_id(self, client: TestClient) -> None:
        """Large item IDs are accepted."""
        response = client.get("/items/999999999")
        assert response.status_code == 200
        assert response.json() == {"item_id": 999999999, "q": None}

    def test_read_item_invalid_id_type(self, client: TestClient) -> None:
        """Non-integer item IDs return validation error."""
        response = client.get("/items/abc")
        assert response.status_code == 422

    def test_read_item_float_id(self, client: TestClient) -> None:
        """Float item IDs return validation error."""
        response = client.get("/items/1.5")
        assert response.status_code == 422

    def test_read_item_with_empty_query(self, client: TestClient) -> None:
        """Empty query parameter is treated as the string value."""
        response = client.get("/items/1?q=")
        assert response.status_code == 200
        assert response.json() == {"item_id": 1, "q": ""}

    def test_read_item_with_special_chars_query(self, client: TestClient) -> None:
        """Query with special characters is properly handled."""
        response = client.get("/items/1?q=hello%20world")
        assert response.status_code == 200
        assert response.json() == {"item_id": 1, "q": "hello world"}


class TestExternalApiErrors:
    """Tests for external API error handling."""

    def test_external_api_http_status_error(self, client: TestClient) -> None:
        """HTTPStatusError from external API returns 502."""
        mock_response = MagicMock()
        mock_response.status_code = 500

        with patch("boss_file_utils.app.httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.get.return_value = mock_response
            mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
                "Server error",
                request=MagicMock(),
                response=mock_response,
            )
            mock_instance.__aenter__.return_value = mock_instance
            mock_instance.__aexit__.return_value = None
            mock_client.return_value = mock_instance

            response = client.get("/external-api")
            assert response.status_code == 502
            assert "External API error" in response.json()["detail"]

    def test_external_api_request_error(self, client: TestClient) -> None:
        """RequestError from external API returns 503."""
        with patch("boss_file_utils.app.httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.get.side_effect = httpx.RequestError(
                "Connection failed",
                request=MagicMock(),
            )
            mock_instance.__aenter__.return_value = mock_instance
            mock_instance.__aexit__.return_value = None
            mock_client.return_value = mock_instance

            response = client.get("/external-api")
            assert response.status_code == 503
            assert "External API unavailable" in response.json()["detail"]

    def test_external_api_timeout(self, client: TestClient) -> None:
        """Timeout from external API returns 503."""
        with patch("boss_file_utils.app.httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.get.side_effect = httpx.TimeoutException(
                "Request timed out",
                request=MagicMock(),
            )
            mock_instance.__aenter__.return_value = mock_instance
            mock_instance.__aexit__.return_value = None
            mock_client.return_value = mock_instance

            response = client.get("/external-api")
            assert response.status_code == 503
            assert "External API unavailable" in response.json()["detail"]

    def test_external_api_connect_error(self, client: TestClient) -> None:
        """Connection error from external API returns 503."""
        with patch("boss_file_utils.app.httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.get.side_effect = httpx.ConnectError(
                "Failed to connect",
                request=MagicMock(),
            )
            mock_instance.__aenter__.return_value = mock_instance
            mock_instance.__aexit__.return_value = None
            mock_client.return_value = mock_instance

            response = client.get("/external-api")
            assert response.status_code == 503


class TestAppMetadata:
    """Tests for app metadata and configuration."""

    def test_app_title(self) -> None:
        """App has correct title."""
        assert app.title == "Logging Lab API"

    def test_app_version(self) -> None:
        """App has correct version."""
        assert app.version == "0.1.0"

    def test_openapi_endpoint(self, client: TestClient) -> None:
        """OpenAPI schema is accessible."""
        response = client.get("/openapi.json")
        assert response.status_code == 200
        data = response.json()
        assert data["info"]["title"] == "Logging Lab API"

    def test_docs_endpoint(self, client: TestClient) -> None:
        """Swagger UI docs are accessible."""
        response = client.get("/docs")
        assert response.status_code == 200

    def test_redoc_endpoint(self, client: TestClient) -> None:
        """ReDoc docs are accessible."""
        response = client.get("/redoc")
        assert response.status_code == 200


class TestNotFoundHandling:
    """Tests for 404 handling."""

    def test_nonexistent_endpoint(self, client: TestClient) -> None:
        """Non-existent endpoints return 404."""
        response = client.get("/nonexistent")
        assert response.status_code == 404

    def test_nonexistent_nested_path(self, client: TestClient) -> None:
        """Non-existent nested paths return 404."""
        response = client.get("/api/v1/nonexistent")
        assert response.status_code == 404

    def test_wrong_method(self, client: TestClient) -> None:
        """Wrong HTTP method returns 405."""
        response = client.post("/ping")
        assert response.status_code == 405
