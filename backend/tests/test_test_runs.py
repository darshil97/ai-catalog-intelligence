"""Covers the temporary test-run endpoints. The first group needs no database."""

from unittest.mock import MagicMock
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

URL = "/internal/test-runs"
HEADERS = {"X-Admin-Token": "secret-token"}


@pytest.fixture
def admin_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "secret-token")


def test_hidden_when_no_token_is_configured(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # An empty header must not match an empty configured token.
    monkeypatch.setenv("ADMIN_TOKEN", "")
    assert client.post(URL, json={}).status_code == 404
    assert client.post(URL, json={}, headers={"X-Admin-Token": ""}).status_code == 404


@pytest.mark.usefixtures("admin_token")
def test_wrong_or_missing_token_is_hidden(client: TestClient) -> None:
    assert client.post(URL, json={}).status_code == 404
    assert client.post(URL, json={}, headers={"X-Admin-Token": "no"}).status_code == 404
    assert client.get(f"{URL}/{UUID(int=1)}", headers={"X-Admin-Token": "no"}).status_code == 404


@pytest.mark.usefixtures("admin_token")
@pytest.mark.parametrize(
    "body",
    [
        {"chunks": 0},
        {"chunks": 21},
        {"sleep_seconds": -1},
        {"sleep_seconds": 901},
        {"fail": "explode"},
        {"owner": ""},
    ],
)
def test_invalid_requests_are_rejected(client: TestClient, body: dict[str, object]) -> None:
    assert client.post(URL, json=body, headers=HEADERS).status_code == 422


@pytest.mark.usefixtures("admin_token")
def test_error_names_the_bad_value_and_the_limit(client: TestClient) -> None:
    response = client.post(URL, json={"chunks": 99}, headers=HEADERS)
    assert "chunks is 99" in response.text
    assert "1 to 20" in response.text


@pytest.mark.usefixtures("admin_token")
def test_processing_disabled_refuses_submissions(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PROCESSING_ENABLED", "false")
    assert client.post(URL, json={}, headers=HEADERS).status_code == 503


@pytest.mark.usefixtures("admin_token", "db")
def test_submit_and_read_a_run(client: TestClient) -> None:
    response = client.post(URL, json={"chunks": 2}, headers=HEADERS)
    assert response.status_code == 201
    run_id = response.json()["run_id"]

    status = client.get(f"{URL}/{run_id}", headers=HEADERS)
    assert status.status_code == 200
    body = status.json()
    assert body["status"] == "queued"
    assert body["stages"][0]["stage"] == "noop"
    assert body["job_counts"] == {"pending": 1}


@pytest.mark.usefixtures("admin_token", "db")
def test_second_active_run_for_an_owner_conflicts(client: TestClient) -> None:
    assert client.post(URL, json={}, headers=HEADERS).status_code == 201
    assert client.post(URL, json={}, headers=HEADERS).status_code == 409
    assert client.post(URL, json={"owner": "other"}, headers=HEADERS).status_code == 201


@pytest.mark.usefixtures("admin_token", "db")
def test_unknown_run_is_not_found(client: TestClient) -> None:
    assert client.get(f"{URL}/{UUID(int=1)}", headers=HEADERS).status_code == 404


@pytest.mark.usefixtures("admin_token", "db")
def test_worker_start_failure_keeps_the_job_queued(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A failed EC2 start must never lose work: the job is committed first.
    controller = MagicMock()
    controller.ensure_running.side_effect = RuntimeError("no capacity")
    monkeypatch.setattr(
        "app.api.services.test_runs.get_worker_controller", lambda _name: controller
    )
    response = client.post(URL, json={}, headers=HEADERS)
    assert response.status_code == 201
    status = client.get(f"{URL}/{response.json()['run_id']}", headers=HEADERS)
    assert status.json()["job_counts"] == {"pending": 1}


@pytest.mark.usefixtures("admin_token")
def test_unreachable_database_returns_503(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATABASE_URL", "")
    assert client.post(URL, json={}, headers=HEADERS).status_code == 503
