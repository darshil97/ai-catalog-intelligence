"""Database access shared by the API and worker."""

from app.db.connection import DatabaseNotConfiguredError, connect
from app.db.pool import close_pool, get_pool
from app.db.queries import query

__all__ = ["DatabaseNotConfiguredError", "close_pool", "connect", "get_pool", "query"]
