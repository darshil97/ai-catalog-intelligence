"""Covers image processing end to end: upload, process, then read images and groups back.

Needs TEST_DATABASE_URL. Jobs run in this process as the worker would run them. Storage is in
memory and the embedder is the fake one, so nothing calls AWS.
"""

import json
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import samples
from app.config import get_config
from app.db import get_pool
from app.images import ComparisonRefusedError, compare
from app.jobs.base import get_handler
from app.storage.backends.memory import MemoryObjectStore
from app.vision import get_embedder
from conftest import Tokens, ensure_user
from pipeline import claim, context_for, create_batch, drain, run_one, submit, upload

pytestmark = pytest.mark.usefixtures("db", "llm")

ALICE = "alice"
UNLIMITED = {"submissions_lifetime": None, "submissions_per_24_hours": None}


@pytest.fixture
def alice(tokens: Tokens) -> dict[str, str]:
    ensure_user(ALICE, "alice@example.com", overrides=UNLIMITED)
    return tokens.headers(ALICE, "alice@example.com")


@pytest.fixture(autouse=True)
def embedder() -> Any:
    fake = get_embedder()
    fake.calls = 0  # type: ignore[attr-defined]
    fake.fail_next = 0  # type: ignore[attr-defined]
    return fake


def _scalar(sql: str, *params: Any) -> Any:
    with get_pool().connection() as conn:
        row = conn.execute(sql, params).fetchone()
    assert row is not None
    return next(iter(row.values()))


def _images(client: TestClient, headers: dict, batch_id: str) -> dict[str, dict[str, Any]]:
    response = client.get(f"/batches/{batch_id}/images", headers=headers)
    assert response.status_code == 200, response.text
    return {item["source_file_name"]: item for item in response.json()["items"]}


def _run(client: TestClient, headers: dict, batch_id: str) -> dict[str, Any]:
    return dict(client.get(f"/batches/{batch_id}/run", headers=headers).json())


def _upload_photos(
    client: TestClient, store: MemoryObjectStore, headers: dict, batch_id: str
) -> None:
    red_mug = samples.product_photo((190, 30, 40), "mug")
    upload(client, store, headers, batch_id, "mug.jpg", samples.jpeg_of(red_mug))
    # The same photo, smaller and re-saved: a near-duplicate.
    small = red_mug.resize((300, 240))
    upload(client, store, headers, batch_id, "mug-small.jpg", samples.jpeg_of(small, 40))
    # The same mug in blue: a different product, however alike the picture.
    blue = samples.product_photo((30, 40, 190), "mug")
    upload(client, store, headers, batch_id, "mug-blue.jpg", samples.jpeg_of(blue))
    # Something else entirely.
    upload(client, store, headers, batch_id, "ball.png", samples.png(64, 64, (20, 160, 60)))


def test_near_duplicates_are_grouped_and_colour_variants_are_not(
    client: TestClient, store: MemoryObjectStore, alice: dict, embedder: Any
) -> None:
    batch_id = create_batch(client, alice)
    _upload_photos(client, store, alice, batch_id)
    submit(client, alice, batch_id)
    ran = drain()
    assert ran.count("images_plan") == 1 and ran.count("images_group") == 1
    assert ran.count("images_process") == 1  # 4 images, 8 to a chunk

    images = _images(client, alice, batch_id)
    mug, small = images["mug.jpg"], images["mug-small.jpg"]
    assert mug["duplicate_group_id"] is not None
    assert mug["duplicate_group_id"] == small["duplicate_group_id"]
    assert (mug["duplicate_kind"], mug["group_size"]) == ("near", 2)
    # The larger copy represents the group.
    assert mug["is_representative"] and not small["is_representative"]
    assert images["mug-blue.jpg"]["duplicate_group_id"] is None
    assert images["ball.png"]["duplicate_group_id"] is None
    assert images["ball.png"]["group_size"] == 1
    assert all(item["embedded"] for item in images.values())
    assert (mug["width"], mug["height"]) == (600, 480)
    assert mug["thumbnail_url"].startswith("https://storage.tests.invalid/test/derived/")
    thumbnail_key = mug["thumbnail_url"].split("storage.tests.invalid/")[1].split("?")[0]
    assert max(Image.open(__import__("io").BytesIO(store.objects[thumbnail_key][0])).size) == 320
    assert _run(client, alice, batch_id)["image_duplicate_groups"] == 1

    # Every image is embedded once, with its model recorded and its cost counted.
    assert embedder.calls == 4
    assert _scalar("SELECT count(*) FROM image_embeddings") == 4
    assert (
        _scalar(
            "SELECT count(*) FROM model_runs WHERE task = 'image_embedding' "
            "AND status = 'succeeded' AND cost_usd = 0.00008"
        )
        == 4
    )
    assert (
        _scalar(
            "SELECT count(DISTINCT e.embedding_model_id) FROM image_embeddings e "
            "JOIN embedding_models m ON m.id = e.embedding_model_id "
            "WHERE m.name = 'amazon.titan-embed-image-v1' AND m.dimension = 1024"
        )
        == 1
    )
    timings = _scalar("SELECT timings FROM images WHERE source_file_id = %s", mug["source_file_id"])
    assert {"download", "load", "features", "thumbnail", "embed"} <= set(timings)


