"""Covers normalization end to end: process a batch, then settle conflicts and categories.

Needs TEST_DATABASE_URL. Jobs run in this process as the worker would run them. Storage is in
memory and LLM replies are scripted, so nothing calls AWS.
"""

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

import samples
from app.db import get_pool
from app.jobs.base import get_handler
from app.llm.backends.fake import FakeBackend
from app.llm.base import LlmReply, LlmRequest
from app.storage.backends.memory import MemoryObjectStore
from conftest import Tokens, categories_on, ensure_user
from pipeline import claim, context_for, create_batch, drain, run_one, submit, upload

pytestmark = pytest.mark.usefixtures("db", "llm")

UNLIMITED = {"submissions_lifetime": None, "submissions_per_24_hours": None}


@pytest.fixture
def alice(tokens: Tokens) -> dict[str, str]:
    ensure_user("alice", "alice@example.com", overrides=UNLIMITED)
    return tokens.headers("alice", "alice@example.com")


@pytest.fixture
def bob(tokens: Tokens) -> dict[str, str]:
    ensure_user("bob", "bob@example.com", overrides=UNLIMITED)
    return tokens.headers("bob", "bob@example.com")


SHEET = samples.table_csv(
    [
        ["SKU", "Brand", "Name", "Capacity", "Price"],
        ["AK-1002-RED", "Acme", "Glass Jug Red", "1.5 L", "₹1,299"],
        ["AK-1002-BLU", "Acme", "Glass Jug Blue", "1500 ml", "1299"],
        ["AK-2001", "Acme", "Steel Mixing Bowl", "3 L", "450"],
    ]
)
PDF = samples.text_pdf([["Glass Jug Red", "Model AK-1002-RED", "Capacity: 1.2 L"]])
PDF_REPLY = json.dumps(
    {
        "values": [
            {
                "product": "AK-1002-RED",
                "attribute": "capacity",
                "value": "1.2 L",
                "quote": "Capacity: 1.2 L",
            }
        ]
    }
)
CATEGORY_PROMPT = "You choose the one category"


def _answer_categories(llm: FakeBackend, monkeypatch: pytest.MonkeyPatch,
                       answers: dict[str, tuple[str, float]]) -> None:  # fmt: skip
    """Answer category calls by the product's code, since their order isn't fixed."""
    scripted = llm.complete

    def complete(request: LlmRequest) -> LlmReply:
        if CATEGORY_PROMPT not in request.system:
            return scripted(request)
        llm.requests.append(request)
        key = next(key for key in answers if f"Code: {key}\n" in request.user)
        category, confidence = answers[key]
        reply = {"category": category, "confidence": confidence, "reason": "From the name."}
        return LlmReply(text=json.dumps(reply), input_tokens=100, output_tokens=20)

    monkeypatch.setattr(llm, "complete", complete)


def _products(client: TestClient, headers: dict, batch_id: str) -> dict[str, dict[str, Any]]:
    items = client.get(f"/batches/{batch_id}/products", headers=headers).json()["items"]
    out = {}
    for item in items:
        response = client.get(f"/products/{item['id']}", headers=headers)
        assert response.status_code == 200, response.text
        out[item["product_key"]] = response.json()
    return out


def _values(product: dict) -> dict[str, dict[str, Any]]:
    return {value["code"]: value for value in product["catalog_values"]}


def _tasks(client: TestClient, headers: dict, batch_id: str) -> list[dict[str, Any]]:
    response = client.get(f"/batches/{batch_id}/review-tasks", headers=headers)
    assert response.status_code == 200, response.text
    return list(response.json()["items"])


def _decide(client: TestClient, headers: dict, task_id: str, body: dict) -> Any:
    return client.post(f"/review-tasks/{task_id}/decision", json=body, headers=headers)


