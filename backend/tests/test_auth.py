"""Covers token verification: signatures, claims, key rotation and the key cache.

Needs no database. Keys are generated per test and served by a stubbed JWKS fetch.
"""

import base64
import json
import time
from typing import Any

import jwt
import pytest

from app.auth import AuthUnavailableError, InvalidTokenError, get_verifier
from app.auth.base import get_registered_verifier_names
from app.auth.jwks import JwksCache
from app.auth.models import ROLE_ADMIN, ROLE_USER
from conftest import Tokens


def _verify(token: str) -> Any:
    return get_verifier("neon_auth").verify(token)


def _reason(token: str) -> str:
    with pytest.raises(InvalidTokenError) as caught:
        _verify(token)
    return caught.value.reason


def _b64(data: dict[str, Any]) -> str:
    return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()


def test_registry_lists_the_verifier_and_names_options_on_error() -> None:
    assert "neon_auth" in get_registered_verifier_names()
    with pytest.raises(ValueError, match="Registered: .*neon_auth"):
        get_verifier("nope")


def test_valid_token_gives_its_identity(tokens: Tokens) -> None:
    identity = _verify(tokens.make("u1", "  Mixed@Example.COM "))
    assert identity.user_id == "u1"
    assert identity.email == "mixed@example.com"
    assert identity.email_verified is True
    assert identity.role == ROLE_USER
    assert identity.banned is False


def test_only_the_admin_role_grants_admin(tokens: Tokens) -> None:
    assert _verify(tokens.make(role="admin")).role == ROLE_ADMIN
    # Neon's default role for an ordinary user is "authenticated".
    for role in ("authenticated", "user", "Admin", "owner"):
        assert _verify(tokens.make(role=role)).role == ROLE_USER


def test_unverified_or_odd_email_verified_claims_count_as_unverified(tokens: Tokens) -> None:
    assert _verify(tokens.make(email_verified=False)).email_verified is False
    # Only a literal true passes: a truthy string must not satisfy the allowlist check.
    assert _verify(tokens.make(emailVerified="true")).email_verified is False
    assert _verify(tokens.make(emailVerified=None)).email_verified is False


def test_expired_token_is_rejected(tokens: Tokens) -> None:
    # Beyond the 30-second leeway.
    assert _reason(tokens.make(expires_in=-120)) == "expired"


def test_token_within_clock_leeway_is_accepted(tokens: Tokens) -> None:
    assert _verify(tokens.make(expires_in=-5)).user_id == "user-a"


def test_wrong_audience_is_rejected(tokens: Tokens) -> None:
    # A token for another Neon Auth project must not work here.
    assert _reason(tokens.make(aud="https://other.neonauth.example")) == "audience"
    # The full auth URL is not the audience: Neon uses its origin.
    assert _reason(tokens.make(aud="https://auth.tests.invalid/neondb/auth")) == "audience"


def test_wrong_issuer_is_rejected(tokens: Tokens) -> None:
    assert _reason(tokens.make(iss="https://evil.example")) == "issuer"


def test_token_signed_by_an_unpublished_key_is_rejected(tokens: Tokens) -> None:
    # An attacker reuses a real key ID but signs with their own key.
    forged = tokens.add_key("attacker", publish=False)
    token = jwt.encode(
        {
            "sub": "x",
            "email": "x@example.com",
            "iss": "https://auth.tests.invalid",
            "aud": "https://auth.tests.invalid",
            "iat": int(time.time()),
            "exp": int(time.time()) + 600,
        },
        forged,
        algorithm="EdDSA",
        headers={"kid": "key-1"},
    )
    assert _reason(token) == "signature"


def test_tampered_payload_is_rejected(tokens: Tokens) -> None:
    header, _, signature = tokens.make("victim").split(".")
    payload = _b64(
        {
            "sub": "attacker",
            "email": "owner@example.com",
            "emailVerified": True,
            "iss": "https://auth.tests.invalid",
            "aud": "https://auth.tests.invalid",
            "iat": int(time.time()),
            "exp": int(time.time()) + 600,
        }
    )
    assert _reason(f"{header}.{payload}.{signature}") == "signature"


@pytest.mark.parametrize("alg", ["none", "HS256", "RS256"])
def test_other_algorithms_are_rejected_before_any_key_lookup(tokens: Tokens, alg: str) -> None:
    header = _b64({"alg": alg, "kid": "key-1", "typ": "JWT"})
    payload = _b64({"sub": "x", "email": "x@example.com"})
    fetches_before = tokens.fetches
    assert _reason(f"{header}.{payload}.c2ln") == "algorithm"
    assert tokens.fetches == fetches_before


