"""Test jobs that do nothing but wait, to prove the platform with no product code.

noop_plan payload: {"chunks": 3, "sleep_seconds": 5, "fail": null}
noop payload:      {"sleep_seconds": 5, "fail": null}

"fail" makes a chunk misbehave on purpose:
- "transient_first_attempt": fails on its first attempt, then succeeds on the retry.
- "transient_always": fails on every attempt, until attempts run out.
- "permanent": fails once with no retry.
"""

from app.jobs.base import JobContext, register_handler
from app.jobs.errors import PermanentJobError, TransientJobError
from app.jobs.models import ChunkSpec, JobOutcome


class NoopPlanHandler:
    """Splits the test run into chunks, as a real planner job will."""

    name = "noop_plan"

    def run(self, context: JobContext) -> JobOutcome:
        """Return the chunks to add to this stage."""
        payload = context.job.payload
        chunk_payload = {
            "sleep_seconds": payload.get("sleep_seconds", 0),
            "fail": payload.get("fail"),
        }
        return JobOutcome(
            chunks=tuple(
                ChunkSpec(key=f"chunk-{index}", job_type="noop", payload=chunk_payload)
                for index in range(int(payload.get("chunks", 1)))
            )
        )


class NoopHandler:
    """Waits, then optionally fails."""

    name = "noop"

    def run(self, context: JobContext) -> JobOutcome:
        """Wait for the requested time, stopping early if interrupted."""
        payload = context.job.payload
        context.wait(float(payload.get("sleep_seconds", 0)))
        fail = payload.get("fail")
        if fail == "permanent":
            raise PermanentJobError("Requested permanent failure")
        if fail == "transient_always":
            raise TransientJobError("Requested transient failure")
        if fail == "transient_first_attempt" and context.job.attempts == 1:
            raise TransientJobError("Requested transient failure on the first attempt")
        return JobOutcome()


register_handler(NoopPlanHandler())
register_handler(NoopHandler())
