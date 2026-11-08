"""Sign-in errors. The API turns these into 401, 403 or 503 responses."""


class InvalidTokenError(ValueError):
    """The token is missing, malformed, forged, expired or meant for someone else.

    Attributes:
        reason: A short machine-readable reason, safe to log. Never the token itself.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class AccessDeniedError(PermissionError):
    """The token is valid, but this account may not use the app.

    Attributes:
        code: banned, email_unverified, waitlisted or disabled.
    """

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class AuthUnavailableError(RuntimeError):
    """Tokens can't be checked right now, because sign-in isn't configured or keys can't load."""
