"""Shared fixtures. No test needs real AWS or Neon access.

Tests that need a database use the `db` fixture, which runs only when TEST_DATABASE_URL is set.
Tokens are signed with keys generated here, and served through a stubbed JWKS fetch, so no
test calls Neon Auth.
"""

import os
from pathlib import Path
from urllib.parse import urlparse

from dotenv import dotenv_values

BACKEND = Path(__file__).resolve().parents[1]
_DOTENV = dotenv_values(BACKEND / ".env")
# The ep-... ID of the production branch, kept out of the repo. Unset skips that part of the check.
_PRODUCTION_ENDPOINT = (
    os.environ.get("PRODUCTION_DB_ENDPOINT") or _DOTENV.get("PRODUCTION_DB_ENDPOINT") or ""
)


def _test_database_url() -> str:
    """Return the test database URL, refusing any database that holds real data.

    Tests erase the queue tables, so a wrong URL here would be destructive.
    """
    url = os.environ.get("TEST_DATABASE_URL") or _DOTENV.get("TEST_DATABASE_URL") or ""
    if not url:
        return ""
    host = urlparse(url).hostname or ""
    dev_host = urlparse(_DOTENV.get("DATABASE_URL") or "").hostname
    if (_PRODUCTION_ENDPOINT and _PRODUCTION_ENDPOINT in host) or host == dev_host:
        raise RuntimeError(f"TEST_DATABASE_URL points at a real database: {host}")
    return url


# Set before anything loads config, so tests can never reach the other URLs in a local .env file.
_TEST_DATABASE_URL = _test_database_url()
os.environ["TEST_DATABASE_URL"] = _TEST_DATABASE_URL
_SAFE_URL = _TEST_DATABASE_URL or "postgresql://tests-have-no-database.invalid/none"
os.environ["DATABASE_URL"] = _SAFE_URL
os.environ["DATABASE_URL_DIRECT"] = _SAFE_URL
# Sign-in points at a host that can't resolve, and access rules are fixed for every test.
AUTH_BASE_URL = "https://auth.tests.invalid/neondb/auth"
AUTH_ORIGIN = "https://auth.tests.invalid"
ALLOWLISTED_EMAIL = "owner@example.com"
os.environ["NEON_AUTH_BASE_URL"] = AUTH_BASE_URL
os.environ["ACCESS_ALLOWLIST"] = ALLOWLISTED_EMAIL
os.environ["SIGNUPS_ENABLED"] = "false"
# Storage stays in memory. The live check in test_storage_live.py opts in to S3 explicitly.
os.environ["STORAGE_BACKEND"] = "memory"
os.environ["S3_PREFIX"] = "test"
# No test calls a real model. The live check in test_llm_live.py opts in to Bedrock explicitly.
os.environ["LLM_BACKEND"] = "fake"
os.environ["EMBEDDINGS_BACKEND"] = "fake"

import subprocess  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402
from collections.abc import Iterator  # noqa: E402
from typing import Any  # noqa: E402

import jwt  # noqa: E402
import psycopg  # noqa: E402
import pytest  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from jwt.algorithms import OKPAlgorithm  # noqa: E402

from app.api.main import app  # noqa: E402
from app.auth import get_verifier, profile_cache  # noqa: E402
from app.auth.jwks import JwksCache  # noqa: E402
from app.config import get_config  # noqa: E402
from app.db import close_pool, get_pool  # noqa: E402
from app.llm import get_chat_backend  # noqa: E402
from app.llm.backends.fake import FakeBackend  # noqa: E402
from app.storage import get_object_store  # noqa: E402
from app.storage.backends.memory import MemoryObjectStore  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_config() -> Iterator[None]:
    """Reload config and the pool around each test, so changes can't leak between tests."""
    get_config.cache_clear()
    close_pool()
    yield
    close_pool()
    get_config.cache_clear()


class Tokens:
    """Signs Neon-Auth-shaped tokens with keys this test controls.

    Attributes:
        jwks: The published key set. Tests change it to simulate key rotation.
        fetches: How many times the API fetched the key set.
    """

    def __init__(self) -> None:
        self.keys: dict[str, Ed25519PrivateKey] = {}
        self.jwks: dict[str, Any] = {"keys": []}
        self.fetches = 0
        self.add_key("key-1")

    def add_key(self, kid: str, publish: bool = True) -> Ed25519PrivateKey:
        """Create a signing key, publishing its public half unless told not to."""
        private = Ed25519PrivateKey.generate()
        self.keys[kid] = private
        if publish:
            jwk = OKPAlgorithm.to_jwk(private.public_key(), as_dict=True)
            self.jwks["keys"].append({**jwk, "kid": kid, "alg": "EdDSA", "use": "sig"})
        return private

    def fetch(self, url: str, timeout: float) -> dict[str, Any]:
        """Stands in for the JWKS download."""
        assert url == f"{AUTH_BASE_URL}/.well-known/jwks.json"
        self.fetches += 1
        return {"keys": list(self.jwks["keys"])}

    def make(
        self,
        sub: str = "user-a",
        email: str = "a@example.com",
        *,
        kid: str = "key-1",
        role: str = "authenticated",
        banned: bool = False,
        email_verified: bool = True,
        expires_in: int = 900,
        **overrides: Any,
    ) -> str:
        """Return a signed token. `overrides` replaces or adds claims; None removes one."""
        now = int(time.time())
        claims: dict[str, Any] = {
            "sub": sub,
            "id": sub,
            "email": email,
            "emailVerified": email_verified,
            "role": role,
            "banned": banned,
            "iat": now,
            "exp": now + expires_in,
            "iss": AUTH_ORIGIN,
            "aud": AUTH_ORIGIN,
        }
        claims.update(overrides)
        claims = {key: value for key, value in claims.items() if value is not None}
        return jwt.encode(claims, self.keys[kid], algorithm="EdDSA", headers={"kid": kid})

    def headers(self, sub: str = "user-a", email: str = "a@example.com", **kwargs: Any) -> dict:
        """Authorization headers carrying a fresh token."""
        return {"Authorization": f"Bearer {self.make(sub, email, **kwargs)}"}


