"""Covers exports end to end: approved products only, stored files, links, variants (M10, D41).

Needs TEST_DATABASE_URL. Batches are processed in this process as the worker would. Storage is
in memory, and no LLM is called.
"""

import csv
import io
import json
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient

import samples
from app.db import get_pool
from app.storage.backends.memory import MemoryObjectStore
from conftest import Tokens, ensure_user
from pipeline import create_batch_for, create_supplier, drain, submit, upload

pytestmark = pytest.mark.usefixtures("db", "llm")

UNLIMITED = {"submissions_lifetime": None, "submissions_per_24_hours": None}
SHEET = samples.table_csv(
    [
        ["SKU", "Brand", "Name", "Price", "Capacity"],
        ["AK-2001", "Acme", "Glass jug", "1299", "1.5 L"],
        ["AK-2002", "Acme", "Steel bottle", "899", "750 ml"],
        ["AK-2003", "Acme", "Ceramic mug", "299", "350 ml"],
    ]
)


@pytest.fixture
def alice(tokens: Tokens) -> dict[str, str]:
    ensure_user("alice", "alice@example.com", overrides=UNLIMITED)
    return tokens.headers("alice", "alice@example.com")


@pytest.fixture
def bob(tokens: Tokens) -> dict[str, str]:
    ensure_user("bob", "bob@example.com", overrides=UNLIMITED)
    return tokens.headers("bob", "bob@example.com")


def _batch(client: TestClient, store: MemoryObjectStore, headers: dict) -> str:
    supplier = create_supplier(client, headers, f"Supplier {uuid.uuid4().hex[:8]}")
    batch_id = create_batch_for(client, headers, supplier)
    upload(client, store, headers, batch_id, "prices.csv", SHEET)
    submit(client, headers, batch_id)
    drain()
    return batch_id


def _products(client: TestClient, headers: dict, batch_id: str) -> dict[str, str]:
    items = client.get(f"/batches/{batch_id}/products", headers=headers).json()["items"]
    return {item["product_key"]: item["id"] for item in items}


def _revision(client: TestClient, headers: dict, product_id: str) -> int:
    detail = client.get(f"/products/{product_id}", headers=headers).json()
    return int(detail["approval"]["revision"])


def _approve(client: TestClient, headers: dict, product_id: str, title: str) -> None:
    """Give the product a category and copy, as the LLM would, then approve it."""
    with get_pool().connection() as conn:
        conn.execute(
            "UPDATE products SET category_id = (SELECT id FROM categories WHERE code = "
            "'dining.drinkware.jugs'), category_state = 'assigned', category_source = 'llm' "
            "WHERE id = %s",
            (product_id,),
        )
    copy = client.put(
        f"/products/{product_id}/copy",
        json={"title": title, "description": "Made well.", "bullets": ["Sturdy"],
              "revision": _revision(client, headers, product_id)},
        headers=headers,
    )  # fmt: skip
    assert copy.status_code == 200, copy.text
    approved = client.post(
        f"/products/{product_id}/approve",
        json={"revision": _revision(client, headers, product_id)},
        headers=headers,
    )
    assert approved.status_code == 200, approved.text


def _stored(store: MemoryObjectStore, url: str) -> bytes:
    key = url.removeprefix("https://storage.tests.invalid/").split("?")[0]
    return store.objects[key][0]


