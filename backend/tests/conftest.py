"""Shared fixtures. No test needs real AWS or Neon access."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.api.main import app
from app.config import get_config


@pytest.fixture(autouse=True)
def fresh_config() -> Iterator[None]:
    """Reload config around each test, so environment changes can't leak between tests."""
    get_config.cache_clear()
    yield
    get_config.cache_clear()


@pytest.fixture
def client() -> TestClient:
    """A client running the real app, so middleware and validators are exercised."""
    return TestClient(app)