def test_values_are_normalized_conflicts_and_categories_go_to_a_person(
    client: TestClient,
    store: MemoryObjectStore,
    alice: dict,
    bob: dict,
    llm: FakeBackend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    categories_on(monkeypatch)
    llm.replies.append(PDF_REPLY)
    _answer_categories(
        llm,
        monkeypatch,
        {
            "AK-1002-RED": ("dining.drinkware.jugs", 0.92),
            "AK-1002-BLU": ("dining.drinkware.jugs", 0.9),
            # Not sure enough: a person decides.
            "AK-2001": ("kitchen.tools.mixing-bowls", 0.5),
        },
    )
    batch_id = create_batch(client, alice)
    upload(client, store, alice, batch_id, "prices.csv", SHEET)
    upload(client, store, alice, batch_id, "specs.pdf", PDF)
    submit(client, alice, batch_id)
    ran = drain()
    assert ran.count("normalize_batch") == 1 and ran.count("categorize_product") == 3
    assert client.get(f"/batches/{batch_id}/run", headers=alice).json()["status"] == "succeeded"

    products = _products(client, alice, batch_id)
    red, blue, bowl = (_values(products[k]) for k in ("AK-1002-RED", "AK-1002-BLU", "AK-2001"))
    # The sheet says 1.5 L and the PDF 1.2 L: nothing is shown until a person decides.
    assert (red["capacity"]["state"], red["capacity"]["shown"]) == ("conflict", None)
    assert sorted(v["display"] for v in red["capacity"]["values"]) == ["1.2 L", "1.5 L"]
    # 1500 ml and 1.5 L are the same thing.
    assert blue["capacity"]["state"] == "verified"
    assert (red["price"]["state"], red["price"]["shown"]) == ("verified", "1299 INR")
    assert blue["price"]["values"][0]["flags"] == ["currency_assumed"]
    assert (bowl["capacity"]["shown"], bowl["net_weight"]["state"]) == ("3 L", "missing")
    # The original values are untouched.
    raw = sorted(o["raw_value"] for o in products["AK-1002-RED"]["observations"]
                 if o["attribute_code"] == "capacity")  # fmt: skip
    assert raw == ["1.2 L", "1.5 L"]

    jug_category = products["AK-1002-RED"]["category"]
    assert (jug_category["state"], jug_category["source"]) == ("assigned", "llm")
    assert jug_category["category"]["code"] == "dining.drinkware.jugs"
    bowl_category = products["AK-2001"]["category"]
    assert (bowl_category["state"], bowl_category["category"]) == ("needs_review", None)
    assert bowl_category["suggested"]["code"] == "kitchen.tools.mixing-bowls"

    tasks = _tasks(client, alice, batch_id)
    assert sorted(t["kind"] for t in tasks) == ["attribute_conflict", "category"]
    conflict = next(t for t in tasks if t["kind"] == "attribute_conflict")
    assert (conflict["product_key"], conflict["attribute_code"]) == ("AK-1002-RED", "capacity")
    category = next(t for t in tasks if t["kind"] == "category")
    assert (category["product_key"], category["suggested_category_name"]) == (
        "AK-2001",
        "Mixing bowls",
    )

    variants = client.get(f"/batches/{batch_id}/variants", headers=alice).json()["items"]
    assert [{v["product_a_key"], v["product_b_key"]} for v in variants] == [
        {"AK-1002-RED", "AK-1002-BLU"}
    ]
    assert {r["rule"] for r in variants[0]["reasons"]} == {
        "same_name_but_colour_or_size",
        "same_base_code",
    }

    # Someone else can't see or decide any of it.
    assert client.get(f"/batches/{batch_id}/variants", headers=bob).status_code == 404
    assert client.get(f"/batches/{batch_id}/review-tasks", headers=bob).status_code == 404
    pdf_value = next(v for v in conflict["values"] if v["display"] == "1.2 L")
    choose_pdf = {"decision": "choose", "value_id": pdf_value["id"]}
    assert _decide(client, bob, conflict["id"], choose_pdf).status_code == 404

    # Only this attribute's values, and only decisions that fit the task.
    other_value = blue["capacity"]["values"][0]["id"]
    wrong = _decide(client, alice, conflict["id"], {"decision": "choose", "value_id": other_value})
    assert (wrong.status_code, wrong.json()["detail"]["code"]) == (422, "not_a_choice")
    assert _decide(client, alice, conflict["id"], {"decision": "reject"}).status_code == 422

    decided = _decide(client, alice, conflict["id"], choose_pdf)
    assert decided.status_code == 200, decided.text
    assert decided.json()["shown"] == "1.2 L"
    assert _decide(client, alice, conflict["id"], choose_pdf).status_code == 409
    capacity = _values(_products(client, alice, batch_id)["AK-1002-RED"])["capacity"]
    assert (capacity["state"], capacity["shown"], capacity["decided_by"]) == (
        "reviewer_set",
        "1.2 L",
        "alice",
    )

    categories = client.get("/categories", headers=alice).json()["items"]
    by_code = {c["code"]: c for c in categories}
    assert "home-kitchen" not in by_code
    assert by_code["dining.drinkware.mugs"]["path"] == "Dining › Drinkware › Mugs and cups"
    root_id = _scalar("SELECT id FROM categories WHERE code = 'home-kitchen'")
    wrong = _decide(
        client, alice, category["id"], {"decision": "choose", "category_id": str(root_id)}
    )
    assert wrong.status_code == 422
    bowls = by_code["kitchen.tools.mixing-bowls"]["id"]
    decided = _decide(client, alice, category["id"], {"decision": "choose", "category_id": bowls})
    assert decided.status_code == 200, decided.text
    bowl_category = _products(client, alice, batch_id)["AK-2001"]["category"]
    assert (bowl_category["state"], bowl_category["source"]) == ("assigned", "person")
    assert {t["status"] for t in _tasks(client, alice, batch_id)} == {"resolved"}

    with get_pool().connection() as conn:
        events = conn.execute(
            "SELECT actor_user_id AS actor, entity_type, action, before, after FROM review_events "
            "ORDER BY created_at"
        ).fetchall()
    assert [(e["actor"], e["entity_type"], e["action"]) for e in events] == [
        ("alice", "product_attribute", "decide"),
        ("alice", "product_category", "decide"),
    ]
    assert events[0]["before"]["state"] == "conflict"
    assert events[0]["after"]["value"] == "1.2 L"


def _scalar(sql: str, *params: Any) -> Any:
    with get_pool().connection() as conn:
        row = conn.execute(sql, params).fetchone()
    assert row is not None
    return next(iter(row.values()))


def test_normalizing_again_keeps_a_persons_decision(
    client: TestClient, store: MemoryObjectStore, alice: dict, llm: FakeBackend
) -> None:
    llm.replies.append(PDF_REPLY)
    batch_id = create_batch(client, alice)
    upload(client, store, alice, batch_id, "prices.csv", SHEET)
    upload(client, store, alice, batch_id, "specs.pdf", PDF)
    submit(client, alice, batch_id)
    while (job_type := run_one()) != "associate_images":
        assert job_type is not None
    job = claim()
    assert job is not None and job.job_type == "normalize_batch"
    handler = get_handler("normalize_batch")
    handler.run(context_for(job))

    [conflict] = _tasks(client, alice, batch_id)
    value = next(v for v in conflict["values"] if v["display"] == "1.5 L")
    response = _decide(client, alice, conflict["id"], {"decision": "set", "value": "1.4 L"})
    assert response.status_code == 200, response.text
    assert value["display"] == "1.5 L"
    counts = "SELECT (SELECT count(*) FROM normalized_values), (SELECT count(*) FROM review_tasks)"
    with get_pool().connection() as conn:
        before = conn.execute(counts).fetchone()

    # The same job again, as after a retry: nothing new, and the person's value stands.
    handler.run(context_for(job))
    with get_pool().connection() as conn:
        assert conn.execute(counts).fetchone() == before
        row = conn.execute(
            "SELECT state, decided_by, reviewer_value FROM product_attributes pa "
            "JOIN attribute_definitions a ON a.id = pa.attribute_id AND a.code = 'capacity' "
            "JOIN products p ON p.id = pa.product_id AND p.product_key = 'AK-1002-RED'"
        ).fetchone()
    assert row == {
        "state": "reviewer_set",
        "decided_by": "alice",
        "reviewer_value": {"text": "1.4 L"},
    }
    [task] = _tasks(client, alice, batch_id)
    assert task["status"] == "resolved"
    capacity = _values(_products(client, alice, batch_id)["AK-1002-RED"])["capacity"]
    assert (capacity["state"], capacity["shown"]) == ("reviewer_set", "1.4 L")
