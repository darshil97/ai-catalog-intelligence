"""The current request ID, kept free of web dependencies so the worker can use it too."""

from contextvars import ContextVar, Token

_request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)


def get_request_id() -> str | None:
    """Return the current request ID, or None outside a request."""
    return _request_id_var.get()


def set_request_id(request_id: str) -> Token[str | None]:
    """Set the current request ID.

    Returns:
        A token to pass to reset_request_id.
    """
    return _request_id_var.set(request_id)


def reset_request_id(token: Token[str | None]) -> None:
    """Restore the request ID that was set before the given token."""
    _request_id_var.reset(token)
