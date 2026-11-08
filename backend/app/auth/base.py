"""Token verifier protocol and registry.

Neon describes its auth service as in active development, so token checks sit behind this
small interface (D6). Swapping provider means adding a verifier, not editing callers.
"""

from typing import Protocol

from app.auth.models import Identity
from app.utils.logger import get_logger

logger = get_logger(__name__)


class TokenVerifier(Protocol):
    """Checks a bearer token without a database query."""

    name: str

    def verify(self, token: str) -> Identity:
        """Return the caller's identity.

        Raises:
            InvalidTokenError: if the token fails any check.
            AuthUnavailableError: if tokens can't be checked right now.
        """
        ...


_REGISTRY: dict[str, TokenVerifier] = {}


def register_verifier(verifier: TokenVerifier) -> TokenVerifier:
    """Add a verifier instance under its name, replacing any with the same name."""
    _REGISTRY[verifier.name] = verifier
    logger.info("Registered token verifier: %s", verifier.name)
    return verifier


def get_registered_verifier_names() -> set[str]:
    """Return the names of all registered verifiers."""
    return set(_REGISTRY)


def get_verifier(name: str) -> TokenVerifier:
    """Return a registered verifier.

    Raises:
        ValueError: if none has this name, naming the ones that exist.
    """
    if name not in _REGISTRY:
        available = ", ".join(sorted(_REGISTRY)) or "(none)"
        raise ValueError(f"Unknown token verifier: {name!r}. Registered: {available}")
    return _REGISTRY[name]
