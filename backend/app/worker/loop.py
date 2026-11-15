"""The worker loop: claim a job, run it, and shut down safely when idle."""

import threading
import time
import uuid
from dataclasses import dataclass

import psycopg

from app.db import get_pool
from app.jobs import queue
from app.jobs.base import JobContext, get_handler
from app.jobs.errors import (
    JobInterruptedError,
    LeaseLostError,
    PermanentJobError,
    QuotaBlockedError,
    TransientJobError,
)
from app.jobs.models import Job, QueueSettings
from app.utils.logger import get_logger
from app.utils.request_id import reset_request_id, set_request_id
from app.worker import state
from app.worker.heartbeat import Heartbeat
from app.worker.power import PowerController

logger = get_logger(__name__)


@dataclass(frozen=True)
class WorkerSettings:
    """Timing and identity for one worker."""

    code_sha: str
    ec2_instance_id: str | None
    heartbeat_seconds: float
    poll_busy_seconds: float
    poll_idle_seconds: float
    busy_window_seconds: float
    idle_timeout_seconds: float
    stop_grace_seconds: float
    reaper_interval_seconds: float
    processing_enabled: bool
    heartbeat_file: str | None = None
    max_job_seconds: float = 1800


class Worker:
    """One worker process. Call run(), and request_stop() to stop it gracefully."""

    def __init__(
        self,
        settings: WorkerSettings,
        queue_settings: QueueSettings,
        power: PowerController,
    ) -> None:
        """Set up the worker. Nothing touches the database until run()."""
        self._settings = settings
        self._queue_settings = queue_settings
        self._power = power
        self._worker_id = f"worker-{uuid.uuid4()}"
        self._stop = threading.Event()
        self._stop_deadline: float | None = None
        self._heartbeat: Heartbeat | None = None

    def request_stop(self) -> None:
        """Stop claiming, and give the current job a grace period to finish."""
        if not self._stop.is_set():
            self._stop_deadline = time.monotonic() + self._settings.stop_grace_seconds
            self._stop.set()
            logger.info("Stop requested: grace_seconds=%s", self._settings.stop_grace_seconds)

    def run(self) -> None:
        """Run until stopped, or until idle shutdown powers off the machine.

        Raises:
            WorkerAlreadyRunningError: if another worker is already running.
        """
        settings = self._settings
        with get_pool().connection() as conn:
            instance_id = state.register(
                conn,
                settings.code_sha,
                settings.ec2_instance_id,
                self._queue_settings.lease_seconds,
            )
        logger.info("Worker started: worker_id=%s code_sha=%s", self._worker_id, settings.code_sha)

        self._heartbeat = Heartbeat(
            instance_id,
            settings.ec2_instance_id,
            settings.heartbeat_seconds,
            self._queue_settings,
            settings.heartbeat_file,
            settings.max_job_seconds,
        )
        self._heartbeat.start()

        powering_off = False
        try:
            powering_off = self._loop()
        finally:
            self._heartbeat.stop()
            self._mark_stopped(instance_id, powering_off)
        if powering_off:
            logger.info("Worker idle: powering off")
            self._power.power_off()

    def _loop(self) -> bool:
        """Run the main loop. Returns True if the machine should now power off."""
        settings = self._settings
        last_work = time.monotonic()
        last_reap = 0.0
        current_state = "running"

        while not self._stop.is_set():
            try:
                now = time.monotonic()
                if now - last_reap >= settings.reaper_interval_seconds:
                    self._reap()
                    last_reap = now

                with get_pool().connection() as conn:
                    paused = state.is_paused(conn)
                    wanted_state = "paused" if paused else "running"
                    if wanted_state != current_state:
                        state.set_state(conn, wanted_state)
                        current_state = wanted_state
                        logger.info("Worker state changed: state=%s", wanted_state)
                if paused:
                    # Being paused for a deploy must not count as idle.
                    last_work = time.monotonic()
                    self._stop.wait(settings.poll_busy_seconds)
                    continue

                job = self._claim() if settings.processing_enabled else None
                if job:
                    self._process(job)
                    last_work = time.monotonic()
                    continue

                idle = time.monotonic() - last_work
                if self._power.stops_machine and idle >= settings.idle_timeout_seconds:
                    with get_pool().connection() as conn:
                        if state.try_begin_shutdown(
                            conn, count_pending=settings.processing_enabled
                        ):
                            return True
                busy = idle < settings.busy_window_seconds
                self._stop.wait(settings.poll_busy_seconds if busy else settings.poll_idle_seconds)
            except psycopg.Error as exc:
                logger.warning("Database unavailable: error=%s", type(exc).__name__, exc_info=True)
                self._stop.wait(settings.poll_idle_seconds)
        return False

    def _reap(self) -> None:
        with get_pool().connection() as conn:
            recovered = queue.reap_expired(conn, self._queue_settings)
        if recovered:
            logger.warning("Expired jobs handled: count=%d", recovered)

    def _claim(self) -> Job | None:
        with get_pool().connection() as conn:
            return queue.claim_next(
                conn, self._worker_id, self._settings.code_sha, self._queue_settings
            )

    def _should_stop(self) -> bool:
        """Return whether the current job must end now."""
        assert self._heartbeat is not None  # noqa: S101
        if self._heartbeat.lease_lost():
            return True
        return self._stop_deadline is not None and time.monotonic() >= self._stop_deadline

    def _wait(self, seconds: float) -> None:
        """Sleep, waking early if the job has to end.

        Raises:
            JobInterruptedError: if the job must end before the time is up.
        """
        deadline = time.monotonic() + seconds
        while True:
            if self._should_stop():
                raise JobInterruptedError("Job interrupted while waiting")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(remaining, 0.2))

    def _process(self, job: Job) -> None:
        """Run one job, and record what happened to it."""
        assert self._heartbeat is not None  # noqa: S101
        self._heartbeat.track(job)
        token = set_request_id(job.request_id or str(job.id))
        started = time.monotonic()
        try:
            self._run_handler(job)
        except LeaseLostError:
            # Another worker owns the job now, so this one must not save anything.
            logger.warning("Result discarded because the lease was lost: job_id=%s", job.id)
        finally:
            self._heartbeat.untrack()
            logger.info(
                "Job finished processing: job_id=%s duration_ms=%.0f",
                job.id,
                (time.monotonic() - started) * 1000,
            )
            reset_request_id(token)

    def _run_handler(self, job: Job) -> None:
        """Run the handler and store the result.

        Raises:
            LeaseLostError: if this worker no longer owns the job.
        """
        settings = self._queue_settings
        context = JobContext(job=job, wait=self._wait, should_stop=self._should_stop)
        try:
            outcome = get_handler(job.job_type).run(context)
        except JobInterruptedError:
            assert self._heartbeat is not None  # noqa: S101
            if self._heartbeat.lease_lost():
                raise LeaseLostError(f"Lease lost for job {job.id}") from None
            with get_pool().connection() as conn:
                queue.release(conn, job)
        except QuotaBlockedError as exc:
            with get_pool().connection() as conn:
                queue.block(conn, job, exc.reason, str(exc))
        except (TransientJobError, psycopg.OperationalError) as exc:
            with get_pool().connection() as conn:
                queue.retry_or_fail(conn, job, exc, settings)
        except PermanentJobError as exc:
            with get_pool().connection() as conn:
                queue.fail(conn, job, exc, settings)
        except Exception as exc:  # noqa: BLE001
            # Unknown errors are bugs. Failing visibly beats retrying them blindly.
            logger.error("Handler crashed: job_id=%s", job.id, exc_info=True)
            with get_pool().connection() as conn:
                queue.fail(conn, job, exc, settings)
        else:
            with get_pool().connection() as conn:
                queue.complete(conn, job, outcome.chunks, settings)

    def _mark_stopped(self, instance_id: uuid.UUID, powering_off: bool) -> None:
        try:
            with get_pool().connection() as conn:
                state.mark_stopped(
                    conn,
                    instance_id,
                    self._settings.ec2_instance_id,
                    keep_shutting_down=powering_off,
                )
        except psycopg.Error as exc:
            # Registration replaces stale records, so this isn't fatal.
            logger.warning("Could not record worker stop: error=%s", type(exc).__name__)
