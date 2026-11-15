"""The image is a near-duplicate (M5A) of an image already accepted for the product.

Evidence: {"image_id": the accepted image, "name": its file name}.
"""

from app.matching.base import Context, Evidence, ImageFacts, ProductFacts, register_signal, weight


class SamePicture:
    """The same picture again: strong, but still only a suggestion (decision 2 of M5B)."""

    name = "same_picture"
    needs_accepted = True

    def evaluate(
        self, image: ImageFacts, product: ProductFacts, context: Context
    ) -> Evidence | None:
        """Check whether an accepted image of the product is in the image's group."""
        group = context.groups.get(image.id)
        if group is None:
            return None
        for accepted in context.accepted.get(product.id, []):
            if accepted != image.id and context.groups.get(accepted) == group:
                name = context.names.get(accepted, "an accepted photo")
                return Evidence(
                    self.name,
                    weight(context, self.name),
                    {"image_id": str(accepted), "name": name},
                    f'It\'s the same picture as "{name}", already accepted for this product.',
                )
        return None


register_signal(SamePicture())
