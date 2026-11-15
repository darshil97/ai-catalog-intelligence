"""A product's code appears in the image's file name, or the ZIP folder or PDF it came from.

Evidence: {"code": the code matched, "name": the name it was in}.
"""

from app.ingest.documents.pages import key_pattern
from app.matching.base import Context, Evidence, ImageFacts, ProductFacts, register_signal, weight

_MIN_LOOSE_CHARS = 4


class CodeInName:
    """Suppliers often name photos after the product: AK-1003-front.jpg."""

    name = "code_in_name"
    needs_accepted = False

    def evaluate(
        self, image: ImageFacts, product: ProductFacts, context: Context
    ) -> Evidence | None:
        """Look for each of the product's codes in the image's name and path."""
        for code in product.codes:
            pattern = key_pattern(code, _MIN_LOOSE_CHARS)
            if pattern is not None and pattern.search(image.path):
                return Evidence(
                    self.name,
                    weight(context, self.name),
                    {"code": code, "name": image.path},
                    f'The file name "{image.path}" has the code {code}.',
                )
        return None


register_signal(CodeInName())
