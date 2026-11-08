"""Covers the admin-only test-run endpoints. The first group needs no database.

Tokens are signed locally (see conftest). The worker start is the real one, with the noop
controller, so nothing reaches AWS.
"""

import os
from unittest.mock import MagicMock
from uuid import UUID

import psycopg
import pytest
from fastapi.testclient import TestClient

from conftest import ALLOWLISTED_EMAIL, Tokens, ensure_user

URL = "/internal/test-runs"


def _admin(tokens: Tokens, sub: str = "admin-1") -> dict[str, str]:
    return tokens.headers(sub, ALLOWLISTED_EMAIL, role="admin")


def test_hidden_without_a_token(client: TestClient) -> None:
    assert client.post(URL, json={}).status_code == 401
    assert client.get(f"{URL}/{UUID(int=1)}").status_code == 401


@pytest.mark.parametrize(
    "body",
    [
        {"chunks": 0},
        {"chunks": 21},
        {"sleep_seconds": -1},
        {"sleep_seconds": 901},
        {"fail": "explode"},
    ],
)
@pytest.mark.usefixtures("db")
def test_invalid_requests_are_rejected(
    client: TestClient, tokens: Tokens, body: dict[str, object]
) -> None:
    assert client.post(URL, json=body, headers=_admin(tokens)).status_code == 422


@pytest.mark.usefixtures("db")
def test_error_names_the_bad_value_and_the_limit(client: TestClient, tokens: Tokens) -> None:
    response = client.post(URL, json={"chunks": 99}, headers=_admin(tokens))
    assert "chunks is 99" in response.text
    assert "1 to 20" in response.text


@pytest.mark.usefixtures("db")
def test_non_admins_get_not_found(client: TestClient, tokens: Tokens) -> None:
    # An ordinary active user must not learn these endpoints exist.
    user = tokens.headers("user-1", ALLOWLISTED_EMAIL)
    assert client.post(URL, json={}, headers=user).status_code == 404
    assert client.get(f"{URL}/{UUID(int=1)}", headers=user).status_code == 404


@pytest.mark.usefixtures("db")
def test_waitlisted_admin_gets_not_found(client: TestClient, tokens: Tokens) -> None:
    # The admin role alone isn't enough: the account must be allowed to use the app.
    headers = tokens.headers("admin-2", "stranger@example.com", role="admin")
    assert client.post(URL, json={}, headers=headers).status_code == 404


@pytest.mark.usefixtures("db")
def test_processing_disabled_refuses_submissions(
    client: TestClient, tokens: Tokens, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PROCESSING_ENABLED", "false")
    assert client.post(URL, json={}, headers=_admin(tokens)).status_code == 503


@pytest.mark.usefixtures("db")
def test_submit_and_read_a_run(client: TestClient, tokens: Tokens) -> None:
    response = client.post(URL, json={"chunks": 2}, headers=_admin(tokens))
    assert response.status_code == 201
    run_id = response.json()["run_id"]

    status = client.get(f"{URL}/{run_id}", headers=_admin(tokens))
    assert status.status_code == 200
    body = status.json()
    assert body["status"] == "queued"
    assert body["stages"][0]["stage"] == "noop"
    assert body["job_counts"] == {"pending": 1}


@pytest.mark.usefixtures("db")
def test_runs_are_owned_by_the_calling_admin(client: TestClient, tokens: Tokens) -> None:
    run_id = client.post(URL, json={}, headers=_admin(tokens, "admin-1")).json()["run_id"]
    # Another admin can't read it, even knowing its ID.
    assert client.get(f"{URL}/{run_id}", headers=_admin(tokens, "admin-2")).status_code == 404
    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as conn:
        row = conn.execute("SELECT owner_user_id FROM pipeline_runs").fetchone()
    assert row == ("admin-1",)


@pytest.mark.usefixtures("db")
def test_second_active_run_for_an_admin_conflicts(client: TestClient, tokens: Tokens) -> None:
    assert client.post(URL, json={}, headers=_admin(tokens, "admin-1")).status_code == 201
    assert client.post(URL, json={}, headers=_admin(tokens, "admin-1")).status_code == 409
    assert client.post(URL, json={}, headers=_admin(tokens, "admin-2")).status_code == 201


@pytest.mark.usefixtures("db")
def test_unknown_run_is_not_found(client: TestClient, tokens: Tokens) -> None:
    assert client.get(f"{URL}/{UUID(int=1)}", headers=_admin(tokens)).status_code == 404


@pytest.mark.usefixtures("db")
def test_worker_start_failure_keeps_the_job_queued(
    client: TestClient, tokens: Tokens, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A failed EC2 start must never lose work: the job is committed first.
    monkeypatch.setattr(
        "app.api.services.test_runs.request_worker_start",
        MagicMock(side_effect=RuntimeError("no capacity")),
    )
    with pytest.raises(RuntimeError):
        client.post(URL, json={}, headers=_admin(tokens))
    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as conn:
        row = conn.execute("SELECT count(*) FROM jobs WHERE status = 'pending'").fetchone()
    assert row == (1,)


def test_unreachable_database_returns_503(
    client: TestClient, tokens: Tokens, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The profile lookup is the first database call, and must fail cleanly too.
    monkeypatch.setenv("DATABASE_URL", "")
    assert client.post(URL, json={}, headers=_admin(tokens)).status_code == 503


@pytest.mark.usefixtures("db")
def test_existing_admin_profile_is_reused(client: TestClient, tokens: Tokens) -> None:
    ensure_user("admin-1", ALLOWLISTED_EMAIL, role="admin")
    assert client.post(URL, json={}, headers=_admin(tokens)).status_code == 201
