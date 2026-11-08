"""Caches the public keys that sign Neon Auth tokens.

Keys are fetched once and reused, so checking a token needs no network call. A token naming
an unknown key ID triggers a refetch, because Neon may have rotated its keys. Refetches are
rate-limited, so a stream of forged key IDs can't turn the API into a load generator.
"""

import json
import threading
import time
import urllib.request
from collections.abc import Callable
from typing import Any

from jwt import PyJWK, PyJWKSet
from jwt.exceptions import PyJWKSetError

from app.auth.errors import AuthUnavailableError
from app.utils.logger import get_logger

logger = get_logger(__name__)

Fetcher = Callable[[str, float], dict[str, Any]]


def fetch_jwks(url: str, timeout_seconds: float) -> dict[str, Any]:
    """Download a JSON Web Key Set.

    Raises:
        OSError: if the request fails or times out.
        ValueError: if the response isn't JSON.
    """
    if not url.startswith("https://"):
        raise ValueError(f"JWKS URL must use https: {url!r}")
    request = urllib.request.Request(url, headers={"Accept": "application/json"})  # noqa: S310
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310
        document: dict[str, Any] = json.loads(response.read())
    return document


class JwksCache:
    """Thread-safe key cache with rate-limited refetching.

    Args:
        url: The JWKS endpoint.
        cache_seconds: Refresh keys older than this.
        min_refetch_seconds: At most one fetch per interval, whatever the reason.
        timeout_seconds: Network timeout for each fetch.
        fetcher: Downloads the key set. Tests replace it.
        clock: Returns monotonic seconds. Tests replace it.
    """

    def __init__(
        self,
        url: str,
        cache_seconds: float,
        min_refetch_seconds: float,
        timeout_seconds: float,
        fetcher: Fetcher = fetch_jwks,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._url = url
        self._cache_seconds = cache_seconds
        self._min_refetch_seconds = min_refetch_seconds
        self._timeout_seconds = timeout_seconds
        self._fetcher = fetcher
        self._clock = clock
        self._lock = threading.Lock()
        self._keys: dict[str, PyJWK] = {}
        self._fetched_at: float | None = None
        # Separate timers, so a recent routine refresh never delays picking up a rotated key.
        self._refresh_attempted_at: float | None = None
        self._unknown_kid_fetched_at: float | None = None

    def get_key(self, kid: str) -> PyJWK | None:
        """Return the key with this ID, refetching if it's unknown or the cache is old.

        Returns:
            The key, or None if Neon doesn't publish it (the token is then forged or stale).

        Raises:
            AuthUnavailableError: if no keys have ever loaded.
        """
        with self._lock:
            now = self._clock()
            stale = self._fetched_at is None or now - self._fetched_at >= self._cache_seconds
            if stale and self._interval_passed(self._refresh_attempted_at, now):
                self._refresh_attempted_at = now
                self._refresh(now, reason="stale")
            elif kid not in self._keys and self._interval_passed(self._unknown_kid_fetched_at, now):
                self._unknown_kid_fetched_at = now
                self._refresh(now, reason="unknown_kid")
            if self._fetched_at is None:
                raise AuthUnavailableError("Signing keys could not be loaded")
            return self._keys.get(kid)

    def _interval_passed(self, last: float | None, now: float) -> bool:
        return last is None or now - last >= self._min_refetch_seconds

    def _refresh(self, now: float, reason: str) -> None:
        """Replace the keys. On failure, keep the old ones, so a Neon blip signs nobody out."""
        try:
            document = self._fetcher(self._url, self._timeout_seconds)
            key_set = PyJWKSet.from_dict(document)
        except (OSError, ValueError, PyJWKSetError) as exc:
            logger.error(
                "JWKS fetch failed: reason=%s error=%s cached_keys=%d",
                reason,
                type(exc).__name__,
                len(self._keys),
                exc_info=True,
            )
            return
        self._keys = {key.key_id: key for key in key_set.keys if key.key_id}
        self._fetched_at = now
        logger.info("JWKS fetched: reason=%s keys=%d", reason, len(self._keys))
