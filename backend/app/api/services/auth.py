"""Authenticates requests: verifies the bearer token, then loads the caller's profile."""

from typing import Any

from fastapi import HTTPException

from app.api.services.common import DATABASE_ERRORS, http_error
from app.auth import (
    STATUS_ACTIVE,
    AccessDeniedError,
    AuthUnavailableError,
    InvalidTokenError,
    Profile,
    get_verifier,
    resolve_profile,
)
from app.config import get_auth_config
from app.db import get_pool
from app.utils.logger import get_logger

logger = get_logger(__name__)

_BEARER_CHALLENGE = {"WWW-Authenticate": "Bearer"}

_ACCESS_MESSAGES = {
    "banned": "This account has been suspended.",
    "email_unverified": "Sign in with Google to use this app.",
    "waitlisted": "You're on the waitlist. We'll let you in when a place opens up.",
    "disabled": "This account has been disabled.",
}


def _pool_connection() -> Any:
    return get_pool().connection()


def authenticate(token: str | None) -> Profile:
    """Return the caller's profile, which may be waitlisted.

    Raises:
        HTTPException: 401 for a missing or invalid token, 403 if the account may not use the
            app, or 503 if tokens or profiles can't be checked right now.
    """
    if not token:
        raise http_error(401, "unauthenticated", "Sign in to continue", _BEARER_CHALLENGE)
    try:
        identity = get_verifier(get_auth_config()["verifier"]).verify(token)
    except InvalidTokenError as exc:
        logger.info("Token rejected: reason=%s", exc.reason)
        raise http_error(401, "invalid_token", "Sign in again", _BEARER_CHALLENGE) from exc
    except AuthUnavailableError as exc:
        logger.error("Token check unavailable: error=%s", exc)
        raise http_error(503, "auth_unavailable", "Sign-in is unavailable right now") from exc
    try:
        profile = resolve_profile(identity, _pool_connection)
    except AccessDeniedError as exc:
        logger.info("Access denied: user_id=%s code=%s", identity.user_id, exc.code)
        raise http_error(403, exc.code, _ACCESS_MESSAGES[exc.code]) from exc
    except DATABASE_ERRORS as exc:
        logger.error("Profile lookup failed: error=%s", type(exc).__name__)
        raise http_error(503, "database_unavailable", "The database is unavailable") from exc
    return profile


def require_active(profile: Profile) -> Profile:
    """Return the profile if it may use the app.

    Raises:
        HTTPException: 403 with code "waitlisted" for a waitlisted profile.
    """
    if profile.status != STATUS_ACTIVE:
        raise http_error(403, profile.status, _ACCESS_MESSAGES[profile.status])
    return profile


def require_admin(profile: Profile) -> Profile:
    """Return the profile if it's an active admin.

    Raises:
        HTTPException: 404 for anyone else, so admin endpoints stay hidden.
    """
    if profile.status != STATUS_ACTIVE or not profile.is_admin:
        logger.info("Admin endpoint hidden: user_id=%s", profile.user_id)
        raise HTTPException(status_code=404, detail="Not Found")
    return profile
