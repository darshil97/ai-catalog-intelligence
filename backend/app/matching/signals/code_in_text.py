"""A product's code appears in the text OCR read on the image, however it is punctuated.

Evidence: {"code": the code matched, "line": the line of text it was in}.
"""

from app.ingest.documents.pages import key_pattern
from app.matching.base import Context, Evidence, ImageFacts, ProductFacts, register_signal, weight

_MIN_LOOSE_CHARS = 4


class CodeInText:
    """The strongest evidence: the photo itself shows the code."""

    name = "code_in_text"
    needs_accepted = False

    def evaluate(
        self, image: ImageFacts, product: ProductFacts, context: Context
    ) -> Evidence | None:
        """Look for each of the product's codes in the image's text."""
        for code in product.codes:
            pattern = key_pattern(code, _MIN_LOOSE_CHARS)
            if pattern is None:
                continue
            for line in image.text.splitlines():
                if pattern.search(line):
                    return Evidence(
                        self.name,
                        weight(context, self.name),
                        {"code": code, "line": line.strip()},
                        f'The photo shows "{line.strip()}", which has the code {code}.',
                    )
        return None


register_signal(CodeInText())
