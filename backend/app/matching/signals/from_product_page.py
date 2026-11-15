"""The image was found on a PDF page whose values were read for the product.

Evidence: {"page_products": count of products that page gave values to}.
"""

from app.matching.base import Context, Evidence, ImageFacts, ProductFacts, register_signal, weight


class FromProductPage:
    """A picture on a product's spec page usually shows that product."""

    name = "from_product_page"
    needs_accepted = False

    def evaluate(
        self, image: ImageFacts, product: ProductFacts, context: Context
    ) -> Evidence | None:
        """Check whether the image's page gave values to the product."""
        if product.id not in image.page_products:
            return None
        shared = len(image.page_products)
        return Evidence(
            self.name,
            # A page about several products says less about each of them.
            weight(context, self.name) / shared,
            {"page_products": shared},
            "It was found on a PDF page with this product's details"
            + (f", along with {shared - 1} other products." if shared > 1 else "."),
        )


register_signal(FromProductPage())