def test_exports_hold_only_approved_products_and_are_listed(
    client: TestClient, store: MemoryObjectStore, alice: dict
) -> None:
    batch_id = _batch(client, store, alice)
    refused = client.post(f"/batches/{batch_id}/exports", json={"format": "csv"}, headers=alice)
    assert refused.status_code == 422, refused.text
    assert refused.json()["detail"]["code"] == "nothing_approved"

    ids = _products(client, alice, batch_id)
    _approve(client, alice, ids["AK-2001"], "Acme Glass Jug")
    _approve(client, alice, ids["AK-2002"], "=Acme Steel Bottle")

    made = client.post(f"/batches/{batch_id}/exports", json={"format": "csv"}, headers=alice)
    assert made.status_code == 201, made.text
    body = made.json()
    assert (body["product_count"], body["left_out_count"]) == (2, 1)
    assert body["left_out"] == [{"product_key": "AK-2003", "reason": "Not approved yet."}]
    assert "/exports/alice/" in body["download_url"] and body["download_url"].endswith("=900")
    text = _stored(store, body["download_url"]).decode("utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(text)))
    assert [r["Product code"] for r in rows] == ["AK-2001", "AK-2002"]
    assert rows[1]["Title"] == "'=Acme Steel Bottle"

    shopify = client.post(
        f"/batches/{batch_id}/exports", json={"format": "shopify_csv"}, headers=alice
    ).json()
    shop_rows = list(csv.DictReader(io.StringIO(_stored(store, shopify["download_url"]).decode())))
    assert {r["Title"] for r in shop_rows if r["Title"]} == {"Acme Glass Jug", "Acme Steel Bottle"}
    assert any("removed" in note for note in shopify["notes"])
    excel = client.post(f"/batches/{batch_id}/exports", json={"format": "xlsx"}, headers=alice)
    assert excel.status_code == 201 and _stored(store, excel.json()["download_url"])[:2] == b"PK"

    listed = client.get(f"/batches/{batch_id}/exports", headers=alice).json()["items"]
    assert [e["format"] for e in listed] == ["xlsx", "shopify_csv", "csv"]
    link = client.get(f"/exports/{listed[0]['id']}/download", headers=alice)
    assert link.status_code == 200 and link.json()["url"].endswith("=900")

    # A change after approval leaves the product out of the next export.
    client.put(
        f"/products/{ids['AK-2001']}/values/capacity",
        json={"value": "1.6 L", "revision": _revision(client, alice, ids["AK-2001"])},
        headers=alice,
    )
    again = client.post(f"/batches/{batch_id}/exports", json={"format": "csv"}, headers=alice)
    left_out = {r["product_key"]: r["reason"] for r in again.json()["left_out"]}
    assert again.json()["product_count"] == 1 and "approve it again" in left_out["AK-2001"]


def test_storage_failure_is_reported_and_nothing_recorded(
    client: TestClient, store: MemoryObjectStore, alice: dict
) -> None:
    batch_id = _batch(client, store, alice)
    _approve(client, alice, _products(client, alice, batch_id)["AK-2001"], "Acme Glass Jug")
    store.fail_next = "upload"
    failed = client.post(f"/batches/{batch_id}/exports", json={"format": "csv"}, headers=alice)
    assert failed.status_code == 503
    assert client.get(f"/batches/{batch_id}/exports", headers=alice).json()["items"] == []


def test_a_variant_decision_is_recorded_and_needs_approval_again(
    client: TestClient, store: MemoryObjectStore, alice: dict
) -> None:
    batch_id = _batch(client, store, alice)
    ids = _products(client, alice, batch_id)
    _approve(client, alice, ids["AK-2001"], "Acme Glass Jug")
    a, b = sorted([ids["AK-2001"], ids["AK-2003"]])
    with get_pool().connection() as conn:
        row = conn.execute(
            "INSERT INTO variant_candidates (batch_id, product_a_id, product_b_id, reason) "
            "VALUES (%s, %s, %s, %s) RETURNING id",
            (batch_id, a, b, json.dumps([{"rule": "same_name", "text": "Same name."}])),
        ).fetchone()
    assert row is not None
    variant_id = str(row["id"])

    bad = client.post(f"/variants/{variant_id}/decision", json={"decision": "x"}, headers=alice)
    assert bad.status_code == 422
    decided = client.post(
        f"/variants/{variant_id}/decision", json={"decision": "confirmed"}, headers=alice
    )
    assert decided.status_code == 200, decided.text
    assert decided.json()["status"] == "confirmed"
    pairs = client.get(f"/batches/{batch_id}/variants", headers=alice).json()["items"]
    assert [p["status"] for p in pairs if p["id"] == variant_id] == ["confirmed"]

    jug = client.get(f"/products/{ids['AK-2001']}", headers=alice).json()
    assert jug["approval"]["state"] == "changed_since_approval"
    history = client.get(f"/products/{ids['AK-2001']}/history", headers=alice).json()["items"]
    assert history[0]["action"] == "confirmed"


def test_endpoints_refuse_other_users_signed_out_and_waitlisted(
    client: TestClient, store: MemoryObjectStore, alice: dict, bob: dict, tokens: Tokens
) -> None:
    batch_id = _batch(client, store, alice)
    _approve(client, alice, _products(client, alice, batch_id)["AK-2001"], "Acme Glass Jug")
    export_id = client.post(
        f"/batches/{batch_id}/exports", json={"format": "csv"}, headers=alice
    ).json()["id"]
    ensure_user("carol", "carol@example.com", status="waitlisted")
    waitlisted = tokens.headers("carol", "carol@example.com")
    calls: list[tuple[str, str, Any]] = [
        ("post", f"/batches/{batch_id}/exports", {"format": "csv"}),
        ("get", f"/batches/{batch_id}/exports", None),
        ("get", f"/exports/{export_id}/download", None),
        ("post", f"/variants/{uuid.uuid4()}/decision", {"decision": "confirmed"}),
    ]
    for method, path, body in calls:
        kwargs = {"json": body} if body is not None else {}
        assert getattr(client, method)(path, headers=bob, **kwargs).status_code == 404, path
        assert getattr(client, method)(path, **kwargs).status_code == 401, path
        assert getattr(client, method)(path, headers=waitlisted, **kwargs).status_code == 403
