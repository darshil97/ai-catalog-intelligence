"""Checks run during a worker deploy: before restarting, and to confirm the new version runs."""

import re
import time
from pathlib import Path

import psycopg

from app.config import get_database_config
from app.jobs.base import get_registered_handler_names

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations" / "versions"
_REVISION = re.compile(r'^revision = "([^"]+)"', re.MULTILINE)
_DOWN_REVISION = re.compile(r'^down_revision = "([^"]+)"', re.MULTILINE)


def migration_head(directory: Path = MIGRATIONS_DIR) -> str:
    """Return the newest migration in this code.

    Raises:
        ValueError: if there isn't exactly one head, which means migrations have diverged.
    """
    revisions: set[str] = set()
    parents: set[str] = set()
    for path in directory.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        if match := _REVISION.search(text):
            revisions.add(match.group(1))
        if match := _DOWN_REVISION.search(text):
            parents.add(match.group(1))
    heads = revisions - parents
    if len(heads) != 1:
        raise ValueError(f"Expected one migration head, found: {sorted(heads)}")
    return heads.pop()


def self_check() -> list[str]:
    """Check this code can run against the configured database.

    Returns:
        Problems found. Empty means it's safe to restart on this code.
    """
    problems: list[str] = []
    if not get_registered_handler_names():
        problems.append("No job handlers are registered")
    try:
        expected = migration_head()
    except ValueError as exc:
        return [*problems, str(exc)]
    try:
        with psycopg.connect(
            get_database_config()["url"], connect_timeout=15, prepare_threshold=None
        ) as conn:
            row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
    except psycopg.Error as exc:
        return [*problems, f"Database unreachable: {type(exc).__name__}"]
    actual = row[0] if row else None
    if actual != expected:
        problems.append(f"Database schema is at {actual}, but this code expects {expected}")
    return problems


def wait_for_version(code_sha: str, timeout_seconds: float) -> bool:
    """Wait until a running worker reports this code version in a fresh heartbeat.

    Returns:
        True if it appeared before the timeout.
    """
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with psycopg.connect(
                get_database_config()["url"], connect_timeout=15, prepare_threshold=None
            ) as conn:
                row = conn.execute(
                    "SELECT count(*) FROM worker_instances WHERE state = 'running' "
                    "AND code_sha = %s AND last_heartbeat_at > now() - interval '90 seconds'",
                    (code_sha,),
                ).fetchone()
            if row and row[0] > 0:
                return True
        except psycopg.Error:
            pass
        time.sleep(5)
    return False
