"""Covers the ingest pipeline end to end: submit through the API, then run the worker's jobs.

Needs TEST_DATABASE_URL. Jobs are run in this process, one at a time, as the worker would.
"""

import hashlib
from typing import Any

import pytest
from fastapi.testclient import TestClient

import app.jobs.handlers  # noqa: F401 - registers the handlers
import samples
from app.db import get_pool
from app.jobs import queue
from app.jobs.base import JobContext, get_handler
from app.jobs.errors import PermanentJobError, TransientJobError
from app.jobs.settings import get_queue_settings
from app.storage.backends.memory import MemoryObjectStore
from conftest import Tokens, ensure_user

pytestmark = pytest.mark.usefixtures("db")

ALICE = "alice"


@pytest.fixture
def alice(tokens: Tokens) -> dict[str, str]:
    ensure_user(ALICE, "alice@example.com")
    return tokens.headers(ALICE, "alice@example.com")


def _batch(client: TestClient, headers: dict[str, str]) -> str:
    supplier_id = client.post("/suppliers", json={"name": "Acme"}, headers=headers).json()["id"]
    return str(
        client.post(
            f"/suppliers/{supplier_id}/batches", json={"name": "Spring"}, headers=headers
        ).json()["id"]
    )


def _upload(
    client: TestClient,
    store: MemoryObjectStore,
    headers: dict,
    batch_id: str,
    name: str,
    data: bytes,
) -> str:
    body = client.post(
        f"/batches/{batch_id}/files",
        json={"name": name, "size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()},
        headers=headers,
    ).json()
    store.put(body["upload"]["fields"]["key"], data)
    assert client.post(f"/files/{body['file']['id']}/complete", headers=headers).status_code == 200
    return str(body["file"]["id"])


def _run_one() -> str | None:
    """Claim and run one job like the worker does. Returns its type, or None if none is ready."""
    settings = get_queue_settings()
    with get_pool().connection() as conn:
        job = queue.claim_next(conn, "test-worker", "test", settings)
    if job is None:
        return None
    context = JobContext(job=job, wait=lambda seconds: None, should_stop=lambda: False)
    try:
        outcome = get_handler(job.job_type).run(context)
    except TransientJobError as exc:
        with get_pool().connection() as conn:
            queue.retry_or_fail(conn, job, exc, settings)
    except (PermanentJobError, Exception) as exc:  # noqa: BLE001
        with get_pool().connection() as conn:
            queue.fail(conn, job, exc, settings)
    else:
        with get_pool().connection() as conn:
            queue.complete(conn, job, outcome.chunks, settings)
    return job.job_type


def _drain() -> list[str]:
    ran = []
    while (job_type := _run_one()) is not None:
        ran.append(job_type)
    return ran


def _files(client: TestClient, headers: dict, batch_id: str) -> dict[str, dict[str, Any]]:
    items = client.get(f"/batches/{batch_id}/files", headers=headers).json()["items"]
    return {item["original_name"]: item for item in items}


def _submit(client: TestClient, headers: dict, batch_id: str) -> None:
    response = client.post(f"/batches/{batch_id}/process", headers=headers)
    assert response.status_code == 202, response.text


def test_mixed_batch_is_checked_and_expanded(
    client: TestClient, store: MemoryObjectStore, alice: dict
) -> None:
    batch_id = _batch(client, alice)
    _upload(client, store, alice, batch_id, "prices.csv", samples.csv())
    _upload(client, store, alice, batch_id, "specs.pdf", samples.pdf(2))
    _upload(client, store, alice, batch_id, "fake.png", b"\x89PNX definitely not a png")
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
    zip_id = _upload(client, store, alice, batch_id, "photos.zip", archive)
    _submit(client, alice, batch_id)

    ran = _drain()
    assert ran.count("ingest_plan") == 1 and ran.count("ingest_finalize") == 1
    assert ran.count("ingest_file") == 4

    files = _files(client, alice, batch_id)
    assert files["prices.csv"]["status"] == "ingested"
    assert files["specs.pdf"]["details"] == {"pages": 2}
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
    batch_id = _batch(client, alice)
    first = {f"img{i:02}.png": samples.png(color=(i, 0, 0)) for i in range(13)}
    second = {f"img{i:02}.png": samples.png(color=(i, 1, 0)) for i in range(13, 26)}
    _upload(client, store, alice, batch_id, "first.zip", samples.zip_of(first))
    _upload(client, store, alice, batch_id, "second.zip", samples.zip_of(second))
    _submit(client, alice, batch_id)
    _drain()

    files = _files(client, alice, batch_id)
    assert files["img25.png"]["rejection_reason"] == "image_limit"
    assert all(files[f"img{i:02}.png"]["status"] == "ingested" for i in range(25))
    run = client.get(f"/batches/{batch_id}/run", headers=alice).json()
    assert (run["images_found"], run["images_kept"], run["images_discarded"]) == (26, 25, 1)


def test_unsafe_archive_is_rejected_whole(
    client: TestClient, store: MemoryObjectStore, alice: dict
) -> None:
    batch_id = _batch(client, alice)
    _upload(
        client,
        store,
        alice,
        batch_id,
        "bomb.zip",
        samples.zip_of({"zeros.csv": b"0" * (5 * 1024 * 1024)}),
    )
    _submit(client, alice, batch_id)
    _drain()
    files = _files(client, alice, batch_id)
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
    batch_id = _batch(client, alice)
    archive = samples.zip_of({"a.png": samples.png(), "b.jpg": samples.jpeg()})
    _upload(client, store, alice, batch_id, "photos.zip", archive)
    _submit(client, alice, batch_id)
    assert _run_one() == "ingest_plan"

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
            for name, item in _files(client, alice, batch_id).items()
        }

    handler.run(context)
    first = snapshot()
    handler.run(context)
    assert snapshot() == first
    assert len(first) == 3


