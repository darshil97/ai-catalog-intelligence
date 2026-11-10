"""Checks the values an LLM read from a page, and decides which product each belongs to.

Nothing the model says is stored as it said it. A value is kept only if its quote is on the
page and the value is inside the quote, and what is stored is the page's own text. Which
product a value belongs to is decided by where product codes appear on the page: a value
belongs to the nearest code above it, and is dropped if that disagrees with the model.
"""

import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

from app.catalog.identifiers import INVALID_GTIN, Identifier, canonicalize
from app.ingest.documents.pages import Found, Page, find_all, key_pattern, locate

MODE_LINK = "link"
MODE_CREATE = "create"

# Why a value was dropped. Stored with the run, with a message for people.
DROP_NOT_ON_PAGE = "value_not_on_page"
DROP_UNKNOWN_ATTRIBUTE = "unknown_attribute"
DROP_UNKNOWN_PRODUCT = "unknown_product"
DROP_PRODUCT_UNCLEAR = "product_unclear"
DROP_NO_IDENTIFIER = "no_product_code"

_NOT_ALPHANUMERIC = re.compile(r"[\W_]+")
# A code that makes a product must look like one: a brand or logo read as a SKU must not.
_MIN_CODE_CHARS = 4


def can_make_product(attribute: str, identifier: Identifier) -> bool:
    """Return True if a code found on a page may create a product.

    A SKU or model number needs a digit and at least four letters and digits, so "TYGOT" on a
    box front can't become a product. A barcode needs a valid check digit.
    """
    if attribute == "gtin":
        return INVALID_GTIN not in identifier.flags
    code = identifier.match_key
    return len(code) >= _MIN_CODE_CHARS and any(char.isdigit() for char in code)


class ValueAnswer(BaseModel):
    """One value the model reported."""

    product: str = Field(..., max_length=200, description="The product it belongs to.")
    attribute: str = Field(..., max_length=64, description="Attribute code.")
    value: str = Field(..., max_length=1000, description="The value as written.")
    quote: str = Field(..., max_length=2000, description="The text it was read from.")


class DocumentAnswer(BaseModel):
    """The model's reply for one page or photo."""

    values: list[ValueAnswer] = Field(..., max_length=500, description="Values found.")


def parse_answer(data: dict[str, Any]) -> DocumentAnswer:
    """Check the reply's shape. Each value is checked against the page afterwards.

    Raises:
        ValueError: if the reply isn't shaped as asked.
    """
    return DocumentAnswer.model_validate(data)


def _code(text: str) -> str:
    """A product code reduced to letters and digits, for comparing how it was written."""
    return _NOT_ALPHANUMERIC.sub("", unicodedata.normalize("NFKC", text)).upper()


@dataclass(frozen=True)
class Dropped:
    """A value that wasn't kept, and why."""

    code: str
    message: str
    attribute: str
    value: str


@dataclass(frozen=True)
class KeptValue:
    """A value found on the page, for a product.

    Attributes:
        product: For MODE_LINK, the product's ID as text. For MODE_CREATE, its product key.
    """

    product: str
    attribute: str
    found: Found


@dataclass
class NewProduct:
    """A product named on a page of a batch with no spreadsheet."""

    identifier: Identifier
    key_source: str
    supplier_sku: str | None
    first_offset: int


@dataclass
class PageResult:
    """What one page gave."""

    kept: list[KeptValue] = field(default_factory=list)
    dropped: list[Dropped] = field(default_factory=list)
    new_products: list[NewProduct] = field(default_factory=list)


def occurrences(text: str, keys: Iterable[str], min_loose_chars: int) -> list[int]:
    """Return where any of the keys appears in the text, in order."""
    starts: set[int] = set()
    for key in keys:
        pattern = key_pattern(key, min_loose_chars)
        if pattern is not None:
            starts.update(match.start() for match in pattern.finditer(text))
    return sorted(starts)


def _nearest_above(position: int, marks: Sequence[tuple[int, str]]) -> str | None:
    """Return the product whose code appears closest before (or at) a position."""
    owner = None
    for offset, product in marks:
        if offset > position:
            break
        owner = product
    return owner


def _drop(code: str, value: ValueAnswer) -> Dropped:
    messages = {
        DROP_NOT_ON_PAGE: f'"{value.value}" for {value.attribute} isn\'t in the text as quoted, '
        "so it wasn't used.",
        DROP_UNKNOWN_ATTRIBUTE: f'"{value.attribute}" isn\'t an attribute, so "{value.value}" '
        "wasn't used.",
        DROP_UNKNOWN_PRODUCT: f'"{value.value}" for {value.attribute} was given for a product '
        "that isn't in your spreadsheet, so it wasn't used.",
        DROP_PRODUCT_UNCLEAR: f'"{value.value}" for {value.attribute} couldn\'t be placed with '
        "one product, because the nearest product code above it is a different one.",
        DROP_NO_IDENTIFIER: f'"{value.value}" for {value.attribute} belongs to a product with no '
        "usable SKU, model number or barcode on this page, so it wasn't used. A code needs a "
        "digit, and a barcode a valid check digit.",
    }
    return Dropped(code, messages[code], value.attribute, value.value)


