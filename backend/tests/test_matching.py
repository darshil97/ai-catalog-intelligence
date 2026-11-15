"""Covers the image association rule: which evidence can accept a match, and what goes to
review. Needs no database: images and products are described directly."""

from typing import Any
from uuid import UUID, uuid4

import pytest

from app.config import get_matching_config
from app.matching import (
    ACCEPTED,
    AUTO_ACCEPTED,
    NEEDS_REVIEW,
    REJECTED,
    Candidate,
    Context,
    ImageFacts,
    ProductFacts,
    associate,
    get_signals,
)

JUG = ProductFacts(uuid4(), "AK-1003", ("AK-1003", "4006381333924"), ("Acme", "Glass Water Jug"))
BOWL = ProductFacts(uuid4(), "AK-1004", ("AK-1004",), ("Acme", "Steel Mixing Bowl"))


def _image(name: str = "photo.jpg", text: str = "", **kwargs: Any) -> ImageFacts:
    return ImageFacts(id=uuid4(), name=name, path=name, text=text, **kwargs)


def _context(products: int = 2, **overrides: Any) -> Context:
    settings = get_matching_config()
    settings.update(overrides)
    return Context(settings=settings, product_count=products)


def _run(
    images: list[ImageFacts],
    products: list[ProductFacts] | None = None,
    context: Context | None = None,
    human: dict[tuple[UUID, UUID], str] | None = None,
) -> dict[tuple[UUID, UUID], Candidate]:
    products = products if products is not None else [JUG, BOWL]
    context = context or _context(len(products))
    context.names = {image.id: image.name for image in images}
    context.groups = {image.id: image.duplicate_group for image in images}
    found = associate(images, products, context, human or {})
    return {(c.image_id, c.product_id): c for c in found}


def test_signals_are_registered_with_weights() -> None:
    names = {signal.name for signal in get_signals()}
    assert names == {
        "code_in_text", "code_in_name", "from_product_page", "brand_or_name_in_text",
        "only_product", "same_picture", "looks_like_accepted",
    }  # fmt: skip
    assert names <= set(get_matching_config()["weights"])


def test_a_code_on_the_photo_with_no_competitor_is_accepted() -> None:
    photo = _image(text="Glass Water Jug\nModel ak 1003\nCapacity 1.2 L")
    found = _run([photo])
    jug = found[(photo.id, JUG.id)]
    assert jug.decision == AUTO_ACCEPTED and jug.score == 1.0
    assert jug.evidence[0].signal == "code_in_text"
    assert jug.evidence[0].detail == {"code": "AK-1003", "line": "Model ak 1003"}
    # The other product isn't kept as a guess once the photo's own code decided.
    assert (photo.id, BOWL.id) not in found


def test_a_barcode_or_file_name_counts_as_a_code() -> None:
    by_barcode = _image(text="EAN 4006381333924")
    by_name = _image(name="AK-1004_side.jpg")
    found = _run([by_barcode, by_name])
    assert found[(by_barcode.id, JUG.id)].decision == AUTO_ACCEPTED
    named = found[(by_name.id, BOWL.id)]
    assert named.decision == AUTO_ACCEPTED and named.evidence[0].signal == "code_in_name"


def test_two_codes_on_one_photo_go_to_review() -> None:
    photo = _image(text="Set contents: AK-1003 and AK-1004")
    found = _run([photo])
    assert {c.decision for c in found.values()} == {NEEDS_REVIEW}
    assert len(found) == 2


def test_a_name_and_a_photo_that_disagree_go_to_review() -> None:
    photo = _image(name="AK-1003-front.jpg", text="Model AK-1004")
    found = _run([photo])
    assert found[(photo.id, JUG.id)].decision == NEEDS_REVIEW
    assert found[(photo.id, BOWL.id)].decision == NEEDS_REVIEW


