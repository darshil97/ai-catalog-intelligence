"""Rules about generated text (M7): it exists, is current, and fits the marketplace."""

from collections.abc import Iterable

from app.validation.base import BatchContext, Finding, Row, register_rule

_TEXT_STATE = {
    "none": "No title, description or bullets have been generated yet.",
    "out_of_date": "The generated text is out of date: values it cites have changed.",
    "skipped": "Text couldn't be generated: not enough settled information.",
    "failed": "Text generation failed; regenerate to try again.",
}


class GeneratedText:
    """Generated text exists and is current."""

    code = "generated_text"
    version = 1
    description = "A title, description and bullets are generated and up to date."

    def check(self, ctx: BatchContext, profile: Row, settings: Row) -> Iterable[Finding]:
        for product in ctx.products:
            if product.text_state == "generated":
                continue
            yield Finding(
                product_id=product.id,
                message=_TEXT_STATE.get(product.text_state, _TEXT_STATE["none"]),
                seen=f"{product.text_state}:{product.row['input_fingerprint'] or ''}",
                fix="generate",
            )


class TitleLength:
    """The title fits the marketplace's limit."""

    code = "title_length"
    version = 1
    description = "The title (generated, or else the product name) fits the marketplace's limit."

    def check(self, ctx: BatchContext, profile: Row, settings: Row) -> Iterable[Finding]:
        limit = profile["title_max_chars"]
        for product in ctx.products:
            name = product.value("product_name")
            title = product.row["generated_title"] or (name.shown if name else None)
            if title and len(title) > limit:
                yield Finding(
                    product_id=product.id,
                    message=f"The title has {len(title)} characters; {profile['name']} allows "
                    f"{limit}.",
                    seen=title,
                    fix="generate",
                )


class Bullets:
    """Generated bullets meet the marketplace's minimum."""

    code = "bullets"
    version = 1
    description = "There are at least as many bullets as the marketplace expects."

    def check(self, ctx: BatchContext, profile: Row, settings: Row) -> Iterable[Finding]:
        minimum = profile["bullets_min"]
        if minimum <= 0:
            return
        for product in ctx.products:
            count = len(product.row["generated_bullets"] or [])
            if count < minimum:
                yield Finding(
                    product_id=product.id,
                    message=f"{profile['name']} expects at least {minimum} bullet points; "
                    f"there {'is' if count == 1 else 'are'} {count}.",
                    seen=str(count),
                    fix="generate",
                )


register_rule(GeneratedText())
register_rule(TitleLength())
register_rule(Bullets())
