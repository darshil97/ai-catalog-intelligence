"""Covers spreadsheet extraction end to end: upload, process, then read products back.

Needs TEST_DATABASE_URL. Jobs are run in this process as the worker would run them. Storage is
in memory and the LLM is scripted, so nothing here calls AWS.
"""

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

import samples
from app.db import get_pool
from app.jobs import queue
from app.jobs.base import get_handler
from app.jobs.settings import get_queue_settings
from app.llm.backends.fake import FakeBackend
from app.storage.backends.memory import MemoryObjectStore
from conftest import Tokens, ensure_user
from pipeline import (
    claim,
    context_for,
    create_batch,
    create_batch_for,
    create_supplier,
    drain,
    run_one,
    submit,
    upload,
)

pytestmark = pytest.mark.usefixtures("db", "llm")

ALICE = "alice"
BOB = "bob"
# Limits lifted, so one user can submit several batches and keep every product.
UNLIMITED = {
    "submissions_lifetime": None,
    "submissions_per_24_hours": None,
    "max_products_per_submission": None,
}


@pytest.fixture
def alice(tokens: Tokens) -> dict[str, str]:
    ensure_user(ALICE, "alice@example.com")
    return tokens.headers(ALICE, "alice@example.com")


def _scalar(sql: str, *params: Any) -> Any:
    with get_pool().connection() as conn:
        row = conn.execute(sql, params).fetchone()
    assert row is not None
    return next(iter(row.values()))


def _products(client: TestClient, headers: dict, batch_id: str) -> list[dict[str, Any]]:
    response = client.get(f"/batches/{batch_id}/products", headers=headers)
    assert response.status_code == 200, response.text
    return list(response.json()["items"])


def _observations(client: TestClient, headers: dict, product_id: str) -> list[dict[str, Any]]:
    response = client.get(f"/products/{product_id}", headers=headers)
    assert response.status_code == 200, response.text
    return list(response.json()["observations"])


def _run(client: TestClient, headers: dict, batch_id: str) -> dict[str, Any]:
    return dict(client.get(f"/batches/{batch_id}/run", headers=headers).json())


def _sheets(client: TestClient, headers: dict, batch_id: str) -> list[dict[str, Any]]:
    return list(client.get(f"/batches/{batch_id}/sheets", headers=headers).json()["items"])


def _price_list(first: int, count: int) -> bytes:
    rows = [["SKU", "Name", "Price"]]
    rows += [[f"P-{n:03}", f"Mug {n}", f"{n}.50"] for n in range(first, first + count)]
    return samples.table_csv(rows)


