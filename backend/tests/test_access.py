"""Covers who may use the app: tokens at the API, profiles, the allowlist, the waitlist, bans
and the user cap. Needs TEST_DATABASE_URL. Tokens are signed locally (see conftest).
"""

import threading
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.auth import Identity, get_verifier, profile_cache, resolve_profile
from app.auth.errors import AccessDeniedError
from app.auth.jwks import JwksCache
from app.config import get_access_config
from app.db import get_pool
from conftest import ALLOWLISTED_EMAIL, Tokens, ensure_user

pytestmark = pytest.mark.usefixtures("db")


def _profile(user_id: str) -> dict[str, Any] | None:
    with get_pool().connection() as conn:
        row: dict[str, Any] | None = conn.execute(
            "SELECT id, email, role, status FROM user_profiles WHERE id = %s", (user_id,)
        ).fetchone()
    return row


def _open_signups(monkeypatch: pytest.MonkeyPatch, cap: int) -> None:
    config = {**get_access_config(), "signups_enabled": True, "max_total_users": cap}
    monkeypatch.setattr("app.auth.profiles.get_access_config", lambda: config)


def _code(response: Any) -> str:
    return str(response.json()["detail"]["code"])


def test_missing_token_is_401_with_a_bearer_challenge(client: TestClient) -> None:
    response = client.get("/me")
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert _code(response) == "unauthenticated"


@pytest.mark.parametrize("header", ["Bearer not-a-token", "Basic dXNlcjpwdw==", "Bearer "])
def test_bad_authorization_headers_are_401(client: TestClient, header: str) -> None:
    assert client.get("/me", headers={"Authorization": header}).status_code == 401


def test_expired_and_wrong_audience_tokens_are_401(client: TestClient, tokens: Tokens) -> None:
    expired = tokens.headers("u1", ALLOWLISTED_EMAIL, expires_in=-120)
    other_app = tokens.headers("u1", ALLOWLISTED_EMAIL, aud="https://other.example")
    for headers in (expired, other_app):
        response = client.get("/me", headers=headers)
        assert response.status_code == 401
        assert _code(response) == "invalid_token"
    # A rejected token must never create a profile.
    assert _profile("u1") is None


def test_allowlisted_user_is_active_on_first_use(client: TestClient, tokens: Tokens) -> None:
    response = client.get("/me", headers=tokens.headers("u1", ALLOWLISTED_EMAIL.upper()))
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "active"
    assert body["limits"]["submissions_lifetime"] == 2
    assert body["usage"] == {"submissions_lifetime": 0, "submissions_per_24_hours": 0}
    assert _profile("u1") == {
        "id": "u1",
        "email": ALLOWLISTED_EMAIL,
        "role": "user",
        "status": "active",
    }


def test_other_users_are_waitlisted_while_signups_are_off(
    client: TestClient, tokens: Tokens
) -> None:
    headers = tokens.headers("u2", "stranger@example.com")
    me = client.get("/me", headers=headers)
    assert me.status_code == 200
    assert me.json() == {
        "id": "u2",
        "email": "stranger@example.com",
        "role": "user",
        "status": "waitlisted",
    }
    response = client.get("/suppliers", headers=headers)
    assert response.status_code == 403
    assert _code(response) == "waitlisted"
    assert "waitlist" in response.json()["detail"]["message"]


def test_unverified_email_cannot_claim_an_allowlisted_address(
    client: TestClient, tokens: Tokens
) -> None:
    # Email-and-password sign-up doesn't verify addresses, so anyone could type the owner's.
    response = client.get(
        "/me", headers=tokens.headers("u3", ALLOWLISTED_EMAIL, email_verified=False)
    )
    assert response.status_code == 403
    assert _code(response) == "email_unverified"
    assert _profile("u3") is None


def test_banned_user_is_refused(client: TestClient, tokens: Tokens) -> None:
    response = client.get("/me", headers=tokens.headers("u4", ALLOWLISTED_EMAIL, banned=True))
    assert response.status_code == 403
    assert _code(response) == "banned"


def test_ban_mid_session_applies_despite_the_profile_cache(
    client: TestClient, tokens: Tokens
) -> None:
    assert (
        client.get("/suppliers", headers=tokens.headers("u5", ALLOWLISTED_EMAIL)).status_code == 200
    )
    # The next token carries the ban. The cached active profile must not let it through.
    response = client.get(
        "/suppliers", headers=tokens.headers("u5", ALLOWLISTED_EMAIL, banned=True)
    )
    assert response.status_code == 403


def test_disabled_profile_is_refused(client: TestClient, tokens: Tokens) -> None:
    ensure_user("u6", ALLOWLISTED_EMAIL, status="disabled")
    response = client.get("/me", headers=tokens.headers("u6", ALLOWLISTED_EMAIL))
    assert response.status_code == 403
    assert _code(response) == "disabled"
    # Being allowlisted doesn't re-enable a disabled account.
    assert _profile("u6")["status"] == "disabled"  # type: ignore[index]


def test_changed_email_and_role_update_the_profile(client: TestClient, tokens: Tokens) -> None:
    ensure_user("u7", "old@example.com", status="active")
    response = client.get("/me", headers=tokens.headers("u7", "new@example.com", role="admin"))
    assert response.json()["email"] == "new@example.com"
    assert response.json()["role"] == "admin"
    assert _profile("u7") == {
        "id": "u7",
        "email": "new@example.com",
        "role": "admin",
        "status": "active",
    }


