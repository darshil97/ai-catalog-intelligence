"""Covers reading PDFs and photos end to end: upload, process, then read products back.

Needs TEST_DATABASE_URL. Jobs run in this process as the worker would run them. Storage is in
memory and LLM replies are scripted, so nothing calls AWS. OCR is real.
"""

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

import samples
from app.db import get_pool
from app.jobs.base import get_handler
from app.llm.backends.fake import FakeBackend
from app.storage.backends.memory import MemoryObjectStore
from conftest import Tokens, ensure_user
from pipeline import claim, context_for, create_batch, drain, run_one, submit, upload

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


def _products(client: TestClient, headers: dict, batch_id: str) -> list[dict[str, Any]]:
    return list(client.get(f"/batches/{batch_id}/products", headers=headers).json()["items"])


def _product(client: TestClient, headers: dict, product_id: str) -> dict[str, Any]:
    response = client.get(f"/products/{product_id}", headers=headers)
    assert response.status_code == 200, response.text
    return dict(response.json())


def _documents(client: TestClient, headers: dict, batch_id: str) -> dict[str, dict[str, Any]]:
    items = client.get(f"/batches/{batch_id}/documents", headers=headers).json()["items"]
    return {item["source_file_name"]: item for item in items}


def _run(client: TestClient, headers: dict, batch_id: str) -> dict[str, Any]:
    return dict(client.get(f"/batches/{batch_id}/run", headers=headers).json())


SHEET = samples.table_csv(
    [
        ["SKU", "Name", "Capacity"],
        ["AK-1003", "Glass Water Jug", "1.5 L"],
        ["AK-1004", "Steel Bowl", "3 L"],
    ]
)


def test_pdf_values_join_spreadsheet_products_and_differences_are_kept(
    client: TestClient, store: MemoryObjectStore, alice: dict, llm: FakeBackend
) -> None:
    pdf = samples.text_pdf(
        [
            ["Glass Water Jug", "Model AK-1003", "Capacity: 1.2 L", "Height: 24 cm"],
            ["Terms and conditions", "Delivery within 14 days of order confirmation."],
        ]
    )
    llm.replies.append(
        _reply(
            ("AK-1003", "capacity", "1.2 L", "Capacity: 1.2 L"),
            ("AK-1003", "height", "24 cm", "Height: 24 cm"),
            # Not on the page: must never be stored.
            ("AK-1003", "net_weight", "610 g", "Net weight: 610 g"),
        )
    )
    batch_id = create_batch(client, alice)
    upload(client, store, alice, batch_id, "prices.csv", SHEET)
    pdf_id = upload(client, store, alice, batch_id, "specs.pdf", pdf)
    submit(client, alice, batch_id)
    ran = drain()
    assert ran.count("documents_read") == 1 and ran.count("document_values") == 1
    # Page 2 names no kept product, so only page 1 was sent to the LLM.
    assert len(llm.requests) == 1
    assert "Model AK-1003" in llm.requests[0].user and "AK-1003" in llm.requests[0].user

    jug = next(p for p in _products(client, alice, batch_id) if p["product_key"] == "AK-1003")
    detail = _product(client, alice, jug["id"])
    capacity = [o for o in detail["observations"] if o["attribute_code"] == "capacity"]
    assert sorted(o["raw_value"] for o in capacity) == ["1.2 L", "1.5 L"]
    assert detail["differing_attributes"] == ["capacity"]
    from_pdf = next(o for o in capacity if o["source_file_id"] == pdf_id)
    assert from_pdf["extractor"] == "pdf_text+llm@1"
    assert from_pdf["location"]["page"] == 1
    assert from_pdf["location"]["quote"] == "Capacity: 1.2 L"
    assert from_pdf["location"]["method"] == "text"
    assert len(from_pdf["location"]["bbox"]) == 4
    assert from_pdf["source_file_name"] == "specs.pdf"
    height = [o["raw_value"] for o in detail["observations"] if o["attribute_code"] == "height"]
    assert height == ["24 cm"]
    # No value exists without a source.
    assert _scalar("SELECT count(*) FROM attribute_observations WHERE raw_value = '610 g'") == 0
    assert (
        _scalar(
            "SELECT count(*) FROM attribute_observations "
            "WHERE source_record_id IS NULL OR location IS NULL"
        )
        == 0
    )

    document = _documents(client, alice, batch_id)["specs.pdf"]
    assert (document["status"], document["pages"], document["pages_with_text"]) == ("read", 2, 2)
    assert document["product_keys"] == ["AK-1003"]
    assert (document["values_kept"], document["values_dropped"]) == (2, 1)
    assert (document["pages_without_product"], document["llm_source"]) == (1, "llm")
    run = _run(client, alice, batch_id)
    assert run["status"] == "succeeded"
    assert [e["error_code"] for e in run["errors"]] == ["value_not_on_page"]
    assert run["errors"][0]["location"] == {"page": 1}