def test_every_value_traces_to_its_file_sheet_row_and_column(
    client: TestClient, store: MemoryObjectStore, alice: dict, tokens: Tokens
) -> None:
    book = samples.workbook(
        {
            "Products": [
                ["Acme price list 2026"],
                [],
                ["Item No.", "Product name", "Price", "Weight (g)", "Stock", "Margin"],
                [123, "Mug, large", 12.5, 250, 40, "=C4*2"],
                ["ab-77", "Cup", 3, None, 12, None],
                [None, "No code on this row", 1, None, None, None],
                [],
                ["AB-77 ", "Cup (new photo)", 3.2, 260, None, None],
            ],
            "Notes": [["Terms", "Net 30"], ["Lead time", "2 weeks"]],
            "Archive": [["SKU", "Name"], ["OLD-1", "Retired mug"]],
        },
        formats={"Products": {"A4": "00000", "C4": "0.00", "C5": "0.00", "C8": "0.00"}},
        hidden=("Archive",),
    )
    batch_id = create_batch(client, alice)
    file_id = upload(client, store, alice, batch_id, "prices.xlsx", book)
    submit(client, alice, batch_id)
    ran = drain()
    assert ran.count("extract_select") == 1 and ran.count("extract_rows") == 2

    products = _products(client, alice, batch_id)
    assert [(p["product_key"], p["supplier_sku"], p["source_order"]) for p in products] == [
        ("00123", "00123", 1),
        ("AB-77", "ab-77", 2),
        ("OLD-1", "OLD-1", 3),
    ]
    assert [p["name"] for p in products] == ["Mug, large", "Cup", "Retired mug"]
    assert all(p["key_source"] == "sku" and p["identifier_flags"] == [] for p in products)

    # The first product: leading zeros and decimals as the sheet shows them, each with its cell.
    mug = {o["attribute_code"]: o for o in _observations(client, alice, products[0]["id"])}
    assert {code: o["raw_value"] for code, o in mug.items()} == {
        "sku": "00123",
        "product_name": "Mug, large",
        "price": "12.50",
        "net_weight": "250",
    }
    assert mug["net_weight"]["location"] == {
        "sheet": "Products",
        "sheet_index": 0,
        "row": 4,
        "column": "D",
        "header": "Weight (g)",
    }
    assert mug["price"]["source_file_id"] == file_id
    assert mug["price"]["source_file_name"] == "prices.xlsx"
    assert mug["price"]["extractor"] == "spreadsheet@1"
    assert (mug["price"]["mapped_by"], mug["price"]["confidence"]) == ("known_name", 1.0)

    # The same SKU on two rows is one product, with what each row said kept side by side.
    cup = _observations(client, alice, products[1]["id"])
    assert [
        (o["raw_value"], o["location"]["row"]) for o in cup if o["attribute_code"] == "price"
    ] == [
        ("3.00", 5),
        ("3.20", 8),
    ]
    assert [o["raw_value"] for o in cup if o["attribute_code"] == "sku"] == ["ab-77", "AB-77 "]
    assert [o["location"]["row"] for o in cup if o["attribute_code"] == "net_weight"] == [8]

    # The whole row is kept, including columns nothing was mapped to.
    with get_pool().connection() as conn:
        record = conn.execute(
            "SELECT raw_data, location FROM source_records WHERE id = %s",
            (mug["price"]["source_record_id"],),
        ).fetchone()
    assert record is not None
    assert record["location"] == {"sheet": "Products", "sheet_index": 0, "row": 4}
    assert record["raw_data"]["cells"] == {
        "A": "00123", "B": "Mug, large", "C": "12.50", "D": "250", "E": "40",
    }  # fmt: skip
    assert record["raw_data"]["flags"] == {"F": ["formula_without_value"]}
    assert record["raw_data"]["formats"] == {"A": "00000", "C": "0.00"}

    sheets = {sheet["sheet_name"]: sheet for sheet in _sheets(client, alice, batch_id)}
    main = sheets["Products"]
    assert (main["status"], main["header_row"], main["mapping_source"]) == ("used", 3, "llm_failed")
    assert {c["letter"]: c["attribute"] for c in main["columns"]} == {
        "A": "sku", "B": "product_name", "C": "price", "D": "net_weight", "E": None, "F": None,
    }  # fmt: skip
    assert (main["row_count"], main["rows_without_identifier"], main["duplicate_rows"]) == (4, 1, 1)
    assert main["source_file_name"] == "prices.xlsx"
    assert (sheets["Notes"]["status"], sheets["Notes"]["skip_reason"]) == (
        "skipped",
        "no_identifier",
    )
    assert sheets["Notes"]["skip_message"]
    assert (sheets["Archive"]["status"], sheets["Archive"]["hidden"]) == ("used", True)

    run = _run(client, alice, batch_id)
    assert run["status"] == "succeeded"
    assert (run["products_found"], run["products_kept"], run["products_discarded"]) == (3, 3, 0)
    assert client.get(f"/batches/{batch_id}", headers=alice).json()["status"] == "completed"

    # Another user sees none of it, and can't tell these IDs exist.
    ensure_user(BOB, "bob@example.com")
    bob = tokens.headers(BOB, "bob@example.com")
    for path in (
        f"/batches/{batch_id}/products",
        f"/batches/{batch_id}/sheets",
        f"/products/{products[0]['id']}",
    ):
        assert client.get(path, headers=bob).status_code == 404, path
        assert client.get(path).status_code == 401, path