@pytest.fixture(autouse=True)
def tokens() -> Iterator[Tokens]:
    """Install fresh signing keys and an empty profile cache for every test."""
    factory = Tokens()
    url = f"{AUTH_BASE_URL}/.well-known/jwks.json"
    cache = JwksCache(url, 3600, 30, 5, fetcher=factory.fetch)
    get_verifier("neon_auth").set_key_cache(cache, url)  # type: ignore[attr-defined]
    profile_cache.clear()
    yield factory
    profile_cache.clear()


def ensure_user(
    user_id: str,
    email: str | None = None,
    *,
    status: str = "active",
    role: str = "user",
    overrides: dict[str, Any] | None = None,
) -> str:
    """Create a profile directly in the database, if it doesn't exist. Returns its ID."""
    with get_pool().connection() as conn:
        conn.execute(
            "INSERT INTO user_profiles (id, email, role, status) VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (id) DO NOTHING",
            (user_id, email or f"{user_id}@example.com", role, status),
        )
        if overrides is not None:
            conn.execute(
                "INSERT INTO user_limits (user_id, overrides) VALUES (%s, %s) "
                "ON CONFLICT (user_id) DO UPDATE SET overrides = EXCLUDED.overrides",
                (user_id, psycopg.types.json.Jsonb(overrides)),
            )
    return user_id


@pytest.fixture
def store() -> Iterator[MemoryObjectStore]:
    """The in-memory object store, emptied before and after each test."""
    memory = get_object_store()
    assert isinstance(memory, MemoryObjectStore)
    memory.clear()
    yield memory
    memory.clear()


@pytest.fixture
def llm() -> Iterator[FakeBackend]:
    """The scripted LLM, with no replies queued. A call with nothing queued fails."""
    fake = get_chat_backend()
    assert isinstance(fake, FakeBackend)
    fake.clear()
    yield fake
    fake.clear()


@pytest.fixture(autouse=True)
def categories_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """Choosing categories calls the LLM for every product, so tests run without it.

    A test about categories puts back the real settings with `categories_on`.
    """
    from app.jobs.handlers import normalize

    real = normalize.get_normalization_config

    def without_categories() -> dict[str, Any]:
        settings = real()
        settings["category"] = {**settings["category"], "enabled": False}
        return settings

    monkeypatch.setattr(normalize, "get_normalization_config", without_categories)


@pytest.fixture(autouse=True)
def auto_copy_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """Writing copy at the end of processing calls the LLM for every product, so tests run
    without it. A test about it puts it back with `auto_copy_on`."""
    from app.jobs.handlers import enrich

    real = enrich.get_enrichment_config

    def without_auto() -> dict[str, Any]:
        return {**real(), "auto": False}

    monkeypatch.setattr(enrich, "get_enrichment_config", without_auto)


def auto_copy_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """Write copy at the end of processing in this test, as configured."""
    from app.config import get_enrichment_config
    from app.jobs.handlers import enrich

    monkeypatch.setattr(enrich, "get_enrichment_config", get_enrichment_config)


def categories_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """Choose categories in this test, as configured."""
    from app.config import get_normalization_config
    from app.jobs.handlers import normalize

    monkeypatch.setattr(normalize, "get_normalization_config", get_normalization_config)


@pytest.fixture
def client() -> TestClient:
    """A client running the real app, so middleware and validators are exercised."""
    return TestClient(app)


@pytest.fixture(scope="session")
def migrated_database() -> str:
    """Bring the test database up to the latest migration, once per test session."""
    if not _TEST_DATABASE_URL:
        pytest.skip("needs a real Postgres: set TEST_DATABASE_URL")
    # Migration 0003 refuses to run while queue rows without profiles exist. This database
    # only ever holds test data, so they're cleared rather than kept.
    with psycopg.connect(_TEST_DATABASE_URL, autocommit=True) as conn:
        conn.execute(
            "DO $$ BEGIN IF to_regclass('public.user_profiles') IS NULL "
            "AND to_regclass('public.pipeline_runs') IS NOT NULL THEN "
            "TRUNCATE pipeline_runs CASCADE; END IF; END $$"
        )
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return _TEST_DATABASE_URL


@pytest.fixture
def db(migrated_database: str) -> Iterator[str]:
    """A clean queue, with no profiles, suppliers or batches. Yields the database URL."""
    with psycopg.connect(migrated_database, autocommit=True) as conn:
        conn.execute(
            "TRUNCATE pipeline_runs, worker_instances, worker_sessions, user_profiles CASCADE"
        )
        conn.execute(
            "UPDATE worker_control SET state = 'stopped', pause_reason = NULL, "
            "pause_expires_at = NULL, last_start_requested_at = NULL"
        )
    yield migrated_database
