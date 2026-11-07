"""Data contracts for the job queue."""

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

# Raise JOB_CONTRACT_VERSION when job payloads change. Keep the minimum at the oldest version
# this code can still run, so jobs created by a newer API wait instead of failing.
JOB_CONTRACT_VERSION = 1
MIN_SUPPORTED_CONTRACT = 1


@dataclass(frozen=True)
class Job:
    """A claimed job, including the lease that proves this worker owns it."""

    id: UUID
    pipeline_run_id: UUID
    stage_run_id: UUID
    owner_user_id: str
    job_type: str
    payload: dict[str, Any]
    attempts: int
    max_attempts: int
    lease_token: UUID
    request_id: str | None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "Job":
        """Build a Job from a row of the jobs table."""
        return cls(**{name: row[name] for name in cls.__dataclass_fields__})


@dataclass(frozen=True)
class ChunkSpec:
    """A chunk of work a handler wants added to its stage."""

    key: str
    job_type: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class JobOutcome:
    """What a handler produced. Chunks are created atomically with the job's completion."""

    chunks: tuple[ChunkSpec, ...] = ()


@dataclass(frozen=True)
class StageSpec:
    """One stage of a pipeline, and the job that starts it."""

    stage: str
    job_type: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class QueueSettings:
    """Timing and retry settings for the queue."""

    lease_seconds: int
    max_attempts: int
    backoff_base_seconds: int
    backoff_max_seconds: int

    def backoff_seconds(self, attempts: int) -> int:
        """Return the delay before a retry, doubling per attempt up to the maximum."""
        delay: int = self.backoff_base_seconds * 2 ** max(attempts - 1, 0)
        return min(delay, self.backoff_max_seconds)


@dataclass(frozen=True)
class RunStatus:
    """A pipeline run's progress, for status endpoints."""

    run: dict[str, Any]
    stages: list[dict[str, Any]]
    job_counts: dict[str, int]