@pytest.mark.parametrize(("second_file_products", "discarded"), [(2, 0), (3, 1)])
def test_only_the_first_n_products_in_file_order_are_kept(
    client: TestClient,
    store: MemoryObjectStore,
    alice: dict,
    second_file_products: int,
    discarded: int,
) -> None:
    # The limit is 5. Three products in the first file leave room for two from the second.
    batch_id = create_batch(client, alice)
    upload(client, store, alice, batch_id, "first.csv", _price_list(1, 3))
    upload(client, store, alice, batch_id, "second.csv", _price_list(4, second_file_products))
    submit(client, alice, batch_id)
    ran = drain()
    assert ran.count("extract_rows") == 2

    products = _products(client, alice, batch_id)
    assert [p["product_key"] for p in products] == ["P-001", "P-002", "P-003", "P-004", "P-005"]
    run = _run(client, alice, batch_id)
    assert (run["products_found"], run["products_kept"], run["products_discarded"]) == (
        5 + discarded,
        5,
        discarded,
    )
    # A product past the limit leaves nothing behind: no row, no values.
    assert _scalar("SELECT count(*) FROM source_records") == 5
    assert _scalar("SELECT count(*) FROM attribute_observations WHERE raw_value = 'P-006'") == 0
    assert _scalar("SELECT count(*) FROM attribute_observations") == 15


def test_large_sheet_is_read_in_chunks_and_a_bad_row_does_not_fail_its_chunk(
    client: TestClient, store: MemoryObjectStore, tokens: Tokens
) -> None:
    ensure_user(ALICE, "alice@example.com", overrides=UNLIMITED)
    alice = tokens.headers(ALICE, "alice@example.com")
    rows = [["SKU", "Name", "Notes"]]
    rows += [[f"S-{n:04}", f"Item {n}", ""] for n in range(1, 2501)]
    # Row 1502 of the file holds more text than a row may store.
    rows[1501][2] = "x" * 70_000
    batch_id = create_batch(client, alice)
    upload(client, store, alice, batch_id, "big.csv", samples.table_csv(rows))
    submit(client, alice, batch_id)
    ran = drain()
    # 2,500 data rows at 1,000 per job.
    assert ran.count("extract_rows") == 3

    run = _run(client, alice, batch_id)
    assert run["status"] == "succeeded"
    assert (run["products_found"], run["products_kept"], run["products_discarded"]) == (
        2500,
        2500,
        0,
    )
    assert _scalar("SELECT count(*) FROM products") == 2500
    # Every row but the bad one was stored, including the rest of its chunk.
    assert _scalar("SELECT count(*) FROM source_records") == 2499
    assert _scalar("SELECT count(*) FROM attribute_observations") == 2499 * 2
    assert [(e["error_code"], e["location"]) for e in run["errors"]] == [
        ("row_too_large", {"sheet": None, "sheet_index": 0, "row": 1502})
    ]
    # The bad row's product was already chosen, so it exists, with nothing read for it.
    assert (
        _scalar(
            "SELECT count(*) FROM attribute_observations o "
            "JOIN products p ON p.id = o.product_id WHERE p.product_key = 'S-1501'"
        )
        == 0
    )


def test_running_extract_jobs_again_changes_nothing(
    client: TestClient, store: MemoryObjectStore, alice: dict
) -> None:
    batch_id = create_batch(client, alice)
    upload(client, store, alice, batch_id, "prices.csv", _price_list(1, 4))
    submit(client, alice, batch_id)
    assert [run_one(), run_one(), run_one()] == ["ingest_plan", "ingest_file", "ingest_finalize"]

    def snapshot() -> str:
        # IDs and timestamps included: a rerun must not replace rows, only leave them.
        with get_pool().connection() as conn:
            tables = {
                name: conn.execute(f"SELECT * FROM {name} ORDER BY id").fetchall()
                for name in ("products", "spreadsheet_sheets", "source_records",
                             "attribute_observations")
            }  # fmt: skip
        return json.dumps(tables, default=str, sort_keys=True)

    select_job = claim()
    assert select_job is not None and select_job.job_type == "extract_select"
    outcome = get_handler("extract_select").run(context_for(select_job))
    again = get_handler("extract_select").run(context_for(select_job))
    assert outcome.chunks == again.chunks and len(outcome.chunks) == 1
    # Product IDs are new after planning twice, so compare from here on.
    with get_pool().connection() as conn:
        queue.complete(conn, select_job, outcome.chunks, get_queue_settings())

    rows_job = claim()
    assert rows_job is not None and rows_job.job_type == "extract_rows"
    get_handler("extract_rows").run(context_for(rows_job))
    first = snapshot()
    get_handler("extract_rows").run(context_for(rows_job))
    assert snapshot() == first
    assert _scalar("SELECT count(*) FROM attribute_observations") == 12