def test_batch_without_spreadsheet_makes_products_from_pdf_and_photo(
    client: TestClient, store: MemoryObjectStore, tokens: Tokens, llm: FakeBackend
) -> None:
    ensure_user(
        ALICE, "alice@example.com", overrides={**UNLIMITED, "max_products_per_submission": 2}
    )
    alice = tokens.headers(ALICE, "alice@example.com")
    pdf = samples.text_pdf(
        [
            ["Glass Water Jug", "Item no. AK-1003", "Capacity: 1.2 L"],
            ["Steel Bowl", "Item no. AK-1004", "Capacity: 3 L"],
        ]
    )
    photo = samples.text_image(["Ceramic Plate", "SKU AK-1005", "EAN 4006381333948"])
    llm.replies += [
        _reply(
            ("AK-1003", "sku", "AK-1003", "Item no. AK-1003"),
            ("AK-1003", "capacity", "1.2 L", "Capacity: 1.2 L"),
        ),
        _reply(
            ("AK-1004", "sku", "AK-1004", "Item no. AK-1004"),
            ("AK-1004", "capacity", "3 L", "Capacity: 3 L"),
        ),
        _reply(
            ("AK-1005", "sku", "AK-1005", "SKU AK-1005"),
            ("AK-1005", "gtin", "4006381333948", "EAN 4006381333948"),
        ),
    ]
    batch_id = create_batch(client, alice)
    upload(client, store, alice, batch_id, "catalogue.pdf", pdf)
    upload(client, store, alice, batch_id, "plate.jpg", photo)
    submit(client, alice, batch_id)
    drain()

    # Every page was read, since codes only show up by reading; the third product is past the
    # limit of 2, so nothing is stored for it.
    assert len(llm.requests) == 3
    assert "photo plate.jpg, read by OCR" in llm.requests[2].user
    products = _products(client, alice, batch_id)
    assert [(p["product_key"], p["source_order"], p["key_source"]) for p in products] == [
        ("AK-1003", 1, "sku"),
        ("AK-1004", 2, "sku"),
    ]
    run = _run(client, alice, batch_id)
    assert (run["products_found"], run["products_kept"], run["products_discarded"]) == (3, 2, 1)
    assert _scalar("SELECT count(*) FROM attribute_observations WHERE raw_value = 'AK-1005'") == 0
    bowl = _product(client, alice, products[1]["id"])
    assert {o["attribute_code"]: o["location"]["page"] for o in bowl["observations"]} == {
        "sku": 2,
        "capacity": 2,
    }
    documents = _documents(client, alice, batch_id)
    assert documents["plate.jpg"]["kind"] == "image" and documents["plate.jpg"]["pages_ocr"] == 1
    assert documents["catalogue.pdf"]["product_keys"] == ["AK-1003", "AK-1004"]
    assert documents["plate.jpg"]["product_keys"] == []


