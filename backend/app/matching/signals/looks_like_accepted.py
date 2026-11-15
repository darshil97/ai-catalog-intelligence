"""The image looks like an image already accepted for the product (Titan similarity).

Evidence: {"image_id", "name", "similarity"}. Points grow from nothing at
matching.visual_min_similarity to the full weight at 1.0. Never decisive (D20).
"""

from app.matching.base import Context, Evidence, ImageFacts, ProductFacts, register_signal, weight


class LooksLikeAccepted:
    """Weak evidence: lookalike products look alike."""

    name = "looks_like_accepted"
    needs_accepted = True

    def evaluate(
        self, image: ImageFacts, product: ProductFacts, context: Context
    ) -> Evidence | None:
        """Find the most similar accepted image of the product."""
        minimum = context.settings["visual_min_similarity"]
        best: tuple[float, str] | None = None
        for accepted in context.accepted.get(product.id, []):
            if accepted == image.id:
                continue
            similarity = context.similarity.get(frozenset({image.id, accepted}))
            if similarity is not None and similarity >= minimum:
                if best is None or similarity > best[0]:
                    best = (similarity, str(accepted))
        if best is None:
            return None
        similarity, accepted_id = best
        share = (similarity - minimum) / (1 - minimum) if minimum < 1 else 1.0
        name = next(
            (n for i, n in context.names.items() if str(i) == accepted_id), "an accepted photo"
        )
        return Evidence(
            self.name,
            round(weight(context, self.name) * max(0.0, min(1.0, share)), 3),
            {"image_id": accepted_id, "name": name, "similarity": round(similarity, 3)},
            f'It looks like "{name}", already accepted for this product '
            f"({similarity:.2f} similar).",
        )


register_signal(LooksLikeAccepted())
