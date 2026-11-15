"""Drives a batch through the pipeline the way the browser and the worker do, for tests.

Jobs are run in this process, one at a time, as the worker would run them.
"""

import hashlib
from typing import Any

from fastapi.testclient import TestClient

import app.jobs.handlers  # noqa: F401 - registers the handlers
from app.db import get_pool
from app.jobs import queue
from app.jobs.base import JobContext, get_handler
from app.jobs.errors import PermanentJobError, QuotaBlockedError, TransientJobError
from app.jobs.models import Job
from app.jobs.settings import get_queue_settings
from app.storage.backends.memory import MemoryObjectStore


def create_supplier(client: TestClient, headers: dict[str, str], name: str = "Acme") -> str:
    return str(client.post("/suppliers", json={"name": name}, headers=headers).json()["id"])


def create_batch_for(
    client: TestClient, headers: dict[str, str], supplier_id: str, name: str = "Spring"
) -> str:
    return str(
        client.post(
            f"/suppliers/{supplier_id}/batches", json={"name": name}, headers=headers
        ).json()["id"]
    )


def claim() -> Job | None:
    """Claim the next job without running it."""
    with get_pool().connection() as conn:
        return queue.claim_next(conn, "test-worker", "test", get_queue_settings())


def context_for(job: Job) -> JobContext:
    return JobContext(job=job, wait=lambda seconds: None, should_stop=lambda: False)


def create_batch(client: TestClient, headers: dict[str, str]) -> str:
    supplier_id = client.post("/suppliers", json={"name": "Acme"}, headers=headers).json()["id"]
    return str(
        client.post(
            f"/suppliers/{supplier_id}/batches", json={"name": "Spring"}, headers=headers
        ).json()["id"]
    )


def upload(
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


def run_one() -> str | None:
    """Claim and run one job like the worker does. Returns its type, or None if none is ready."""
    settings = get_queue_settings()
    with get_pool().connection() as conn:
        job = queue.claim_next(conn, "test-worker", "test", settings)
    if job is None:
        return None
    context = JobContext(job=job, wait=lambda seconds: None, should_stop=lambda: False)
    try:
        outcome = get_handler(job.job_type).run(context)
    except QuotaBlockedError as exc:
        with get_pool().connection() as conn:
            queue.block(conn, job, exc.reason, str(exc))
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


def drain() -> list[str]:
    ran = []
    while (job_type := run_one()) is not None:
        ran.append(job_type)
    return ran


def list_files(client: TestClient, headers: dict, batch_id: str) -> dict[str, dict[str, Any]]:
    items = client.get(f"/batches/{batch_id}/files", headers=headers).json()["items"]
    return {item["original_name"]: item for item in items}


def submit(client: TestClient, headers: dict, batch_id: str) -> None:
    response = client.post(f"/batches/{batch_id}/process", headers=headers)
    assert response.status_code == 202, response.text
