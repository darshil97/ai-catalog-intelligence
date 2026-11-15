"""Covers enrichment end to end: generate, refuse fabrication, pause at the spend cap, resume.

Needs TEST_DATABASE_URL. Jobs run in this process as the worker would run them. Storage is in
memory and LLM replies are scripted, so nothing calls AWS.
"""

import json
import re
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient

import samples
from app.config import get_config
from app.db import get_pool
from app.jobs import queue
from app.jobs.base import get_handler
from app.jobs.errors import LeaseLostError
from app.llm.backends.fake import FakeBackend
from app.llm.base import LlmReply, LlmRequest
from app.storage.backends.memory import MemoryObjectStore
from conftest import Tokens, auto_copy_on, ensure_user
from pipeline import (
    claim,
    context_for,
    create_batch_for,
    create_supplier,
    drain,
    run_one,
    submit,
    upload,
)

pytestmark = pytest.mark.usefixtures("db", "llm")

UNLIMITED = {"submissions_lifetime": None, "submissions_per_24_hours": None}
ENRICH_PROMPT = "You write a product's title"
FACT_LINE = re.compile(r"^(F\d+)  ([^:]+): (.*)$", re.MULTILINE)

SHEET = samples.table_csv(
    [
        ["SKU", "Brand", "Name", "Capacity", "Material", "Price"],
        ["AK-1001", "Acme", "Glass jug", "1.5 L", "Borosilicate glass", "1299"],
        ["AK-1002", "Acme", "Steel bottle", "750 ml", "Stainless steel", "899"],
        ["AK-1003", "Acme", "Ceramic mug", "350 ml", "Ceramic", "299"],
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


def _facts(request: LlmRequest) -> dict[str, tuple[str, str]]:
    """The prompt's facts by attribute name: name -> (id, value)."""
    return {name: (fid, value) for fid, name, value in FACT_LINE.findall(request.user)}


def grounded_reply(request: LlmRequest) -> str:
    """A reply that keeps to the prompt's facts."""
    facts = _facts(request)
    name_id, name = facts["Product name"]
    brand_id, brand = facts["Brand"]
    others = [(fid, f"{label}: {value}") for label, (fid, value) in facts.items()
              if label not in ("Product name", "Brand", "Model number")]  # fmt: skip
    bullets = [{"text": text, "facts": [fid]} for fid, text in others][:4]
    bullets.append({"text": f"Made by {brand}", "facts": [brand_id]})
    while len(bullets) < 3:
        bullets.append({"text": name, "facts": [name_id]})
    return json.dumps(
        {
            "title": {"text": f"{brand} {name}", "facts": [brand_id, name_id]},
            "description": [
                {"text": f"A {name.lower()} by {brand}.", "facts": [name_id, brand_id]},
                {"text": f"It is a {name.lower()}.", "facts": [name_id]},
            ],
            "bullets": bullets,
        }
    )


def fabricating_reply(request: LlmRequest) -> str:
    """A reply that adds a claim no fact supports."""
    reply = json.loads(grounded_reply(request))
    name_id, _ = _facts(request)["Product name"]
    reply["bullets"][0] = {"text": "Dishwasher safe and holds 2 L", "facts": [name_id]}
    return json.dumps(reply)


def _answer(llm: FakeBackend, monkeypatch: pytest.MonkeyPatch, make: Any) -> list[str]:
    """Answer enrichment calls with `make(request)`. Returns the product keys asked about."""
    asked: list[str] = []
    scripted = llm.complete

    def complete(request: LlmRequest) -> LlmReply:
        if ENRICH_PROMPT not in request.system:
            return scripted(request)
        llm.requests.append(request)
        asked.append(re.search(r"## Product (\S+)", request.user).group(1))  # type: ignore[union-attr]
        text = make(request)
        if isinstance(text, Exception):
            raise text
        return LlmReply(text=text, input_tokens=100, output_tokens=20)

    monkeypatch.setattr(llm, "complete", complete)
    return asked


def create_batch(client: TestClient, headers: dict) -> str:
    """A batch under a supplier of its own, so a test can make several."""
    supplier = create_supplier(client, headers, f"Supplier {uuid.uuid4().hex[:8]}")
    return create_batch_for(client, headers, supplier)


def _processed_batch(client: TestClient, store: MemoryObjectStore, headers: dict) -> str:
    batch_id = create_batch(client, headers)
    upload(client, store, headers, batch_id, "prices.csv", SHEET)
    submit(client, headers, batch_id)
    drain()
    assert client.get(f"/batches/{batch_id}", headers=headers).json()["status"] == "completed"
    return batch_id


def _state(client: TestClient, headers: dict, batch_id: str) -> dict[str, Any]:
    response = client.get(f"/batches/{batch_id}/enrichment", headers=headers)
    assert response.status_code == 200, response.text
    return dict(response.json())


def _by_key(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["product_key"]: item for item in state["items"]}


def _product(client: TestClient, headers: dict, product_id: str) -> dict[str, Any]:
    response = client.get(f"/products/{product_id}", headers=headers)
    assert response.status_code == 200, response.text
    return dict(response.json())


def _start(client: TestClient, headers: dict, batch_id: str, body: dict | None = None) -> Any:
    return client.post(f"/batches/{batch_id}/enrichment", json=body, headers=headers)


def _calls() -> int:
    with get_pool().connection() as conn:
        row = conn.execute(
            "SELECT count(*) AS n FROM model_runs WHERE task = 'enrichment'"
        ).fetchone()
    return int(row["n"]) if row else 0


def test_text_is_written_from_settled_facts_with_citations(
    client: TestClient,
    store: MemoryObjectStore,
    alice: dict,
    bob: dict,
    llm: FakeBackend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked = _answer(llm, monkeypatch, grounded_reply)
    batch_id = _processed_batch(client, store, alice)
    state = _state(client, alice, batch_id)
    assert state["can_generate"] == 3 and state["run"] is None
    assert all(item["state"] == "none" for item in state["items"])

    response = _start(client, alice, batch_id)
    assert response.status_code == 202, response.text
    assert response.json()["products"] == 3
    # The batch keeps its status: enrichment isn't tied to it.
    assert client.get(f"/batches/{batch_id}", headers=alice).json()["status"] == "completed"
    ran = drain()
    assert ran.count("enrich_product") == 3 and sorted(asked) == ["AK-1001", "AK-1002", "AK-1003"]
    # Prices and SKUs never reach the model.
    assert all("1299" not in r.user and "SKU" not in r.user for r in llm.requests)

    state = _state(client, alice, batch_id)
    assert state["run"]["status"] == "succeeded" and state["can_generate"] == 0
    jug = _by_key(state)["AK-1001"]
    assert (jug["state"], jug["version"], jug["title"]) == ("generated", 1, "Acme Glass jug")

    content = _product(client, alice, jug["product_id"])["generated_content"]
    assert content["version"] == 1 and content["state"] == "generated"
    assert content["description"] == "A glass jug by Acme. It is a glass jug."
    assert content["model_id"] == "openai.gpt-oss-120b-1:0"
    assert content["rules_version"] == "enrich@1"
    title = content["claims"][0]
    assert {f["code"] for f in title["facts"]} == {"brand", "product_name"}
    assert all(f["state"] == "verified" for f in title["facts"])
    capacity = next(c for c in content["claims"] if "1.5 L" in c["text"])
    assert capacity["facts"][0]["display"] == "1.5 L"
    assert content["can_generate"] is False
    # Width, colour and friends are missing, and listed as left out.
    assert {"color", "width"} <= {item["code"] for item in content["left_out"]}

    # Generating again with nothing changed has nothing to do.
    assert _start(client, alice, batch_id).json()["detail"]["code"] == "nothing_to_generate"

    # Bob can't see or start anything on Alice's batch.
    assert client.get(f"/batches/{batch_id}/enrichment", headers=bob).status_code == 404
    assert _start(client, bob, batch_id).status_code == 404
    assert client.post(f"/batches/{batch_id}/enrichment/resume", headers=bob).status_code == 404
    assert client.get(f"/products/{jug['product_id']}", headers=bob).status_code == 404


def test_fabricated_claims_are_retried_then_nothing_is_kept(
    client: TestClient,
    store: MemoryObjectStore,
    alice: dict,
    llm: FakeBackend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replies = iter([fabricating_reply, grounded_reply])
    _answer(llm, monkeypatch, lambda request: next(replies)(request))
    batch_id = _processed_batch(client, store, alice)
    jug = _by_key(_state(client, alice, batch_id))["AK-1001"]

    # First reply invents a claim; the retry is told why, and its grounded reply is kept.
    assert _start(client, alice, batch_id, {"product_ids": [jug["product_id"]]}).status_code == 202
    drain()
    assert len(llm.requests) == 2
    assert '"dishwasher safe", which no fact it cites says' in llm.requests[1].user
    content = _product(client, alice, jug["product_id"])["generated_content"]
    assert "Dishwasher" not in json.dumps(content) and content["version"] == 1

    # Both replies invent: nothing is stored, the product says why, and the run succeeds.
    llm.requests.clear()
    _answer(llm, monkeypatch, fabricating_reply)
    mug = _by_key(_state(client, alice, batch_id))["AK-1003"]
    assert _start(client, alice, batch_id, {"product_ids": [mug["product_id"]]}).status_code == 202
    drain()
    assert len(llm.requests) == 2
    state = _state(client, alice, batch_id)
    failed = _by_key(state)["AK-1003"]
    assert failed["state"] == "failed" and failed["version"] is None
    assert "didn't pass the checks" in failed["message"] and "holds 2 L" in failed["message"]
    assert state["run"]["status"] == "succeeded" and failed["can_generate"] is True


@pytest.mark.parametrize(
    "bad",
    [
        '{"title": {"text": "Acme Glass jug", "facts": ["F1", "F2"]}, "description": [',
        "Sure! Here is the product copy you asked for.",
        '{"title": "Acme Glass jug", "description": "x", "bullets": "y"}',
    ],
)
def test_malformed_or_truncated_replies_fail_the_product_not_the_run(
    client: TestClient,
    store: MemoryObjectStore,
    alice: dict,
    llm: FakeBackend,
    monkeypatch: pytest.MonkeyPatch,
    bad: str,
) -> None:
    _answer(llm, monkeypatch, lambda request: bad)
    batch_id = _processed_batch(client, store, alice)
    jug = _by_key(_state(client, alice, batch_id))["AK-1001"]
    _start(client, alice, batch_id, {"product_ids": [jug["product_id"]]})
    drain()
    state = _state(client, alice, batch_id)
    assert _by_key(state)["AK-1001"]["state"] == "failed"
    assert state["run"]["status"] == "succeeded"
    assert len(llm.requests) == 2  # one retry


def test_a_retry_after_a_lost_lease_reuses_the_stored_reply(
    client: TestClient,
    store: MemoryObjectStore,
    alice: dict,
    llm: FakeBackend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _answer(llm, monkeypatch, grounded_reply)
    batch_id = _processed_batch(client, store, alice)
    jug = _by_key(_state(client, alice, batch_id))["AK-1001"]
    _start(client, alice, batch_id, {"product_ids": [jug["product_id"]]})
    assert run_one() == "enrich_plan"

    job = claim()
    assert job is not None and job.job_type == "enrich_product"
    # Another worker takes the job over while this one is calling the model.
    with get_pool().connection() as conn:
        conn.execute("UPDATE jobs SET lease_token = gen_random_uuid() WHERE id = %s", (job.id,))
    with pytest.raises(LeaseLostError):
        get_handler("enrich_product").run(context_for(job))
    assert _calls() == 1
    with get_pool().connection() as conn:
        conn.execute(
            "UPDATE jobs SET status = 'pending', lease_token = NULL, run_after = now() "
            "WHERE id = %s",
            (job.id,),
        )
    assert drain() == ["enrich_product"]
    assert _calls() == 1 and len(llm.requests) == 1  # reused, not asked again
    assert _by_key(_state(client, alice, batch_id))["AK-1001"]["version"] == 1


def test_reaching_the_cap_pauses_the_run_and_resume_finishes_it(
    client: TestClient,
    store: MemoryObjectStore,
    alice: dict,
    llm: FakeBackend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked = _answer(llm, monkeypatch, grounded_reply)
    batch_id = _processed_batch(client, store, alice)
    # Each scripted call costs about $0.000028, so a third call would pass this cap.
    get_config()["llm"]["monthly_spend_cap_usd"] = 0.00005
    assert _start(client, alice, batch_id).status_code == 202
    ran = drain()
    assert ran.count("enrich_product") == 3
    assert len(asked) == 2

    state = _state(client, alice, batch_id)
    run = state["run"]
    assert (run["status"], run["pause_reason"], run["blocked"]) == ("paused", "llm_spend_cap", 1)
    assert state["budget_reached"] is True
    items = _by_key(state)
    assert [items[k]["state"] for k in ("AK-1001", "AK-1002", "AK-1003")] == [
        "generated", "generated", "none",
    ]  # fmt: skip
    with get_pool().connection() as conn:
        job = conn.execute(
            "SELECT status, attempts FROM jobs WHERE job_type = 'enrich_product' "
            "AND status <> 'succeeded'"
        ).fetchone()
        assert job is not None and (job["status"], job["attempts"]) == ("blocked_quota", 0)
    # A blocked job is never claimed, and doesn't keep the worker awake.
    assert run_one() is None
    with get_pool().connection() as conn:
        outstanding = conn.execute(
            "SELECT count(*) AS n FROM jobs WHERE status IN ('pending', 'running')"
        ).fetchone()
    assert outstanding is not None and outstanding["n"] == 0

    # Still over the cap: resuming and starting are refused.
    resume = client.post(f"/batches/{batch_id}/enrichment/resume", headers=alice)
    assert resume.status_code == 409 and resume.json()["detail"]["code"] == "llm_budget_reached"
    assert _start(client, alice, batch_id).json()["detail"]["code"] == "llm_budget_reached"

    # A paused run doesn't hold the active-run slot: another batch can be processed.
    other = create_batch(client, alice)
    upload(client, store, alice, other, "more.csv", SHEET)
    submit(client, alice, other)
    drain()

    get_config()["llm"]["monthly_spend_cap_usd"] = 10
    resume = client.post(f"/batches/{batch_id}/enrichment/resume", headers=alice)
    assert resume.status_code == 202, resume.text
    assert resume.json()["products"] == 1
    assert drain() == ["enrich_product"]
    assert asked[-1] == "AK-1003" and len(asked) == 3
    state = _state(client, alice, batch_id)
    assert state["run"]["status"] == "succeeded"
    assert all(item["state"] == "generated" for item in state["items"])
    again = client.post(f"/batches/{batch_id}/enrichment/resume", headers=alice)
    assert again.json()["detail"]["code"] == "not_paused"


def test_resume_is_refused_while_another_run_is_active(
    client: TestClient,
    store: MemoryObjectStore,
    alice: dict,
    llm: FakeBackend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _answer(llm, monkeypatch, grounded_reply)
    batch_id = _processed_batch(client, store, alice)
    get_config()["llm"]["monthly_spend_cap_usd"] = 0.00001
    _start(client, alice, batch_id)
    drain()
    assert _state(client, alice, batch_id)["run"]["status"] == "paused"
    get_config()["llm"]["monthly_spend_cap_usd"] = 10
    other = create_batch(client, alice)
    upload(client, store, alice, other, "more.csv", SHEET)
    submit(client, alice, other)  # queued, not yet run
    resume = client.post(f"/batches/{batch_id}/enrichment/resume", headers=alice)
    assert resume.status_code == 409 and resume.json()["detail"]["code"] == "active_run"
    assert _state(client, alice, batch_id)["run"]["status"] == "paused"


def test_a_review_decision_makes_text_out_of_date_and_regenerating_adds_a_version(
    client: TestClient,
    store: MemoryObjectStore,
    alice: dict,
    llm: FakeBackend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _answer(llm, monkeypatch, grounded_reply)
    batch_id = _processed_batch(client, store, alice)
    _start(client, alice, batch_id)
    drain()
    jug = _by_key(_state(client, alice, batch_id))["AK-1001"]

    # A person sets the capacity by hand: the text's facts have changed.
    with get_pool().connection() as conn:
        conn.execute(
            "UPDATE product_attributes pa SET state = 'reviewer_set', selected_value_id = NULL, "
            "reviewer_value = '{\"text\": \"1.6 L\"}', decided_by = 'alice' "
            "FROM attribute_definitions a WHERE a.id = pa.attribute_id AND a.code = 'capacity' "
            "AND pa.product_id = %s",
            (jug["product_id"],),
        )
    state = _state(client, alice, batch_id)
    stale = _by_key(state)["AK-1001"]
    assert stale["state"] == "out_of_date" and stale["can_generate"] and state["can_generate"] == 1
    product = _product(client, alice, jug["product_id"])
    assert product["generated_content"]["state"] == "out_of_date"
    assert product["generated_content"]["version"] == 1  # still shown until replaced

    response = _start(client, alice, batch_id, {"product_ids": [jug["product_id"]]})
    assert response.status_code == 202, response.text
    drain()
    content = _product(client, alice, jug["product_id"])["generated_content"]
    assert content["version"] == 2 and content["state"] == "generated"
    capacity = next(c for c in content["claims"] if "Capacity" in c["text"])
    assert capacity["text"] == "Capacity: 1.6 L"
    assert capacity["facts"][0]["state"] == "reviewer_set"
    with get_pool().connection() as conn:
        rows = conn.execute(
            "SELECT version, is_current FROM generated_content WHERE product_id = %s "
            "ORDER BY version",
            (jug["product_id"],),
        ).fetchall()
    assert [(r["version"], r["is_current"]) for r in rows] == [(1, False), (2, True)]


def test_a_product_without_enough_settled_facts_is_skipped_with_no_call(
    client: TestClient,
    store: MemoryObjectStore,
    alice: dict,
    llm: FakeBackend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _answer(llm, monkeypatch, grounded_reply)
    sheet = samples.table_csv([["SKU", "Capacity"], ["ZZ-9001", "2 L"]])
    batch_id = create_batch(client, alice)
    upload(client, store, alice, batch_id, "bare.csv", sheet)
    submit(client, alice, batch_id)
    drain()
    item = _state(client, alice, batch_id)["items"][0]
    assert item["can_generate"] is True  # never tried
    _start(client, alice, batch_id)
    drain()
    item = _state(client, alice, batch_id)["items"][0]
    assert item["state"] == "skipped" and "Not enough verified information" in item["message"]
    assert item["can_generate"] is False and llm.requests == []


def test_starting_needs_a_processed_batch_and_its_own_products(
    client: TestClient, store: MemoryObjectStore, alice: dict, bob: dict
) -> None:
    draft = create_batch(client, alice)
    assert _start(client, alice, draft).json()["detail"]["code"] == "batch_not_ready"
    batch_id = _processed_batch(client, store, alice)
    bobs = _processed_batch(client, store, bob)
    bob_product = _state(client, bob, bobs)["items"][0]["product_id"]
    response = _start(client, alice, batch_id, {"product_ids": [bob_product]})
    assert response.status_code == 422 and response.json()["detail"]["code"] == "not_in_batch"


def test_endpoints_refuse_signed_out_and_waitlisted_users(
    client: TestClient, store: MemoryObjectStore, alice: dict, tokens: Tokens
) -> None:
    batch_id = _processed_batch(client, store, alice)
    ensure_user("carol", "carol@example.com", status="waitlisted")
    waitlisted = tokens.headers("carol", "carol@example.com")
    paths = [
        ("get", f"/batches/{batch_id}/enrichment"),
        ("post", f"/batches/{batch_id}/enrichment"),
        ("post", f"/batches/{batch_id}/enrichment/resume"),
    ]
    for method, path in paths:
        assert getattr(client, method)(path).status_code == 401
        assert getattr(client, method)(path, headers=waitlisted).status_code == 403


def test_block_is_fenced_and_resume_needs_a_paused_run(
    client: TestClient,
    store: MemoryObjectStore,
    alice: dict,
    llm: FakeBackend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _answer(llm, monkeypatch, grounded_reply)
    batch_id = _processed_batch(client, store, alice)
    _start(client, alice, batch_id)
    assert run_one() == "enrich_plan"
    job = claim()
    assert job is not None
    with get_pool().connection() as conn:
        conn.execute("UPDATE jobs SET lease_token = gen_random_uuid() WHERE id = %s", (job.id,))
    with pytest.raises(LeaseLostError), get_pool().connection() as conn:
        queue.block(conn, job, "llm_spend_cap", "test")
    with get_pool().connection() as conn:
        assert queue.resume_run(conn, job.pipeline_run_id) is None  # running, not paused


def test_copy_is_written_automatically_at_the_end_of_processing(
    client: TestClient,
    store: MemoryObjectStore,
    alice: dict,
    llm: FakeBackend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auto_copy_on(monkeypatch)
    asked = _answer(llm, monkeypatch, grounded_reply)
    # Two calls fit the cap; the third product finds it used up during processing.
    get_config()["llm"]["monthly_spend_cap_usd"] = 0.00005
    batch_id = _processed_batch(client, store, alice)
    assert len(asked) == 2
    # Processing finishes; nothing is left paused or blocked.
    assert client.get(f"/batches/{batch_id}/run", headers=alice).json()["status"] == "succeeded"
    state = _state(client, alice, batch_id)
    assert state["run"] is None  # no separate enrich run was needed
    items = _by_key(state)
    assert [items[k]["state"] for k in ("AK-1001", "AK-1002")] == ["generated", "generated"]
    waiting = items["AK-1003"]
    assert waiting["state"] == "failed" and "budget is used up" in waiting["message"]
    assert waiting["can_generate"] is True
    with get_pool().connection() as conn:
        blocked = conn.execute(
            "SELECT count(*) AS n FROM jobs WHERE status = 'blocked_quota'"
        ).fetchone()
    assert blocked is not None and blocked["n"] == 0


def test_copy_waits_for_review_and_is_written_when_the_last_item_is_decided(
    client: TestClient,
    store: MemoryObjectStore,
    alice: dict,
    llm: FakeBackend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auto_copy_on(monkeypatch)
    asked = _answer(llm, monkeypatch, grounded_reply)
    # The spec sheet disagrees with the price list on the jug's capacity: a review item.
    llm.replies.append(
        json.dumps(
            {
                "values": [
                    {
                        "product": "AK-1001",
                        "attribute": "capacity",
                        "value": "1.2 L",
                        "quote": "Capacity: 1.2 L",
                    }
                ]
            }
        )
    )
    supplier = create_supplier(client, alice, "Spec supplier")
    batch_id = create_batch_for(client, alice, supplier)
    upload(client, store, alice, batch_id, "prices.csv", SHEET)
    upload(client, store, alice, batch_id, "specs.pdf",
           samples.text_pdf([["Glass jug", "Model AK-1001", "Capacity: 1.2 L"]]))  # fmt: skip
    submit(client, alice, batch_id)
    drain()

    # Copy was written for the products with nothing to review; the jug waits.
    assert sorted(asked) == ["AK-1002", "AK-1003"]
    jug = _by_key(_state(client, alice, batch_id))["AK-1001"]
    assert (jug["state"], jug["open_reviews"], jug["can_generate"]) == ("none", 1, True)
    assert jug["reasons"][0].startswith("Waiting for 1 review item")

    tasks = client.get(f"/batches/{batch_id}/review-tasks", headers=alice).json()["items"]
    conflict = next(t for t in tasks if t["kind"] == "attribute_conflict")
    chosen = next(v for v in conflict["values"] if v["display"] == "1.5 L")
    decision = client.post(
        f"/review-tasks/{conflict['id']}/decision",
        json={"decision": "choose", "value_id": chosen["id"]},
        headers=alice,
    )
    assert decision.status_code == 200, decision.text
    # The batch's last review item: copy for the jug is queued at once.
    assert decision.json()["copy_writing"] == "queued"
    assert drain() == ["enrich_plan", "enrich_product"]
    assert asked[-1] == "AK-1001"
    jug = _by_key(_state(client, alice, batch_id))["AK-1001"]
    assert (jug["state"], jug["reasons"]) == ("generated", [])
    content = _product(client, alice, jug["product_id"])["generated_content"]
    assert any("1.5 L" in claim["text"] for claim in content["claims"])
