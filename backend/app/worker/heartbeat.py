"""A background thread that keeps this worker's lease and heartbeat alive."""

import threading
import time
from pathlib import Path
from uuid import UUID

import psycopg

from app.db import get_pool
from app.jobs import queue
from app.jobs.models import Job, QueueSettings
from app.utils.logger import get_logger
from app.worker import state

logger = get_logger(__name__)


class Heartbeat(threading.Thread):
    """Renews the current job's lease and records the worker as alive."""

    def __init__(
        self,
        instance_id: UUID,
        ec2_instance_id: str | None,
        interval_seconds: float,
        settings: QueueSettings,
        heartbeat_file: str | None,
    ) -> None:
        """Set up the thread. Call start() to begin."""
        super().__init__(name="heartbeat", daemon=True)
        self._instance_id = instance_id
        self._ec2_instance_id = ec2_instance_id
        self._interval = interval_seconds
        self._settings = settings
        self._heartbeat_file = Path(heartbeat_file) if heartbeat_file else None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._job: Job | None = None
        self._lease_lost = threading.Event()
        self._last_renewed = time.monotonic()

    def track(self, job: Job) -> None:
        """Start renewing this job's lease."""
        with self._lock:
            self._job = job
            self._lease_lost.clear()
            self._last_renewed = time.monotonic()

    def untrack(self) -> None:
        """Stop renewing the current job's lease."""
        with self._lock:
            self._job = None

    def lease_lost(self) -> bool:
        """Return whether the current job's lease was lost."""
        return self._lease_lost.is_set()

    def stop(self) -> None:
        """Stop the thread, and wait for it to finish."""
        self._stop_event.set()
        self.join(timeout=self._interval + 5)

    def run(self) -> None:
        """Beat until stopped."""
        while not self._stop_event.wait(self._interval):
            self.beat()

    def beat(self) -> None:
        """Send one heartbeat. A database failure is tolerated until the lease would expire."""
        with self._lock:
            job = self._job
        try:
            with get_pool().connection() as conn:
                state.heartbeat(conn, self._instance_id, self._ec2_instance_id)
                if job and not queue.renew_lease(conn, job, self._settings):
                    logger.warning("Lease lost: job_id=%s", job.id)
                    self._lease_lost.set()
            self._last_renewed = time.monotonic()
            # The watchdog checks this file's age, so it only updates after a successful beat.
            if self._heartbeat_file:
                self._heartbeat_file.touch()
        except psycopg.Error as exc:
            logger.warning("Heartbeat failed: error=%s", type(exc).__name__)
            if job and time.monotonic() - self._last_renewed > self._settings.lease_seconds:
                logger.warning("Lease assumed lost after failed heartbeats: job_id=%s", job.id)
                self._lease_lost.set()