def test_llm_maps_unknown_headers_once_and_the_supplier_layout_is_remembered(
    client: TestClient, store: MemoryObjectStore, tokens: Tokens, llm: FakeBackend
) -> None:
    ensure_user(ALICE, "alice@example.com", overrides=UNLIMITED)
    alice = tokens.headers(ALICE, "alice@example.com")
    supplier_id = create_supplier(client, alice)

    def book(code: str, name: str) -> bytes:
        return samples.workbook(
            {
                "Artikel": [
                    ["Bestellnr", "Bezeichnung", "Maße", None, "Lagerbestand"],
                    [None, None, "Breite", "Höhe", None],
                    [code, name, 80, 95, 12],
                ]
            },
            merges={"Artikel": ["C1:D1", "A1:A2", "B1:B2", "E1:E2"]},
        )

    answer = {
        "header_row": 2,
        "columns": [
            {"column": "A", "attribute": "sku", "confidence": 0.97, "reason": "order number"},
            {"column": "B", "attribute": "product_name", "confidence": 0.9, "reason": "name"},
            {"column": "C", "attribute": "width", "confidence": 0.92, "reason": "Breite"},
            {"column": "D", "attribute": "height", "confidence": 0.92, "reason": "Höhe"},
            {"column": "E", "attribute": None, "confidence": 0.9, "reason": "stock level"},
        ],
    }
    # The first reply is unusable, so the model is asked once more and told why.
    llm.replies += [
        "Sure! Here is the mapping you asked for.",
        f"```json\n{json.dumps(answer)}\n```",
    ]
    first = create_batch_for(client, alice, supplier_id, "Spring")
    upload(client, store, alice, first, "artikel.xlsx", book("4711", "Becher"))
    submit(client, alice, first)
    drain()

    assert len(llm.requests) == 2
    assert "rejected" in llm.requests[1].user and "rejected" not in llm.requests[0].user
    assert "Bestellnr" in llm.requests[0].user and "Bestellnr" not in llm.requests[0].system
    sheet = _sheets(client, alice, first)[0]
    assert (sheet["mapping_source"], sheet["header_row"], sheet["header_rows"]) == ("llm", 2, 2)
    assert {c["header"]: (c["attribute"], c["source"]) for c in sheet["columns"]} == {
        "Bestellnr": ("sku", "llm"),
        "Bezeichnung": ("product_name", "llm"),
        "Maße / Breite": ("width", "llm"),
        "Maße / Höhe": ("height", "llm"),
        "Lagerbestand": (None, None),
    }
    product = _products(client, alice, first)[0]
    assert (product["product_key"], product["name"]) == ("4711", "Becher")
    width = next(
        o for o in _observations(client, alice, product["id"]) if o["attribute_code"] == "width"
    )
    # The value is the cell's, never the model's; the model only said what the column means.
    assert (width["raw_value"], width["mapped_by"], width["confidence"]) == ("80", "llm", 0.92)
    assert width["location"]["header"] == "Maße / Breite"

    with get_pool().connection() as conn:
        runs = conn.execute(
            "SELECT status, task, provider, model_id, input_tokens, cost_usd, response, error "
            "FROM model_runs ORDER BY created_at"
        ).fetchall()
    assert [r["status"] for r in runs] == ["failed", "succeeded"]
    assert runs[0]["error"] and runs[0]["response"] is None
    assert (runs[1]["task"], runs[1]["model_id"]) == ("column_mapping", "openai.gpt-oss-120b-1:0")
    assert runs[1]["response"]["header_row"] == 2 and runs[1]["cost_usd"] > 0

    # The same layout from the same supplier: no call at all, and the same mapping.
    second = create_batch_for(client, alice, supplier_id, "Summer")
    upload(client, store, alice, second, "artikel-neu.xlsx", book("4712", "Tasse"))
    submit(client, alice, second)
    drain()
    assert len(llm.requests) == 2
    again = _sheets(client, alice, second)[0]
    assert again["mapping_source"] == "saved"
    assert [c["attribute"] for c in again["columns"]] == [c["attribute"] for c in sheet["columns"]]
    assert _products(client, alice, second)[0]["product_key"] == "4712"

    # Another supplier isn't given this supplier's saved mapping. The very same file asks the
    # very same question, though, so the stored reply answers it and no new call is made.
    other = create_batch_for(client, alice, create_supplier(client, alice, "Other"), "Autumn")
    upload(client, store, alice, other, "artikel.xlsx", book("4711", "Becher"))
    submit(client, alice, other)
    drain()
    assert _sheets(client, alice, other)[0]["mapping_source"] == "llm"
    assert len(llm.requests) == 2
    assert _scalar("SELECT count(*) FROM model_runs") == 2
    assert _scalar("SELECT count(*) FROM column_mappings") == 2


