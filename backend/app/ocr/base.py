"""The OcrEngine contract and its registry (D7).

An engine reads an image and returns lines of text, each with its box and confidence. Which
engine runs is set in config, so changing it needs no pipeline change.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from app.utils.logger import get_logger

logger = get_logger(__name__)

# Left, top, right, bottom, in the image's pixels.
Box = tuple[float, float, float, float]


@dataclass(frozen=True)
class OcrLine:
    """One line of text found in an image."""

    text: str
    box: Box
    confidence: float


@dataclass(frozen=True)
class OcrResult:
    """What an engine read from one image, in reading order."""

    lines: tuple[OcrLine, ...]
    width: int
    height: int

    @property
    def text(self) -> str:
        """All lines, one per line."""
        return "\n".join(line.text for line in self.lines)


class OcrEngine(Protocol):
    """Reads text from images. One instance is shared, so it must keep no per-image state."""

    name: str

    @property
    def version(self) -> str:
        """The engine and model version, recorded with every result."""
        ...

    def read(self, image: Any, min_confidence: float) -> OcrResult:
        """Read a PIL image. Lines below `min_confidence` are dropped."""
        ...


_REGISTRY: dict[str, Callable[[], OcrEngine]] = {}
_INSTANCES: dict[str, OcrEngine] = {}


def register_ocr_engine(name: str, factory: Callable[[], OcrEngine]) -> None:
    """Make an engine available under a name. It is created on first use."""
    _REGISTRY[name] = factory


def get_registered_ocr_engine_names() -> set[str]:
    """Return the names of every registered engine."""
    import app.ocr.engines  # noqa: F401 - registers the engines

    return set(_REGISTRY)


def get_ocr_engine(name: str) -> OcrEngine:
    """Return an engine, created once. Models load on the first call, which takes a moment.

    Raises:
        ValueError: if no engine has that name, naming the ones that exist.
    """
    import app.ocr.engines  # noqa: F401 - registers the engines

    if name not in _REGISTRY:
        raise ValueError(
            f"Unknown OCR engine: {name!r}. Registered: {', '.join(sorted(_REGISTRY))}"
        )
    if name not in _INSTANCES:
        _INSTANCES[name] = _REGISTRY[name]()
        logger.info("OCR engine ready: engine=%s", name)
    return _INSTANCES[name]


def reading_order(lines: list[OcrLine]) -> tuple[OcrLine, ...]:
    """Sort lines top to bottom, then left to right within a row.

    Lines whose vertical centres are within half a line height of each other count as one
    row, so a label and its value side by side stay together.
    """
    if not lines:
        return ()
    by_top = sorted(lines, key=lambda line: (line.box[1] + line.box[3]) / 2)
    rows: list[list[OcrLine]] = []
    for line in by_top:
        centre = (line.box[1] + line.box[3]) / 2
        height = max(line.box[3] - line.box[1], 1.0)
        if rows:
            last = rows[-1][-1]
            last_centre = (last.box[1] + last.box[3]) / 2
            if abs(centre - last_centre) <= height / 2:
                rows[-1].append(line)
                continue
        rows.append([line])
    return tuple(line for row in rows for line in sorted(row, key=lambda item: item.box[0]))
