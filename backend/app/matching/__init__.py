"""Which product each image shows: signals, their evidence, and the decision rule (M5B)."""

from app.matching.base import (
    CODE_SIGNALS,
    Context,
    Evidence,
    ImageFacts,
    ProductFacts,
    Signal,
    get_signals,
    register_signal,
)
from app.matching.decide import (
    ACCEPTED,
    AUTO_ACCEPTED,
    HUMAN_DECISIONS,
    NEEDS_REVIEW,
    REJECTED,
    Candidate,
    associate,
)

__all__ = [
    "ACCEPTED",
    "AUTO_ACCEPTED",
    "CODE_SIGNALS",
    "HUMAN_DECISIONS",
    "NEEDS_REVIEW",
    "REJECTED",
    "Candidate",
    "Context",
    "Evidence",
    "ImageFacts",
    "ProductFacts",
    "Signal",
    "associate",
    "get_signals",
    "register_signal",
]
