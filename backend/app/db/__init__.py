"""Database access shared by the API and worker."""

from app.db.connection import DatabaseNotConfiguredError, connect

__all__ = ["DatabaseNotConfiguredError", "connect"]
