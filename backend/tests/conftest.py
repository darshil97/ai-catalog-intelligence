"""Shared fixtures. No test needs real AWS or Neon access.

Tests that need a database use the `db` fixture, which runs only when TEST_DATABASE_URL is set.
"""

import os
from pathlib import Path
from urllib.parse import urlparse

from dotenv import dotenv_values

BACKEND = Path(__file__).resolve().parents[1]
_DOTENV = dotenv_values(BACKEND / ".env")
_PRODUCTION_ENDPOINT = "<PROD_DB_ENDPOINT>"


def _test_database_url() -> str:
    """Return the test database URL, refusing any database that holds real data.

    Tests erase the queue tables, so a wrong URL here would be destructive.
    """
    url = os.environ.get("TEST_DATABASE_URL") or _DOTENV.get("TEST_DATABASE_URL") or ""
    if not url:
        return ""
    host = urlparse(url).hostname or ""
    dev_host = urlparse(_DOTENV.get("DATABASE_URL") or "").hostname
    if _PRODUCTION_ENDPOINT in host or host == dev_host:
        raise RuntimeError(f"TEST_DATABASE_URL points at a real database: {host}")
    return url


# Set before anything loads config, so tests can never reach the other URLs in a local .env file.
_TEST_DATABASE_URL = _test_database_url()
os.environ["TEST_DATABASE_URL"] = _TEST_DATABASE_URL
_SAFE_URL = _TEST_DATABASE_URL or "postgresql://tests-have-no-database.invalid/none"
os.environ["DATABASE_URL"] = _SAFE_URL
os.environ["DATABASE_URL_DIRECT"] = _SAFE_URL

import subprocess  # noqa: E402
import sys  # noqa: E402
from collections.abc import Iterator  # noqa: E402

import psycopg  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.api.main import app  # noqa: E402
from app.config import get_config  # noqa: E402
from app.db import close_pool  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_config() -> Iterator[None]:
    """Reload config and the pool around each test, so changes can't leak between tests."""
    get_config.cache_clear()
    close_pool()
    yield
    close_pool()
    get_config.cache_clear()


@pytest.fixture
def client() -> TestClient:
    """A client running the real app, so middleware and validators are exercised."""
    return TestClient(app)


@pytest.fixture(scope="session")
def migrated_database() -> str:
    """Bring the test database up to the latest migration, once per test session."""
    if not _TEST_DATABASE_URL:
        pytest.skip("needs a real Postgres: set TEST_DATABASE_URL")
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return _TEST_DATABASE_URL


@pytest.fixture
def db(migrated_database: str) -> Iterator[str]:
    """A clean queue. Yields the database URL."""
    with psycopg.connect(migrated_database, autocommit=True) as conn:
        conn.execute("TRUNCATE pipeline_runs, worker_instances, worker_sessions CASCADE")
        conn.execute(
            "UPDATE worker_control SET state = 'stopped', pause_reason = NULL, "
            "pause_expires_at = NULL, last_start_requested_at = NULL"
        )
    yield migrated_database
