"""A page of text with positions, and finding exact quotes in it.

PDF text and OCR both become a Page: lines in reading order, each with a box in the page's own
units (points for a PDF, pixels for a photo). Values the LLM reports are only accepted if their
quote is found here, so every stored value is text that was actually on the page.
"""

import re
import unicodedata
from dataclasses import dataclass, field

# Left, top, right, bottom.
Box = tuple[float, float, float, float]

METHOD_TEXT = "text"
METHOD_OCR = "ocr"

_SPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class Line:
    """One line of text and where it is."""

    text: str
    box: Box


@dataclass
class Page:
    """The text of one PDF page or one photo.

    Attributes:
        number: The PDF page, from 1, or None for a photo.
        method: METHOD_TEXT if the PDF held the text, METHOD_OCR if it was read from pixels.
        lines: Lines in reading order.
        width, height: The page's size, in the same units as the boxes.
        confidence: Mean OCR confidence, for OCR pages.
    """

    number: int | None
    method: str
    lines: list[Line]
    width: float
    height: float
    confidence: float | None = None
    _offsets: list[int] = field(default_factory=list, repr=False)

    @property
    def text(self) -> str:
        """All lines, one per line."""
        return "\n".join(line.text for line in self.lines)

    def line_offsets(self) -> list[int]:
        """Return where each line starts in `text`."""
        if len(self._offsets) != len(self.lines):
            offsets, position = [], 0
            for line in self.lines:
                offsets.append(position)
                position += len(line.text) + 1
            self._offsets = offsets
        return self._offsets

    def box_for(self, start: int, end: int) -> Box | None:
        """Return the box around the lines that text[start:end] spans."""
        offsets = self.line_offsets()
        boxes = [
            line.box
            for line, line_start in zip(self.lines, offsets, strict=True)
            if line_start < end and line_start + len(line.text) >= start
        ]
        if not boxes:
            return None
        return (
            min(box[0] for box in boxes),
            min(box[1] for box in boxes),
            max(box[2] for box in boxes),
            max(box[3] for box in boxes),
        )


def _fold(char: str) -> str:
    """Compare characters ignoring case and width ("Ａ" is "a")."""
    return unicodedata.normalize("NFKC", char).casefold()


def _normalized(text: str) -> tuple[str, list[int]]:
    """Return text with runs of whitespace as one space and case folded, plus where each
    character of the result came from in the original."""
    out: list[str] = []
    origin: list[int] = []
    previous_space = True
    for index, char in enumerate(text):
        if char.isspace():
            if not previous_space:
                out.append(" ")
                origin.append(index)
            previous_space = True
            continue
        folded = _fold(char)
        out.extend(folded)
        origin.extend([index] * len(folded))
        previous_space = False
    while out and out[-1] == " ":
        out.pop()
        origin.pop()
    return "".join(out), origin


def find_all(text: str, needle: str) -> list[tuple[int, int]]:
    """Return every (start, end) of needle in text, ignoring case and spacing differences."""
    haystack, origin = _normalized(text)
    wanted, _ = _normalized(needle)
    if not wanted:
        return []
    spans = []
    position = haystack.find(wanted)
    while position != -1:
        start = origin[position]
        end = origin[position + len(wanted) - 1] + 1
        spans.append((start, end))
        position = haystack.find(wanted, position + 1)
    return spans


@dataclass(frozen=True)
class Found:
    """A value located in a page.

    Attributes:
        raw_value: The value exactly as it appears in the page text.
        quote: The quote exactly as it appears in the page text.
        start, end: Where the value is in the page text.
        box: The box around the lines the quote spans.
    """

    raw_value: str
    quote: str
    start: int
    end: int
    box: Box | None


def locate(page_text: str, page: Page, quote: str, value: str) -> Found | None:
    """Find a quote in the page, and the value inside that quote.

    Returns:
        Where both are, with the page's own text for each, or None if the quote isn't on the
        page or the value isn't in the quote.
    """
    if not value.strip() or not quote.strip():
        return None
    for quote_start, quote_end in find_all(page_text, quote):
        quoted = page_text[quote_start:quote_end]
        inside = find_all(quoted, value)
        if inside:
            value_start, value_end = inside[0]
            return Found(
                raw_value=quoted[value_start:value_end],
                quote=quoted,
                start=quote_start + value_start,
                end=quote_start + value_end,
                box=page.box_for(quote_start, quote_end),
            )
    return None


def key_pattern(key: str, min_loose_chars: int) -> re.Pattern[str] | None:
    """Return a pattern that finds a product key in text however its punctuation is written.

    "AK-1003" matches "AK 1003", "ak1003" and "AK.1003", but never inside a longer code such as
    "XAK-10035". A short key must be written exactly, as a whole word, so "12" can't match
    "1200".
    """
    normalized = unicodedata.normalize("NFKC", key)
    parts = [part for part in re.split(r"[^0-9A-Za-z]+", normalized) if part]
    if not parts:
        return None
    letters = "".join(parts)
    if len(letters) < min_loose_chars:
        body = re.escape(key.strip())
    else:
        # Any punctuation or spacing may separate the characters, as long as it is short.
        body = r"[\s\-_./]?".join(re.escape(char) for char in letters)
    return re.compile(rf"(?<![0-9A-Za-z]){body}(?![0-9A-Za-z])", re.IGNORECASE)