def test_photos_only_batch_is_read_by_ocr(
    client: TestClient, store: MemoryObjectStore, alice: dict, llm: FakeBackend
) -> None:
    llm.replies.append(
        _reply(
            ("AK-1003", "model_number", "AK-1003", "Model AK-1003"),
            ("AK-1003", "capacity", "1.2 L", "Capacity 1.2 L"),
        )
    )
    batch_id = create_batch(client, alice)
    upload(
        client, store, alice, batch_id, "box.jpg",
        samples.text_image(["Model AK-1003", "Capacity 1.2 L"]),
    )  # fmt: skip
    upload(client, store, alice, batch_id, "blank.png", samples.png(200, 200, (255, 255, 255)))
    submit(client, alice, batch_id)
    drain()

    product = _products(client, alice, batch_id)[0]
    assert (product["product_key"], product["key_source"]) == ("AK-1003", "model_number")
    capacity = next(
        o for o in _product(client, alice, product["id"])["observations"]
        if o["attribute_code"] == "capacity"
    )  # fmt: skip
    assert capacity["extractor"] == "ocr+llm@1" and capacity["raw_value"] == "1.2 L"
    assert capacity["location"]["page"] is None and capacity["location"]["method"] == "ocr"
    assert 0.5 <= capacity["confidence"] <= 1
    documents = _documents(client, alice, batch_id)
    assert (documents["blank.png"]["status"], documents["blank.png"]["skip_reason"]) == (
        "skipped",
        "no_text",
    )
    assert _scalar("SELECT count(*) FROM ocr_results WHERE engine = 'rapidocr'") == 2


def test_scanned_and_broken_pdfs(
    client: TestClient, store: MemoryObjectStore, alice: dict, llm: FakeBackend
) -> None:
    scanned = samples.text_pdf([[]], scanned={0: ["Spec sheet AK-1003", "Height: 24 cm"]})
    llm.replies.append(_reply(("AK-1003", "height", "24 cm", "Height: 24 cm")))
    batch_id = create_batch(client, alice)
    upload(client, store, alice, batch_id, "prices.csv", SHEET)
    upload(client, store, alice, batch_id, "scan.pdf", scanned)
    submit(client, alice, batch_id)
    drain()

    documents = _documents(client, alice, batch_id)
    assert (documents["scan.pdf"]["pages_ocr"], documents["scan.pdf"]["values_kept"]) == (1, 1)
    # The picture inside the scan is also kept as an image of the batch, and read too.
    assert documents["page-1-image-1.jpg"]["kind"] == "image"
    assert _run(client, alice, batch_id)["status"] == "succeeded"
    jug = next(p for p in _products(client, alice, batch_id) if p["product_key"] == "AK-1003")
    height = next(
        o for o in _product(client, alice, jug["id"])["observations"]
        if o["attribute_code"] == "height"
    )  # fmt: skip
    assert (height["extractor"], height["location"]["method"]) == ("ocr+llm@1", "ocr")


def test_pdf_images_are_files_of_the_batch_under_the_image_limit(
    client: TestClient, store: MemoryObjectStore, tokens: Tokens
) -> None:
    ensure_user(ALICE, "alice@example.com", overrides={**UNLIMITED, "max_images_per_submission": 2})
    alice = tokens.headers(ALICE, "alice@example.com")
    logo = samples.jpeg(400, 300, (10, 20, 30))
    photo = samples.jpeg(300, 300, (200, 10, 10))
    pdf = samples.text_pdf(
        [["Page one"], ["Page two"]],
        images={0: [logo, samples.jpeg(20, 20)], 1: [logo, photo]},
    )
    batch_id = create_batch(client, alice)
    upload(client, store, alice, batch_id, "front.png", samples.png(200, 200))
    upload(client, store, alice, batch_id, "brochure.pdf", pdf)
    submit(client, alice, batch_id)
    drain()

    files = {
        f["original_name"]: f
        for f in client.get(f"/batches/{batch_id}/files", headers=alice).json()["items"]
    }
    assert files["brochure.pdf"]["details"]["images"] == 2
    assert files["page-1-image-1.jpg"]["parent_file_id"] == files["brochure.pdf"]["id"]
    assert files["page-1-image-1.jpg"]["status"] == "ingested"
    # Upload order decides: the photo uploaded first, then the PDF's images in page order.
    assert files["page-2-image-1.jpg"]["rejection_reason"] == "image_limit"
    run = _run(client, alice, batch_id)
    assert (run["images_found"], run["images_kept"], run["images_discarded"]) == (3, 2, 1)


