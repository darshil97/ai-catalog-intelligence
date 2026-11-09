"""Per-user POC limits and the append-only usage ledger.

Functions take an open connection and run inside the caller's transaction, so a submission's
ledger entry commits together with the job it pays for, or not at all.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

from psycopg import Connection

from app.config import get_limits_config, get_valid_limit_names
from app.db import query
from app.utils.logger import get_logger

logger = get_logger(__name__)

KIND_SUBMISSION = "submission"

# Limits the ledger enforces at submit time. The others are enforced where the work happens.
SUBMISSION_LIMITS = ("submissions_lifetime", "submissions_per_24_hours")


class LimitExceededError(Exception):
    """A per-user limit would be exceeded.

    Attributes:
        limit: The limit's name, as in config.yaml.
        allowed: The user's effective value for it.
        used: How much the user has already used.
    """

    def __init__(self, limit: str, allowed: int, used: int) -> None:
        super().__init__(f"{limit}: used {used} of {allowed}")
        self.limit = limit
        self.allowed = allowed
        self.used = used


@dataclass(frozen=True)
class UsageSummary:
    """A user's effective limits and current usage.

    Attributes:
        limits: Limit name to value. None means unlimited.
        used: Limit name to usage, for the limits the ledger tracks.
    """

    limits: dict[str, int | None]
    used: dict[str, int]


def effective_limits(overrides: dict[str, Any] | None) -> dict[str, int | None]:
    """Combine config defaults with a user's overrides.

    An override of null means unlimited. Unknown names and invalid values are ignored with a
    warning, so a typo in user_limits can't make a limit disappear or crash a request.
    """
    limits: dict[str, int | None] = dict(get_limits_config())
    valid = get_valid_limit_names()
    for name, value in (overrides or {}).items():
        if name not in valid:
            logger.warning("Limit override ignored: reason=unknown_name name=%s", name)
        elif value is None:
            limits[name] = None
        elif isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            limits[name] = value
        else:
            logger.warning("Limit override ignored: reason=invalid_value name=%s", name)
    return limits


def _summary(conn: Connection[Any], user_id: str) -> UsageSummary:
    row = conn.execute(query("usage", "get_limits_and_usage"), {"user_id": user_id}).fetchone()
    assert row is not None  # noqa: S101 - an aggregate always returns one row
    return UsageSummary(
        limits=effective_limits(row["overrides"]),
        used={name: int(row[name]) for name in SUBMISSION_LIMITS},
    )


def get_usage_summary(conn: Connection[Any], user_id: str) -> UsageSummary:
    """Return a user's effective limits and submission usage, in one query."""
    return _summary(conn, user_id)


def record_submission(
    conn: Connection[Any], user_id: str, pipeline_run_id: UUID | None = None
) -> UUID:
    """Charge one submission, if the user's limits allow it.

    Locks the user's profile row first, so simultaneous submissions are checked one after the
    other. Call inside the transaction that creates the job, so both commit or neither does.

    Returns:
        The ledger entry's ID, needed to restore it after a platform failure.

    Raises:
        LimitExceededError: if a submission limit is reached. Nothing is written.
        LookupError: if the user has no profile.
    """
    if conn.execute(query("usage", "lock_user"), {"user_id": user_id}).fetchone() is None:
        raise LookupError(f"No profile for user {user_id!r}")
    summary = _summary(conn, user_id)
    for name in SUBMISSION_LIMITS:
        allowed = summary.limits[name]
        used = summary.used[name]
        if allowed is not None and used >= allowed:
            logger.info(
                "Submission refused: user_id=%s limit=%s used=%d allowed=%d",
                user_id,
                name,
                used,
                allowed,
            )
            raise LimitExceededError(name, allowed, used)
    row = conn.execute(
        query("usage", "insert_entry"),
        {
            "user_id": user_id,
            "kind": KIND_SUBMISSION,
            "quantity": Decimal(1),
            "cost_usd": None,
            "pipeline_run_id": pipeline_run_id,
        },
    ).fetchone()
    assert row is not None  # noqa: S101
    entry_id: UUID = row["id"]
    logger.info("Submission recorded: user_id=%s entry_id=%s", user_id, entry_id)
    return entry_id


def restore_entry(conn: Connection[Any], user_id: str, entry_id: UUID) -> UUID | None:
    """Give back a charged entry, after a platform failure. Never call it for a bad user file.

    Appends a restoration row rather than editing the original, so the ledger stays a history.

    Returns:
        The restoration's ID, or None if the entry doesn't exist for this user, is itself a
        restoration, or was already restored.
    """
    row = conn.execute(
        query("usage", "insert_restoration"), {"user_id": user_id, "entry_id": entry_id}
    ).fetchone()
    if row is None:
        logger.warning("Restoration skipped: user_id=%s entry_id=%s", user_id, entry_id)
        return None
    restoration_id: UUID = row["id"]
    logger.info("Usage restored: user_id=%s entry_id=%s", user_id, entry_id)
    return restoration_id


def restore_run_submissions(conn: Connection[Any], run_id: UUID) -> int:
    """Give back the submission a failed run charged. Safe to call more than once.

    Returns:
        How many entries were restored: 0 or 1.
    """
    rows = conn.execute(query("usage", "restore_run_submissions"), {"run_id": run_id}).fetchall()
    if rows:
        logger.info("Submission restored after a failed run: run_id=%s", run_id)
    return len(rows)