def check_link(
    page: Page,
    answer: DocumentAnswer,
    products: dict[str, list[str]],
    allowed: set[str],
    min_loose_chars: int,
) -> PageResult:
    """Check values for a batch whose products came from spreadsheets.

    Args:
        products: For each product on this page, its ID mapped to the codes that name it
            (its key and every identifier the spreadsheet gave it).
        allowed: Attribute codes the model may use.
    """
    text = page.text
    result = PageResult()
    marks = sorted(
        (offset, product_id)
        for product_id, keys in products.items()
        for offset in occurrences(text, keys, min_loose_chars)
    )
    by_code = {_code(key): product_id for product_id, keys in products.items() for key in keys}
    for value in answer.values:
        if value.attribute not in allowed:
            result.dropped.append(_drop(DROP_UNKNOWN_ATTRIBUTE, value))
            continue
        named = by_code.get(_code(value.product))
        if named is None and len(products) == 1:
            # One product on the page: a loosely written name still means that product.
            named = next(iter(products))
        if named is None:
            result.dropped.append(_drop(DROP_UNKNOWN_PRODUCT, value))
            continue
        found = locate(text, page, value.quote, value.value)
        if found is None:
            result.dropped.append(_drop(DROP_NOT_ON_PAGE, value))
            continue
        if len(products) > 1 and _nearest_above(found.start, marks) != named:
            result.dropped.append(_drop(DROP_PRODUCT_UNCLEAR, value))
            continue
        result.kept.append(KeptValue(product=named, attribute=value.attribute, found=found))
    return result


def check_create(
    page: Page,
    answer: DocumentAnswer,
    allowed: set[str],
    identifier_attributes: Sequence[str],
    min_loose_chars: int,
) -> PageResult:
    """Check values for a batch with no spreadsheet, where codes on the page make products.

    A product is only made from a code that is itself found on the page. The most preferred
    identifier decides its key, as for spreadsheet rows.
    """
    text = page.text
    result = PageResult()
    groups: dict[str, list[tuple[ValueAnswer, Found]]] = {}
    for value in answer.values:
        if value.attribute not in allowed:
            result.dropped.append(_drop(DROP_UNKNOWN_ATTRIBUTE, value))
            continue
        found = locate(text, page, value.quote, value.value)
        if found is None:
            result.dropped.append(_drop(DROP_NOT_ON_PAGE, value))
            continue
        groups.setdefault(_code(value.product), []).append((value, found))

    # Each group becomes a product through its most preferred identifier on the page.
    products: dict[str, NewProduct] = {}
    group_key: dict[str, str] = {}
    for label, entries in groups.items():
        chosen: tuple[str, Identifier, Found] | None = None
        for attribute in identifier_attributes:
            for value, found in entries:
                if value.attribute != attribute:
                    continue
                identifier = canonicalize(attribute, found.raw_value)
                if identifier is not None and can_make_product(attribute, identifier):
                    chosen = (attribute, identifier, found)
                    break
            if chosen:
                break
        if chosen is None:
            result.dropped.extend(_drop(DROP_NO_IDENTIFIER, value) for value, _ in entries)
            continue
        attribute, identifier, found = chosen
        group_key[label] = identifier.key
        known = products.get(identifier.key)
        if known is None:
            sku = next((f.raw_value for v, f in entries if v.attribute == "sku"), None)
            products[identifier.key] = NewProduct(
                identifier=identifier,
                key_source=attribute,
                supplier_sku=sku,
                first_offset=found.start,
            )
        else:
            known.first_offset = min(known.first_offset, found.start)

    # Where codes appear on the page decides ownership when the page names several products.
    marks = sorted(
        (offset, key)
        for label, key in group_key.items()
        for value, found in groups[label]
        if value.attribute in identifier_attributes
        for offset, _ in find_all(text, found.raw_value)
    )
    for label, key in group_key.items():
        for value, found in groups[label]:
            if len(products) > 1 and _nearest_above(found.start, marks) != key:
                result.dropped.append(_drop(DROP_PRODUCT_UNCLEAR, value))
                continue
            result.kept.append(KeptValue(product=key, attribute=value.attribute, found=found))
    result.new_products = sorted(products.values(), key=lambda product: product.first_offset)
    return result
