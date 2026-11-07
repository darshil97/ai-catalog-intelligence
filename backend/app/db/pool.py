"""A shared connection pool, because each new connection from Render takes about 580 ms."""

import threading
from typing import Any

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.config import get_database_config
from app.db.connection import DatabaseNotConfiguredError

_lock = threading.Lock()
_pool: ConnectionPool[Any] | None = None


def get_pool() -> ConnectionPool[Any]:
    """Return the process-wide pool, creating it on first use.

    Connections return rows as dicts. `with pool.connection() as conn` commits on success
    and rolls back on an exception.

    Raises:
        DatabaseNotConfiguredError: if no database URL is configured.
    """
    global _pool
    with _lock:
        if _pool is None:
            config = get_database_config()
            if not config["url"]:
                raise DatabaseNotConfiguredError("DATABASE_URL is not set")
            _pool = ConnectionPool(
                config["url"],
                min_size=int(config["pool_min_size"]),
                max_size=int(config["pool_max_size"]),
                # Idle connections close quickly so they can't keep Neon awake.
                max_idle=float(config["pool_max_idle_seconds"]),
                timeout=float(config["connect_timeout_seconds"]),
                # Neon's suspends drop connections, so each is checked before use.
                check=ConnectionPool.check_connection,
                # Prepared statements are off because the pooler shares connections.
                kwargs={"prepare_threshold": None, "row_factory": dict_row},
                open=True,
            )
        return _pool


def close_pool() -> None:
    """Close the pool, if open. The next get_pool call creates a new one."""
    global _pool
    with _lock:
        if _pool is not None:
            _pool.close()
            _pool = None
