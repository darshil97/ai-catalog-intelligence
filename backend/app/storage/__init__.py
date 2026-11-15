"""Object storage for uploaded and derived files, shared by the API and worker.

boto3 loads only when an S3 call is first made, so importing this costs the API nothing.
"""

from app.storage.base import (
    ObjectInfo,
    ObjectStore,
    ObjectTooLargeError,
    PresignedUpload,
    StorageError,
    derived_key,
    export_key,
    get_object_store,
    raw_key,
    register_object_store,
)

__all__ = [
    "ObjectInfo",
    "ObjectStore",
    "ObjectTooLargeError",
    "PresignedUpload",
    "StorageError",
    "derived_key",
    "export_key",
    "get_object_store",
    "raw_key",
    "register_object_store",
]
