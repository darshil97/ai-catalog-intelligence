"""Covers request IDs through the real middleware. Nothing is stubbed."""

import logging
import uuid

import pytest
from fastapi.testclient import TestClient

from app.utils.logger import RequestIdFilter
from app.utils.request_id import get_request_id


def test_generates_id_when_none_supplied(client: TestClient) -> None:
    response = client.get("/health")
    assert uuid.UUID(response.headers["X-Request-ID"])


def test_valid_caller_id_is_kept(client: TestClient) -> None:
    response = client.get("/health", headers={"X-Request-ID": "abc-123"})
    assert response.headers["X-Request-ID"] == "abc-123"


@pytest.mark.parametrize("bad_id", ["has spaces", "x" * 129, "a|b", "<script>"])
def test_malformed_caller_id_is_replaced(client: TestClient, bad_id: str) -> None:
    # These would otherwise reach every log line for the request.
    response = client.get("/health", headers={"X-Request-ID": bad_id})
    returned = response.headers["X-Request-ID"]
    assert returned != bad_id
    assert uuid.UUID(returned)


def test_id_is_cleared_after_the_request(client: TestClient) -> None:
    client.get("/health", headers={"X-Request-ID": "abc-123"})
    assert get_request_id() is None


def test_log_filter_uses_na_outside_a_request() -> None:
    record = logging.LogRecord("n", logging.INFO, "p", 1, "m", None, None)
    assert RequestIdFilter().filter(record)
    assert record.request_id == "N/A"  # type: ignore[attr-defined]


def test_request_is_logged_with_id_and_without_query_string(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    # Query strings can carry sensitive values, so they stay out of the logs.
    with caplog.at_level(logging.INFO, logger="app.middleware.request_context"):
        client.get("/health?token=should-not-be-logged")
    # Only this module's records: the test client logs its own request URLs.
    messages = [
        record.getMessage()
        for record in caplog.records
        if record.name == "app.middleware.request_context"
    ]
    assert any("path=/health status=200" in message for message in messages)
    assert not any("should-not-be-logged" in message for message in messages)
