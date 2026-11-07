"""Covers opening database connections. The database driver is stubbed."""

from unittest.mock import MagicMock

import pytest

from app.db import DatabaseNotConfiguredError, connect


def test_missing_url_fails_clearly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "")
    with pytest.raises(DatabaseNotConfiguredError):
        connect()


def test_connects_without_prepared_statements(monkeypatch: pytest.MonkeyPatch) -> None:
    # Neon's pooler shares connections between clients, so prepared statements must stay off.
    monkeypatch.setenv("DATABASE_URL", "postgresql://example.invalid/db")
    driver = MagicMock()
    monkeypatch.setattr("psycopg.connect", driver)
    connect()
    assert driver.call_args.args == ("postgresql://example.invalid/db",)
    assert driver.call_args.kwargs["prepare_threshold"] is None
    assert driver.call_args.kwargs["connect_timeout"] == 15
