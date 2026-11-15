"""Covers image association and review end to end: process a batch, then decide in the API.

Needs TEST_DATABASE_URL. Jobs run in this process as the worker would run them. Storage is in
memory, the LLM is scripted and embeddings are fake, so nothing calls AWS. OCR is real.
"""

import json
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

import samples
from app.db import get_pool
from app.llm.backends.fake import FakeBackend
from app.storage.backends.memory import MemoryObjectStore
from conftest import Tokens, ensure_user
from pipeline import create_batch, drain, submit, upload

pytestmark = pytest.mark.usefixtures("db", "llm")

ALICE = "alice"
UNLIMITED = {"submissions_lifetime": None, "submissions_per_24_hours": None}


@pytest.fixture
def alice(tokens: Tokens) -> dict[str, str]:
    ensure_user(ALICE, "alice@example.com", overrides=UNLIMITED)
    return tokens.headers(ALICE, "alice@example.com")


def _reply(*values: tuple[str, str, str, str]) -> str:
    return json.dumps(
        {
            "values": [
                {"product": p, "attribute": a, "value": v, "quote": q} for p, a, v, q in values
            ]
        }
    )


def _scalar(sql: str, *params: Any) -> Any:
    with get_pool().connection() as conn:
        row = conn.execute(sql, params).fetchone()
    assert row is not None
    return next(iter(row.values()))


def _matches(client: TestClient, headers: dict, batch_id: str) -> dict[str, list[dict]]:
    items = client.get(f"/batches/{batch_id}/matches", headers=headers).json()["items"]
    by_image: dict[str, list[dict]] = {}
    for item in items:
        by_image.setdefault(item["image_name"], []).append(item)
    return by_image


def _tasks(client: TestClient, headers: dict, batch_id: str) -> dict[str, Any]:
    response = client.get(f"/batches/{batch_id}/review-tasks", headers=headers)
    assert response.status_code == 200, response.text
    body = dict(response.json())
    body["items"] = [task for task in body["items"] if task["kind"] == "image_match"]
    return body


SHEET = samples.table_csv(
    [
        ["SKU", "Brand", "Name"],
        ["AK-1003", "Acme", "Glass Water Jug"],
        ["AK-1004", "Acme", "Steel Bowl"],
    ]
)


def test_codes_accept_photos_and_the_rest_waits_or_stays_unmatched(
    client: TestClient, store: MemoryObjectStore, alice: dict
) -> None:
    batch_id = create_batch(client, alice)
    upload(client, store, alice, batch_id, "prices.csv", SHEET)
    # Named after its product.
    upload(
        client,
        store,
        alice,
        batch_id,
        "AK-1003-front.jpg",
        samples.jpeg_of(samples.product_photo()),
    )
    # Its code is printed on it.
    upload(
        client,
        store,
        alice,
        batch_id,
        "label.jpg",
        samples.text_image(["Steel Bowl", "Model AK-1004"]),
    )
    # Only the brand, which both products share.
    upload(
        client,
        store,
        alice,
        batch_id,
        "brand.jpg",
        samples.text_image(["ACME", "Kitchen classics"]),
    )
    # Nothing to go on, and two products to choose from.
    upload(client, store, alice, batch_id, "plain.png", samples.png(200, 200, (20, 160, 60)))
    submit(client, alice, batch_id)
    ran = drain()
    assert ran.count("associate_images") == 1

    matches = _matches(client, alice, batch_id)
    front = matches["AK-1003-front.jpg"]
    assert [(m["product_key"], m["decision"]) for m in front] == [("AK-1003", "auto_accepted")]
    assert front[0]["evidence"][0]["signal"] == "code_in_name"
    assert "AK-1003-front.jpg" in front[0]["evidence"][0]["text"]
    assert [(m["product_key"], m["decision"]) for m in matches["label.jpg"]] == [
        ("AK-1004", "auto_accepted")
    ]
    assert {m["decision"] for m in matches["brand.jpg"]} == {"needs_review"}
    assert "plain.png" not in matches

    tasks = _tasks(client, alice, batch_id)
    assert [t["image_name"] for t in tasks["items"]] == ["brand.jpg"]
    assert {c["product_key"] for c in tasks["items"][0]["candidates"]} == {"AK-1003", "AK-1004"}
    assert tasks["items"][0]["thumbnail_url"].startswith("https://storage.tests.invalid/")

    run = client.get(f"/batches/{batch_id}/run", headers=alice).json()
    assert (run["images_matched"], run["images_to_review"], run["images_unmatched"]) == (2, 1, 1)
    jug = next(
        p for p in client.get(f"/batches/{batch_id}/products", headers=alice).json()["items"]
        if p["product_key"] == "AK-1003"
    )  # fmt: skip
    photos = client.get(f"/products/{jug['id']}", headers=alice).json()["images"]
    assert [(p["image_name"], p["decision"]) for p in photos] == [
        ("AK-1003-front.jpg", "auto_accepted")
    ]


