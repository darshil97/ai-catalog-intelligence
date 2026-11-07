"""Covers the temporary latency endpoint. The database driver is stubbed."""

from unittest.mock import MagicMock

import psycopg
import pytest
from fastapi.testclient import TestClient

URL = "/internal/db-latency"
HEADERS = {"X-Diagnostics-Token": "secret-token"}


@pytest.fixture
def token_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DIAGNOSTICS_TOKEN", "secret-token")
    monkeypatch.setenv("DATABASE_URL", "postgresql://example.invalid/db")


def test_hidden_when_no_token_is_configured(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # An empty header must not match an empty configured token.
    monkeypatch.setenv("DIAGNOSTICS_TOKEN", "")
    assert client.get(URL).status_code == 404
    assert client.get(URL, headers={"X-Diagnostics-Token": ""}).status_code == 404


@pytest.mark.usefixtures("token_set")
def test_wrong_or_missing_token_is_hidden(client: TestClient) -> None:
    assert client.get(URL).status_code == 404
    assert client.get(URL, headers={"X-Diagnostics-Token": "wrong"}).status_code == 404


@pytest.mark.usefixtures("token_set")
def test_returns_timings(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("psycopg.connect", MagicMock())
    response = client.get(URL, headers=HEADERS)
    assert response.status_code == 200
    body = response.json()
    assert body["samples"] == 10
    assert body["query_min_ms"] <= body["query_median_ms"] <= body["query_max_ms"]


@pytest.mark.usefixtures("token_set")
def test_unreachable_database_returns_503(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("psycopg.connect", MagicMock(side_effect=psycopg.OperationalError("down")))
    assert client.get(URL, headers=HEADERS).status_code == 503


def test_unconfigured_database_returns_503(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DIAGNOSTICS_TOKEN", "secret-token")
    monkeypatch.setenv("DATABASE_URL", "")
    assert client.get(URL, headers=HEADERS).status_code == 503


def test_not_listed_in_the_public_schema(client: TestClient) -> None:
    assert URL not in client.get("/openapi.json").json()["paths"]