def test_broken_pdf_page_text_is_reported_not_fatal(
    client: TestClient, store: MemoryObjectStore, alice: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.ingest.documents import pdf as pdf_module
    from app.ingest.documents.pdf import PdfUnreadableError

    real = pdf_module.text_page

    def flaky(page: Any, number: int) -> Any:
        if number == 2:
            raise PdfUnreadableError("bad content stream")
        return real(page, number)

    monkeypatch.setattr("app.jobs.handlers.documents.text_page", flaky)
    batch_id = create_batch(client, alice)
    upload(
        client, store, alice, batch_id, "specs.pdf",
        samples.text_pdf([["Page one text, long enough to be read"], ["Two"], ["Three"]]),
    )  # fmt: skip
    submit(client, alice, batch_id)
    drain()
    document = _documents(client, alice, batch_id)["specs.pdf"]
    assert (document["status"], document["pages"], document["pages_unreadable"]) == ("read", 3, 1)
    run = _run(client, alice, batch_id)
    assert run["status"] == "succeeded"
    assert ("page_unreadable", {"page": 2}) in [
        (e["error_code"], e["location"]) for e in run["errors"]
    ]


def test_spend_cap_skips_llm_steps_and_says_so(
    client: TestClient, store: MemoryObjectStore, alice: dict, llm: FakeBackend
) -> None:
    from app.config import get_config

    # A tiny cap that an earlier call this month has already passed. Config is reloaded after
    # every test, so this change ends with it.
    get_config()["llm"]["monthly_spend_cap_usd"] = 0.0001
    with get_pool().connection() as conn:
        conn.execute(
            "INSERT INTO model_runs (owner_user_id, task, provider, model_id, input_hash, status, "
            "cost_usd) VALUES (%s, 'test', 'fake', 'm', %s, 'failed', 0.01)",
            (ALICE, "0" * 64),
        )
    sheet = samples.table_csv([["SKU", "Bezeichnung"], ["AK-1003", "Becher"]])
    pdf = samples.text_pdf([["Model AK-1003", "Capacity: 1.2 L"]])
    batch_id = create_batch(client, alice)
    upload(client, store, alice, batch_id, "liste.csv", sheet)
    upload(client, store, alice, batch_id, "specs.pdf", pdf)
    submit(client, alice, batch_id)
    drain()

    assert llm.requests == []
    sheet_row = client.get(f"/batches/{batch_id}/sheets", headers=alice).json()["items"][0]
    assert sheet_row["mapping_source"] == "llm_budget" and "budget" in sheet_row["mapping_message"]
    document = _documents(client, alice, batch_id)["specs.pdf"]
    assert document["llm_source"] == "llm_budget" and "budget" in document["llm_message"]
    assert _run(client, alice, batch_id)["status"] == "succeeded"


def test_reading_values_again_changes_nothing(
    client: TestClient, store: MemoryObjectStore, alice: dict, llm: FakeBackend
) -> None:
    pdf = samples.text_pdf([["Model AK-1003", "Capacity: 1.2 L"]])
    llm.replies.append(_reply(("AK-1003", "capacity", "1.2 L", "Capacity: 1.2 L")))
    batch_id = create_batch(client, alice)
    upload(client, store, alice, batch_id, "specs.pdf", pdf)
    upload(client, store, alice, batch_id, "prices.csv", SHEET)
    submit(client, alice, batch_id)
    while (job_type := run_one()) != "documents_read":
        assert job_type is not None

    job = claim()
    assert job is not None and job.job_type == "document_values"
    handler = get_handler("document_values")

    def snapshot() -> str:
        with get_pool().connection() as conn:
            rows = conn.execute(
                "SELECT product_id, attribute_id, raw_value, location, extractor, model_run_id "
                "FROM attribute_observations ORDER BY raw_value, extractor"
            ).fetchall()
            documents = conn.execute(
                "SELECT values_kept, values_dropped, product_keys, llm_source FROM document_files"
            ).fetchall()
        return json.dumps([rows, documents], default=str, sort_keys=True)

    handler.run(context_for(job))
    first = snapshot()
    # The second run reuses the stored reply: no new call, and the same values.
    handler.run(context_for(job))
    assert snapshot() == first
    assert len(llm.requests) == 1


def test_documents_are_private(client: TestClient, alice: dict, tokens: Tokens) -> None:
    batch_id = create_batch(client, alice)
    ensure_user("bob", "bob@example.com")
    bob = tokens.headers("bob", "bob@example.com")
    assert client.get(f"/batches/{batch_id}/documents", headers=bob).status_code == 404
    assert client.get(f"/batches/{batch_id}/documents").status_code == 401
    assert client.get(f"/batches/{batch_id}/documents", headers=alice).json()["items"] == []