def test_photos_of_one_product_are_confirmed_by_a_person_and_their_values_read(
    client: TestClient, store: MemoryObjectStore, alice: dict, llm: FakeBackend
) -> None:
    # The owner's case: photos of one box, only one of which shows the code.
    llm.replies += [
        _reply(
            ("B094YFFSMY", "sku", "B094YFFSMY", "B094YFFSMY"),
            ("B094YFFSMY", "net_weight", "113 gm", "Net Weight : 113 gm"),
        ),
        # The features side names no product, so in a batch without a spreadsheet the model
        # finds nothing it may report.
        _reply(),
    ]
    batch_id = create_batch(client, alice)
    upload(
        client,
        store,
        alice,
        batch_id,
        "label.jpg",
        samples.text_image(["B094YFFSMY", "Net Weight : 113 gm"]),
    )
    upload(
        client,
        store,
        alice,
        batch_id,
        "features.jpg",
        samples.text_image(["FEATURES", "Lightweight", "113 Grams"]),
    )
    submit(client, alice, batch_id)
    drain()

    matches = _matches(client, alice, batch_id)
    assert matches["label.jpg"][0]["decision"] == "auto_accepted"
    # Its values were read when the product was made from it: nothing more to read.
    assert matches["label.jpg"][0]["values_read_at"] is not None
    task = _tasks(client, alice, batch_id)["items"][0]
    assert task["image_name"] == "features.jpg"
    candidate = task["candidates"][0]
    assert candidate["product_key"] == "B094YFFSMY"
    assert [e["signal"] for e in candidate["evidence"]] == ["only_product"]

    llm.replies.append(_reply(("B094YFFSMY", "net_weight", "113 Grams", "Lightweight 113 Grams")))
    response = client.post(
        f"/review-tasks/{task['id']}/decision",
        json={"decision": "accept", "product_id": candidate["product_id"]},
        headers=alice,
    )
    assert response.status_code == 200, response.text
    assert response.json()["photo_values"] == "queued"
    ran = drain()
    assert ran == ["photo_values_plan", "photo_value", "normalize_batch"]

    product = client.get(f"/products/{candidate['product_id']}", headers=alice).json()
    weights = sorted(
        (o["raw_value"], o["source_file_name"])
        for o in product["observations"]
        if o["attribute_code"] == "net_weight"
    )
    assert weights == [("113 Grams", "features.jpg"), ("113 gm", "label.jpg")]
    assert product["differing_attributes"] == ["net_weight"]
    assert [p["image_name"] for p in product["images"]] == ["label.jpg", "features.jpg"]
    assert _tasks(client, alice, batch_id)["pending_photo_values"] == 0
    # The batch's own run is untouched by the photo values run.
    assert client.get(f"/batches/{batch_id}", headers=alice).json()["status"] == "completed"


def test_decisions_are_checked_recorded_and_private(
    client: TestClient, store: MemoryObjectStore, alice: dict, tokens: Tokens
) -> None:
    batch_id = create_batch(client, alice)
    upload(client, store, alice, batch_id, "prices.csv", SHEET)
    upload(
        client,
        store,
        alice,
        batch_id,
        "brand.jpg",
        samples.text_image(["ACME", "Kitchen classics"]),
    )
    submit(client, alice, batch_id)
    drain()
    task = _tasks(client, alice, batch_id)["items"][0]
    url = f"/review-tasks/{task['id']}/decision"

    ensure_user("bob", "bob@example.com")
    bob = tokens.headers("bob", "bob@example.com")
    for path in (f"/batches/{batch_id}/matches", f"/batches/{batch_id}/review-tasks"):
        assert client.get(path, headers=bob).status_code == 404
    assert client.post(f"/batches/{batch_id}/photo-values", headers=bob).status_code == 404
    assert client.post(url, json={"decision": "reject"}, headers=bob).status_code == 404

    other = client.post(url, json={"decision": "accept", "product_id": task["id"]}, headers=alice)
    assert other.status_code == 422 and other.json()["detail"]["code"] == "not_a_candidate"
    assert client.post(url, json={"decision": "accept"}, headers=alice).status_code == 422

    rejected = client.post(url, json={"decision": "reject"}, headers=alice)
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["photo_values"] == "not_needed"
    again = client.post(url, json={"decision": "reject"}, headers=alice)
    assert again.status_code == 409 and again.json()["detail"]["code"] == "already_decided"

    with get_pool().connection() as conn:
        events = conn.execute(
            "SELECT actor_user_id, action, before, after FROM review_events ORDER BY created_at"
        ).fetchall()
    assert len(events) == 2
    assert all(e["actor_user_id"] == ALICE for e in events)
    assert {e["before"]["decision"] for e in events} == {"needs_review"}
    assert {e["after"]["decision"] for e in events} == {"rejected"}
    # The audit trail can only grow.
    with pytest.raises(psycopg.errors.RaiseException), get_pool().connection() as conn:
        conn.execute("UPDATE review_events SET action = 'edited'")

    # Running association again keeps the person's decision.
    from app.jobs.base import get_handler
    from pipeline import claim, context_for

    with get_pool().connection() as conn:
        run_id = conn.execute(
            "SELECT id FROM pipeline_runs WHERE batch_id = %s", (batch_id,)
        ).fetchone()
    assert run_id is not None
    before = _matches(client, alice, batch_id)["brand.jpg"]
    with get_pool().connection() as conn:
        conn.execute(
            "UPDATE jobs SET status = 'pending', attempts = 0, finished_at = NULL "
            "WHERE job_type = 'associate_images'"
        )
    job = claim()
    assert job is not None and job.job_type == "associate_images"
    get_handler("associate_images").run(context_for(job))
    after = _matches(client, alice, batch_id)["brand.jpg"]
    assert [(m["decision"], m["decided_by"]) for m in after] == [
        (m["decision"], m["decided_by"]) for m in before
    ]
    assert _scalar("SELECT count(*) FROM review_tasks WHERE status = 'open'") == 0
