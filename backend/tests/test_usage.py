"""Covers POC limits and the usage ledger against real Postgres, since the concurrency rule
depends on row locks. Needs TEST_DATABASE_URL.
"""

import threading
from typing import Any

import pytest

from app.db import get_pool
from app.usage import LimitExceededError, effective_limits, record_submission, restore_entry
from app.usage.ledger import get_usage_summary
from conftest import ensure_user


def test_effective_limits_use_config_defaults() -> None:
    limits = effective_limits(None)
    assert limits["submissions_lifetime"] == 2
    assert limits["submissions_per_24_hours"] == 1
    assert limits["max_products_per_submission"] == 5


def test_overrides_replace_defaults_and_null_means_unlimited() -> None:
    limits = effective_limits({"submissions_lifetime": 10, "submissions_per_24_hours": None})
    assert limits["submissions_lifetime"] == 10
    assert limits["submissions_per_24_hours"] is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"no_such_limit": 5},
        {"submissions_lifetime": -1},
        {"submissions_lifetime": "lots"},
        {"submissions_lifetime": True},  # A JSON true must not become 1.
        {"submissions_lifetime": 2.5},
    ],
)
def test_bad_overrides_are_ignored(overrides: dict[str, Any]) -> None:
    assert effective_limits(overrides)["submissions_lifetime"] == 2


def _submit(user_id: str) -> Any:
    with get_pool().connection() as conn:
        return record_submission(conn, user_id)


def _age_entries(user_id: str, hours: int) -> None:
    with get_pool().connection() as conn:
        conn.execute(
            "UPDATE usage_ledger SET created_at = created_at - make_interval(hours => %s) "
            "WHERE user_id = %s",
            (hours, user_id),
        )


@pytest.mark.usefixtures("db")
def test_rolling_24_hour_limit_blocks_the_second_submission() -> None:
    ensure_user("u1")
    _submit("u1")
    with pytest.raises(LimitExceededError) as caught:
        _submit("u1")
    assert (caught.value.limit, caught.value.allowed, caught.value.used) == (
        "submissions_per_24_hours",
        1,
        1,
    )


@pytest.mark.usefixtures("db")
def test_rolling_window_frees_up_after_24_hours() -> None:
    ensure_user("u2")
    _submit("u2")
    _age_entries("u2", 25)
    _submit("u2")


@pytest.mark.usefixtures("db")
def test_lifetime_limit_blocks_the_third_submission() -> None:
    ensure_user("u3")
    _submit("u3")
    _age_entries("u3", 25)
    _submit("u3")
    _age_entries("u3", 25)
    with pytest.raises(LimitExceededError) as caught:
        _submit("u3")
    assert caught.value.limit == "submissions_lifetime"


@pytest.mark.usefixtures("db")
def test_refused_submission_writes_nothing() -> None:
    ensure_user("u4")
    _submit("u4")
    with pytest.raises(LimitExceededError):
        _submit("u4")
    with get_pool().connection() as conn:
        assert get_usage_summary(conn, "u4").used["submissions_lifetime"] == 1


@pytest.mark.usefixtures("db")
def test_admin_override_exempts_an_account() -> None:
    ensure_user(
        "exempt",
        overrides={"submissions_lifetime": None, "submissions_per_24_hours": None},
    )
    for _ in range(5):
        _submit("exempt")


@pytest.mark.usefixtures("db")
def test_restoration_gives_a_submission_back_once() -> None:
    ensure_user("u5")
    entry = _submit("u5")
    with get_pool().connection() as conn:
        assert restore_entry(conn, "u5", entry) is not None
        # Restoring twice must not give back two submissions.
        assert restore_entry(conn, "u5", entry) is None
    _submit("u5")


@pytest.mark.usefixtures("db")
def test_restoring_another_users_entry_does_nothing() -> None:
    ensure_user("u6")
    ensure_user("u7")
    entry = _submit("u6")
    with get_pool().connection() as conn:
        assert restore_entry(conn, "u7", entry) is None
    with pytest.raises(LimitExceededError):
        _submit("u6")


@pytest.mark.usefixtures("db")
def test_submission_without_a_profile_is_refused() -> None:
    with pytest.raises(LookupError):
        _submit("nobody")


@pytest.mark.usefixtures("db")
def test_simultaneous_submissions_cannot_both_pass_a_limit() -> None:
    ensure_user("racer")
    barrier = threading.Barrier(3)
    passed: list[Any] = []
    refused: list[str] = []

    def submit() -> None:
        barrier.wait()
        try:
            passed.append(_submit("racer"))
        except LimitExceededError as exc:
            refused.append(exc.limit)

    threads = [threading.Thread(target=submit) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert len(passed) == 1
    assert refused == ["submissions_per_24_hours"] * 2


@pytest.mark.usefixtures("db")
def test_me_reports_usage(client: Any, tokens: Any) -> None:
    ensure_user("u8", "u8@example.com", overrides={"submissions_lifetime": 7})
    _submit("u8")
    body = client.get("/me", headers=tokens.headers("u8", "u8@example.com")).json()
    assert body["limits"]["submissions_lifetime"] == 7
    assert body["usage"] == {"submissions_lifetime": 1, "submissions_per_24_hours": 1}
