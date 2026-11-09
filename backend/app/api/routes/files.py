"""File endpoints: uploading to a batch, and submitting it for processing."""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Response, status

from app.api.dependencies import ActiveUser
from app.api.schemas.files import (
    BatchRun,
    SourceFile,
    SourceFileList,
    UploadRegistration,
    UploadRequest,
)
from app.api.services import files

router = APIRouter(tags=["files"])


@router.get("/batches/{batch_id}/files", response_model=SourceFileList)
def list_files(batch_id: UUID, profile: ActiveUser) -> dict[str, Any]:
    """List a batch's files, including what was found inside ZIPs."""
    return files.list_files(profile, batch_id)


@router.post(
    "/batches/{batch_id}/files",
    response_model=UploadRegistration,
    status_code=status.HTTP_201_CREATED,
)
def register_upload(batch_id: UUID, request: UploadRequest, profile: ActiveUser) -> dict[str, Any]:
    """Register a file and get a link to upload it to. Checks type, size and batch limits.

    Then POST the returned fields and the file to the link, and call /files/{id}/complete.
    """
    return files.register_upload(
        profile, batch_id, request.name, request.size_bytes, request.sha256
    )


@router.post("/files/{file_id}/complete", response_model=SourceFile)
def complete_upload(file_id: UUID, profile: ActiveUser) -> dict[str, Any]:
    """Confirm the upload finished. Storage's size and checksum are checked again."""
    return files.complete_upload(profile, file_id)


@router.delete("/files/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_file(file_id: UUID, profile: ActiveUser) -> Response:
    """Remove a file from a batch that hasn't been processed."""
    files.delete_file(profile, file_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/batches/{batch_id}/process",
    response_model=BatchRun,
    status_code=status.HTTP_202_ACCEPTED,
)
def submit_batch(batch_id: UUID, profile: ActiveUser) -> dict[str, Any]:
    """Process the batch's uploaded files. Uses one submission from your limits."""
    return files.submit_batch(profile, batch_id)


@router.get("/batches/{batch_id}/run", response_model=BatchRun | None)
def get_latest_run(batch_id: UUID, profile: ActiveUser) -> dict[str, Any] | None:
    """Return the batch's latest processing run, or null if it was never submitted."""
    return files.get_latest_run(profile, batch_id)