def test_admitted_user_stays_active_after_an_email_change(
    client: TestClient, tokens: Tokens
) -> None:
    client.get("/me", headers=tokens.headers("u8", ALLOWLISTED_EMAIL))
    response = client.get("/me", headers=tokens.headers("u8", "elsewhere@example.com"))
    assert response.json()["status"] == "active"


def test_waitlisted_user_is_admitted_once_signups_open(
    client: TestClient, tokens: Tokens, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = tokens.headers("u9", "later@example.com")
    assert client.get("/me", headers=headers).json()["status"] == "waitlisted"
    _open_signups(monkeypatch, cap=25)
    assert client.get("/me", headers=headers).json()["status"] == "active"
    assert _profile("u9")["status"] == "active"  # type: ignore[index]


def test_user_cap_waitlists_new_sign_ups_but_not_allowlisted_ones(
    client: TestClient, tokens: Tokens, monkeypatch: pytest.MonkeyPatch
) -> None:
    _open_signups(monkeypatch, cap=1)
    assert (
        client.get("/me", headers=tokens.headers("c1", "c1@example.com")).json()["status"]
        == "active"
    )
    assert (
        client.get("/me", headers=tokens.headers("c2", "c2@example.com")).json()["status"]
        == "waitlisted"
    )
    # The owner always gets in, even with the cap reached.
    assert (
        client.get("/me", headers=tokens.headers("c3", ALLOWLISTED_EMAIL)).json()["status"]
        == "active"
    )


def test_simultaneous_sign_ups_cannot_exceed_the_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    _open_signups(monkeypatch, cap=2)
    results: list[str] = []
    errors: list[BaseException] = []
    barrier = threading.Barrier(5)

    def sign_in(index: int) -> None:
        identity = Identity(f"s{index}", f"s{index}@example.com", True, "user", False)
        barrier.wait()
        try:
            results.append(resolve_profile(identity, get_pool().connection).status)
        except BaseException as exc:  # noqa: BLE001 - reported by the assertion below
            errors.append(exc)

    threads = [threading.Thread(target=sign_in, args=(i,)) for i in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert not errors
    assert sorted(results) == ["active", "active", "waitlisted", "waitlisted", "waitlisted"]


def test_same_user_signing_in_twice_at_once_creates_one_profile() -> None:
    identity = Identity("dup", ALLOWLISTED_EMAIL, True, "user", False)
    barrier = threading.Barrier(3)
    statuses: list[str] = []

    def sign_in() -> None:
        barrier.wait()
        statuses.append(resolve_profile(identity, get_pool().connection).status)

    threads = [threading.Thread(target=sign_in) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert statuses == ["active"] * 3
    with get_pool().connection() as conn:
        row = conn.execute("SELECT count(*) AS n FROM user_profiles WHERE id = 'dup'").fetchone()
    assert row is not None and row["n"] == 1


def test_active_profiles_are_cached(client: TestClient, tokens: Tokens) -> None:
    headers = tokens.headers("u10", ALLOWLISTED_EMAIL)
    client.get("/me", headers=headers)
    # Removing the row proves the next lookup came from the cache, not the database.
    with get_pool().connection() as conn:
        conn.execute("DELETE FROM user_profiles WHERE id = 'u10'")
    identity = Identity("u10", ALLOWLISTED_EMAIL, True, "user", False)
    assert profile_cache.get(identity) is not None


def test_cache_is_bypassed_when_the_token_email_changes(tokens: Tokens) -> None:
    resolve_profile(Identity("u11", ALLOWLISTED_EMAIL, True, "user", False), get_pool().connection)
    assert profile_cache.get(Identity("u11", "x@example.com", True, "user", False)) is None


def test_waitlisted_profiles_are_never_cached() -> None:
    profile = resolve_profile(
        Identity("u12", "w@example.com", True, "user", False), get_pool().connection
    )
    assert profile.status == "waitlisted"
    assert profile_cache.get(Identity("u12", "w@example.com", True, "user", False)) is None


def test_access_denied_never_touches_the_database() -> None:
    def no_database() -> Any:
        raise AssertionError("must not connect")

    with pytest.raises(AccessDeniedError):
        resolve_profile(Identity("u13", ALLOWLISTED_EMAIL, True, "user", True), no_database)


def test_database_outage_during_sign_in_is_503(
    client: TestClient, tokens: Tokens, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken() -> Any:
        raise psycopg.OperationalError("down")

    monkeypatch.setattr("app.api.services.auth._pool_connection", broken)
    response = client.get("/me", headers=tokens.headers("u14", ALLOWLISTED_EMAIL))
    assert response.status_code == 503


def test_unavailable_signing_keys_are_503(client: TestClient, tokens: Tokens) -> None:
    def down(url: str, timeout: float) -> dict[str, Any]:
        raise OSError("Neon Auth is down")

    url = "https://auth.tests.invalid/neondb/auth/.well-known/jwks.json"
    get_verifier("neon_auth").set_key_cache(  # type: ignore[attr-defined]
        JwksCache(url, 3600, 30, 5, fetcher=down), url
    )
    response = client.get("/me", headers=tokens.headers("u15", ALLOWLISTED_EMAIL))
    assert response.status_code == 503
    assert _code(response) == "auth_unavailable"
