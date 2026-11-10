"""The ChatBackend contract and its registry. Only the worker uses LLMs (D24).

A backend sends one system message and one user message, and returns the model's text. What
the text means, and whether it is acceptable, is decided by the caller.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from app.config import get_llm_config
from app.utils.logger import get_logger

logger = get_logger(__name__)


class LlmUnavailableError(Exception):
    """The model couldn't be reached, was throttled, or timed out."""


@dataclass(frozen=True)
class LlmRequest:
    """One call to a model."""

    model_id: str
    system: str
    user: str
    max_tokens: int
    temperature: float
    timeout_seconds: int


@dataclass(frozen=True)
class LlmReply:
    """A model's answer.

    Attributes:
        text: The answer only. A model's reasoning is never included.
        input_tokens: Tokens sent, or None if the provider didn't say.
        output_tokens: Tokens received, or None if the provider didn't say.
    """

    text: str
    input_tokens: int | None
    output_tokens: int | None


class ChatBackend(Protocol):
    """Calls models. Implementations must be safe to share between threads."""

    name: str

    def complete(self, request: LlmRequest) -> LlmReply:
        """Send the request and return the reply.

        Raises:
            LlmUnavailableError: if no reply could be obtained.
        """
        ...


_REGISTRY: dict[str, Callable[[], ChatBackend]] = {}
_INSTANCES: dict[str, ChatBackend] = {}


def register_chat_backend(name: str, factory: Callable[[], ChatBackend]) -> None:
    """Make a backend available under a name."""
    _REGISTRY[name] = factory


def get_chat_backend() -> ChatBackend:
    """Return the configured backend, created once.

    Raises:
        ValueError: if llm.backend names no registered backend.
    """
    import app.llm.backends  # noqa: F401 - registers the backends

    name = get_llm_config()["backend"]
    if name not in _REGISTRY:
        raise ValueError(
            f"Unknown LLM backend: {name!r}. Registered: {', '.join(sorted(_REGISTRY))}"
        )
    if name not in _INSTANCES:
        _INSTANCES[name] = _REGISTRY[name]()
        logger.info("LLM backend ready: backend=%s", name)
    return _INSTANCES[name]
