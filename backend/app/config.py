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
from urllib.parse import urlparse

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
    return {"enabled": _as_bool(get_config()["processing"]["enabled"])}


def _as_bool(value: Any) -> bool:
    return str(value).strip().lower() == "true"


def _as_list(value: Any) -> list[str]:
    """Split a comma-separated string, or copy a YAML list, dropping empty items."""
    items = value if isinstance(value, list) else str(value).split(",")
    return [str(item).strip() for item in items if str(item).strip()]


def get_auth_config() -> dict[str, Any]:
    """Return sign-in settings.

    Keys: base_url, origin (the token issuer and audience, derived from base_url), jwks_url,
    verifier, algorithms (list), leeway_seconds, jwks_timeout_seconds, jwks_cache_seconds,
    jwks_min_refetch_seconds, profile_cache_seconds, profile_cache_max_entries.
    """
    section = dict(get_config()["auth"])
    base_url = str(section["base_url"]).strip().rstrip("/")
    parsed = urlparse(base_url)
    section["base_url"] = base_url
    # Neon sets both iss and aud to the origin of the auth URL, not the full URL.
    section["origin"] = f"{parsed.scheme}://{parsed.netloc}" if parsed.netloc else ""
    section["jwks_url"] = f"{base_url}/.well-known/jwks.json" if base_url else ""
    section["algorithms"] = _as_list(section["algorithms"])
    return section


def get_access_config() -> dict[str, Any]:
    """Return who may use the app.

    Keys: signups_enabled (bool), allowlisted_emails (set of lowercase emails),
    max_total_users (int).
    """
    section = get_config()["access"]
    return {
        "signups_enabled": _as_bool(section["signups_enabled"]),
        "allowlisted_emails": {email.lower() for email in _as_list(section["allowlisted_emails"])},
        "max_total_users": int(section["max_total_users"]),
    }


def get_limits_config() -> dict[str, int]:
    """Return the default per-user limits, keyed by limit name."""
    return {name: int(value) for name, value in get_config()["limits"].items()}


def get_valid_limit_names() -> set[str]:
    """Return the limit names that user_limits.overrides may use."""
    return set(get_config()["limits"])


def get_catalog_config() -> dict[str, int]:
    """Return catalog settings. Keys: retention_days, max_name_length, max_list_items."""
    return {name: int(value) for name, value in get_config()["catalog"].items()}


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
    """Return admin test-run bounds. Keys: max_chunks, max_sleep_seconds."""
    return dict(get_config()["test_runs"])


def get_storage_config() -> dict[str, Any]:
    """Return object storage settings.

    Keys: backend, bucket, prefix (no slashes at either end), region,
    connect_timeout_seconds, read_timeout_seconds.

    Raises:
        ValueError: if the prefix is empty, since every key must sit under an environment.
    """
    section = dict(get_config()["storage"])
    section["prefix"] = str(section["prefix"]).strip().strip("/")
    if not section["prefix"]:
        raise ValueError("storage.prefix is empty; set S3_PREFIX to prod, dev or test")
    return section


def get_uploads_config() -> dict[str, Any]:
    """Return upload settings.

    Keys: upload_url_seconds, max_original_name_length, types (extension to a dict with keys
    kind and content_type).
    """
    section = get_config()["uploads"]
    return {
        "upload_url_seconds": int(section["upload_url_seconds"]),
        "max_original_name_length": int(section["max_original_name_length"]),
        "types": {ext.lower(): dict(spec) for ext, spec in section["types"].items()},
    }


def get_ingest_config() -> dict[str, int]:
    """Return the worker's file checks.

    Keys: zip_max_entries, zip_max_total_bytes, zip_max_entry_bytes, zip_max_ratio,
    zip_ratio_min_bytes, max_image_pixels, csv_sniff_bytes, pdf_image_min_side,
    pdf_max_images_per_file.
    """
    return {name: int(value) for name, value in get_config()["ingest"].items()}


