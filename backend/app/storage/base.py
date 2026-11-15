"""The ObjectStore contract, its registry, and how keys are laid out in the bucket.

Every key sits under the environment's prefix:
    {prefix}/raw/{owner}/{batch}/{file}      files as the user uploaded them
    {prefix}/derived/{owner}/{batch}/{file}  files the worker extracted, such as ZIP entries
Keys never contain user-supplied names, so a filename can't escape its folder.
"""

import base64
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import UUID

from app.config import get_storage_config
from app.utils.logger import get_logger

logger = get_logger(__name__)

_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


class StorageError(Exception):
    """Storage couldn't be reached or refused a request. Usually worth retrying."""


class ObjectTooLargeError(Exception):
    """An object is bigger than the caller allowed, so it wasn't read in full."""


@dataclass(frozen=True)
class ObjectInfo:
    """What storage knows about a stored object, without its contents.

    Attributes:
        size_bytes: The object's size.
        sha256: Lowercase hex SHA-256 that storage verified on upload, or None if it has none.
    """

    size_bytes: int
    sha256: str | None


@dataclass(frozen=True)
class PresignedUpload:
    """A browser form upload. Every field must be sent, followed by the file itself.

    Attributes:
        url: Where the browser POSTs the form.
        fields: Form fields, in order. They pin the key, size, type and checksum.
    """

    url: str
    fields: dict[str, str]


class ObjectStore(Protocol):
    """Stores files. Implementations must be safe to share between threads."""

    name: str

    def presign_upload(
        self, key: str, content_type: str, size_bytes: int, sha256: str, expires_seconds: int
    ) -> PresignedUpload:
        """Return a form upload that only accepts exactly this size, type and checksum."""
        ...

    def presign_download(
        self, key: str, expires_seconds: int, *, save_as: str | None = None
    ) -> str:
        """Return a link that lets a browser read this one object until it expires.

        With save_as, the browser saves the object under that name instead of showing it.

        Raises:
            StorageError: if the link can't be made.
        """
        ...

    def head(self, key: str) -> ObjectInfo | None:
        """Return the object's size and checksum, or None if it doesn't exist.

        Raises:
            StorageError: if storage can't be reached.
        """
        ...

    def download(self, key: str, destination: Path, max_bytes: int) -> str:
        """Write the object to a local file, and return its lowercase hex SHA-256.

        Raises:
            ObjectTooLargeError: if it's bigger than max_bytes. The partial file is removed.
            StorageError: if storage can't be reached or the object is missing.
        """
        ...

    def upload(self, key: str, source: Path, content_type: str, sha256: str) -> None:
        """Store a local file. Storage checks it against the checksum.

        Raises:
            StorageError: if storage can't be reached or rejects the file.
        """
        ...

    def delete(self, keys: Sequence[str]) -> None:
        """Delete objects. Missing ones are ignored.

        Raises:
            StorageError: if storage can't be reached.
        """
        ...


_REGISTRY: dict[str, Callable[[], ObjectStore]] = {}
_INSTANCES: dict[str, ObjectStore] = {}


def register_object_store(name: str, factory: Callable[[], ObjectStore]) -> None:
    """Make a storage backend available under a name."""
    _REGISTRY[name] = factory


def get_object_store() -> ObjectStore:
    """Return the configured backend, created once.

    Raises:
        ValueError: if storage.backend names no registered backend.
    """
    import app.storage.backends  # noqa: F401 - registers the backends

    name = get_storage_config()["backend"]
    if name not in _REGISTRY:
        raise ValueError(
            f"Unknown storage backend: {name!r}. Registered: {', '.join(sorted(_REGISTRY))}"
        )
    if name not in _INSTANCES:
        _INSTANCES[name] = _REGISTRY[name]()
        logger.info("Object store ready: backend=%s", name)
    return _INSTANCES[name]


def _segment(value: str | UUID) -> str:
    text = str(value)
    if not _SAFE_SEGMENT.match(text):
        raise ValueError(f"Unsafe storage key segment: {text!r}")
    return text


def _key(area: str, owner: str, batch_id: UUID, file_id: UUID) -> str:
    prefix = get_storage_config()["prefix"]
    return "/".join([prefix, area, _segment(owner), _segment(batch_id), _segment(file_id)])


def raw_key(owner: str, batch_id: UUID, file_id: UUID) -> str:
    """Return the key for a file the user uploads.

    Raises:
        ValueError: if an ID contains characters that aren't safe in a key.
    """
    return _key("raw", owner, batch_id, file_id)


def derived_key(owner: str, batch_id: UUID, file_id: UUID) -> str:
    """Return the key for a file the worker produces.

    Raises:
        ValueError: if an ID contains characters that aren't safe in a key.
    """
    return _key("derived", owner, batch_id, file_id)


_EXPORT_NAME = re.compile(r"^[a-z0-9-]{1,80}\.(csv|xlsx)$")


def export_key(owner: str, batch_id: UUID, export_id: UUID, filename: str) -> str:
    """Return the key for an export the API builds (D41), named for the person's download.

    Raises:
        ValueError: if an ID or the filename isn't safe in a key.
    """
    if not _EXPORT_NAME.match(filename):
        raise ValueError(f"Unsafe export filename: {filename!r}")
    prefix = get_storage_config()["prefix"]
    parts = [prefix, "exports", _segment(owner), _segment(batch_id), _segment(export_id)]
    return "/".join([*parts, filename])


def hex_to_base64(sha256: str) -> str:
    """Convert a hex SHA-256 to the base64 form S3 uses."""
    return base64.b64encode(bytes.fromhex(sha256)).decode()


def base64_to_hex(sha256: str) -> str:
    """Convert S3's base64 SHA-256 to lowercase hex."""
    return base64.b64decode(sha256).hex()
