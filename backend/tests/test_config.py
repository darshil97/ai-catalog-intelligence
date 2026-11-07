"""Covers config loading and environment resolution. Nothing is stubbed."""

from pathlib import Path

import pytest

from app.config import get_api_config, get_config


def _use_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, text: str) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    monkeypatch.setenv("APP_CONFIG", str(path))


def test_missing_required_variable_fails_and_names_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The name is unusual so a developer's real .env can't accidentally satisfy it.
    _use_config(tmp_path, monkeypatch, "section:\n  value: ${CATALOG_TEST_REQUIRED_VAR}\n")
    with pytest.raises(ValueError, match="CATALOG_TEST_REQUIRED_VAR"):
        get_config()


def test_default_used_when_variable_unset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _use_config(
        tmp_path, monkeypatch, "section:\n  value: ${CATALOG_TEST_OPTIONAL_VAR:-fallback}\n"
    )
    assert get_config()["section"]["value"] == "fallback"


def test_environment_overrides_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CATALOG_TEST_OPTIONAL_VAR", "from-env")
    _use_config(tmp_path, monkeypatch, "items:\n  - ${CATALOG_TEST_OPTIONAL_VAR:-fallback}\n")
    assert get_config()["items"] == ["from-env"]


def test_empty_default_is_allowed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _use_config(tmp_path, monkeypatch, "section:\n  value: ${CATALOG_TEST_OPTIONAL_VAR:-}\n")
    assert get_config()["section"]["value"] == ""


def test_api_config_converts_types(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PORT", "9001")
    # Spaces and a trailing comma are the kind of slip made when editing env settings.
    monkeypatch.setenv("CORS_ORIGINS", "https://a.example, https://b.example,")
    config = get_api_config()
    assert config["port"] == 9001
    assert config["cors_origins"] == ["https://a.example", "https://b.example"]


def test_accessor_result_can_be_changed_without_affecting_the_cache() -> None:
    get_api_config()["host"] = "changed"
    assert get_api_config()["host"] != "changed"
