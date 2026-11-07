"""Opens PostgreSQL connections using the configured pooled endpoint."""

from typing import Any

import psycopg

from app.config import get_database_config


class DatabaseNotConfiguredError(RuntimeError):
    """DATABASE_URL is not set."""


def connect() -> psycopg.Connection[Any]:
    """Open a connection to the pooled endpoint.

    Returns:
        A new connection, which the caller must close.

    Raises:
        DatabaseNotConfiguredError: if no database URL is configured.
        psycopg.OperationalError: if the database can't be reached.
    """
    config = get_database_config()
    if not config["url"]:
        raise DatabaseNotConfiguredError("DATABASE_URL is not set")
    # Prepared statements are off because the pooler shares connections between clients.
    return psycopg.connect(
        config["url"],
        connect_timeout=int(config["connect_timeout_seconds"]),
        prepare_threshold=None,
    )
