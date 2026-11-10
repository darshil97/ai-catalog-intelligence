"""Covers the ingest pipeline end to end: submit through the API, then run the worker's jobs.

Needs TEST_DATABASE_URL. Jobs are run in this process, one at a time, as the worker would.
"""

from typing import Any

import pytest
from fastapi.testclient import TestClient

import samples
from app.db import get_pool
from app.jobs import queue
from app.jobs.base import JobContext, get_handler
from app.jobs.settings import get_queue_settings
from app.storage.backends.memory import MemoryObjectStore
from conftest import Tokens, ensure_user
from pipeline import create_batch, drain, list_files, run_one, submit, upload

pytestmark = pytest.mark.usefixtures("db")

ALICE = "alice"


@pytest.fixture
def alice(tokens: Tokens) -> dict[str, str]:
    ensure_user(ALICE, "alice@example.com")
    return tokens.headers(ALICE, "alice@example.com")


def test_mixed_batch_is_checked_and_expanded(
    client: TestClient, store: MemoryObjectStore, alice: dict
) -> None:
    batch_id = create_batch(client, alice)
    upload(client, store, alice, batch_id, "prices.csv", samples.csv())
    upload(client, store, alice, batch_id, "specs.pdf", samples.pdf(2))
    upload(client, store, alice, batch_id, "fake.png", b"\x89PNX definitely not a png")
    archive = samples.zip_of(
        {
            "photos/front.png": samples.png(color=(1, 2, 3)),
            "photos/back.jpg": samples.jpeg(),
            "photos/copy-of-prices.csv": samples.csv(),
            "inner.zip": samples.zip_of({"a.csv": b"a"}),
            "__MACOSX/._front.png": b"junk",
            "readme.txt": b"hello",
        }
    )
    zip_id = upload(client, store, alice, batch_id, "photos.zip", archive)
    submit(client, alice, batch_id)

    ran = drain()
    assert ran.count("ingest_plan") == 1 and ran.count("ingest_finalize") == 1
    assert ran.count("ingest_file") == 4

    files = list_files(client, alice, batch_id)
    assert files["prices.csv"]["status"] == "ingested"
    # A PDF also records the images kept from inside it; this one has none.
    assert files["specs.pdf"]["details"] == {"pages": 2, "images": 0, "images_over_limit": 0}
    assert files["fake.png"]["rejection_reason"] == "wrong_type"
    assert files["photos.zip"]["status"] == "ingested"
    assert files["photos.zip"]["details"] == {
        "entries": 5,
        "kept": 2,
        "rejected": 3,
        "skipped_system_files": 1,
    }
    assert files["front.png"]["parent_file_id"] == zip_id
    assert files["front.png"]["archive_member"] == "photos/front.png"
    assert files["back.jpg"]["status"] == "ingested"
    assert files["copy-of-prices.csv"]["rejection_reason"] == "duplicate_file"
    assert "prices.csv" in files["copy-of-prices.csv"]["rejection_message"]
    assert files["inner.zip"]["rejection_reason"] == "nested_archive"
    assert files["readme.txt"]["rejection_reason"] == "unsupported_type"
    derived = [key for key in store.objects if key.startswith(f"test/derived/{ALICE}/{batch_id}/")]
    assert len(derived) == 2

    run = client.get(f"/batches/{batch_id}/run", headers=alice).json()
    assert run["status"] == "succeeded"
    assert (run["files_found"], run["files_kept"], run["files_discarded"]) == (8, 4, 4)
    assert (run["images_found"], run["images_kept"], run["images_discarded"]) == (3, 2, 1)
    assert client.get(f"/batches/{batch_id}", headers=alice).json()["status"] == "completed"


def test_images_past_the_limit_are_left_out_in_order(
    client: TestClient, store: MemoryObjectStore, alice: dict
) -> None:
    batch_id = create_batch(client, alice)
    first = {f"img{i:02}.png": samples.png(color=(i, 0, 0)) for i in range(13)}
    second = {f"img{i:02}.png": samples.png(color=(i, 1, 0)) for i in range(13, 26)}
    upload(client, store, alice, batch_id, "first.zip", samples.zip_of(first))
    upload(client, store, alice, batch_id, "second.zip", samples.zip_of(second))
    submit(client, alice, batch_id)
    drain()

    files = list_files(client, alice, batch_id)
    assert files["img25.png"]["rejection_reason"] == "image_limit"
    assert all(files[f"img{i:02}.png"]["status"] == "ingested" for i in range(25))
    run = client.get(f"/batches/{batch_id}/run", headers=alice).json()
    assert (run["images_found"], run["images_kept"], run["images_discarded"]) == (26, 25, 1)


