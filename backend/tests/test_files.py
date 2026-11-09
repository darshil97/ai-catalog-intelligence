"""Covers uploading files to a batch and submitting it, including cross-user access.

Needs TEST_DATABASE_URL. Storage is the in-memory store, which enforces the same upload policy
S3 does (exact size and checksum).
"""

import hashlib
import threading
from typing import Any

import pytest
from fastapi.testclient import TestClient

import samples
from app.storage.backends.memory import MemoryObjectStore
from conftest import Tokens, ensure_user

pytestmark = pytest.mark.usefixtures("db")

ALICE = "alice"
BOB = "bob"
MB = 1024 * 1024


@pytest.fixture
def alice(tokens: Tokens) -> dict[str, str]:
    ensure_user(ALICE, "alice@example.com")
    return tokens.headers(ALICE, "alice@example.com")


@pytest.fixture
def bob(tokens: Tokens) -> dict[str, str]:
    ensure_user(BOB, "bob@example.com")
    return tokens.headers(BOB, "bob@example.com")


def _batch(client: TestClient, headers: dict[str, str], supplier: str = "Acme") -> str:
    supplier_id = client.post("/suppliers", json={"name": supplier}, headers=headers).json()["id"]
    response = client.post(
        f"/suppliers/{supplier_id}/batches", json={"name": "Spring"}, headers=headers
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def _register(
    client: TestClient,
    headers: dict[str, str],
    batch_id: str,
    name: str,
    data: bytes,
    size: int | None = None,
) -> Any:
    return client.post(
        f"/batches/{batch_id}/files",
        json={
            "name": name,
            "size_bytes": len(data) if size is None else size,
            "sha256": hashlib.sha256(data).hexdigest(),
        },
        headers=headers,
    )


def _upload(
    client: TestClient,
    store: MemoryObjectStore,
    headers: dict[str, str],
    batch_id: str,
    name: str,
    data: bytes,
) -> dict[str, Any]:
    """Register, upload and complete a file, as the browser does."""
    registered = _register(client, headers, batch_id, name, data)
    assert registered.status_code == 201, registered.text
    body = registered.json()
    store.put(body["upload"]["fields"]["key"], data)
    completed = client.post(f"/files/{body['file']['id']}/complete", headers=headers)
    assert completed.status_code == 200, completed.text
    return dict(completed.json())


def _files(client: TestClient, headers: dict[str, str], batch_id: str) -> list[dict[str, Any]]:
    response = client.get(f"/batches/{batch_id}/files", headers=headers)
    assert response.status_code == 200, response.text
    return list(response.json()["items"])


def _code(response: Any) -> str:
    return str(response.json()["detail"]["code"])


def _sql(statement: str, *params: Any) -> None:
    from app.db import get_pool

    with get_pool().connection() as conn:
        conn.execute(statement, params)


def test_upload_lifecycle(client: TestClient, store: MemoryObjectStore, alice: dict) -> None:
    batch_id = _batch(client, alice)
    data = samples.csv()
    registered = _register(client, alice, batch_id, "folder/Price List.CSV", data)
    assert registered.status_code == 201
    body = registered.json()
    file = body["file"]
    assert file["status"] == "pending_upload"
    assert file["original_name"] == "Price List.CSV"
    assert file["extension"] == "csv" and file["kind"] == "spreadsheet"
    assert body["resumed"] is False
    key = body["upload"]["fields"]["key"]
    assert key == f"test/raw/{ALICE}/{batch_id}/{file['id']}"
    # The upload policy fixes the type, whatever the browser would have sent.
    assert body["upload"]["fields"]["Content-Type"] == "text/csv"

    store.put(key, data)
    completed = client.post(f"/files/{file['id']}/complete", headers=alice)
    assert completed.status_code == 200
    assert completed.json()["status"] == "uploaded"
    # Completing again is harmless.
    assert (
        client.post(f"/files/{file['id']}/complete", headers=alice).json()["status"] == "uploaded"
    )
    assert [item["id"] for item in _files(client, alice, batch_id)] == [file["id"]]


@pytest.mark.parametrize(
    ("name", "data", "size", "code"),
    [
        ("notes.docx", b"docx", None, "unsupported_type"),
        ("no-extension", b"data", None, "unsupported_type"),
        (".csv", b"data", None, "unsupported_type"),
        ("empty.csv", b"", None, "empty_file"),
        ("huge.pdf", b"%PDF-", 10 * MB + 1, "file_too_large"),
    ],
)
def test_refused_files(
    client: TestClient, alice: dict, name: str, data: bytes, size: int | None, code: str
) -> None:
    batch_id = _batch(client, alice)
    response = _register(client, alice, batch_id, name, data, size)
    assert response.status_code == 422
    assert _code(response) == code
    assert response.json()["detail"]["message"]
    assert _files(client, alice, batch_id) == []


def test_invalid_hash_is_a_validation_error(client: TestClient, alice: dict) -> None:
    batch_id = _batch(client, alice)
    response = client.post(
        f"/batches/{batch_id}/files",
        json={"name": "a.csv", "size_bytes": 3, "sha256": "not-a-hash"},
        headers=alice,
    )
    assert response.status_code == 422


def test_sixth_file_is_refused(client: TestClient, store: MemoryObjectStore, alice: dict) -> None:
    batch_id = _batch(client, alice)
    for index in range(5):
        _upload(client, store, alice, batch_id, f"f{index}.csv", samples.csv(index + 1))
    response = _register(client, alice, batch_id, "f5.csv", samples.csv(9))
    assert response.status_code == 422
    assert _code(response) == "too_many_files"


def test_batch_total_over_the_limit_is_refused(client: TestClient, alice: dict) -> None:
    batch_id = _batch(client, alice)
    for index in range(2):
        response = _register(client, alice, batch_id, f"big{index}.pdf", bytes([index]), 10 * MB)
        assert response.status_code == 201
    response = _register(client, alice, batch_id, "big2.pdf", b"\x02", 6 * MB)
    assert response.status_code == 422
    assert _code(response) == "submission_too_large"


def test_limits_follow_user_overrides(client: TestClient, tokens: Tokens) -> None:
    ensure_user("carol", overrides={"files_per_submission": 1, "max_file_bytes": None})
    headers = tokens.headers("carol", "carol@example.com")
    batch_id = _batch(client, headers)
    # No per-file limit now, but the 25 MB batch total still applies.
    assert _code(_register(client, headers, batch_id, "big.pdf", b"x", 26 * MB)) == (
        "submission_too_large"
    )
    assert _register(client, headers, batch_id, "a.pdf", b"x", 11 * MB).status_code == 201
    assert _code(_register(client, headers, batch_id, "b.pdf", b"y", 1)) == "too_many_files"


def test_same_content_twice_is_refused(
    client: TestClient, store: MemoryObjectStore, alice: dict
) -> None:
    batch_id = _batch(client, alice)
    _upload(client, store, alice, batch_id, "prices.csv", samples.csv())
    response = _register(client, alice, batch_id, "copy of prices.csv", samples.csv())
    assert response.status_code == 409
    assert _code(response) == "duplicate_file"
    assert "prices.csv" in response.json()["detail"]["message"]


def test_same_content_in_another_batch_is_fine(
    client: TestClient, store: MemoryObjectStore, alice: dict
) -> None:
    first = _batch(client, alice, "Acme")
    second = _batch(client, alice, "Beta")
    _upload(client, store, alice, first, "prices.csv", samples.csv())
    _upload(client, store, alice, second, "prices.csv", samples.csv())


def test_registering_an_unfinished_upload_again_renews_it(client: TestClient, alice: dict) -> None:
    batch_id = _batch(client, alice)
    first = _register(client, alice, batch_id, "prices.csv", samples.csv()).json()
    again = _register(client, alice, batch_id, "prices (1).csv", samples.csv())
    assert again.status_code == 201
    assert again.json()["resumed"] is True
    assert again.json()["file"]["id"] == first["file"]["id"]
    assert len(_files(client, alice, batch_id)) == 1


def test_complete_before_the_upload_arrives(client: TestClient, alice: dict) -> None:
    batch_id = _batch(client, alice)
    file_id = _register(client, alice, batch_id, "a.csv", samples.csv()).json()["file"]["id"]
    response = client.post(f"/files/{file_id}/complete", headers=alice)
    assert response.status_code == 409
    assert _code(response) == "upload_missing"
    assert _files(client, alice, batch_id)[0]["status"] == "pending_upload"


def test_upload_that_differs_from_its_registration_is_rejected(
    client: TestClient, store: MemoryObjectStore, alice: dict
) -> None:
    batch_id = _batch(client, alice)
    body = _register(client, alice, batch_id, "a.csv", samples.csv()).json()
    key = body["upload"]["fields"]["key"]
    # Only possible if the bucket's policy failed; the API must not trust it blindly.
    store.put(key, b"something else", enforce_policy=False)
    response = client.post(f"/files/{body['file']['id']}/complete", headers=alice)
    assert response.status_code == 200
    assert response.json()["status"] == "rejected"
    assert response.json()["rejection_reason"] == "upload_mismatch"
    assert key not in store.objects


def test_storage_outage_while_completing(
    client: TestClient, store: MemoryObjectStore, alice: dict
) -> None:
    batch_id = _batch(client, alice)
    file_id = _register(client, alice, batch_id, "a.csv", samples.csv()).json()["file"]["id"]
    store.fail_next = "head"
    response = client.post(f"/files/{file_id}/complete", headers=alice)
    assert response.status_code == 503
    assert _code(response) == "storage_unavailable"


def test_abandoned_upload_stops_counting(
    client: TestClient, store: MemoryObjectStore, alice: dict
) -> None:
    batch_id = _batch(client, alice)
    abandoned = _register(client, alice, batch_id, "lost.csv", samples.csv(1)).json()["file"]
    _sql(
        "UPDATE source_files SET upload_expires_at = now() - interval '1 minute' WHERE id = %s",
        abandoned["id"],
    )

    listed = _files(client, alice, batch_id)[0]
    assert listed["status"] == "rejected"
    assert listed["rejection_reason"] == "upload_not_completed"
    # The same content can be uploaded again, as a new file.
    again = _register(client, alice, batch_id, "lost.csv", samples.csv(1))
    assert again.status_code == 201
    assert again.json()["resumed"] is False
    # Four more fit beside it, because the abandoned one no longer counts.
    for index in range(4):
        _upload(client, store, alice, batch_id, f"f{index}.csv", samples.csv(index + 2))
    assert _code(_register(client, alice, batch_id, "f9.csv", samples.csv(9))) == "too_many_files"
    late = client.post(f"/files/{abandoned['id']}/complete", headers=alice)
    assert late.status_code == 409
    assert _code(late) == "file_not_pending"


def test_delete_file_removes_its_object(
    client: TestClient, store: MemoryObjectStore, alice: dict
) -> None:
    batch_id = _batch(client, alice)
    file = _upload(client, store, alice, batch_id, "a.csv", samples.csv())
    assert len(store.objects) == 1
    assert client.delete(f"/files/{file['id']}", headers=alice).status_code == 204
    assert store.objects == {}
    assert _files(client, alice, batch_id) == []


def test_submit_queues_the_batch_and_charges_once(
    client: TestClient, store: MemoryObjectStore, alice: dict
) -> None:
    batch_id = _batch(client, alice)
    assert client.get(f"/batches/{batch_id}/run", headers=alice).json() is None
    _upload(client, store, alice, batch_id, "a.csv", samples.csv())
    pending = _register(client, alice, batch_id, "b.csv", samples.csv(7)).json()["file"]

    response = client.post(f"/batches/{batch_id}/process", headers=alice)
    assert response.status_code == 202, response.text
    run = response.json()
    assert run["status"] == "queued"
    assert [stage["stage"] for stage in run["stages"]] == ["ingest", "finalize"]
    assert client.get(f"/batches/{batch_id}", headers=alice).json()["status"] == "queued"
    assert client.get(f"/batches/{batch_id}/run", headers=alice).json()["run_id"] == run["run_id"]
    assert client.get("/me", headers=alice).json()["usage"]["submissions_lifetime"] == 1
    # The unfinished upload was given up rather than processed.
    statuses = {item["id"]: item["status"] for item in _files(client, alice, batch_id)}
    assert statuses[pending["id"]] == "rejected"

    again = client.post(f"/batches/{batch_id}/process", headers=alice)
    assert again.status_code == 409
    assert _code(again) == "batch_not_editable"
    assert client.get("/me", headers=alice).json()["usage"]["submissions_lifetime"] == 1


def test_files_cannot_change_while_processing(
    client: TestClient, store: MemoryObjectStore, alice: dict
) -> None:
    batch_id = _batch(client, alice)
    file = _upload(client, store, alice, batch_id, "a.csv", samples.csv())
    assert client.post(f"/batches/{batch_id}/process", headers=alice).status_code == 202
    assert (
        _code(_register(client, alice, batch_id, "b.csv", samples.csv(4))) == "batch_not_editable"
    )
    assert _code(client.delete(f"/files/{file['id']}", headers=alice)) == "batch_not_editable"
    response = client.delete(f"/batches/{batch_id}", headers=alice)
    assert response.status_code == 409
    assert _code(response) == "batch_busy"


def test_deleting_a_batch_removes_its_objects(
    client: TestClient, store: MemoryObjectStore, alice: dict
) -> None:
    batch_id = _batch(client, alice)
    _upload(client, store, alice, batch_id, "a.csv", samples.csv())
    _upload(client, store, alice, batch_id, "b.png", samples.png())
    assert client.delete(f"/batches/{batch_id}", headers=alice).status_code == 204
    assert store.objects == {}


def test_submit_with_no_files(client: TestClient, alice: dict) -> None:
    batch_id = _batch(client, alice)
    response = client.post(f"/batches/{batch_id}/process", headers=alice)
    assert response.status_code == 422
    assert _code(response) == "no_files"


def test_submission_limit(client: TestClient, store: MemoryObjectStore, tokens: Tokens) -> None:
    ensure_user("dave", overrides={"submissions_lifetime": 0})
    headers = tokens.headers("dave", "dave@example.com")
    batch_id = _batch(client, headers)
    _upload(client, store, headers, batch_id, "a.csv", samples.csv())
    response = client.post(f"/batches/{batch_id}/process", headers=headers)
    assert response.status_code == 429
    assert _code(response) == "limit_reached"
    # Nothing was queued, so the batch can still be changed.
    assert client.get(f"/batches/{batch_id}", headers=headers).json()["status"] == "draft"
    assert client.get(f"/batches/{batch_id}/run", headers=headers).json() is None


def test_one_active_run_per_user(client: TestClient, store: MemoryObjectStore, alice: dict) -> None:
    first = _batch(client, alice, "Acme")
    second = _batch(client, alice, "Beta")
    _upload(client, store, alice, first, "a.csv", samples.csv())
    _upload(client, store, alice, second, "b.csv", samples.csv(5))
    assert client.post(f"/batches/{first}/process", headers=alice).status_code == 202
    response = client.post(f"/batches/{second}/process", headers=alice)
    assert response.status_code == 409
    assert _code(response) == "active_run"
    assert client.get("/me", headers=alice).json()["usage"]["submissions_lifetime"] == 1


def test_simultaneous_submissions_charge_once(
    client: TestClient, store: MemoryObjectStore, alice: dict
) -> None:
    batch_id = _batch(client, alice)
    _upload(client, store, alice, batch_id, "a.csv", samples.csv())
    statuses: list[int] = []

    def submit() -> None:
        statuses.append(client.post(f"/batches/{batch_id}/process", headers=alice).status_code)

    threads = [threading.Thread(target=submit) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(statuses) == [202, 409, 409]
    assert client.get("/me", headers=alice).json()["usage"]["submissions_lifetime"] == 1


def test_emergency_stop_refuses_submissions(
    client: TestClient, store: MemoryObjectStore, alice: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    batch_id = _batch(client, alice)
    _upload(client, store, alice, batch_id, "a.csv", samples.csv())
    monkeypatch.setenv("PROCESSING_ENABLED", "false")
    from app.config import get_config

    get_config.cache_clear()
    response = client.post(f"/batches/{batch_id}/process", headers=alice)
    assert response.status_code == 503
    assert _code(response) == "processing_disabled"


def test_every_endpoint_hides_other_users_batches_and_files(
    client: TestClient, store: MemoryObjectStore, alice: dict, bob: dict
) -> None:
    batch_id = _batch(client, alice)
    file = _upload(client, store, alice, batch_id, "a.csv", samples.csv())
    data = samples.png()
    attempts = [
        client.get(f"/batches/{batch_id}/files", headers=bob),
        _register(client, bob, batch_id, "b.png", data),
        client.post(f"/files/{file['id']}/complete", headers=bob),
        client.delete(f"/files/{file['id']}", headers=bob),
        client.post(f"/batches/{batch_id}/process", headers=bob),
        client.get(f"/batches/{batch_id}/run", headers=bob),
    ]
    assert [response.status_code for response in attempts] == [404] * 6
    assert len(_files(client, alice, batch_id)) == 1
    assert client.get(f"/batches/{batch_id}", headers=alice).json()["status"] == "draft"


def test_archive_entries_cannot_be_deleted_alone(
    client: TestClient, store: MemoryObjectStore, alice: dict
) -> None:
    batch_id = _batch(client, alice)
    archive = _upload(
        client, store, alice, batch_id, "photos.zip", samples.zip_of({"a.png": samples.png()})
    )
    child_id = "00000000-0000-0000-0000-000000000001"
    _sql(
        "INSERT INTO source_files (id, batch_id, owner_user_id, parent_file_id, archive_member, "
        "position, member_index, s3_key, original_name, extension, kind, content_type, "
        "size_bytes, sha256, status) VALUES (%s, %s, %s, %s, 'a.png', 1, 1, 'k', 'a.png', 'png', "
        "'image', 'image/png', 1, %s, 'ingested')",
        child_id,
        batch_id,
        ALICE,
        archive["id"],
        "a" * 64,
    )
    response = client.delete(f"/files/{child_id}", headers=alice)
    assert response.status_code == 409
    assert _code(response) == "archive_entry"
