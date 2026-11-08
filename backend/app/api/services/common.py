"""Helpers shared by API services: database access and consistent error responses."""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, NoReturn

import psycopg
import psycopg_pool
from fastapi import HTTPException
from psycopg import Connection

from app.db import DatabaseNotConfiguredError, get_pool
from app.utils.logger import get_logger

logger = get_logger(__name__)

DATABASE_ERRORS = (DatabaseNotConfiguredError, psycopg.OperationalError, psycopg_pool.PoolTimeout)


def http_error(
    status_code: int, code: str, message: str, headers: dict[str, str] | None = None
) -> HTTPException:
    """Build an error whose body is {"detail": {"code": ..., "message": ...}}.

    The frontend branches on `code`, and shows `message` to the user.
    """
    return HTTPException(
        status_code=status_code, detail={"code": code, "message": message}, headers=headers
    )


def not_found() -> NoReturn:
    """Raise the same 404 for missing rows and other users' rows, so neither can be probed."""
    raise HTTPException(status_code=404, detail="Not Found")


@contextmanager
def database(label: str) -> Iterator[Connection[Any]]:
    """Yield a pooled connection that commits on success and rolls back on an exception.

    Args:
        label: Names the operation in logs, such as "create_supplier".

    Raises:
        HTTPException: 503 if the database can't be reached.
    """
    try:
        with get_pool().connection() as conn:
            yield conn
    except DATABASE_ERRORS as exc:
        logger.error("Database unavailable: operation=%s error=%s", label, type(exc).__name__)
        raise http_error(503, "database_unavailable", "The database is unavailable") from exc
