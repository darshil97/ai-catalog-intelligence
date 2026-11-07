"""Temporary (M0A): measures how long the API takes to reach the database."""

import hmac
import statistics
import time
from typing import Any

import psycopg
from fastapi import HTTPException

from app.config import get_diagnostics_config
from app.db import DatabaseNotConfiguredError, connect
from app.utils.logger import get_logger

logger = get_logger(__name__)


def is_authorized(token: str | None) -> bool:
    """Check the diagnostics token. With no token configured, nothing is authorized."""
    expected = get_diagnostics_config()["token"]
    if not expected or token is None:
        return False
    return hmac.compare_digest(token.encode(), expected.encode())


def measure_db_latency() -> dict[str, Any]:
    """Measure connection time and trivial query round trips.

    Returns:
        Timings in milliseconds, matching DbLatencyResponse.

    Raises:
        HTTPException: 503 if the database is unconfigured or unreachable.
    """
    samples = int(get_diagnostics_config()["latency_samples"])
    timings: list[float] = []
    try:
        started = time.perf_counter()
        with connect() as connection:
            connect_ms = (time.perf_counter() - started) * 1000
            for _ in range(samples):
                query_started = time.perf_counter()
                connection.execute("SELECT 1").fetchone()
                timings.append((time.perf_counter() - query_started) * 1000)
    except (DatabaseNotConfiguredError, psycopg.Error) as exc:
        logger.error("Latency measurement failed: error=%s", type(exc).__name__, exc_info=True)
        raise HTTPException(status_code=503, detail="Database unavailable") from exc

    logger.info(
        "Latency measured: connect_ms=%.1f, median_ms=%.1f, samples=%d",
        connect_ms,
        statistics.median(timings),
        samples,
    )
    return {
        "connect_ms": round(connect_ms, 1),
        "query_median_ms": round(statistics.median(timings), 1),
        "query_min_ms": round(min(timings), 1),
        "query_max_ms": round(max(timings), 1),
        "samples": samples,
    }
