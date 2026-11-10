"""Runs one LLM task: render its prompt, call the model, check the reply, and record the call.

Every call is recorded in model_runs with its tokens and cost, whether it worked or not. A
request that already succeeded for the same user is answered from the record, so a retried job
doesn't pay twice. No provider feature is relied on for structure: the reply's JSON is checked
by the caller's own parser, and an unusable reply is tried again a limited number of times.
"""

import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb

from app.config import get_llm_config, get_llm_task_config
from app.db import get_pool, query
from app.llm.base import LlmReply, LlmRequest, LlmUnavailableError, get_chat_backend
from app.llm.prompts import render_prompt
from app.utils.logger import get_logger

logger = get_logger(__name__)

_MAX_ERROR_LENGTH = 1000
_TOKENS_PER_PRICE_UNIT = Decimal(1_000_000)


class LlmTaskError(Exception):
    """The task got no usable reply within its attempts.

    Attributes:
        calls: How many calls were made before giving up.
    """

    def __init__(self, message: str, calls: int) -> None:
        super().__init__(message)
        self.calls = calls


@dataclass(frozen=True)
class TaskResult[T]:
    """A task's checked result.

    Attributes:
        value: What the caller's parser returned.
        model_run_id: The record of the call that produced it.
        calls: Calls made now. 0 means the result was reused from an earlier call.
    """

    value: T
    model_run_id: UUID
    calls: int


def extract_json(text: str) -> dict[str, Any]:
    """Return the JSON object in a reply, ignoring anything around it such as code fences.

    Raises:
        ValueError: if the reply holds no JSON object.
    """
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("The reply contains no JSON object")
    try:
        parsed = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ValueError(f"The reply isn't valid JSON: {exc.msg}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("The reply isn't a JSON object")
    return parsed


def _cost(model_id: str, reply: LlmReply) -> Decimal | None:
    price = get_llm_config()["prices"].get(model_id)
    if price is None or reply.input_tokens is None or reply.output_tokens is None:
        return None
    cost = (
        Decimal(str(price["input"])) * reply.input_tokens
        + Decimal(str(price["output"])) * reply.output_tokens
    ) / _TOKENS_PER_PRICE_UNIT
    return cost.quantize(Decimal("0.000001"))


def _record(
    *,
    owner: str,
    job_id: UUID | None,
    task: str,
    config: dict[str, Any],
    input_hash: str,
    status: str,
    response: dict[str, Any] | None,
    reply: LlmReply | None,
    latency_ms: int,
    error: str | None,
) -> UUID | None:
    """Store one call. Returns its ID, or None if the same request had already succeeded."""
    with get_pool().connection() as conn:
        row = conn.execute(
            query("llm", "insert_run"),
            {
                "owner": owner,
                "job_id": job_id,
                "task": task,
                "provider": get_chat_backend().name,
                "model_id": config["model_id"],
                "prompt_version": config["prompt_version"],
                "input_hash": input_hash,
                "status": status,
                "response": Jsonb(response) if response is not None else None,
                "input_tokens": reply.input_tokens if reply else None,
                "output_tokens": reply.output_tokens if reply else None,
                "cost_usd": _cost(config["model_id"], reply) if reply else None,
                "latency_ms": latency_ms,
                "error": error[:_MAX_ERROR_LENGTH] if error else None,
            },
        ).fetchone()
    return row["id"] if row else None


def _find_succeeded(owner: str, input_hash: str) -> dict[str, Any] | None:
    with get_pool().connection() as conn:
        row: dict[str, Any] | None = conn.execute(
            query("llm", "find_succeeded"), {"owner": owner, "input_hash": input_hash}
        ).fetchone()
    return row


def run_task[T](
    task: str,
    *,
    owner: str,
    job_id: UUID | None,
    variables: dict[str, Any],
    parse: Callable[[dict[str, Any]], T],
    to_record: Callable[[T], dict[str, Any]],
) -> TaskResult[T]:
    """Run a configured task and return its checked result.

    Args:
        task: A key under llm.tasks in config.yaml.
        owner: The user the work is for. Results are never shared between users.
        job_id: The job making the call, for the record.
        variables: Values the task's prompt template renders.
        parse: Checks the reply's JSON and returns the result. It raises ValueError (which
            includes pydantic's ValidationError) if the reply is unusable.
        to_record: Turns the result into the JSON stored for reuse. `parse` must accept it.

    Raises:
        LlmTaskError: if no usable reply came back within the task's attempts.
    """
    config = get_llm_task_config(task)
    system, user = render_prompt(config["prompt"], **variables)
    input_hash = hashlib.sha256(
        json.dumps(
            [
                task,
                config["model_id"],
                config["prompt_version"],
                config["max_tokens"],
                config["temperature"],
                system,
                user,
            ],
            ensure_ascii=False,
        ).encode()
    ).hexdigest()

    stored = _find_succeeded(owner, input_hash)
    if stored is not None:
        try:
            value = parse(stored["response"])
        except ValueError:
            # The rules changed since this was stored. Ask again rather than trust it.
            logger.warning("Stored LLM reply no longer valid: task=%s", task)
        else:
            logger.info("LLM reply reused: task=%s model_run_id=%s", task, stored["id"])
            return TaskResult(value=value, model_run_id=stored["id"], calls=0)

    backend = get_chat_backend()
    last_error = "No attempt was made"
    calls = 0
    for attempt in range(1, config["max_attempts"] + 1):
        # The model is told what was wrong with its last reply, so a retry isn't a repeat.
        feedback = (
            ""
            if attempt == 1
            else f"\n\nYour previous reply was rejected: {last_error}\n"
            "Reply again with only the JSON object described."
        )
        request = LlmRequest(
            model_id=config["model_id"],
            system=system,
            user=user + feedback,
            max_tokens=config["max_tokens"],
            temperature=config["temperature"],
            timeout_seconds=config["timeout_seconds"],
        )
        started = time.monotonic()
        calls += 1
        reply: LlmReply | None = None
        try:
            reply = backend.complete(request)
            value = parse(extract_json(reply.text))
        except (LlmUnavailableError, ValueError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"[:_MAX_ERROR_LENGTH]
            latency_ms = int((time.monotonic() - started) * 1000)
            _record(
                owner=owner,
                job_id=job_id,
                task=task,
                config=config,
                input_hash=input_hash,
                status="failed",
                response=None,
                reply=reply,
                latency_ms=latency_ms,
                error=last_error,
            )
            logger.warning(
                "LLM attempt failed: task=%s attempt=%d/%d error_class=%s",
                task,
                attempt,
                config["max_attempts"],
                type(exc).__name__,
            )
            continue
        latency_ms = int((time.monotonic() - started) * 1000)
        run_id = _record(
            owner=owner,
            job_id=job_id,
            task=task,
            config=config,
            input_hash=input_hash,
            status="succeeded",
            response=to_record(value),
            reply=reply,
            latency_ms=latency_ms,
            error=None,
        )
        if run_id is None:
            # Another attempt of this job stored the same request first.
            existing = _find_succeeded(owner, input_hash)
            assert existing is not None  # noqa: S101
            run_id = existing["id"]
        logger.info(
            "LLM task succeeded: task=%s model_id=%s attempt=%d latency_ms=%d "
            "input_tokens=%s output_tokens=%s",
            task,
            config["model_id"],
            attempt,
            latency_ms,
            reply.input_tokens,
            reply.output_tokens,
        )
        return TaskResult(value=value, model_run_id=run_id, calls=calls)
    raise LlmTaskError(last_error, calls)
