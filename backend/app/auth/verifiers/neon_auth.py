"""Verifies Neon Auth (managed Better Auth) JWTs locally.

Input: the `Authorization: Bearer` value, a 15-minute EdDSA (Ed25519) JWT whose header names
its key with `kid`. Neon sets both `iss` and `aud` to the origin of the auth URL, not the full
URL. Relevant claims:

    {"sub": "<user id>", "email": "a@b.com", "emailVerified": true, "role": "authenticated",
     "banned": false, "iat": 1766320685, "exp": 1766321585, "iss": "<origin>", "aud": "<origin>"}

Quirk: an ordinary user's role is "authenticated", not "user". Only "admin" grants admin.
"""

import threading
from typing import Any

import jwt

from app.auth.base import register_verifier
from app.auth.errors import AuthUnavailableError, InvalidTokenError
from app.auth.jwks import JwksCache
from app.auth.models import ROLE_ADMIN, ROLE_USER, Identity
from app.config import get_auth_config

_REQUIRED_CLAIMS = ["exp", "iat", "iss", "aud", "sub"]


class NeonAuthVerifier:
    """Checks the signature, algorithm, issuer, audience and expiry, then reads the claims."""

    name = "neon_auth"

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cache: JwksCache | None = None
        self._cache_url: str | None = None

    def _jwks(self, config: dict[str, Any]) -> JwksCache:
        """Return the key cache, rebuilding it only if the configured URL changed."""
        with self._lock:
            if self._cache is None or self._cache_url != config["jwks_url"]:
                self._cache = JwksCache(
                    config["jwks_url"],
                    cache_seconds=float(config["jwks_cache_seconds"]),
                    min_refetch_seconds=float(config["jwks_min_refetch_seconds"]),
                    timeout_seconds=float(config["jwks_timeout_seconds"]),
                )
                self._cache_url = config["jwks_url"]
            return self._cache

    def set_key_cache(self, cache: JwksCache, url: str) -> None:
        """Use this key cache for the given URL. Tests use it to supply their own keys."""
        with self._lock:
            self._cache = cache
            self._cache_url = url

    def verify(self, token: str) -> Identity:
        """Return the identity in a valid token.

        Raises:
            InvalidTokenError: if any check fails.
            AuthUnavailableError: if sign-in isn't configured or keys can't load.
        """
        config = get_auth_config()
        if not config["origin"]:
            raise AuthUnavailableError("NEON_AUTH_BASE_URL is not set")
        try:
            header = jwt.get_unverified_header(token)
        except jwt.DecodeError as exc:
            raise InvalidTokenError("malformed") from exc
        # Checked before any key lookup, so "none" or HMAC tokens never reach the key cache.
        if header.get("alg") not in config["algorithms"]:
            raise InvalidTokenError("algorithm")
        kid = header.get("kid")
        if not isinstance(kid, str) or not kid:
            raise InvalidTokenError("missing_kid")
        key = self._jwks(config).get_key(kid)
        if key is None:
            raise InvalidTokenError("unknown_kid")
        try:
            claims: dict[str, Any] = jwt.decode(
                token,
                key=key,
                algorithms=config["algorithms"],
                issuer=config["origin"],
                audience=config["origin"],
                leeway=float(config["leeway_seconds"]),
                options={"require": _REQUIRED_CLAIMS},
            )
        except jwt.ExpiredSignatureError as exc:
            raise InvalidTokenError("expired") from exc
        except jwt.InvalidAudienceError as exc:
            raise InvalidTokenError("audience") from exc
        except jwt.InvalidIssuerError as exc:
            raise InvalidTokenError("issuer") from exc
        except jwt.InvalidSignatureError as exc:
            raise InvalidTokenError("signature") from exc
        except jwt.PyJWTError as exc:
            raise InvalidTokenError("invalid") from exc
        return _identity(claims)


def _identity(claims: dict[str, Any]) -> Identity:
    """Read the claims this app relies on, rejecting tokens that lack them."""
    user_id = claims.get("sub")
    email = claims.get("email")
    if not isinstance(user_id, str) or not user_id.strip():
        raise InvalidTokenError("missing_sub")
    if not isinstance(email, str) or "@" not in email:
        raise InvalidTokenError("missing_email")
    return Identity(
        user_id=user_id,
        email=email.strip().lower(),
        # Only a literal true counts, so a missing or odd claim can't pass the allowlist.
        email_verified=claims.get("emailVerified") is True,
        role=ROLE_ADMIN if claims.get("role") == ROLE_ADMIN else ROLE_USER,
        banned=claims.get("banned") is True,
    )


register_verifier(NeonAuthVerifier())