def test_unsafe_archive_is_rejected_whole(
    client: TestClient, store: MemoryObjectStore, alice: dict
) -> None:
    batch_id = create_batch(client, alice)
    upload(
        client,
        store,
        alice,
        batch_id,
        "bomb.zip",
        samples.zip_of({"zeros.csv": b"0" * (5 * 1024 * 1024)}),
    )
    submit(client, alice, batch_id)
    drain()
    files = list_files(client, alice, batch_id)
    assert list(files) == ["bomb.zip"]
    assert files["bomb.zip"]["rejection_reason"] == "zip_bomb"
    # A bad file is the user's, so the run succeeds and the submission is kept.
    run = client.get(f"/batches/{batch_id}/run", headers=alice).json()
    assert run["status"] == "succeeded"
    assert (run["files_found"], run["files_kept"], run["files_discarded"]) == (1, 0, 1)
    assert client.get("/me", headers=alice).json()["usage"]["submissions_lifetime"] == 1


def test_rerunning_a_file_job_changes_nothing(
    client: TestClient, store: MemoryObjectStore, alice: dict
) -> None:
    batch_id = create_batch(client, alice)
    archive = samples.zip_of({"a.png": samples.png(), "b.jpg": samples.jpeg()})
    upload(client, store, alice, batch_id, "photos.zip", archive)
    submit(client, alice, batch_id)
    assert run_one() == "ingest_plan"

    settings = get_queue_settings()
    with get_pool().connection() as conn:
        job = queue.claim_next(conn, "test-worker", "test", settings)
    assert job is not None and job.job_type == "ingest_file"
    context = JobContext(job=job, wait=lambda seconds: None, should_stop=lambda: False)
    handler = get_handler("ingest_file")

    def snapshot() -> dict[str, dict[str, Any]]:
        # Timestamps move on a rerun; nothing else may.
        return {
            name: {k: v for k, v in item.items() if k not in ("created_at", "updated_at")}
            for name, item in list_files(client, alice, batch_id).items()
        }

    handler.run(context)
    first = snapshot()
    handler.run(context)
    assert snapshot() == first
    assert len(first) == 3


def test_storage_outage_retries_the_job(
    client: TestClient, store: MemoryObjectStore, alice: dict
) -> None:
    batch_id = create_batch(client, alice)
    upload(client, store, alice, batch_id, "prices.csv", samples.csv())
    submit(client, alice, batch_id)
    assert run_one() == "ingest_plan"
    store.fail_next = "download"
    assert run_one() == "ingest_file"
    with get_pool().connection() as conn:
        row = conn.execute(
            "SELECT status, attempts FROM jobs WHERE job_type = 'ingest_file'"
        ).fetchone()
    assert row is not None
    assert (row["status"], row["attempts"]) == ("pending", 1)


def test_failed_run_restores_the_submission_and_can_be_retried(
    client: TestClient, store: MemoryObjectStore, alice: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    batch_id = create_batch(client, alice)
    upload(client, store, alice, batch_id, "prices.csv", samples.csv())
    submit(client, alice, batch_id)

    def crash(self: Any, context: JobContext) -> Any:
        raise RuntimeError("bug in our code")

    monkeypatch.setattr(type(get_handler("ingest_file")), "run", crash)
    drain()
    assert client.get(f"/batches/{batch_id}", headers=alice).json()["status"] == "failed"
    run = client.get(f"/batches/{batch_id}/run", headers=alice).json()
    assert run["status"] == "failed"
    assert run["errors"][0]["error_code"] == "RuntimeError"
    assert client.get("/me", headers=alice).json()["usage"]["submissions_lifetime"] == 0

    monkeypatch.undo()
    submit(client, alice, batch_id)
    drain()
    assert client.get(f"/batches/{batch_id}", headers=alice).json()["status"] == "completed"
    assert list_files(client, alice, batch_id)["prices.csv"]["status"] == "ingested"
    assert client.get("/me", headers=alice).json()["usage"]["submissions_lifetime"] == 1
