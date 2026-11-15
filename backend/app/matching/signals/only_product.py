"""The batch has exactly one product, so any image might show it.

Evidence: {}. It is enough to put the image in review, never to accept it: a batch can hold
photos of products its spreadsheet or labels don't name.
"""

from app.matching.base import Context, Evidence, ImageFacts, ProductFacts, register_signal, weight


class OnlyProduct:
    """Lets a person confirm photos with no code, such as the side of a box."""

    name = "only_product"
    needs_accepted = False

    def evaluate(
        self, image: ImageFacts, product: ProductFacts, context: Context
    ) -> Evidence | None:
        """Fire for every image when the batch has one product."""
        if context.product_count != 1:
            return None
        return Evidence(
            self.name,
            weight(context, self.name),
            {},
            "It's the only product in this batch.",
        )


register_signal(OnlyProduct())
