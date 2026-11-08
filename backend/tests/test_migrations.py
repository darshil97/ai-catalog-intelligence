"""Covers migrations. The online test needs real Postgres, via TEST_DATABASE_URL."""

import os
import subprocess
import sys
from pathlib import Path

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


def test_upgrade_and_downgrade_on_real_postgres(migrated_database: str) -> None:
    env = {"DATABASE_URL_DIRECT": migrated_database}
    for command in (("upgrade", "head"), ("downgrade", "base"), ("upgrade", "head")):
        result = _alembic(*command, env=env)
        assert result.returncode == 0, result.stderr


def test_0003_refuses_queue_rows_without_profiles(migrated_database: str) -> None:
    # Production held M0B test runs owned by "system". The migration must stop with a clear
    # message rather than delete them, so the owner decides.
    import psycopg

    url = migrated_database
    env = {"DATABASE_URL_DIRECT": url}
    assert _alembic("downgrade", "0002", env=env).returncode == 0
    try:
        with psycopg.connect(url, autocommit=True) as conn:
            conn.execute("TRUNCATE pipeline_runs CASCADE")
            conn.execute(
                "INSERT INTO pipeline_runs (pipeline, owner_user_id) VALUES ('noop', 'system')"
            )
        result = _alembic("upgrade", "head", env=env)
        assert result.returncode != 0
        assert "needs empty queue tables" in result.stderr
    finally:
        with psycopg.connect(url, autocommit=True) as conn:
            conn.execute("TRUNCATE pipeline_runs CASCADE")
        assert _alembic("upgrade", "head", env=env).returncode == 0


def test_0003_sql_adds_owner_foreign_keys() -> None:
    result = _alembic("upgrade", "0002:0003", "--sql")
    assert result.returncode == 0, result.stderr
    for statement in (
        "CREATE TABLE user_profiles",
        "CREATE TABLE suppliers",
        "CREATE TABLE batches",
        "pipeline_runs_owner_fk",
        "jobs_owner_fk",
    ):
        assert statement in result.stdout
