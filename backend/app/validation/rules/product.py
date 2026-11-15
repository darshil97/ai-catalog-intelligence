"""Rules about the product as a whole: category, identifier, photos, open review items."""

from collections.abc import Iterable

from app.validation.base import BatchContext, Finding, Row, register_rule

_FLAG_TEXT = {
    "invalid_gtin": "The barcode's check digit is wrong, so it isn't a valid GTIN.",
    "scientific_notation": "The code was stored in scientific notation (such as 4.0E+12) and "
    "may have lost digits.",
    "precision_lost": "The code lost digits in the spreadsheet; check it against the source.",
}
_REVIEW_TEXT = {
    "attribute_conflict": "a value to choose",
    "category": "a category to choose",
    "image_match": "a photo to confirm",
}


class Category:
    """The product has a settled category."""

    code = "category"
    version = 1
    description = "A category is settled: chosen by a person, or by the LLM when it was sure."

    def check(self, ctx: BatchContext, profile: Row, settings: Row) -> Iterable[Finding]:
        for product in ctx.products:
            row = product.row
            settled = row["category_id"] is not None and (
                row["category_source"] == "person" or row["category_state"] == "assigned"
            )
            if settled:
                continue
            waiting = row["category_state"] == "needs_review"
            yield Finding(
                product_id=product.id,
                message="The category needs choosing in review."
                if waiting
                else "No category has been chosen yet.",
                seen=str(row["category_state"]),
                fix="review" if waiting else None,
            )


class Identifier:
    """The product's code came through intact."""

    code = "identifier"
    version = 1
    description = "The product's code is intact: a valid barcode, no lost digits."

    def check(self, ctx: BatchContext, profile: Row, settings: Row) -> Iterable[Finding]:
        for product in ctx.products:
            for flag in product.row["identifier_flags"] or []:
                yield Finding(
                    product_id=product.id,
                    subject=str(flag),
                    message=_FLAG_TEXT.get(str(flag), f"The product's code is flagged: {flag}."),
                    seen=product.key,
                )


class Photo:
    """At least one photo is confirmed for the product."""

    code = "photo"
    version = 1
    description = "At least one photo is confirmed as showing the product."

    def check(self, ctx: BatchContext, profile: Row, settings: Row) -> Iterable[Finding]:
        for product in ctx.products:
            if not product.photos:
                yield Finding(
                    product_id=product.id,
                    message="No photo is confirmed for this product.",
                    seen="none",
                    fix="photos",
                )


class OpenReview:
    """Review items about the product are still open."""

    code = "open_review"
    version = 1
    description = "Review items about the product are still waiting for a decision."

    def check(self, ctx: BatchContext, profile: Row, settings: Row) -> Iterable[Finding]:
        for product in ctx.products:
            if not product.open_reviews:
                continue
            kinds = sorted(set(product.open_reviews))
            count = len(product.open_reviews)
            what = ", ".join(_REVIEW_TEXT.get(kind, kind) for kind in kinds)
            yield Finding(
                product_id=product.id,
                message=f"{count} review {'item is' if count == 1 else 'items are'} waiting: "
                f"{what}.",
                seen=",".join(sorted(product.open_reviews)),
                fix="review",
            )


register_rule(Category())
register_rule(Identifier())
register_rule(Photo())
register_rule(OpenReview())
