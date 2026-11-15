"""Duplicate products within one batch (D38): flagged in pairs on both products, never merged.

Variants (M6) differ in colour or size and aren't duplicates: a pair flagged or confirmed as
variants isn't reported by the name, model number or photo rules. A pair a person dismissed as
variants is treated like any other pair. Batches and users are never compared.
"""

from collections import defaultdict
from collections.abc import Callable, Iterable, Iterator
from itertools import combinations
from uuid import UUID

from app.normalization.values import fold
from app.validation.base import BatchContext, Finding, ProductData, Row, register_rule


def _is_variant(ctx: BatchContext, a: UUID, b: UUID) -> bool:
    return ctx.variant_pairs.get(frozenset((a, b)), "dismissed") != "dismissed"


def _pairs(
    ctx: BatchContext,
    keys: Callable[[ProductData], set[str]],
    skip_variants: bool,
) -> Iterator[tuple[ProductData, ProductData, str]]:
    """Each pair of products sharing a key, with the key."""
    by_key: dict[str, list[ProductData]] = defaultdict(list)
    # A product rejected (for example as the other one's duplicate) is no longer compared.
    for product in (p for p in ctx.products if not p.rejected):
        for key in keys(product):
            by_key[key].append(product)
    seen: set[frozenset[UUID]] = set()
    for key, group in sorted(by_key.items()):
        for a, b in combinations(group, 2):
            pair = frozenset((a.id, b.id))
            if a.id == b.id or pair in seen:
                continue
            if skip_variants and _is_variant(ctx, a.id, b.id):
                continue
            seen.add(pair)
            yield a, b, key


def _both(
    a: ProductData, b: ProductData, message: str, seen: str, attribute: str | None = None
) -> Iterator[Finding]:
    for this, other in ((a, b), (b, a)):
        yield Finding(
            product_id=this.id,
            subject=str(other.id),
            other_product_id=other.id,
            message=message.format(other=other.key),
            seen=seen,
            attribute=attribute,
        )


def _settled(product: ProductData, code: str) -> str | None:
    attribute = product.value(code)
    if attribute is None:
        return None
    return attribute.comparable or fold(attribute.shown or "")


class DuplicateGtin:
    """Two products share a barcode."""

    code = "duplicate_gtin"
    version = 1
    description = "Two products in the batch share the same barcode (GTIN)."

    def check(self, ctx: BatchContext, profile: Row, settings: Row) -> Iterable[Finding]:
        def keys(product: ProductData) -> set[str]:
            found = {value for value in [_settled(product, "gtin")] if value}
            if product.row["key_source"] == "gtin":
                found.add(product.key)
            return found

        for a, b, key in _pairs(ctx, keys, skip_variants=False):
            yield from _both(a, b, f"{{other}} has the same barcode, {key}.", key, "gtin")


class DuplicateModelNumber:
    """Two products share a model number."""

    code = "duplicate_model_number"
    version = 1
    description = "Two products in the batch share the same model number."

    def check(self, ctx: BatchContext, profile: Row, settings: Row) -> Iterable[Finding]:
        def keys(product: ProductData) -> set[str]:
            value = _settled(product, "model_number")
            return {value} if value else set()

        for a, b, key in _pairs(ctx, keys, skip_variants=True):
            yield from _both(a, b, "{other} has the same model number.", key, "model_number")


class DuplicateName:
    """Two products have the same brand and name."""

    code = "duplicate_name"
    version = 1
    description = "Two products in the batch have the same brand and name."

    def check(self, ctx: BatchContext, profile: Row, settings: Row) -> Iterable[Finding]:
        def keys(product: ProductData) -> set[str]:
            name = product.value("product_name")
            brand = product.value("brand")
            if name is None or brand is None:
                return set()
            return {f"{fold(brand.shown or '')}\x1f{fold(name.shown or '')}"}

        for a, b, key in _pairs(ctx, keys, skip_variants=True):
            yield from _both(a, b, "{other} has the same brand and name.", key)


class DuplicatePhoto:
    """Two products have the same photo, or near-duplicates of one."""

    code = "duplicate_photo"
    version = 1
    description = "Two products in the batch have the same photo, or near-copies of one."

    def check(self, ctx: BatchContext, profile: Row, settings: Row) -> Iterable[Finding]:
        def keys(product: ProductData) -> set[str]:
            return {
                f"group:{photo['duplicate_group_id']}"
                if photo["duplicate_group_id"]
                else f"image:{photo['image_id']}"
                for photo in product.photos
            }

        for a, b, key in _pairs(ctx, keys, skip_variants=True):
            yield from _both(a, b, "{other} has the same photo, or a near-copy of it.", key)


register_rule(DuplicateGtin())
register_rule(DuplicateModelNumber())
register_rule(DuplicateName())
register_rule(DuplicatePhoto())
