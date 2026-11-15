"""Covers human review end to end: edit values and copy, settle duplicates, approve (M9, D40).

Needs TEST_DATABASE_URL. Batches are processed in this process as the worker would. Storage is
in memory, and no LLM is called.
"""

import uuid
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

import samples
from app.approval import approved_products
from app.db import get_pool
from app.storage.backends.memory import MemoryObjectStore
from conftest import Tokens, ensure_user
from pipeline import create_batch_for, create_supplier, drain, submit, upload

pytestmark = pytest.mark.usefixtures("db", "llm")

UNLIMITED = {"submissions_lifetime": None, "submissions_per_24_hours": None}
SHEET = samples.table_csv(
    [
        ["SKU", "Brand", "Name", "Barcode", "Price", "Capacity"],
        ["AK-1001", "Acme", "Glass jug", "4006381333931", "1299", "1.5 L"],
        ["AK-1002", "Acme", "Steel bottle", "4006381333931", "899", "750 ml"],
        ["AK-1003", "", "Ceramic mug", "", "299", "350 ml"],
        ["AK-1004", "Acme", "Ceramic mug", "", "299", "350 ml"],
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


def _products(client: TestClient, headers: dict, batch_id: str) -> dict[str, dict[str, Any]]:
    items = client.get(f"/batches/{batch_id}/products", headers=headers).json()["items"]
    return {item["product_key"]: item for item in items}


def _detail(client: TestClient, headers: dict, product_id: str) -> dict[str, Any]:
    response = client.get(f"/products/{product_id}", headers=headers)
    assert response.status_code == 200, response.text
    return dict(response.json())


def _issues(detail: dict[str, Any]) -> set[str]:
    return {issue["rule"] + ":" + issue["subject"] for issue in detail["validation"]["issues"]}


def _settle_category(product_id: str) -> None:
    """Categories are off in tests; give the product one, as the LLM would."""
    with get_pool().connection() as conn:
        conn.execute(
            "UPDATE products SET category_id = (SELECT id FROM categories WHERE code = "
            "'dining.drinkware.jugs'), category_state = 'assigned', category_source = 'llm' "
            "WHERE id = %s",
            (product_id,),
        )


def _write_copy(client: TestClient, headers: dict, product_id: str) -> Any:
    revision = _detail(client, headers, product_id)["approval"]["revision"]
    return client.put(
        f"/products/{product_id}/copy",
        json={
            "title": "Acme Glass Jug",
            "description": "A glass jug.",
            "bullets": ["Glass jug"],
            "revision": revision,
        },
        headers=headers,
    )


def test_values_can_be_set_and_cleared_to_fix_errors(
    client: TestClient, store: MemoryObjectStore, alice: dict
) -> None:
    batch_id = _batch(client, store, alice)
    products = _products(client, alice, batch_id)
    mug_id = products["AK-1003"]["id"]
    client.put(
        f"/batches/{batch_id}/validation/profile", json={"profile": "generic"}, headers=alice
    )
    mug = _detail(client, alice, mug_id)
    assert "required_attribute:brand" in _issues(mug)
    brand = next(i for i in mug["validation"]["issues"] if i["subject"] == "brand")
    assert brand["attribute"] == "brand"

    revision = mug["approval"]["revision"]
    response = client.put(
        f"/products/{mug_id}/values/brand",
        json={"value": "  Vardhan ", "note": "From the box", "revision": revision},
        headers=alice,
    )
    assert response.status_code == 200, response.text
    assert response.json()["revision"] == revision + 1
    mug = _detail(client, alice, mug_id)
    assert "required_attribute:brand" not in _issues(mug)
    shown = {v["code"]: v for v in mug["catalog_values"]}["brand"]
    assert (shown["shown"], shown["state"]) == ("Vardhan", "reviewer_set")

    # A stale page is refused, and nothing is lost.
    stale = client.put(
        f"/products/{mug_id}/values/brand",
        json={"value": "Other", "revision": revision},
        headers=alice,
    )
    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "changed_elsewhere"
    # A quantity needs its unit; the product's own code can't be edited.
    revision = mug["approval"]["revision"]
    bad = client.put(
        f"/products/{mug_id}/values/capacity",
        json={"value": "350", "revision": revision},
        headers=alice,
    )
    assert bad.status_code == 422 and "unit" in bad.json()["detail"]["message"]
    sku = client.put(
        f"/products/{mug_id}/values/sku", json={"value": "X-1", "revision": revision}, headers=alice
    )
    assert sku.status_code == 422

    # Clearing the bottle's wrong barcode ends the duplicate-barcode error on both.
    jug_id, bottle_id = products["AK-1001"]["id"], products["AK-1002"]["id"]
    assert f"duplicate_gtin:{bottle_id}" in _issues(_detail(client, alice, jug_id))
    bottle = _detail(client, alice, bottle_id)
    cleared = client.put(
        f"/products/{bottle_id}/values/gtin",
        json={"value": None, "revision": bottle["approval"]["revision"]},
        headers=alice,
    )
    assert cleared.status_code == 200, cleared.text
    assert f"duplicate_gtin:{bottle_id}" not in _issues(_detail(client, alice, jug_id))
    # Processing again (photo values) never brings a cleared value back.
    with get_pool().connection() as conn:
        state = conn.execute(
            "SELECT pa.state, pa.decided_by FROM product_attributes pa JOIN attribute_definitions"
            " a ON a.id = pa.attribute_id WHERE a.code = 'gtin' AND pa.product_id = %s",
            (bottle_id,),
        ).fetchone()
    assert state is not None and (state["state"], state["decided_by"]) == ("missing", "alice")

    history = client.get(f"/products/{mug_id}/history", headers=alice).json()["items"]
    edit = next(entry for entry in history if entry["action"] == "edit")
    assert edit["text"] == "Set Brand to “Vardhan”" and edit["by"] == "you"
    assert edit["after"]["note"] == "From the box" and edit["before"]["state"] == "missing"
    assert history[-1]["text"] == "Read from the supplier's files"


def test_approval_needs_a_ready_product_and_holds_only_while_unchanged(
    client: TestClient, store: MemoryObjectStore, alice: dict
) -> None:
    batch_id = _batch(client, store, alice)
    jug_id = _products(client, alice, batch_id)["AK-1001"]["id"]
    jug = _detail(client, alice, jug_id)
    refused = client.post(
        f"/products/{jug_id}/approve", json={"revision": jug["approval"]["revision"]}, headers=alice
    )
    assert refused.status_code == 422
    blockers = " ".join(_detail(client, alice, jug_id)["approval"]["blockers"])
    assert "error" in blockers and "no copy yet" in blockers

    _settle_category(jug_id)
    assert _write_copy(client, alice, jug_id).status_code == 200
    # The shared barcode is still an error: approval still refused, with that reason.
    jug = _detail(client, alice, jug_id)
    assert not jug["approval"]["can_approve"]
    bottle_id = _products(client, alice, batch_id)["AK-1002"]["id"]
    bottle = _detail(client, alice, bottle_id)
    client.put(
        f"/products/{bottle_id}/values/gtin",
        json={"value": None, "revision": bottle["approval"]["revision"]},
        headers=alice,
    )
    jug = _detail(client, alice, jug_id)
    assert jug["approval"]["can_approve"], jug["approval"]["blockers"]
    assert jug["generated_content"]["edited"] is True

    approved = client.post(
        f"/products/{jug_id}/approve", json={"revision": jug["approval"]["revision"]}, headers=alice
    )
    assert approved.status_code == 200, approved.text
    assert _detail(client, alice, jug_id)["approval"]["state"] == "approved"
    with get_pool().connection() as conn:
        assert approved_products(conn, "alice", uuid.UUID(batch_id)) == [uuid.UUID(jug_id)]

    # Any change after approval: it needs approving again, and leaves export.
    jug = _detail(client, alice, jug_id)
    client.put(
        f"/products/{jug_id}/values/capacity",
        json={"value": "1.6 L", "revision": jug["approval"]["revision"]},
        headers=alice,
    )
    jug = _detail(client, alice, jug_id)
    assert jug["approval"]["state"] == "changed_since_approval"
    with get_pool().connection() as conn:
        assert approved_products(conn, "alice", uuid.UUID(batch_id)) == []

    # Back to draft, then rejected with a reason.
    restored = client.post(
        f"/products/{jug_id}/restore", json={"revision": jug["approval"]["revision"]}, headers=alice
    )
    assert restored.status_code == 200
    jug = _detail(client, alice, jug_id)
    rejected = client.post(
        f"/products/{jug_id}/reject",
        json={"reason": "Discontinued", "revision": jug["approval"]["revision"]},
        headers=alice,
    )
    assert rejected.status_code == 200
    jug = _detail(client, alice, jug_id)
    assert (jug["approval"]["state"], jug["approval"]["rejection_reason"]) == (
        "rejected",
        "Discontinued",
    )
    actions = [
        e["action"]
        for e in client.get(f"/products/{jug_id}/history", headers=alice).json()["items"]
    ]
    assert actions[:3] == ["reject", "restore", "edit"]
    assert "approve" in actions


def test_bulk_approval_approves_only_ready_products_and_says_why(
    client: TestClient, store: MemoryObjectStore, alice: dict
) -> None:
    batch_id = _batch(client, store, alice)
    products = _products(client, alice, batch_id)
    mug_id = products["AK-1004"]["id"]
    _settle_category(mug_id)
    assert _write_copy(client, alice, mug_id).status_code == 200
    result = client.post(f"/batches/{batch_id}/approve", json={}, headers=alice).json()
    assert result["approved"] == [mug_id]
    skipped = {item["product_key"]: item["reasons"] for item in result["skipped"]}
    assert set(skipped) == {"AK-1001", "AK-1002", "AK-1003"}
    assert all(reasons for reasons in skipped.values())
    summary = client.get(f"/batches/{batch_id}/approval", headers=alice).json()
    assert (summary["approved"], summary["ready"]) == (1, 0)

    rejected = client.post(
        f"/batches/{batch_id}/reject",
        json={"product_ids": [products["AK-1003"]["id"]], "reason": "Old"},
        headers=alice,
    )
    assert rejected.status_code == 200
    states = {
        i["product_key"]: i["state"]
        for i in client.get(f"/batches/{batch_id}/approval", headers=alice).json()["items"]
    }
    assert states["AK-1003"] == "rejected" and states["AK-1004"] == "approved"


def test_duplicates_are_settled_by_rejecting_one_or_calling_them_different(
    client: TestClient, store: MemoryObjectStore, alice: dict
) -> None:
    batch_id = _batch(client, store, alice)
    products = _products(client, alice, batch_id)
    jug_id, bottle_id = products["AK-1001"]["id"], products["AK-1002"]["id"]
    mug_a, mug_b = products["AK-1003"]["id"], products["AK-1004"]["id"]

    # A shared barcode can't be called "different": the barcode must be fixed.
    jug = _detail(client, alice, jug_id)
    refused = client.post(
        f"/products/{jug_id}/duplicate",
        json={"other_id": bottle_id, "keep": "different", "revision": jug["approval"]["revision"]},
        headers=alice,
    )
    assert refused.status_code == 422 and "barcode" in refused.json()["detail"]["message"]

    # Same product: keep the jug; the bottle is rejected as its duplicate.
    kept = client.post(
        f"/products/{jug_id}/duplicate",
        json={"other_id": bottle_id, "keep": "this", "revision": jug["approval"]["revision"]},
        headers=alice,
    )
    assert kept.status_code == 200, kept.text
    bottle = _detail(client, alice, bottle_id)
    assert bottle["approval"]["state"] == "rejected"
    assert bottle["approval"]["duplicate_of"] == jug_id
    assert "Duplicate of AK-1001" in bottle["approval"]["rejection_reason"]
    assert f"duplicate_gtin:{bottle_id}" not in _issues(_detail(client, alice, jug_id))

    # Two mugs with one name but one has no brand: brand and name differ, so give it one.
    mug = _detail(client, alice, mug_a)
    client.put(
        f"/products/{mug_a}/values/brand",
        json={"value": "Acme", "revision": mug["approval"]["revision"]},
        headers=alice,
    )
    mug = _detail(client, alice, mug_a)
    assert f"duplicate_name:{mug_b}" in _issues(mug)
    different = client.post(
        f"/products/{mug_a}/duplicate",
        json={"other_id": mug_b, "keep": "different", "revision": mug["approval"]["revision"]},
        headers=alice,
    )
    assert different.status_code == 200, different.text
    issue = next(
        i
        for i in _detail(client, alice, mug_a)["validation"]["issues"]
        if i["rule"] == "duplicate_name"
    )
    assert issue["set_aside"]["reason"] == "Different products."


def test_review_summary_and_the_audit_trail_cannot_be_rewritten(
    client: TestClient, store: MemoryObjectStore, alice: dict
) -> None:
    batch_id = _batch(client, store, alice)
    summary = client.get("/review", headers=alice).json()
    assert summary["open"] == 0 and summary["batches"] == []
    mug_id = _products(client, alice, batch_id)["AK-1003"]["id"]
    mug = _detail(client, alice, mug_id)
    client.put(
        f"/products/{mug_id}/values/brand",
        json={"value": "Acme", "revision": mug["approval"]["revision"]},
        headers=alice,
    )
    with get_pool().connection() as conn, pytest.raises(psycopg.Error):
        conn.execute("UPDATE review_events SET action = 'nothing' WHERE batch_id = %s", (batch_id,))


def test_endpoints_refuse_other_users_signed_out_and_waitlisted(
    client: TestClient, store: MemoryObjectStore, alice: dict, bob: dict, tokens: Tokens
) -> None:
    batch_id = _batch(client, store, alice)
    product_id = _products(client, alice, batch_id)["AK-1001"]["id"]
    ensure_user("carol", "carol@example.com", status="waitlisted")
    waitlisted = tokens.headers("carol", "carol@example.com")
    calls = [
        ("put", f"/products/{product_id}/values/brand", {"value": "X", "revision": 0}),
        ("put", f"/products/{product_id}/copy", {"title": "X", "revision": 0}),
        ("post", f"/products/{product_id}/approve", {"revision": 0}),
        ("post", f"/products/{product_id}/reject", {"reason": "x", "revision": 0}),
        ("post", f"/products/{product_id}/restore", {"revision": 0}),
        (
            "post",
            f"/products/{product_id}/duplicate",
            {"other_id": product_id, "keep": "this", "revision": 0},
        ),
        ("get", f"/products/{product_id}/history", None),
        ("get", f"/batches/{batch_id}/approval", None),
        ("post", f"/batches/{batch_id}/approve", {}),
        ("post", f"/batches/{batch_id}/reject", {"product_ids": [product_id], "reason": "x"}),
    ]
    for method, path, body in calls:
        kwargs = {"json": body} if body is not None else {}
        assert getattr(client, method)(path, headers=bob, **kwargs).status_code == 404, path
        assert getattr(client, method)(path, **kwargs).status_code == 401, path
        assert getattr(client, method)(path, headers=waitlisted, **kwargs).status_code == 403
    assert client.get("/review").status_code == 401
    assert client.get("/review", headers=bob).json()["open"] == 0
