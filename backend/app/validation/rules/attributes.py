"""Rules about a product's attribute values: required, conflicting, unclear, price, measurements."""

from collections.abc import Iterable

from app.validation.base import (
    Attribute,
    BatchContext,
    Finding,
    ProductData,
    Row,
    register_rule,
)

_STATE_TEXT = {
    "conflict": "sources disagree; choose one in review",
    "unclear": "its value can't be relied on (no unit, a range, …); set it in review",
    "missing": "no source gives it",
}
_FAMILY_WORDS = {"mass": "weight", "volume": "capacity", "length": "size"}


def _attribute(product: ProductData, code: str) -> Attribute | None:
    return product.attributes.get(code)


def _seen(attribute: Attribute | None) -> str:
    if attribute is None:
        return "missing"
    return f"{attribute.state}:{attribute.shown or ''}"


class RequiredAttribute:
    """Each attribute the profile requires has a settled value."""

    code = "required_attribute"
    version = 1
    description = "Attributes the marketplace requires have a settled value."

    def check(self, ctx: BatchContext, profile: Row, settings: Row) -> Iterable[Finding]:
        for product in ctx.products:
            for code in profile["required"]:
                attribute = _attribute(product, code)
                if attribute is not None and attribute.settled:
                    continue
                state = attribute.state if attribute else "missing"
                name = attribute.name if attribute else code.replace("_", " ")
                yield Finding(
                    product_id=product.id,
                    subject=code,
                    message=f"{name} is required for {profile['name']}, but "
                    f"{_STATE_TEXT.get(state, _STATE_TEXT['missing'])}.",
                    seen=_seen(attribute),
                    fix="review" if state in ("conflict", "unclear") else None,
                )


class _StateRule:
    """An attribute the profile doesn't require, in a given state."""

    code = ""
    version = 1
    description = ""
    state = ""

    def check(self, ctx: BatchContext, profile: Row, settings: Row) -> Iterable[Finding]:
        required = set(profile["required"])
        for product in ctx.products:
            for code, attribute in product.attributes.items():
                if code in required or attribute.state != self.state:
                    continue
                yield Finding(
                    product_id=product.id,
                    subject=code,
                    message=f"{attribute.name}: {_STATE_TEXT[self.state]}.",
                    seen=_seen(attribute),
                    fix="review",
                )


class AttributeConflict(_StateRule):
    """Sources disagree on an attribute the profile doesn't require."""

    code = "attribute_conflict"
    description = "Sources disagree on a value, and a person hasn't chosen yet."
    state = "conflict"


class AttributeUnclear(_StateRule):
    """An attribute's values can't be relied on alone."""

    code = "attribute_unclear"
    description = "A value can't be relied on alone (no unit, a range, approximate…)."
    state = "unclear"


class PricePositive:
    """A settled price is above zero."""

    code = "price_positive"
    version = 1
    description = "A settled price is above zero."

    def check(self, ctx: BatchContext, profile: Row, settings: Row) -> Iterable[Finding]:
        for product in ctx.products:
            price = product.value("price")
            if price is not None and price.number is not None and price.number <= 0:
                yield Finding(
                    product_id=product.id,
                    subject="price",
                    message=f"The price is {price.shown}; it must be above zero.",
                    seen=_seen(price),
                    fix="review",
                )


class CurrencyAssumed:
    """The price's currency was assumed, not written."""

    code = "currency_assumed"
    version = 1
    description = 'The price had no currency (taken as rupees) or a lone "$" (taken as US$).'

    def check(self, ctx: BatchContext, profile: Row, settings: Row) -> Iterable[Finding]:
        for product in ctx.products:
            price = product.value("price")
            if price is not None and "currency_assumed" in price.flags:
                yield Finding(
                    product_id=product.id,
                    subject="price",
                    message=f"The price {price.shown} had its currency assumed. Check it's right.",
                    seen=_seen(price),
                )


class Measurements:
    """Settled weights, capacities and sizes are above zero and plausible."""

    code = "measurements"
    version = 1
    description = "Weights, capacities and sizes are above zero and under a sanity ceiling."

    def check(self, ctx: BatchContext, profile: Row, settings: Row) -> Iterable[Finding]:
        ceilings = settings["ceilings"]
        for product in ctx.products:
            for code, attribute in product.attributes.items():
                if not attribute.settled or not attribute.family or attribute.number is None:
                    continue
                ceiling = ceilings.get(attribute.family)
                if attribute.number <= 0:
                    problem = "is zero or less"
                elif ceiling is not None and attribute.number > ceiling:
                    word = _FAMILY_WORDS.get(attribute.family, "value")
                    problem = f"is an unusually large {word}; check the unit"
                else:
                    continue
                yield Finding(
                    product_id=product.id,
                    subject=code,
                    message=f"{attribute.name} {attribute.shown} {problem}.",
                    seen=_seen(attribute),
                    fix="review",
                )


register_rule(RequiredAttribute())
register_rule(AttributeConflict())
register_rule(AttributeUnclear())
register_rule(PricePositive())
register_rule(CurrencyAssumed())
register_rule(Measurements())