def test_storage_outage_retries_the_job(
    client: TestClient, store: MemoryObjectStore, alice: dict
) -> None:
    batch_id = _batch(client, alice)
    _upload(client, store, alice, batch_id, "prices.csv", samples.csv())
    _submit(client, alice, batch_id)
    assert _run_one() == "ingest_plan"
    store.fail_next = "download"
    assert _run_one() == "ingest_file"
    with get_pool().connection() as conn:
        row = conn.execute(
            "SELECT status, attempts FROM jobs WHERE job_type = 'ingest_file'"
        ).fetchone()
    assert row is not None
    assert (row["status"], row["attempts"]) == ("pending", 1)


def test_failed_run_restores_the_submission_and_can_be_retried(
    client: TestClient, store: MemoryObjectStore, alice: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    batch_id = _batch(client, alice)
    _upload(client, store, alice, batch_id, "prices.csv", samples.csv())
    _submit(client, alice, batch_id)

    def crash(self: Any, context: JobContext) -> Any:
        raise RuntimeError("bug in our code")

    monkeypatch.setattr(type(get_handler("ingest_file")), "run", crash)
    _drain()
    assert client.get(f"/batches/{batch_id}", headers=alice).json()["status"] == "failed"
    run = client.get(f"/batches/{batch_id}/run", headers=alice).json()
    assert run["status"] == "failed"
    assert run["errors"][0]["error_code"] == "RuntimeError"
    assert client.get("/me", headers=alice).json()["usage"]["submissions_lifetime"] == 0

    monkeypatch.undo()
    _submit(client, alice, batch_id)
    _drain()
    assert client.get(f"/batches/{batch_id}", headers=alice).json()["status"] == "completed"
    assert _files(client, alice, batch_id)["prices.csv"]["status"] == "ingested"
    assert client.get("/me", headers=alice).json()["usage"]["submissions_lifetime"] == 1
