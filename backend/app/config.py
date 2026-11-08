"""Configuration: the only module that reads config.yaml and environment variables.

Tunables live in config.yaml and secrets in .env. Values written as ${VAR} or
${VAR:-default} are resolved when the file loads, so a missing required variable
fails at startup rather than in the middle of a request.
"""

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

BACKEND_ROOT = Path(__file__).resolve().parents[1]
_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_]\w*)(?::-([^}]*))?\}")


def _resolve_env(node: Any) -> Any:
    """Recursively expand ${VAR} and ${VAR:-default}.

    Raises:
        ValueError: if a variable with no default is unset.
    """
    if isinstance(node, dict):
        return {key: _resolve_env(value) for key, value in node.items()}
    if isinstance(node, list):
        return [_resolve_env(value) for value in node]
    if not isinstance(node, str):
        return node

    def _substitute(match: re.Match[str]) -> str:
        value = os.getenv(match.group(1), match.group(2))
        if value is None:
            raise ValueError(f"config.yaml references ${{{match.group(1)}}} but it is not set")
        return value

    return _ENV_PATTERN.sub(_substitute, node)


@lru_cache(maxsize=1)
def get_config() -> dict[str, Any]:
    """Load the config file once. The APP_CONFIG environment variable overrides its path.

    Returns:
        The resolved config. Callers must not mutate it.
    """
    load_dotenv(BACKEND_ROOT / ".env")
    path = Path(os.getenv("APP_CONFIG", str(BACKEND_ROOT / "config.yaml")))
    with path.open(encoding="utf-8") as config_file:
        resolved: dict[str, Any] = _resolve_env(yaml.safe_load(config_file))
    return resolved


def get_app_config() -> dict[str, str]:
    """Return app settings. Keys: environment, code_sha."""
    return dict(get_config()["app"])


def get_logging_config() -> dict[str, str]:
    """Return logging settings. Keys: level."""
    return dict(get_config()["logging"])


def get_api_config() -> dict[str, Any]:
    """Return API settings. Keys: host, port (int), cors_origins (list), cors_max_age_seconds."""
    section = dict(get_config()["api"])
    section["port"] = int(section["port"])
    section["cors_origins"] = [
        origin.strip() for origin in section["cors_origins"].split(",") if origin.strip()
    ]
    return section


def get_database_config() -> dict[str, Any]:
    """Return database settings.

    Keys: url, direct_url, connect_timeout_seconds, pool_min_size, pool_max_size,
    pool_max_idle_seconds.
    """
    return dict(get_config()["database"])


def get_processing_config() -> dict[str, bool]:
    """Return the emergency stop setting. Keys: enabled (bool)."""
    return {"enabled": str(get_config()["processing"]["enabled"]).strip().lower() == "true"}


def get_admin_config() -> dict[str, str]:
    """Return temporary admin settings. Keys: token."""
    return dict(get_config()["admin"])


def get_compute_config() -> dict[str, Any]:
    """Return compute settings.

    Keys: worker_controller, instance_id, region, heartbeat_fresh_seconds,
    start_cooldown_seconds, start_retry_seconds, start_retry_max_attempts,
    max_ec2_hours_per_month.
    """
    return dict(get_config()["compute"])


def get_queue_config() -> dict[str, int]:
    """Return queue settings.

    Keys: lease_seconds, max_attempts, backoff_base_seconds, backoff_max_seconds.
    """
    return dict(get_config()["queue"])


def get_worker_config() -> dict[str, Any]:
    """Return worker settings.

    Keys: power_controller, code_sha, ec2_instance_id, heartbeat_file, power_request_file,
    heartbeat_seconds, poll_busy_seconds, poll_idle_seconds, busy_window_seconds,
    idle_timeout_seconds, stop_grace_seconds, reaper_interval_seconds, max_job_seconds,
    production_db_endpoint.
    """
    return dict(get_config()["worker"])


def get_pipeline_config(name: str) -> list[dict[str, Any]]:
    """Return a pipeline's stages, each with keys: stage, job_type.

    Raises:
        ValueError: if the pipeline doesn't exist, naming the ones that do.
    """
    pipelines = get_config()["pipelines"]
    if name not in pipelines:
        raise ValueError(f"Unknown pipeline: {name!r}. Available: {', '.join(sorted(pipelines))}")
    return [dict(stage) for stage in pipelines[name]]


def get_test_run_config() -> dict[str, int]:
    """Return temporary test-run bounds. Keys: max_chunks, max_sleep_seconds."""
    return dict(get_config()["test_runs"])
