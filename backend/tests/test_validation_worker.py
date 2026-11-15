"""Covers catalog checks end to end: read, change profile, set aside, and always current (M8).

Needs TEST_DATABASE_URL. Batches are processed in this process as the worker would process
them. Storage is in memory, and no LLM is called.
"""

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
        ["SKU", "Brand", "Name", "Barcode", "Price"],
        ["AK-1001", "Acme", "Glass jug", "4006381333931", "1299"],
        ["AK-1002", "Acme", "Steel bottle", "4006381333931", "899"],
        ["AK-1003", "", "Ceramic mug", "", "299"],
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


def _checks(client: TestClient, headers: dict, batch_id: str) -> dict[str, Any]:
    response = client.get(f"/batches/{batch_id}/validation", headers=headers)
    assert response.status_code == 200, response.text
    return dict(response.json())


def _by_key(checks: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["product_key"]: item for item in checks["items"]}


def _rules(item: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    return {(i["rule"], i["subject"]): i for i in item["issues"]}


def _product_checks(client: TestClient, headers: dict, product_id: str) -> dict[str, Any]:
    response = client.get(f"/products/{product_id}", headers=headers)
    assert response.status_code == 200, response.text
    return dict(response.json()["validation"])


def _set_state(product_id: str, code: str, state: str, text: str | None = None) -> None:
    reviewer = None if text is None else f'{{"text": "{text}"}}'
    with get_pool().connection() as conn:
        conn.execute(
            "UPDATE product_attributes pa SET state = %s, reviewer_value = %s::jsonb, "
            "selected_value_id = CASE WHEN %s::jsonb IS NULL THEN selected_value_id END "
            "FROM attribute_definitions a WHERE a.id = pa.attribute_id AND a.code = %s "
            "AND pa.product_id = %s",
            (state, reviewer, reviewer, code, product_id),
        )


def test_checks_follow_the_batch_and_its_profile(
    client: TestClient, store: MemoryObjectStore, alice: dict, bob: dict
) -> None:
    batch_id = _batch(client, store, alice)
    checks = _checks(client, alice, batch_id)
    assert (checks["profile"], checks["rules_version"]) == ("shopify", "checks@1")
    assert {p["code"] for p in checks["profiles"]} == {"generic", "shopify", "amazon_in"}
    items = _by_key(checks)
    jug, bottle, mug = items["AK-1001"], items["AK-1002"], items["AK-1003"]

    # Categories are off in tests, so none is settled: an error everywhere.
    assert ("category", "") in _rules(jug) and not jug["can_approve"]
    assert checks["ready"] == 0
    # The shared barcode is an error on both products, naming the other.
    shared = _rules(jug)[("duplicate_gtin", bottle["product_id"])]
    assert shared["severity"] == "error" and "AK-1002" in shared["message"]
    assert ("duplicate_gtin", jug["product_id"]) in _rules(bottle)
    # Shopify doesn't require a brand; Amazon India does, and a barcode and a photo.
    assert ("required_attribute", "brand") not in _rules(mug)
    assert _rules(mug)[("photo", "")]["severity"] == "warning"
    assert _rules(mug)[("generated_text", "")]["fix"] == "generate"

    response = client.put(
        f"/batches/{batch_id}/validation/profile", json={"profile": "amazon_in"}, headers=alice
    )
    assert response.status_code == 200, response.text
    mug = _by_key(response.json())["AK-1003"]
    assert {("required_attribute", "brand"), ("required_attribute", "gtin")} <= set(_rules(mug))
    assert _rules(mug)[("photo", "")]["severity"] == "error"
    assert _checks(client, alice, batch_id)["profile"] == "amazon_in"
    bad = client.put(
        f"/batches/{batch_id}/validation/profile", json={"profile": "ebay"}, headers=alice
    )
    assert bad.status_code == 422 and bad.json()["detail"]["code"] == "unknown_profile"

    # The product page carries the same checks.
    product = _product_checks(client, alice, mug["product_id"])
    assert product["profile_name"] == "Amazon India" and product["errors"] == mug["errors"]

    # Bob sees none of it.
    assert client.get(f"/batches/{batch_id}/validation", headers=bob).status_code == 404
    assert (
        client.put(
            f"/batches/{batch_id}/validation/profile", json={"profile": "generic"}, headers=bob
        ).status_code
        == 404
    )
    body = {"rule": "photo", "subject": "", "reason": "x"}
    path = f"/products/{mug['product_id']}/validation/set-aside"
    assert client.post(path, json=body, headers=bob).status_code == 404
    assert client.delete(f"{path}?rule=photo", headers=bob).status_code == 404


def test_checks_are_current_after_a_change(
    client: TestClient, store: MemoryObjectStore, alice: dict
) -> None:
    batch_id = _batch(client, store, alice)
    jug = _by_key(_checks(client, alice, batch_id))["AK-1001"]
    _set_state(jug["product_id"], "price", "conflict")
    jug = _by_key(_checks(client, alice, batch_id))["AK-1001"]
    issue = _rules(jug)[("required_attribute", "price")]
    assert "sources disagree" in issue["message"] and issue["fix"] == "review"
    # A person settles it: the next read no longer reports it.
    _set_state(jug["product_id"], "price", "reviewer_set", "1350 INR")
    jug = _by_key(_checks(client, alice, batch_id))["AK-1001"]
    assert ("required_attribute", "price") not in _rules(jug)


def test_a_warning_is_set_aside_with_a_reason_until_it_changes(
    client: TestClient, store: MemoryObjectStore, alice: dict
) -> None:
    batch_id = _batch(client, store, alice)
    mug = _by_key(_checks(client, alice, batch_id))["AK-1003"]
    path = f"/products/{mug['product_id']}/validation/set-aside"
    before = mug["warnings"]

    response = client.post(
        path,
        json={"rule": "generated_text", "subject": "", "reason": "Written by hand later"},
        headers=alice,
    )
    assert response.status_code == 200, response.text
    after = response.json()
    assert after["warnings"] == before - 1
    aside = _rules(after)[("generated_text", "")]["set_aside"]
    assert aside["reason"] == "Written by hand later" and aside["by"] == "alice"
    assert after["issues"][-1]["rule"] == "generated_text"

    # Errors can't be set aside, and neither can something that isn't an issue.
    for rule in ("category", "bullets"):
        refused = client.post(path, json={"rule": rule, "subject": "", "reason": "x"},
                              headers=alice)  # fmt: skip
        assert refused.status_code == 422 and refused.json()["detail"]["code"] == "not_a_warning"

    # What it checked changes (text generation now failed): the warning comes back.
    with get_pool().connection() as conn:
        conn.execute(
            "UPDATE products SET enrichment_state = 'failed', enrichment_message = 'x' "
            "WHERE id = %s",
            (mug["product_id"],),
        )
    back = _product_checks(client, alice, mug["product_id"])
    assert _rules(back)[("generated_text", "")]["set_aside"] is None

    undo = client.delete(f"{path}?rule=generated_text&subject=", headers=alice)
    assert undo.status_code == 200, undo.text
    again = client.delete(f"{path}?rule=generated_text&subject=", headers=alice)
    assert again.status_code == 409 and again.json()["detail"]["code"] == "not_set_aside"

    with get_pool().connection() as conn:
        events = conn.execute(
            "SELECT action, before, after FROM review_events WHERE entity_type = "
            "'validation_issue' AND entity_id = %s ORDER BY created_at",
            (mug["product_id"],),
        ).fetchall()
    assert [e["action"] for e in events] == ["set_aside", "undo_set_aside"]
    assert events[0]["after"]["reason"] == "Written by hand later"


def test_endpoints_refuse_signed_out_and_waitlisted_users(
    client: TestClient, store: MemoryObjectStore, alice: dict, tokens: Tokens
) -> None:
    batch_id = _batch(client, store, alice)
    product_id = _checks(client, alice, batch_id)["items"][0]["product_id"]
    ensure_user("carol", "carol@example.com", status="waitlisted")
    waitlisted = tokens.headers("carol", "carol@example.com")
    calls = [
        ("get", f"/batches/{batch_id}/validation", None),
        ("put", f"/batches/{batch_id}/validation/profile", {"profile": "generic"}),
        ("post", f"/products/{product_id}/validation/set-aside",
         {"rule": "photo", "subject": "", "reason": "x"}),
        ("delete", f"/products/{product_id}/validation/set-aside?rule=photo", None),
    ]  # fmt: skip
    for method, path, body in calls:
        kwargs = {"json": body} if body is not None else {}
        assert getattr(client, method)(path, **kwargs).status_code == 401
        assert getattr(client, method)(path, headers=waitlisted, **kwargs).status_code == 403


def test_a_batch_without_products_has_no_checks(client: TestClient, alice: dict) -> None:
    supplier = create_supplier(client, alice, "Empty")
    batch_id = create_batch_for(client, alice, supplier)
    checks = _checks(client, alice, batch_id)
    assert (checks["items"], checks["ready"], checks["profile"]) == ([], 0, "shopify")
