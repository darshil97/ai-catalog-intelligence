"""Covers supplier and batch endpoints, including cross-user access on every endpoint.

Needs TEST_DATABASE_URL. Tokens are signed locally (see conftest). Users are made active
directly in the database, so these tests don't depend on the allowlist.
"""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from conftest import Tokens, ensure_user

pytestmark = pytest.mark.usefixtures("db")

ALICE = "alice"
BOB = "bob"


@pytest.fixture
def alice(tokens: Tokens) -> dict[str, str]:
    ensure_user(ALICE, "alice@example.com")
    return tokens.headers(ALICE, "alice@example.com")


@pytest.fixture
def bob(tokens: Tokens) -> dict[str, str]:
    ensure_user(BOB, "bob@example.com")
    return tokens.headers(BOB, "bob@example.com")


def _supplier(client: TestClient, headers: dict[str, str], name: str = "Acme") -> dict[str, Any]:
    response = client.post("/suppliers", json={"name": name}, headers=headers)
    assert response.status_code == 201, response.text
    return dict(response.json())


def _batch(
    client: TestClient, headers: dict[str, str], supplier_id: str, name: str = "Spring"
) -> dict[str, Any]:
    response = client.post(
        f"/suppliers/{supplier_id}/batches", json={"name": name}, headers=headers
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


def test_supplier_lifecycle(client: TestClient, alice: dict[str, str]) -> None:
    created = _supplier(client, alice, "  Acme   Kitchenware ")
    assert created["name"] == "Acme Kitchenware"
    assert created["batch_count"] == 0

    listed = client.get("/suppliers", headers=alice).json()["items"]
    assert [item["id"] for item in listed] == [created["id"]]

    renamed = client.patch(f"/suppliers/{created['id']}", json={"name": "Acme Home"}, headers=alice)
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "Acme Home"
    assert client.get(f"/suppliers/{created['id']}", headers=alice).json()["name"] == "Acme Home"

    assert client.delete(f"/suppliers/{created['id']}", headers=alice).status_code == 204
    assert client.get(f"/suppliers/{created['id']}", headers=alice).status_code == 404


def test_suppliers_are_listed_by_name(client: TestClient, alice: dict[str, str]) -> None:
    for name in ("beta", "Alpha", "gamma"):
        _supplier(client, alice, name)
    names = [item["name"] for item in client.get("/suppliers", headers=alice).json()["items"]]
    assert names == ["Alpha", "beta", "gamma"]


def test_duplicate_supplier_names_conflict_ignoring_case(
    client: TestClient, alice: dict[str, str]
) -> None:
    _supplier(client, alice, "Acme")
    response = client.post("/suppliers", json={"name": "ACME"}, headers=alice)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "duplicate_name"


def test_renaming_onto_another_name_conflicts(client: TestClient, alice: dict[str, str]) -> None:
    _supplier(client, alice, "Acme")
    other = _supplier(client, alice, "Globex")
    response = client.patch(f"/suppliers/{other['id']}", json={"name": "acme"}, headers=alice)
    assert response.status_code == 409
    # Renaming to its own name in a different case is fine.
    response = client.patch(f"/suppliers/{other['id']}", json={"name": "GLOBEX"}, headers=alice)
    assert response.status_code == 200


def test_two_users_may_use_the_same_supplier_name(
    client: TestClient, alice: dict[str, str], bob: dict[str, str]
) -> None:
    _supplier(client, alice, "Acme")
    _supplier(client, bob, "Acme")


@pytest.mark.parametrize(
    ("name", "message"),
    [
        ("", "name is empty"),
        ("   \n\t ", "name is empty"),
        ("x" * 201, "201 characters"),
        ("Ac​me", "invisible"),  # Zero-width space would dodge the duplicate check.
        ("Ac\x00me", "control"),
    ],
)
def test_invalid_names_are_rejected(
    client: TestClient, alice: dict[str, str], name: str, message: str
) -> None:
    response = client.post("/suppliers", json={"name": name}, headers=alice)
    assert response.status_code == 422
    assert message in response.text


def test_missing_name_is_rejected(client: TestClient, alice: dict[str, str]) -> None:
    assert client.post("/suppliers", json={}, headers=alice).status_code == 422


def test_batch_lifecycle(client: TestClient, alice: dict[str, str]) -> None:
    supplier = _supplier(client, alice)
    batch = _batch(client, alice, supplier["id"])
    assert batch["status"] == "draft"
    assert batch["supplier_name"] == "Acme"
    expires = datetime.fromisoformat(batch["expires_at"])
    assert abs(expires - datetime.now(UTC) - timedelta(days=30)) < timedelta(minutes=5)

    listing = client.get(f"/suppliers/{supplier['id']}/batches", headers=alice).json()
    assert listing["supplier_name"] == "Acme"
    assert [item["id"] for item in listing["items"]] == [batch["id"]]
    assert client.get(f"/suppliers/{supplier['id']}", headers=alice).json()["batch_count"] == 1

    renamed = client.patch(f"/batches/{batch['id']}", json={"name": "Summer"}, headers=alice)
    assert renamed.json()["name"] == "Summer"
    assert client.get(f"/batches/{batch['id']}", headers=alice).json()["name"] == "Summer"

    assert client.delete(f"/batches/{batch['id']}", headers=alice).status_code == 204
    assert client.get(f"/batches/{batch['id']}", headers=alice).status_code == 404


def test_batches_are_listed_newest_first(client: TestClient, alice: dict[str, str]) -> None:
    supplier = _supplier(client, alice)
    first = _batch(client, alice, supplier["id"], "first")
    second = _batch(client, alice, supplier["id"], "second")
    items = client.get(f"/suppliers/{supplier['id']}/batches", headers=alice).json()["items"]
    assert [item["id"] for item in items] == [second["id"], first["id"]]


def test_supplier_with_batches_cannot_be_deleted(client: TestClient, alice: dict[str, str]) -> None:
    supplier = _supplier(client, alice)
    batch = _batch(client, alice, supplier["id"])
    response = client.delete(f"/suppliers/{supplier['id']}", headers=alice)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "supplier_has_batches"
    assert client.get(f"/batches/{batch['id']}", headers=alice).status_code == 200

    client.delete(f"/batches/{batch['id']}", headers=alice)
    assert client.delete(f"/suppliers/{supplier['id']}", headers=alice).status_code == 204


def test_missing_ids_are_404_and_bad_ids_are_422(client: TestClient, alice: dict[str, str]) -> None:
    missing = str(UUID(int=7))
    assert client.get(f"/suppliers/{missing}", headers=alice).status_code == 404
    assert client.get(f"/batches/{missing}", headers=alice).status_code == 404
    assert (
        client.post(f"/suppliers/{missing}/batches", json={"name": "x"}, headers=alice).status_code
        == 404
    )
    assert client.get("/suppliers/not-a-uuid", headers=alice).status_code == 422


def _requests(supplier_id: str, batch_id: str) -> list[tuple[str, str, dict[str, Any] | None]]:
    """Every catalog endpoint, aimed at the given supplier and batch."""
    return [
        ("GET", "/suppliers", None),
        ("POST", "/suppliers", {"name": "Intruder"}),
        ("GET", f"/suppliers/{supplier_id}", None),
        ("PATCH", f"/suppliers/{supplier_id}", {"name": "Taken over"}),
        ("DELETE", f"/suppliers/{supplier_id}", None),
        ("GET", f"/suppliers/{supplier_id}/batches", None),
        ("POST", f"/suppliers/{supplier_id}/batches", {"name": "Injected"}),
        ("GET", f"/batches/{batch_id}", None),
        ("PATCH", f"/batches/{batch_id}", {"name": "Taken over"}),
        ("DELETE", f"/batches/{batch_id}", None),
    ]


def test_every_endpoint_hides_other_users_data(
    client: TestClient, alice: dict[str, str], bob: dict[str, str]
) -> None:
    supplier = _supplier(client, alice)
    batch = _batch(client, alice, supplier["id"])

    for method, path, body in _requests(supplier["id"], batch["id"]):
        response = client.request(method, path, json=body, headers=bob)
        if path == "/suppliers":
            # Bob's own collection: allowed, and must not include Alice's data.
            assert response.status_code in (200, 201), (method, path)
            if method == "GET":
                assert response.json()["items"] == []
        else:
            # The same 404 as a missing row, so Bob can't even confirm the ID exists.
            assert response.status_code == 404, (method, path, response.text)
            assert response.json() == {"detail": "Not Found"}

    # Alice's data is untouched.
    assert client.get(f"/suppliers/{supplier['id']}", headers=alice).json()["name"] == "Acme"
    assert client.get(f"/batches/{batch['id']}", headers=alice).json()["name"] == "Spring"
    listing = client.get(f"/suppliers/{supplier['id']}/batches", headers=alice).json()["items"]
    assert [item["id"] for item in listing] == [batch["id"]]
    assert [s["name"] for s in client.get("/suppliers", headers=alice).json()["items"]] == ["Acme"]


def test_every_endpoint_requires_a_token(client: TestClient) -> None:
    ids = str(UUID(int=1))
    for method, path, body in _requests(ids, ids):
        response = client.request(method, path, json=body)
        assert response.status_code == 401, (method, path)


def test_every_endpoint_refuses_waitlisted_users(client: TestClient, tokens: Tokens) -> None:
    ensure_user("waiting", "waiting@example.com", status="waitlisted")
    headers = tokens.headers("waiting", "waiting@example.com")
    ids = str(UUID(int=1))
    for method, path, body in _requests(ids, ids):
        response = client.request(method, path, json=body, headers=headers)
        assert response.status_code == 403, (method, path)
        assert response.json()["detail"]["code"] == "waitlisted"


def test_auth_is_checked_before_the_body(client: TestClient) -> None:
    # An anonymous caller must not learn validation rules: 401, not 422.
    assert client.post("/suppliers", json={"name": ""}).status_code == 401