@pytest.mark.parametrize("token", ["", "abc", "a.b", "a.b.c", "Bearer x.y.z"])
def test_malformed_tokens_are_rejected(token: str) -> None:
    assert _reason(token) in {"malformed", "algorithm"}


def test_missing_key_id_is_rejected(tokens: Tokens) -> None:
    token = jwt.encode({"sub": "x"}, tokens.keys["key-1"], algorithm="EdDSA")
    assert _reason(token) == "missing_kid"


@pytest.mark.parametrize("claim", ["sub", "exp", "iat", "aud", "iss"])
def test_required_claims_must_be_present(tokens: Tokens, claim: str) -> None:
    assert _reason(tokens.make(**{claim: None})) == "invalid"


def test_missing_email_is_rejected(tokens: Tokens) -> None:
    assert _reason(tokens.make(email=None)) == "missing_email"


def test_unknown_key_triggers_one_refetch_and_rotation_works(tokens: Tokens) -> None:
    assert _verify(tokens.make()).user_id == "user-a"
    assert tokens.fetches == 1
    # Neon rotates its keys: a token arrives signed by a key the cache hasn't seen.
    tokens.add_key("key-2")
    assert _verify(tokens.make(kid="key-2")).user_id == "user-a"
    assert tokens.fetches == 2
    # Known keys need no further fetch.
    _verify(tokens.make(kid="key-1"))
    _verify(tokens.make(kid="key-2"))
    assert tokens.fetches == 2


def test_unknown_key_refetches_are_rate_limited(tokens: Tokens) -> None:
    _verify(tokens.make())
    tokens.add_key("bogus", publish=False)
    # A flood of tokens naming unpublished keys causes at most one extra fetch per interval.
    for _ in range(20):
        assert _reason(tokens.make(kid="bogus")) == "unknown_kid"
    assert tokens.fetches == 2


def test_key_cache_refetches_after_the_interval() -> None:
    now = [0.0]
    calls: list[str] = []
    published: dict[str, Any] = {"keys": []}

    def fetch(url: str, timeout: float) -> dict[str, Any]:
        calls.append(url)
        return published

    factory = Tokens()
    published["keys"] = factory.jwks["keys"]
    cache = JwksCache("https://x", 3600, 30, 5, fetcher=fetch, clock=lambda: now[0])
    assert cache.get_key("key-1") is not None
    assert len(calls) == 1
    # A rotated key may appear at any time, even just after the first fetch.
    assert cache.get_key("missing") is None
    assert len(calls) == 2
    # Further unknown keys wait for the 30-second interval.
    assert cache.get_key("missing") is None
    assert len(calls) == 2
    now[0] = 31
    assert cache.get_key("missing") is None
    assert len(calls) == 3
    now[0] = 3700  # Stale: refreshed even for a known key.
    cache.get_key("key-1")
    assert len(calls) == 4


def test_failed_refresh_keeps_the_old_keys() -> None:
    now = [0.0]
    factory = Tokens()
    responses: list[Any] = [factory.jwks, OSError("Neon is down")]

    def fetch(url: str, timeout: float) -> dict[str, Any]:
        result = responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result  # type: ignore[no-any-return]

    cache = JwksCache("https://x", 60, 30, 5, fetcher=fetch, clock=lambda: now[0])
    assert cache.get_key("key-1") is not None
    now[0] = 120
    # Signing everyone out because Neon blipped would be worse than using hour-old keys.
    assert cache.get_key("key-1") is not None


def test_no_keys_at_all_is_unavailable_not_invalid() -> None:
    def fetch(url: str, timeout: float) -> dict[str, Any]:
        raise OSError("unreachable")

    cache = JwksCache("https://x", 60, 30, 5, fetcher=fetch)
    with pytest.raises(AuthUnavailableError):
        cache.get_key("key-1")


def test_unconfigured_auth_is_unavailable(tokens: Tokens, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NEON_AUTH_BASE_URL", "")
    with pytest.raises(AuthUnavailableError):
        _verify(tokens.make())


def test_garbage_key_set_counts_as_a_failed_fetch() -> None:
    cache = JwksCache("https://x", 60, 30, 5, fetcher=lambda url, timeout: {"keys": "nope"})
    with pytest.raises(AuthUnavailableError):
        cache.get_key("key-1")