def get_extraction_config() -> dict[str, Any]:
    """Return spreadsheet extraction settings.

    Keys: extractor, header_scan_rows, sample_rows, sample_cell_chars, chunk_rows,
    max_rows_per_sheet, max_columns, max_sheets_per_file, max_row_bytes, max_merged_ranges,
    max_row_notes_per_chunk, max_llm_calls_per_run, min_llm_confidence (float),
    mapping_version, identifier_attributes (list of attribute codes, most preferred first),
    header_names (attribute code to its known column names).
    """
    section = dict(get_config()["extraction"])
    for name, value in section.items():
        if name not in ("extractor", "min_llm_confidence", "identifier_attributes", "header_names"):
            section[name] = int(value)
    section["min_llm_confidence"] = float(section["min_llm_confidence"])
    section["identifier_attributes"] = _as_list(section["identifier_attributes"])
    section["header_names"] = {
        str(code): _as_list(names) for code, names in section["header_names"].items()
    }
    return section


def get_llm_config() -> dict[str, Any]:
    """Return LLM settings shared by every task.

    Keys: backend, region, connect_timeout_seconds, monthly_spend_cap_usd (float, or None for
    no cap), prices (model ID to a dict with keys input and output, in USD per million tokens).
    """
    section = get_config()["llm"]
    cap = section.get("monthly_spend_cap_usd")
    return {
        "backend": str(section["backend"]),
        "region": str(section["region"]),
        "connect_timeout_seconds": int(section["connect_timeout_seconds"]),
        "monthly_spend_cap_usd": None if cap in (None, "") else float(cap),
        "prices": {
            str(model): {"input": float(price["input"]), "output": float(price["output"])}
            for model, price in section["prices"].items()
        },
    }


def get_llm_task_config(task: str) -> dict[str, Any]:
    """Return one LLM task's settings.

    Keys: model_id, prompt, prompt_version, max_tokens, temperature (float), timeout_seconds,
    max_attempts.

    Raises:
        ValueError: if the task isn't configured, naming the ones that are.
    """
    tasks = get_config()["llm"]["tasks"]
    if task not in tasks:
        raise ValueError(f"Unknown LLM task: {task!r}. Configured: {', '.join(sorted(tasks))}")
    section = dict(tasks[task])
    return {
        "model_id": str(section["model_id"]),
        "prompt": str(section["prompt"]),
        "prompt_version": str(section["prompt_version"]),
        "max_tokens": int(section["max_tokens"]),
        "temperature": float(section["temperature"]),
        "timeout_seconds": int(section["timeout_seconds"]),
        "max_attempts": int(section["max_attempts"]),
    }


def get_documents_config() -> dict[str, Any]:
    """Return PDF and photo reading settings.

    Keys: extractor_text, extractor_ocr, min_page_chars, render_dpi, ocr_engine,
    ocr_min_confidence (float), max_llm_chars, max_llm_calls_per_run, max_notes_per_run,
    min_key_chars_for_loose_match.
    """
    section = dict(get_config()["documents"])
    text_keys = ("extractor_text", "extractor_ocr", "ocr_engine")
    for name, value in section.items():
        if name == "ocr_min_confidence":
            section[name] = float(value)
        elif name not in text_keys:
            section[name] = int(value)
    return section


def get_images_config() -> dict[str, Any]:
    """Return image processing settings.

    Keys: chunk_size, thumbnail_side, thumbnail_quality, embed_max_side, embed_min_side,
    duplicate_min_similarity (float), duplicate_max_colour_distance (float),
    thumbnail_url_seconds.
    """
    section = dict(get_config()["images"])
    floats = ("duplicate_min_similarity", "duplicate_max_colour_distance")
    return {name: float(value) if name in floats else int(value) for name, value in section.items()}


