"""Amazon S3. boto3 loads on first use, keeping it out of the API's memory until needed.

Uploads are form POSTs whose policy pins the exact size, the content type and the SHA-256, so
S3 itself rejects anything else. The browser never gets broader access than that one key.
"""

import hashlib
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from app.config import get_storage_config
from app.storage.base import (
    ObjectInfo,
    ObjectTooLargeError,
    PresignedUpload,
    StorageError,
    base64_to_hex,
    hex_to_base64,
    register_object_store,
)
from app.utils.logger import get_logger

logger = get_logger(__name__)

_DELETE_BATCH = 1000
_CHUNK_BYTES = 1024 * 1024


class S3ObjectStore:
    """Stores objects in the configured bucket."""

    name = "s3"

    def __init__(self) -> None:
        """Read settings. The client is created on first use."""
        config = get_storage_config()
        self._bucket: str = config["bucket"]
        self._config = config
        self._client: Any = None
        self._lock = threading.Lock()

    def _s3(self) -> Any:
        with self._lock:
            if self._client is None:
                import boto3
                from botocore.config import Config

                self._client = boto3.client(
                    "s3",
                    region_name=self._config["region"],
                    config=Config(
                        connect_timeout=int(self._config["connect_timeout_seconds"]),
                        read_timeout=int(self._config["read_timeout_seconds"]),
                        retries={"max_attempts": 3, "mode": "standard"},
                        signature_version="s3v4",
                        # Regional URLs, so a new bucket works before global DNS catches up.
                        s3={"addressing_style": "virtual"},
                    ),
                    endpoint_url=f"https://s3.{self._config['region']}.amazonaws.com",
                )
            return self._client

    def presign_upload(
        self, key: str, content_type: str, size_bytes: int, sha256: str, expires_seconds: int
    ) -> PresignedUpload:
        """Return a form upload pinned to this key, size, type and checksum."""
        checksum = hex_to_base64(sha256)
        fields = {
            "Content-Type": content_type,
            "x-amz-checksum-algorithm": "SHA256",
            "x-amz-checksum-sha256": checksum,
        }
        conditions: list[Any] = [
            {"Content-Type": content_type},
            {"x-amz-checksum-algorithm": "SHA256"},
            {"x-amz-checksum-sha256": checksum},
            ["content-length-range", size_bytes, size_bytes],
        ]
        try:
            response = self._s3().generate_presigned_post(
                Bucket=self._bucket,
                Key=key,
                Fields=fields,
                Conditions=conditions,
                ExpiresIn=expires_seconds,
            )
        except Exception as exc:
            raise _storage_error("presign_upload", exc) from exc
        return PresignedUpload(url=response["url"], fields=dict(response["fields"]))

    def head(self, key: str) -> ObjectInfo | None:
        """Return the object's size and verified checksum, or None if it's missing."""
        from botocore.exceptions import ClientError

        try:
            response = self._s3().head_object(Bucket=self._bucket, Key=key, ChecksumMode="ENABLED")
        except ClientError as exc:
            # Without list permission, S3 reports a missing key as 403 rather than 404.
            if exc.response.get("Error", {}).get("Code") in {"404", "403", "NoSuchKey"}:
                return None
            raise _storage_error("head", exc) from exc
        except Exception as exc:
            raise _storage_error("head", exc) from exc
        checksum = response.get("ChecksumSHA256")
        # A checksum ending in "-N" covers multipart parts, not the whole object.
        sha256 = base64_to_hex(checksum) if checksum and "-" not in checksum else None
        return ObjectInfo(size_bytes=int(response["ContentLength"]), sha256=sha256)

    def download(self, key: str, destination: Path, max_bytes: int) -> str:
        """Stream the object to a file, stopping as soon as it passes max_bytes."""
        digest = hashlib.sha256()
        written = 0
        try:
            body = self._s3().get_object(Bucket=self._bucket, Key=key)["Body"]
            with destination.open("wb") as out:
                for chunk in body.iter_chunks(_CHUNK_BYTES):
                    written += len(chunk)
                    if written > max_bytes:
                        raise ObjectTooLargeError(f"{key} is over {max_bytes} bytes")
                    digest.update(chunk)
                    out.write(chunk)
        except ObjectTooLargeError:
            destination.unlink(missing_ok=True)
            raise
        except Exception as exc:
            destination.unlink(missing_ok=True)
            raise _storage_error("download", exc) from exc
        return digest.hexdigest()

    def upload(self, key: str, source: Path, content_type: str, sha256: str) -> None:
        """Store a file, with S3 verifying the checksum."""
        try:
            with source.open("rb") as body:
                self._s3().put_object(
                    Bucket=self._bucket,
                    Key=key,
                    Body=body,
                    ContentType=content_type,
                    ChecksumSHA256=hex_to_base64(sha256),
                )
        except Exception as exc:
            raise _storage_error("upload", exc) from exc

    def delete(self, keys: Sequence[str]) -> None:
        """Delete objects in batches of 1000. Missing ones are ignored by S3."""
        unique = sorted(set(keys))
        for start in range(0, len(unique), _DELETE_BATCH):
            batch = unique[start : start + _DELETE_BATCH]
            try:
                response = self._s3().delete_objects(
                    Bucket=self._bucket,
                    Delete={"Objects": [{"Key": key} for key in batch], "Quiet": True},
                )
            except Exception as exc:
                raise _storage_error("delete", exc) from exc
            errors = response.get("Errors") or []
            if errors:
                raise StorageError(
                    f"delete failed for {len(errors)} objects: {errors[0].get('Code')}"
                )


def _storage_error(operation: str, exc: Exception) -> StorageError:
    logger.warning("Storage call failed: operation=%s error=%s", operation, type(exc).__name__)
    return StorageError(f"{operation}: {type(exc).__name__}: {exc}")


register_object_store("s3", S3ObjectStore)
