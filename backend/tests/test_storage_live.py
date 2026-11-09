"""A live check of the S3 upload policy, against the test/ prefix of the real bucket.

Skipped unless LIVE_STORAGE_TEST=1, with the catalog-dev user's access key in backend/.env.
It proves what the in-memory store only imitates: S3 itself refuses
an upload whose size or SHA-256 differs from the presigned policy.
"""

import hashlib
import os
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import pytest

from app.config import get_config
from app.storage.backends.s3 import S3ObjectStore

pytestmark = pytest.mark.skipif(
    os.environ.get("LIVE_STORAGE_TEST") != "1", reason="set LIVE_STORAGE_TEST=1 to use real S3"
)


def _post_form(url: str, fields: dict[str, str], data: bytes) -> int:
    boundary = uuid.uuid4().hex
    parts = []
    for name, value in fields.items():
        header = f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'
        parts.append(f"{header}{value}\r\n".encode())
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="f"\r\n'
        "Content-Type: application/octet-stream\r\n\r\n".encode()
        + data
        + f"\r\n--{boundary}--\r\n".encode()
    )
    request = urllib.request.Request(  # noqa: S310 - the URL comes from our own presign
        url,
        data=b"".join(parts),
        method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
            return int(response.status)
    except urllib.error.HTTPError as exc:
        return int(exc.code)


@pytest.fixture
def s3(monkeypatch: pytest.MonkeyPatch) -> S3ObjectStore:
    monkeypatch.setenv("STORAGE_BACKEND", "s3")
    monkeypatch.setenv("S3_PREFIX", "test")
    get_config.cache_clear()
    return S3ObjectStore()


def test_presigned_upload_enforces_size_and_checksum(s3: S3ObjectStore, tmp_path: Path) -> None:
    key = f"test/live/{uuid.uuid4()}"
    data = b"sku,name\nX1,Mug\n"
    sha256 = hashlib.sha256(data).hexdigest()
    try:
        upload = s3.presign_upload(key, "text/csv", len(data), sha256, 300)
        tampered = data.replace(b"Mug", b"Cup")
        assert _post_form(upload.url, upload.fields, tampered) in (400, 403)
        assert _post_form(upload.url, upload.fields, data + b"x") in (400, 403)
        assert s3.head(key) is None

        assert _post_form(upload.url, upload.fields, data) in (200, 204)
        info = s3.head(key)
        assert info is not None
        assert info.size_bytes == len(data)
        assert info.sha256 == sha256
        assert s3.download(key, tmp_path / "copy", len(data)) == sha256
    finally:
        s3.delete([key])
    assert s3.head(key) is None
