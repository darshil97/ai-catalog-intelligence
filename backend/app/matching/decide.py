"""Decides which product each image shows, from the signals' evidence.

1. Code signals and other first-pass signals run for every image and product.
2. An image is auto-accepted for a product when a code signal links them and no other
   product has a code signal for the same image (and no person rejected the pair).
3. Signals that compare with accepted images (same picture, looks alike) then run, using the
   auto-accepted images and those a person accepted.
4. Every other pair scoring at least the review threshold goes to review. Visual signals can
   raise a score but never accept: only step 2 accepts.

A person's decision always stands: a rerun recomputes scores and evidence, never decisions.
"""

from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Any
from uuid import UUID

from app.matching.base import (
    CODE_SIGNALS,
    Context,
    Evidence,
    ImageFacts,
    ProductFacts,
    get_signals,
)

AUTO_ACCEPTED = "auto_accepted"
NEEDS_REVIEW = "needs_review"
ACCEPTED = "accepted"
REJECTED = "rejected"
HUMAN_DECISIONS = frozenset({ACCEPTED, REJECTED})


@dataclass(frozen=True)
class Candidate:
    """A possible link between an image and a product."""

    image_id: UUID
    product_id: UUID
    score: float
    evidence: tuple[Evidence, ...]
    decision: str

    def signals_json(self) -> list[dict[str, Any]]:
        """The evidence as stored with the candidate."""
        return [asdict(item) for item in self.evidence]


def associate(
    images: Sequence[ImageFacts],
    products: Sequence[ProductFacts],
    context: Context,
    human: dict[tuple[UUID, UUID], str],
) -> list[Candidate]:
    """Return the candidates to store, best first for each image.

    Args:
        human: A person's decision on each (image, product) pair they decided.
    """
    signals = get_signals()
    first = [signal for signal in signals if not signal.needs_accepted]
    second = [signal for signal in signals if signal.needs_accepted]

    found: dict[tuple[UUID, UUID], list[Evidence]] = {}
    for image in images:
        for product in products:
            for signal in first:
                evidence = signal.evaluate(image, product, context)
                if evidence is not None:
                    found.setdefault((image.id, product.id), []).append(evidence)

    auto: dict[UUID, UUID] = {}
    for image in images:
        coded = [
            product.id
            for product in products
            if any(e.signal in CODE_SIGNALS for e in found.get((image.id, product.id), []))
            and human.get((image.id, product.id)) != REJECTED
        ]
        if len(coded) == 1:
            auto[image.id] = coded[0]

    accepted: dict[UUID, list[UUID]] = {}
    for image_id, product_id in auto.items():
        accepted.setdefault(product_id, []).append(image_id)
    for (image_id, product_id), decision in human.items():
        if decision == ACCEPTED and image_id not in accepted.get(product_id, []):
            accepted.setdefault(product_id, []).append(image_id)
    context.accepted = accepted

    for image in images:
        for product in products:
            for signal in second:
                evidence = signal.evaluate(image, product, context)
                if evidence is not None:
                    found.setdefault((image.id, product.id), []).append(evidence)

    settings = context.settings
    candidates: list[Candidate] = []
    for image in images:
        mine: list[Candidate] = []
        for product in products:
            pair = (image.id, product.id)
            # Strongest evidence first, as people read it.
            items = tuple(sorted(found.get(pair, []), key=lambda item: -item.points))
            score = round(min(1.0, sum(item.points for item in items)), 3)
            if pair in human:
                decision = human[pair]
            elif auto.get(image.id) == product.id:
                decision = AUTO_ACCEPTED
            elif image.id in auto:
                # Accepted for another product by its code: weaker guesses aren't worth a review.
                continue
            elif items and score >= settings["review_threshold"]:
                decision = NEEDS_REVIEW
            else:
                continue
            mine.append(Candidate(image.id, product.id, score, items, decision))
        decided = [c for c in mine if c.decision != NEEDS_REVIEW]
        review = sorted((c for c in mine if c.decision == NEEDS_REVIEW), key=lambda c: -c.score)[
            : max(0, settings["max_candidates_per_image"] - len(decided))
        ]
        candidates.extend(sorted(decided + review, key=lambda c: -c.score))
    return candidates