def test_llm_outage_falls_back_to_known_names_and_says_so(
    client: TestClient, store: MemoryObjectStore, alice: dict, llm: FakeBackend
) -> None:
    rows = [["SKU", "Bezeichnung", "Price"], ["A-1", "Becher", "4.50"]]
    batch_id = create_batch(client, alice)
    upload(client, store, alice, batch_id, "liste.csv", samples.table_csv(rows))
    submit(client, alice, batch_id)
    drain()

    # Nothing was scripted, so every call fails as an unreachable model would.
    assert len(llm.requests) == 2
    sheet = _sheets(client, alice, batch_id)[0]
    assert (sheet["status"], sheet["mapping_source"]) == ("used", "llm_failed")
    assert "LLM" in sheet["mapping_message"]
    assert [c["attribute"] for c in sheet["columns"]] == ["sku", None, "price"]
    product = _products(client, alice, batch_id)[0]
    assert {o["attribute_code"] for o in _observations(client, alice, product["id"])} == {
        "sku", "price",
    }  # fmt: skip
    assert _run(client, alice, batch_id)["status"] == "succeeded"
    assert _scalar("SELECT count(*) FROM model_runs WHERE status = 'failed'") == 2
    assert _scalar("SELECT count(*) FROM column_mappings") == 0


def test_batch_of_images_only_completes_with_no_products(
    client: TestClient, store: MemoryObjectStore, alice: dict, llm: FakeBackend
) -> None:
    batch_id = create_batch(client, alice)
    upload(client, store, alice, batch_id, "front.png", samples.png())
    upload(client, store, alice, batch_id, "box.jpg", samples.jpeg())
    submit(client, alice, batch_id)
    ran = drain()
    assert ran.count("extract_select") == 1 and "extract_rows" not in ran

    run = _run(client, alice, batch_id)
    assert run["status"] == "succeeded"
    assert (run["products_found"], run["products_kept"], run["products_discarded"]) == (0, 0, 0)
    assert (run["images_found"], run["images_kept"]) == (2, 2)
    assert _products(client, alice, batch_id) == [] and _sheets(client, alice, batch_id) == []
    assert llm.requests == []


def test_unreadable_spreadsheet_is_reported_and_processing_again_starts_clean(
    client: TestClient, store: MemoryObjectStore, tokens: Tokens, monkeypatch: pytest.MonkeyPatch
) -> None:
    ensure_user(ALICE, "alice@example.com", overrides=UNLIMITED)
    alice = tokens.headers(ALICE, "alice@example.com")
    batch_id = create_batch(client, alice)
    # Passes the upload check, which only looks at structure, but isn't a real workbook.
    upload(client, store, alice, batch_id, "broken.xlsx", samples.xlsx())
    upload(client, store, alice, batch_id, "prices.csv", _price_list(1, 2))
    submit(client, alice, batch_id)

    def crash(self: Any, context: Any) -> Any:
        raise RuntimeError("bug in our code")

    # The first run plans the batch, then fails on our side while reading rows.
    monkeypatch.setattr(type(get_handler("extract_rows")), "run", crash)
    drain()
    assert _run(client, alice, batch_id)["status"] == "failed"
    assert len(_products(client, alice, batch_id)) == 2

    monkeypatch.undo()
    submit(client, alice, batch_id)
    drain()
    assert _run(client, alice, batch_id)["status"] == "succeeded"
    # Processed again from nothing: the same two products, not four.
    assert [p["product_key"] for p in _products(client, alice, batch_id)] == ["P-001", "P-002"]
    assert _scalar("SELECT count(*) FROM products") == 2
    assert _scalar("SELECT count(*) FROM attribute_observations") == 6
    sheets = {s["source_file_name"]: s for s in _sheets(client, alice, batch_id)}
    assert (sheets["broken.xlsx"]["status"], sheets["broken.xlsx"]["skip_reason"]) == (
        "skipped",
        "unreadable",
    )
    assert sheets["prices.csv"]["status"] == "used"
