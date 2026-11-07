"""Covers /health, including that it can't touch the database. The driver is stubbed to fail."""

import pytest
from fastapi.testclient import TestClient


def test_health_reports_status_and_sha(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RENDER_GIT_COMMIT", "abc123")
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["code_sha"] == "abc123"


def test_health_makes_no_database_call(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    # A DB call here would keep Neon awake and burn its free compute hours.
    def fail(*args: object, **kwargs: object) -> None:
        raise AssertionError("/health tried to connect to the database")

    monkeypatch.setattr("psycopg.connect", fail)
    assert client.get("/health").status_code == 200
