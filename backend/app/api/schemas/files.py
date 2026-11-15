"""Schemas for uploaded files and batch processing."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class UploadRequest(BaseModel):
    """A file the browser is about to upload. The browser computes the SHA-256 first."""

    name: str = Field(
        ...,
        min_length=1,
        max_length=1000,
        description="The file's name. Folders are removed; only the extension decides the type.",
        examples=["price-list.xlsx"],
    )
    size_bytes: int = Field(..., ge=0, description="Exact size in bytes.")
    sha256: str = Field(
        ...,
        pattern=r"^[0-9a-fA-F]{64}$",
        description="Hex SHA-256 of the file's contents. Storage rejects an upload that differs.",
    )

    @field_validator("sha256")
    @classmethod
    def _lowercase(cls, value: str) -> str:
        return value.lower()


class SourceFile(BaseModel):
    """An uploaded file, or a file found inside an uploaded ZIP."""

    id: UUID = Field(..., description="File ID.")
    batch_id: UUID = Field(..., description="The batch it belongs to.")
    parent_file_id: UUID | None = Field(None, description="The ZIP it came from, if any.")
    archive_member: str | None = Field(None, description="Its path inside that ZIP.")
    position: int = Field(..., description="Upload order within the batch.")
    member_index: int = Field(..., description="Order inside its ZIP. 0 for uploaded files.")
    original_name: str = Field(..., description="The name it was uploaded with, cleaned.")
    extension: str = Field(..., description="Lowercase extension, without the dot.")
    kind: str = Field(..., description="spreadsheet, pdf, image, zip, or other.")
    size_bytes: int = Field(..., description="Size in bytes.")
    sha256: str | None = Field(None, description="Hex SHA-256 of its contents.")
    status: str = Field(
        ..., description="pending_upload, uploaded, ingested (checked and kept), or rejected."
    )
    rejection_reason: str | None = Field(None, description="Why it was rejected, as a code.")
    rejection_message: str | None = Field(None, description="Why it was rejected, for people.")
    details: dict[str, Any] = Field(
        default_factory=dict, description="What the checks found, such as page counts."
    )
    upload_expires_at: datetime | None = Field(
        None, description="When an unfinished upload's link expires."
    )
    created_at: datetime = Field(..., description="When it was registered.")
    updated_at: datetime = Field(..., description="When it last changed.")


class UploadTarget(BaseModel):
    """Where to send the file: a form POST with these fields, then the file as `file`."""

    url: str = Field(..., description="The form's action URL.")
    fields: dict[str, str] = Field(..., description="Form fields to send before the file.")
    expires_at: datetime = Field(..., description="The link stops working after this.")


class UploadRegistration(BaseModel):
    """A registered file and its upload link."""

    file: SourceFile = Field(..., description="The file, waiting for its upload.")
    upload: UploadTarget = Field(..., description="Where to upload it.")
    resumed: bool = Field(
        ..., description="True if this content was already waiting, and its link was renewed."
    )


class SourceFileList(BaseModel):
    """A batch's files in upload order, each followed by what was found inside it."""

    batch_id: UUID = Field(..., description="The batch.")
    items: list[SourceFile] = Field(..., description="Files in upload order.")


class RunStage(BaseModel):
    """Progress of one stage."""

    stage: str = Field(..., description="Stage name.")
    position: int = Field(..., description="Order within the run.")
    status: str = Field(..., description="pending, running, succeeded, failed or cancelled.")
    total_chunks: int = Field(..., description="Jobs created for the stage so far.")
    completed_chunks: int = Field(..., description="Jobs finished.")
    failed_chunks: int = Field(..., description="Jobs failed.")


class RunError(BaseModel):
    """Something that went wrong during a run.

    A failed run lists the failure on our side. A run that succeeded can still list rows and
    cells that couldn't be used, each with its place in the file.
    """

    error_code: str = Field(
        ...,
        description="What kind of problem. Row and cell problems are row_unreadable, "
        "row_too_large, formula_without_value, error_value and more_row_problems.",
    )
    message: str = Field(..., description="Details.")
    source_file_id: UUID | None = Field(None, description="The file involved, if any.")
    location: dict[str, Any] | None = Field(
        None, description="Where in the file: sheet, sheet_index, row, and column for a cell."
    )
    created_at: datetime = Field(..., description="When it happened.")


class BatchRun(BaseModel):
    """The latest processing run of a batch, with what was kept and left out."""

    run_id: UUID = Field(..., description="Run ID.")
    status: str = Field(..., description="queued, running, succeeded or failed.")
    created_at: datetime = Field(..., description="When it was submitted.")
    started_at: datetime | None = Field(None, description="When the worker started it.")
    finished_at: datetime | None = Field(None, description="When it ended.")
    files_found: int | None = Field(None, description="Files found, including ZIP entries.")
    files_kept: int | None = Field(None, description="Files that passed every check.")
    files_discarded: int | None = Field(None, description="Files rejected or left out.")
    images_found: int | None = Field(None, description="Images found.")
    images_kept: int | None = Field(None, description="Images kept.")
    images_discarded: int | None = Field(None, description="Images rejected or left out.")
    products_found: int | None = Field(
        None, description="Distinct products found in the batch's spreadsheets."
    )
    products_kept: int | None = Field(
        None, description="Products processed: the first ones in file order, up to your limit."
    )
    products_discarded: int | None = Field(
        None, description="Products past your limit. Nothing is stored for them."
    )
    image_duplicate_groups: int | None = Field(
        None, description="Groups of images that show the same picture."
    )
    images_matched: int | None = Field(None, description="Images linked to a product.")
    images_to_review: int | None = Field(
        None, description="Images whose product needs a person to confirm."
    )
    images_unmatched: int | None = Field(None, description="Images with no likely product.")
    stages: list[RunStage] = Field(..., description="Stage progress, in order.")
    errors: list[RunError] = Field(
        ..., description="Failures, and rows or cells that couldn't be used, oldest first."
    )
