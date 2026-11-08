"""Schemas for admin-only test runs."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class TestRunRequest(BaseModel):
    """A test run that does nothing except wait."""

    __test__ = False  # Stops pytest treating this class as a test.

    chunks: int = Field(1, description="How many chunks to split the run into.")
    sleep_seconds: float = Field(0, ge=0, description="How long each chunk waits.")
    fail: Literal["transient_first_attempt", "transient_always", "permanent"] | None = Field(
        None, description="Makes each chunk fail on purpose, to test recovery."
    )

    @field_validator("chunks")
    @classmethod
    def _chunks_in_range(cls, value: int) -> int:
        from app.config import get_test_run_config

        maximum = get_test_run_config()["max_chunks"]
        if not 1 <= value <= maximum:
            raise ValueError(f"chunks is {value}, and must be from 1 to {maximum}")
        return value

    @field_validator("sleep_seconds")
    @classmethod
    def _sleep_in_range(cls, value: float) -> float:
        from app.config import get_test_run_config

        maximum = get_test_run_config()["max_sleep_seconds"]
        if value > maximum:
            raise ValueError(f"sleep_seconds is {value}, and must be at most {maximum}")
        return value


class TestRunCreated(BaseModel):
    """The run that was created."""

    __test__ = False

    run_id: UUID = Field(..., description="Use this to check the run's status.")


class StageStatus(BaseModel):
    """One stage's progress."""

    stage: str = Field(..., description="Stage name.")
    status: str = Field(..., description="Stage status.")
    total_chunks: int = Field(..., description="Chunks in this stage so far.")
    completed_chunks: int = Field(..., description="Chunks that succeeded.")
    failed_chunks: int = Field(..., description="Chunks that failed.")


class TestRunStatus(BaseModel):
    """A run's progress."""

    __test__ = False

    run_id: UUID = Field(..., description="The run.")
    status: str = Field(..., description="Run status.")
    created_at: datetime = Field(..., description="When the run was submitted.")
    started_at: datetime | None = Field(None, description="When the first job was claimed.")
    finished_at: datetime | None = Field(None, description="When the run ended.")
    stages: list[StageStatus] = Field(..., description="Progress of each stage.")
    job_counts: dict[str, int] = Field(..., description="Jobs per status.")