def get_embeddings_config() -> dict[str, Any]:
    """Return image embedding settings.

    Keys: backend, provider, model_id, model_version, dimension, region, timeout_seconds,
    price_per_image (float).
    """
    section = dict(get_config()["embeddings"])
    return {
        "backend": str(section["backend"]),
        "provider": str(section["provider"]),
        "model_id": str(section["model_id"]),
        "model_version": str(section["model_version"]),
        "dimension": int(section["dimension"]),
        "region": str(section["region"]),
        "timeout_seconds": int(section["timeout_seconds"]),
        "price_per_image": float(section["price_per_image"]),
    }


def get_matching_config() -> dict[str, Any]:
    """Return image association settings.

    Keys: weights (signal name to float), visual_min_similarity (float),
    min_text_match_chars, review_threshold (float), max_candidates_per_image.
    """
    section = dict(get_config()["matching"])
    return {
        "weights": {str(name): float(value) for name, value in section["weights"].items()},
        "visual_min_similarity": float(section["visual_min_similarity"]),
        "min_text_match_chars": int(section["min_text_match_chars"]),
        "review_threshold": float(section["review_threshold"]),
        "max_candidates_per_image": int(section["max_candidates_per_image"]),
    }


def get_normalization_config() -> dict[str, Any]:
    """Return normalization settings.

    Keys: rule_version, default_currency, dollar_currency, agreement_tolerance (float),
    source_ranks (extractor to int), standard_units (family to unit), units (family to
    {alias: factor}), currencies (alias to code), approximate_words (list), category
    ({enabled, min_confidence}), variants ({colour_words, size_words, code_suffix_max_chars,
    image_support_min_similarity}).
    """
    section = get_config()["normalization"]
    variants = dict(section["variants"])
    return {
        "rule_version": str(section["rule_version"]),
        "default_currency": str(section["default_currency"]),
        "dollar_currency": str(section["dollar_currency"]),
        "agreement_tolerance": float(section["agreement_tolerance"]),
        "source_ranks": {str(k): int(v) for k, v in section["source_ranks"].items()},
        "standard_units": {str(k): str(v) for k, v in section["standard_units"].items()},
        "units": {
            str(family): {str(alias).lower(): float(factor) for alias, factor in aliases.items()}
            for family, aliases in section["units"].items()
        },
        "currencies": {str(k).lower(): str(v) for k, v in section["currencies"].items()},
        "approximate_words": [str(word).lower() for word in section["approximate_words"]],
        "category": {
            "enabled": bool(section["category"]["enabled"]),
            "min_confidence": float(section["category"]["min_confidence"]),
        },
        "variants": {
            "colour_words": [str(w).lower() for w in variants["colour_words"]],
            "size_words": [str(w).lower() for w in variants["size_words"]],
            "code_suffix_max_chars": int(variants["code_suffix_max_chars"]),
            "image_support_min_similarity": float(variants["image_support_min_similarity"]),
        },
    }


def get_enrichment_config() -> dict[str, Any]:
    """Return enrichment settings (D37).

    Keys: rules_version, usable_states, excluded_attributes, title_only_attributes (lists),
    max_versions_per_product, title_max_chars, bullets_min, bullets_max, bullet_max_chars,
    sentences_min, sentences_max, description_max_chars, fact_max_chars (ints), claim_phrases,
    material_words, banned_phrases (lowercased lists).
    """
    section = get_config()["enrichment"]
    ints = (
        "max_versions_per_product",
        "title_max_chars",
        "bullets_min",
        "bullets_max",
        "bullet_max_chars",
        "sentences_min",
        "sentences_max",
        "description_max_chars",
        "fact_max_chars",
    )
    lists = (
        "usable_states",
        "excluded_attributes",
        "title_only_attributes",
        "claim_phrases",
        "material_words",
        "banned_phrases",
    )
    return {
        "rules_version": str(section["rules_version"]),
        **{key: int(section[key]) for key in ints},
        **{key: [str(item).lower() for item in section[key]] for key in lists},
    }
