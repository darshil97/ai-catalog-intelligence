"""The product's brand or name appears in the image's text, as whole words.

Evidence: {"words": what matched}. Words shorter than matching.min_text_match_chars are
ignored, so a word like "Mug" can't match on its own.
"""

import re
import unicodedata

from app.matching.base import Context, Evidence, ImageFacts, ProductFacts, register_signal, weight


def _fold(text: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text)).strip().casefold()


class BrandOrNameInText:
    """Weak on its own: one brand usually covers many products."""

    name = "brand_or_name_in_text"
    needs_accepted = False

    def evaluate(
        self, image: ImageFacts, product: ProductFacts, context: Context
    ) -> Evidence | None:
        """Look for the product's brand or name in the image's text."""
        text = _fold(image.text)
        if not text:
            return None
        minimum = context.settings["min_text_match_chars"]
        for words in product.words:
            folded = _fold(words)
            if len(folded) < minimum:
                continue
            if re.search(rf"(?<!\w){re.escape(folded)}(?!\w)", text):
                return Evidence(
                    self.name,
                    weight(context, self.name),
                    {"words": words},
                    f'The photo shows "{words}", this product\'s brand or name.',
                )
        return None


register_signal(BrandOrNameInText())
