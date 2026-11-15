"""Flags pairs of products that look like variants of one product. Never merges anything.

A pair is flagged when their names match once colour and size words are removed (and their
brands don't differ), or when their codes share a base and differ only in a short final part.
Similar photos are recorded as support for a flagged pair, never as a reason on their own.
"""

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

_WORD = re.compile(r"[a-z0-9]+")
_CODE_PARTS = re.compile(r"[\W_]+")


@dataclass(frozen=True)
class ProductSummary:
    """What variant rules look at for one product."""

    id: UUID
    key: str
    names: tuple[str, ...]
    brands: tuple[str, ...]


@dataclass
class VariantPair:
    """Two products that may be variants, with why."""

    a: UUID
    b: UUID
    reasons: list[dict[str, Any]] = field(default_factory=list)


def _words(text: str) -> list[str]:
    return _WORD.findall(unicodedata.normalize("NFKC", text).casefold())


def name_core(name: str, ignore: set[str]) -> str:
    """A name without colour and size words, for comparing variants."""
    return " ".join(word for word in _words(name) if word not in ignore)


def code_base(code: str, max_suffix: int) -> str | None:
    """The code without its final part, if that part is short: "AK-1002-RED" gives "AK-1002".

    The base must keep a digit, so sequential codes such as AK-1003 and AK-1004 (base "AK")
    are different products, not variants.
    """
    parts = [part for part in _CODE_PARTS.split(code.upper()) if part]
    if len(parts) < 2 or len(parts[-1]) > max_suffix:
        return None
    base = "-".join(parts[:-1])
    return base if len(base) >= 3 and any(c.isdigit() for c in base) else None


def find_variants(
    products: Sequence[ProductSummary],
    settings: dict[str, Any],
    similarity: dict[frozenset[UUID], float],
) -> list[VariantPair]:
    """Return the flagged pairs, each ordered (smaller ID first) as the table requires."""
    ignore = set(settings["colour_words"]) | set(settings["size_words"])
    pairs: dict[tuple[UUID, UUID], VariantPair] = {}

    def add(first: ProductSummary, second: ProductSummary, reason: dict[str, Any]) -> None:
        a, b = sorted((first.id, second.id), key=str)
        pairs.setdefault((a, b), VariantPair(a, b)).reasons.append(reason)

    for index, first in enumerate(products):
        for second in products[index + 1 :]:
            brands_differ = (
                first.brands and second.brands
                and not {b.casefold() for b in first.brands} & {b.casefold() for b in second.brands}
            )  # fmt: skip
            if not brands_differ:
                for name_a in first.names:
                    for name_b in second.names:
                        core = name_core(name_a, ignore)
                        if (
                            core
                            and core == name_core(name_b, ignore)
                            and _words(name_a) != _words(name_b)
                        ):
                            add(first, second, {
                                "rule": "same_name_but_colour_or_size",
                                "names": [name_a, name_b],
                                "text": f'"{name_a}" and "{name_b}" differ only in colour '
                                "or size words.",
                            })  # fmt: skip
                            break
                    else:
                        continue
                    break
            base_a = code_base(first.key, settings["code_suffix_max_chars"])
            if base_a and base_a == code_base(second.key, settings["code_suffix_max_chars"]):
                add(first, second, {
                    "rule": "same_base_code",
                    "base": base_a,
                    "text": f"{first.key} and {second.key} share the base code {base_a}.",
                })  # fmt: skip

    for pair in pairs.values():
        score = similarity.get(frozenset({pair.a, pair.b}))
        if score is not None and score >= settings["image_support_min_similarity"]:
            pair.reasons.append({
                "rule": "similar_photos",
                "similarity": round(score, 3),
                "text": f"Their photos are {score:.2f} similar.",
            })  # fmt: skip
    return list(pairs.values())
