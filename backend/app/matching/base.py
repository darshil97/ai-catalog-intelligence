"""The Signal contract, its registry, and the facts signals look at.

A signal looks at one image and one product and says whether it found evidence they belong
together, with how much it counts and what it saw. Signals never decide: decide.py does.
"""

from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import UUID

from app.utils.logger import get_logger

logger = get_logger(__name__)

# Signals that look at codes. Only these can accept a match by themselves.
CODE_SIGNALS = frozenset({"code_in_text", "code_in_name"})


@dataclass(frozen=True)
class ImageFacts:
    """What is known about one image.

    Attributes:
        name: Its file name.
        path: Its file name with the ZIP path or PDF it came from, for matching codes in names.
        text: What OCR read on it, "" if nothing.
        page_products: Products whose values came from the PDF page this image was found on.
        duplicate_group: Its near-duplicate group, if any.
    """

    id: UUID
    name: str
    path: str
    text: str
    page_products: frozenset[UUID] = frozenset()
    duplicate_group: UUID | None = None


@dataclass(frozen=True)
class ProductFacts:
    """What is known about one product.

    Attributes:
        codes: Its key and every SKU, model number and barcode its sources gave.
        words: Its brand and name as its sources gave them.
    """

    id: UUID
    key: str
    codes: tuple[str, ...]
    words: tuple[str, ...]


@dataclass
class Context:
    """What signals may consult beyond one image and one product.

    Attributes:
        settings: The matching config section.
        product_count: Products in the batch.
        accepted: Images already accepted for each product, by the system or a person.
        similarity: Titan similarity of two images, when both have embeddings.
        groups: Each image's near-duplicate group.
    """

    settings: dict[str, Any]
    product_count: int
    accepted: dict[UUID, list[UUID]] = field(default_factory=dict)
    similarity: dict[frozenset[UUID], float] = field(default_factory=dict)
    groups: dict[UUID, UUID | None] = field(default_factory=dict)
    names: dict[UUID, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Evidence:
    """What one signal found.

    Attributes:
        signal: The signal's name.
        points: How much it counts, from its configured weight.
        detail: What it saw, stored with the candidate.
        text: What it saw, for people.
    """

    signal: str
    points: float
    detail: dict[str, Any]
    text: str


class Signal(Protocol):
    """Finds one kind of evidence. `needs_accepted` signals run after code-based accepts."""

    name: str
    needs_accepted: bool

    def evaluate(
        self, image: ImageFacts, product: ProductFacts, context: Context
    ) -> Evidence | None:
        """Return evidence that the image shows the product, or None."""
        ...


_REGISTRY: dict[str, Signal] = {}


def register_signal(signal: Signal) -> Signal:
    """Add a signal under its name."""
    _REGISTRY[signal.name] = signal
    logger.info("Registered match signal: name=%s", signal.name)
    return signal


def get_signals() -> list[Signal]:
    """Return every registered signal, in registration order."""
    import app.matching.signals  # noqa: F401 - registers the signals

    return list(_REGISTRY.values())


def weight(context: Context, signal: str) -> float:
    """Return a signal's configured weight.

    Raises:
        ValueError: if config has no weight for it, naming the ones it has.
    """
    weights = context.settings["weights"]
    if signal not in weights:
        raise ValueError(
            f"No weight for signal {signal!r}. Configured: {', '.join(sorted(weights))}"
        )
    return float(weights[signal])
