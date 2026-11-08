"""Sign-in: Neon Auth token checks, profiles and access rules. Used by the API only."""

import app.auth.verifiers  # noqa: F401  Registers the verifiers.
from app.auth.base import get_registered_verifier_names, get_verifier, register_verifier
from app.auth.errors import AccessDeniedError, AuthUnavailableError, InvalidTokenError
from app.auth.models import (
    ROLE_ADMIN,
    ROLE_USER,
    STATUS_ACTIVE,
    STATUS_DISABLED,
    STATUS_WAITLISTED,
    Identity,
    Profile,
)
from app.auth.profiles import profile_cache, resolve_profile

__all__ = [
    "ROLE_ADMIN",
    "ROLE_USER",
    "STATUS_ACTIVE",
    "STATUS_DISABLED",
    "STATUS_WAITLISTED",
    "AccessDeniedError",
    "AuthUnavailableError",
    "Identity",
    "InvalidTokenError",
    "Profile",
    "get_registered_verifier_names",
    "get_verifier",
    "profile_cache",
    "register_verifier",
    "resolve_profile",
]
