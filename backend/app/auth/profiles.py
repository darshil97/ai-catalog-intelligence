"""Turns a verified identity into a profile, deciding who may use the app.

Admission rules, applied on first use and again while a profile is waitlisted:
1. An allowlisted email is always admitted, ignoring the user cap.
2. Otherwise, with sign-ups on and fewer active users than the cap, the user is admitted.
3. Otherwise the user is waitlisted.

Active profiles are cached in memory for a short time, so most requests need no profile
query. The token is still checked on every request, including its `banned` claim.
"""

import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from typing import Any

from psycopg import Connection

from app.auth.errors import AccessDeniedError
from app.auth.models import STATUS_ACTIVE, STATUS_DISABLED, STATUS_WAITLISTED, Identity, Profile
from app.config import get_access_config, get_auth_config
from app.db import query
from app.utils.logger import get_logger

logger = get_logger(__name__)


class ProfileCache:
    """A small thread-safe LRU of active profiles, each kept for a limited time."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._entries: OrderedDict[str, tuple[float, Profile]] = OrderedDict()

    def get(self, identity: Identity) -> Profile | None:
        """Return the cached profile if it's fresh and still matches the token's email and role."""
        with self._lock:
            entry = self._entries.get(identity.user_id)
            if entry is None:
                return None
            expires_at, profile = entry
            if self._clock() >= expires_at or (profile.email, profile.role) != (
                identity.email,
                identity.role,
            ):
                del self._entries[identity.user_id]
                return None
            self._entries.move_to_end(identity.user_id)
            return profile

    def put(self, profile: Profile) -> None:
        """Cache an active profile. Other statuses are always rechecked in the database."""
        config = get_auth_config()
        if profile.status != STATUS_ACTIVE or int(config["profile_cache_seconds"]) <= 0:
            return
        with self._lock:
            self._entries[profile.user_id] = (
                self._clock() + float(config["profile_cache_seconds"]),
                profile,
            )
            self._entries.move_to_end(profile.user_id)
            while len(self._entries) > int(config["profile_cache_max_entries"]):
                self._entries.popitem(last=False)

    def clear(self) -> None:
        """Forget every cached profile."""
        with self._lock:
            self._entries.clear()


profile_cache = ProfileCache()


def check_identity(identity: Identity) -> None:
    """Refuse tokens that may not use the app, whatever their profile says.

    Raises:
        AccessDeniedError: banned, or email_unverified. An unverified email could otherwise
            claim an allowlisted address.
    """
    if identity.banned:
        raise AccessDeniedError("banned")
    if not identity.email_verified:
        raise AccessDeniedError("email_unverified")


def _admitted_status(conn: Connection[Any], identity: Identity) -> str:
    """Decide between active and waitlisted. Takes the admission lock first.

    Call inside a transaction. The lock is held until it commits, so the cap check and the
    write that follows can't interleave with another admission.
    """
    access = get_access_config()
    if identity.email in access["allowlisted_emails"]:
        return STATUS_ACTIVE
    if not access["signups_enabled"]:
        return STATUS_WAITLISTED
    conn.execute(query("accounts", "lock_profiles_for_admission"))
    row = conn.execute(query("accounts", "count_active_profiles")).fetchone()
    active = int(row["count"]) if row else 0
    if active >= access["max_total_users"]:
        logger.info(
            "Admission refused: reason=user_cap active=%d cap=%d",
            active,
            access["max_total_users"],
        )
        return STATUS_WAITLISTED
    return STATUS_ACTIVE


def load_profile(conn: Connection[Any], identity: Identity) -> Profile:
    """Create or refresh the caller's profile, inside the caller's transaction.

    Keeps the stored email and role in step with the token, and re-admits a waitlisted user
    whenever the rules now allow it. A disabled profile stays disabled.
    """
    row = conn.execute(query("accounts", "lock_profile"), {"user_id": identity.user_id}).fetchone()
    params = {"user_id": identity.user_id, "email": identity.email, "role": identity.role}
    if row is None:
        status = _admitted_status(conn, identity)
        row = conn.execute(
            query("accounts", "insert_profile"), {**params, "status": status}
        ).fetchone()
        assert row is not None  # noqa: S101
        logger.info("Profile created: user_id=%s status=%s", identity.user_id, row["status"])
    else:
        status = row["status"]
        if status == STATUS_WAITLISTED:
            status = _admitted_status(conn, identity)
        if (row["email"], row["role"], row["status"]) != (identity.email, identity.role, status):
            email_changed = row["email"] != identity.email
            row = conn.execute(
                query("accounts", "update_profile"), {**params, "status": status}
            ).fetchone()
            assert row is not None  # noqa: S101
            logger.info(
                "Profile updated: user_id=%s status=%s email_changed=%s",
                identity.user_id,
                row["status"],
                email_changed,
            )
    # The token is the source of truth for email and role, even before the update lands.
    return Profile(
        user_id=identity.user_id, email=identity.email, role=identity.role, status=row["status"]
    )


def resolve_profile(identity: Identity, connect: Callable[[], Any]) -> Profile:
    """Return the caller's profile, from the cache when possible.

    Args:
        identity: A verified token's identity.
        connect: Returns a context manager yielding a connection that commits on success,
            such as `get_pool().connection`.

    Raises:
        AccessDeniedError: banned, email_unverified or disabled. A waitlisted profile is
            returned, so the caller can show the waitlist message.
    """
    check_identity(identity)
    profile = profile_cache.get(identity)
    if profile is None:
        with connect() as conn:
            profile = load_profile(conn, identity)
        profile_cache.put(profile)
    if profile.status == STATUS_DISABLED:
        raise AccessDeniedError("disabled")
    return profile