def test_looking_alike_never_accepts() -> None:
    labelled = _image(text="Model AK-1003")
    lookalike = _image(name="other-angle.jpg")
    context = _context()
    context.similarity = {frozenset({labelled.id, lookalike.id}): 0.99}
    found = _run([labelled, lookalike], context=context)
    assert found[(labelled.id, JUG.id)].decision == AUTO_ACCEPTED
    candidate = found[(lookalike.id, JUG.id)]
    assert candidate.decision == NEEDS_REVIEW
    assert candidate.evidence[0].signal == "looks_like_accepted"
    assert candidate.evidence[0].detail["similarity"] == 0.99


def test_a_lookalike_product_is_decided_by_its_own_code() -> None:
    # Same box design in another colour, with another code: it looks like the jug's photo,
    # but shows the bowl's code, so it is the bowl's.
    jug_photo = _image(text="AK-1003 red")
    bowl_photo = _image(text="AK-1004 blue")
    context = _context()
    context.similarity = {frozenset({jug_photo.id, bowl_photo.id}): 0.97}
    found = _run([jug_photo, bowl_photo], context=context)
    assert found[(bowl_photo.id, BOWL.id)].decision == AUTO_ACCEPTED
    assert (bowl_photo.id, JUG.id) not in found


def test_a_near_duplicate_of_an_accepted_photo_is_suggested_first() -> None:
    group = uuid4()
    labelled = _image(text="Model AK-1003", duplicate_group=group)
    copy = _image(name="copy.jpg", duplicate_group=group)
    found = _run([labelled, copy])
    candidate = found[(copy.id, JUG.id)]
    assert candidate.decision == NEEDS_REVIEW
    assert [e.signal for e in candidate.evidence] == ["same_picture"]


def test_a_persons_decision_always_stands() -> None:
    photo = _image(text="Model AK-1003")
    found = _run([photo], human={(photo.id, JUG.id): REJECTED})
    assert found[(photo.id, JUG.id)].decision == REJECTED
    other = _image(name="plain.jpg")
    found = _run([other], human={(other.id, BOWL.id): ACCEPTED})
    assert found[(other.id, BOWL.id)].decision == ACCEPTED


def test_with_one_product_every_photo_can_be_reviewed() -> None:
    plain = _image(name="side.jpg", text="FEATURES\\nPortable 18.5 cm")
    found = _run([plain], products=[JUG])
    candidate = found[(plain.id, JUG.id)]
    assert candidate.decision == NEEDS_REVIEW
    assert [e.signal for e in candidate.evidence] == ["only_product"]
    # With two products, a photo with nothing to go on stays unmatched.
    assert _run([plain]) == {}


def test_brand_and_name_are_weak_and_need_whole_words() -> None:
    branded = _image(text="ACME glass water jug, 1.2 L")
    found = _run([branded])
    jug = found[(branded.id, JUG.id)]
    assert jug.decision == NEEDS_REVIEW
    assert {e.signal for e in jug.evidence} == {"brand_or_name_in_text"}
    assert jug.score < 0.5
    # "Acme" inside "Acmeware" isn't a match.
    assert _run([_image(text="Acmeware catalogue")]) == {}


def test_page_evidence_is_shared_between_the_pages_products() -> None:
    alone = _image(page_products=frozenset({JUG.id}))
    shared = _image(page_products=frozenset({JUG.id, BOWL.id}))
    found = _run([alone, shared])
    assert found[(alone.id, JUG.id)].score == 0.6
    assert found[(shared.id, JUG.id)].score == 0.3


@pytest.mark.parametrize(("threshold", "kept"), [(0.2, True), (0.5, False)])
def test_review_threshold_comes_from_config(threshold: float, kept: bool) -> None:
    branded = _image(text="Acme")
    found = _run([branded], context=_context(review_threshold=threshold))
    assert ((branded.id, JUG.id) in found) is kept


def test_candidates_per_image_are_capped_best_first() -> None:
    products = [
        ProductFacts(uuid4(), f"P-{n}", (f"P-{n}",), ("Acme", f"Item {n}")) for n in range(5)
    ]
    photo = _image(text="Acme")
    found = associate([photo], products, _context(len(products)), {})
    assert len(found) == get_matching_config()["max_candidates_per_image"]