def test_comparisons_across_models_or_users_are_refused(
    client: TestClient, store: MemoryObjectStore, alice: dict, tokens: Tokens
) -> None:
    batch_id = create_batch(client, alice)
    _upload_photos(client, store, alice, batch_id)
    submit(client, alice, batch_id)
    drain()
    images = _images(client, alice, batch_id)
    a, b = UUID(images["mug.jpg"]["id"]), UUID(images["mug-small.jpg"]["id"])
    model = UUID(str(_scalar("SELECT id FROM embedding_models LIMIT 1")))
    with get_pool().connection() as conn:
        assert compare(conn, ALICE, a, b, model) >= 0.9
        other_model = conn.execute(
            "INSERT INTO embedding_models (provider, name, version, dimension) "
            "VALUES ('test', 'other', '1', 1024) RETURNING id"
        ).fetchone()
        assert other_model is not None
        with pytest.raises(ComparisonRefusedError):
            compare(conn, ALICE, a, b, other_model["id"])
        with pytest.raises(ComparisonRefusedError):
            compare(conn, "bob", a, b, model)
        conn.execute("DELETE FROM embedding_models WHERE id = %s", (other_model["id"],))

    ensure_user("bob", "bob@example.com")
    bob = tokens.headers("bob", "bob@example.com")
    assert client.get(f"/batches/{batch_id}/images", headers=bob).status_code == 404
    assert client.get(f"/batches/{batch_id}/images").status_code == 401


def test_images_inside_zips_and_pdfs_are_processed(
    client: TestClient, store: MemoryObjectStore, alice: dict
) -> None:
    photo = samples.jpeg_of(samples.product_photo((190, 30, 40), "box"))
    batch_id = create_batch(client, alice)
    upload(client, store, alice, batch_id, "photos.zip", samples.zip_of({"box.jpg": photo}))
    upload(
        client, store, alice, batch_id, "brochure.pdf",
        samples.text_pdf([["Brochure page"]], images={0: [photo]}),
    )  # fmt: skip
    submit(client, alice, batch_id)
    drain()
    images = _images(client, alice, batch_id)
    assert images["box.jpg"]["parent_file_name"] == "photos.zip"
    assert images["page-1-image-1.jpg"]["parent_file_name"] == "brochure.pdf"


def test_an_unreadable_image_or_embedding_outage_does_not_fail_the_batch(
    client: TestClient,
    store: MemoryObjectStore,
    alice: dict,
    embedder: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.jobs.handlers import images as images_module
    from app.vision import ImageUnreadableError

    real = images_module.load_rgb
    calls = {"n": 0}

    def flaky(path: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            raise ImageUnreadableError("bad pixels")
        return real(path)

    monkeypatch.setattr(images_module, "load_rgb", flaky)
    embedder.fail_next = 1
    batch_id = create_batch(client, alice)
    _upload_photos(client, store, alice, batch_id)
    submit(client, alice, batch_id)
    drain()

    images = _images(client, alice, batch_id)
    assert images["mug.jpg"]["status"] == "skipped"
    assert images["mug.jpg"]["skip_message"] == "This image couldn't be read."
    assert not images["mug-small.jpg"]["embedded"]
    assert "couldn't be reached" in images["mug-small.jpg"]["embedding_note"]
    assert images["mug-blue.jpg"]["embedded"]
    assert _run(client, alice, batch_id)["status"] == "succeeded"
    assert _scalar("SELECT count(*) FROM model_runs WHERE task = 'image_embedding' "
                   "AND status = 'failed'") == 1  # fmt: skip


def test_spend_cap_skips_embeddings(
    client: TestClient, store: MemoryObjectStore, alice: dict, embedder: Any
) -> None:
    get_config()["llm"]["monthly_spend_cap_usd"] = 0.0001
    with get_pool().connection() as conn:
        conn.execute(
            "INSERT INTO model_runs (owner_user_id, task, provider, model_id, input_hash, status, "
            "cost_usd) VALUES (%s, 'test', 'fake', 'm', %s, 'failed', 0.01)",
            (ALICE, "0" * 64),
        )
    batch_id = create_batch(client, alice)
    upload(client, store, alice, batch_id, "mug.jpg", samples.jpeg_of(samples.product_photo()))
    submit(client, alice, batch_id)
    drain()
    image = _images(client, alice, batch_id)["mug.jpg"]
    assert not image["embedded"] and "budget" in image["embedding_note"]
    assert image["thumbnail_url"] is not None
    assert embedder.calls == 0


def test_processing_images_again_changes_nothing(
    client: TestClient, store: MemoryObjectStore, alice: dict, embedder: Any
) -> None:
    batch_id = create_batch(client, alice)
    _upload_photos(client, store, alice, batch_id)
    submit(client, alice, batch_id)
    while (job_type := run_one()) != "images_plan":
        assert job_type is not None

    job = claim()
    assert job is not None and job.job_type == "images_process"

    def snapshot() -> str:
        with get_pool().connection() as conn:
            rows = conn.execute(
                "SELECT id, status, phash, colour_signature, width, height, thumbnail_s3_key "
                "FROM images ORDER BY id"
            ).fetchall()
            embeddings = conn.execute(
                "SELECT image_id, embedding::text FROM image_embeddings ORDER BY image_id"
            ).fetchall()
        return json.dumps([rows, embeddings], default=str, sort_keys=True)

    get_handler("images_process").run(context_for(job))
    first = snapshot()
    get_handler("images_process").run(context_for(job))
    assert snapshot() == first
    # The second run found each embedding already stored: no new call, no new charge.
    assert embedder.calls == 4
    assert _scalar("SELECT count(*) FROM model_runs WHERE task = 'image_embedding'") == 4
