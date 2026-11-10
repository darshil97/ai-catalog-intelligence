"""An in-process store for tests. It applies the same size and checksum rules as S3."""

import hashlib
import threading
from collections.abc import Sequence
from pathlib import Path

from app.storage.base import (
    ObjectInfo,
    ObjectTooLargeError,
    PresignedUpload,
    StorageError,
    register_object_store,
)


class MemoryObjectStore:
    """Keeps objects in a dictionary. Tests upload with put(), standing in for the browser."""

    name = "memory"

    def __init__(self) -> None:
        """Start empty."""
        self.objects: dict[str, tuple[bytes, str]] = {}
        self.policies: dict[str, tuple[str, int, str]] = {}
        self.fail_next: str | None = None
        self._lock = threading.Lock()

    def _maybe_fail(self, operation: str) -> None:
        if self.fail_next == operation:
            self.fail_next = None
            raise StorageError(f"{operation}: simulated failure")

    def presign_upload(
        self, key: str, content_type: str, size_bytes: int, sha256: str, expires_seconds: int
    ) -> PresignedUpload:
        """Remember the policy, so put() can enforce it as S3 would."""
        self._maybe_fail("presign_upload")
        with self._lock:
            self.policies[key] = (content_type, size_bytes, sha256)
        return PresignedUpload(
            url="https://storage.tests.invalid/upload",
            fields={"key": key, "Content-Type": content_type, "x-amz-checksum-sha256": sha256},
        )

    def put(self, key: str, data: bytes, *, enforce_policy: bool = True) -> None:
        """Upload as a browser would. With the policy enforced, a mismatch raises ValueError."""
        with self._lock:
            policy = self.policies.get(key)
            if enforce_policy:
                if policy is None:
                    raise ValueError(f"no upload policy for {key}")
                content_type, size, sha256 = policy
                if len(data) != size or hashlib.sha256(data).hexdigest() != sha256:
                    raise ValueError("upload doesn't match its policy")
            content_type = policy[0] if policy else "application/octet-stream"
            self.objects[key] = (data, content_type)

    def presign_download(self, key: str, expires_seconds: int) -> str:
        """Return a link naming the key, so tests can see which object it points at."""
        self._maybe_fail("presign_download")
        return f"https://storage.tests.invalid/{key}?expires={expires_seconds}"

    def head(self, key: str) -> ObjectInfo | None:
        """Return size and checksum, or None."""
        self._maybe_fail("head")
        with self._lock:
            stored = self.objects.get(key)
        if stored is None:
            return None
        return ObjectInfo(size_bytes=len(stored[0]), sha256=hashlib.sha256(stored[0]).hexdigest())

    def download(self, key: str, destination: Path, max_bytes: int) -> str:
        """Write the object out, refusing one over max_bytes."""
        self._maybe_fail("download")
        with self._lock:
            stored = self.objects.get(key)
        if stored is None:
            raise StorageError(f"download: {key} is missing")
        if len(stored[0]) > max_bytes:
            raise ObjectTooLargeError(f"{key} is over {max_bytes} bytes")
        destination.write_bytes(stored[0])
        return hashlib.sha256(stored[0]).hexdigest()

    def upload(self, key: str, source: Path, content_type: str, sha256: str) -> None:
        """Store a local file after checking its checksum."""
        self._maybe_fail("upload")
        data = source.read_bytes()
        if hashlib.sha256(data).hexdigest() != sha256:
            raise StorageError("upload: checksum mismatch")
        with self._lock:
            self.objects[key] = (data, content_type)

    def delete(self, keys: Sequence[str]) -> None:
        """Remove objects, ignoring missing ones."""
        self._maybe_fail("delete")
        with self._lock:
            for key in keys:
                self.objects.pop(key, None)

    def clear(self) -> None:
        """Forget everything, between tests."""
        with self._lock:
            self.objects.clear()
            self.policies.clear()
            self.fail_next = None


register_object_store("memory", MemoryObjectStore)
