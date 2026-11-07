"""Covers migrations. The online test needs real Postgres, via TEST_DATABASE_URL."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]


def _alembic(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        env={**os.environ, **(env or {})},
    )


def test_baseline_enables_vector_and_touches_nothing_else() -> None:
    result = _alembic("upgrade", "head", "--sql")
    assert result.returncode == 0, result.stderr
    assert "CREATE EXTENSION IF NOT EXISTS vector" in result.stdout
    assert "neon_auth" not in result.stdout


def test_only_one_migration_head() -> None:
    result = _alembic("heads")
    assert result.returncode == 0, result.stderr
    assert len(result.stdout.strip().splitlines()) == 1


@pytest.mark.skipif("TEST_DATABASE_URL" not in os.environ, reason="needs a real Postgres")
def test_upgrade_and_downgrade_on_real_postgres() -> None:
    env = {"DATABASE_URL_DIRECT": os.environ["TEST_DATABASE_URL"]}
    for command in (("upgrade", "head"), ("downgrade", "base"), ("upgrade", "head")):
        result = _alembic(*command, env=env)
        assert result.returncode == 0, result.stderr
