"""Covers human review's rules: approval, value parsing, history wording, and checks (M9, D40).

No database.
"""

from typing import Any
from uuid import uuid4

import pytest

from app.approval import rules
from app.approval.history import describe
from app.approval.repository import InvalidError, standard_text
from app.config import get_enrichment_config, get_normalization_config
from app.enrichment import build_facts
from app.enrichment.grounding import Grounding
from app.validation.base import Attribute, BatchContext, ProductData
from app.validation.engine import evaluate


def _attr(code: str, shown: str | None, state: str = "verified") -> Attribute:
    return Attribute(code, code.replace("_", " ").capitalize(), state, None, shown, None, None, ())


def _product(**row: Any) -> ProductData:
    attributes = {a.code: a for a in [_attr("product_name", "Glass jug"), _attr("brand", "Acme"),
                                      _attr("price", "1299 INR")]}  # fmt: skip
    return ProductData(
        id=uuid4(),
        key=row.pop("key", "AK-1001"),
        row={
            "key_source": "sku",
            "identifier_flags": [],
            "category_id": uuid4(),
            "category_state": "assigned",
            "category_source": "llm",
            "generated_title": "Acme Glass jug",
            "generated_bullets": ["a", "b", "c"],
            "generated_description": "A jug.",
            "input_fingerprint": "f",
            "content_version": 1,
            "content_status": "generated",
            "status": "draft",
            **row,
        },
        attributes=attributes,
        photos=[{"image_id": uuid4(), "duplicate_group_id": None}],
        text_state="generated",
    )


def _report(product: ProductData, profile: str = "shopify") -> Any:
    report = evaluate(BatchContext(batch_id=uuid4(), products=[product]), profile, [])
    return report.product(product.id)


# Approval ----------------------------------------------------------------------------------


def test_a_clean_product_can_be_approved() -> None:
    product = _product()
    assert rules.blockers(product, _report(product)) == []
    assert rules.state(product, "shopify") == rules.DRAFT


def test_blockers_name_every_reason() -> None:
    product = _product(category_id=None, category_state="none")
    product.text_state = "out_of_date"
    product.open_reviews = ["attribute_conflict", "image_match"]
    reasons = rules.blockers(product, _report(product))
    assert reasons == [
        "1 error to fix in its checks.",
        "Its copy is out of date; regenerate or edit it.",
        "2 review items are still open.",
    ]
    product.text_state = "none"
    assert "It has no copy yet." in rules.blockers(product, _report(product))
    rejected = _product(status="rejected")
    assert rules.blockers(rejected, _report(rejected))[0].startswith("It's rejected")


def test_an_approval_holds_only_while_nothing_changes() -> None:
    product = _product(status="approved")
    product.row["approval_fingerprint"] = rules.fingerprint(product, "shopify")
    assert rules.state(product, "shopify") == rules.APPROVED
    # A different marketplace, a new value, new copy or a new photo each break it.
    assert rules.state(product, "amazon_in") == rules.CHANGED
    for change in (
        lambda p: p.attributes.__setitem__("brand", _attr("brand", "Other", "reviewer_set")),
        lambda p: p.row.__setitem__("content_version", 2),
        lambda p: p.photos.append({"image_id": uuid4(), "duplicate_group_id": None}),
        lambda p: p.row.__setitem__("category_id", uuid4()),
    ):
        changed = _product(status="approved")
        changed.row["approval_fingerprint"] = rules.fingerprint(changed, "shopify")
        change(changed)
        assert rules.state(changed, "shopify") == rules.CHANGED
    assert rules.state(_product(status="rejected"), "shopify") == rules.REJECTED


# Editing values ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("code", "family", "data_type", "value", "expected"),
    [
        ("capacity", "volume", "number", "1.5 Ltr", "1.5 L"),
        ("net_weight", "mass", "number", "640 gm", "640 g"),
        ("price", None, "number", "₹1,299", "1299 INR"),
        ("brand", None, "text", "  Vardhan  ", "Vardhan"),
        ("gtin", None, "text", "8901234501016", "8901234501016"),
    ],
)
def test_a_value_is_read_with_the_unit_rules(
    code: str, family: str | None, data_type: str, value: str, expected: str
) -> None:
    attribute = {"code": code, "unit_family": family, "data_type": data_type}
    assert standard_text(attribute, value) == expected


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("1.5", "Add a unit"),
        ("1-2 L", "not a range"),
        ("approx 1.5 L", "exact value"),
        ("2 kg", "doesn't fit"),
        ("   ", "Type a value"),
    ],
)
def test_a_value_that_cant_be_relied_on_is_refused(value: str, message: str) -> None:
    attribute = {"code": "capacity", "unit_family": "volume", "data_type": "number"}
    with pytest.raises(InvalidError, match=message):
        standard_text(attribute, value)


# History -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("event", "text"),
    [
        ({"entity_type": "product_attribute", "action": "edit", "attribute": "Brand",
          "before": {"value": None}, "after": {"value": "Vardhan"}},
         "Set Brand to “Vardhan”"),
        ({"entity_type": "product_attribute", "action": "decide", "attribute": "Capacity",
          "before": {"value": "1.2 L"}, "after": {"value": "1.5 L"}},
         "Chose Capacity to “1.5 L” (was “1.2 L”)"),
        ({"entity_type": "product_attribute", "action": "clear", "attribute": "EAN",
          "before": {"value": "890"}, "after": {}}, "Cleared EAN (was “890”)"),
        ({"entity_type": "image_match_candidate", "action": "decide", "image": "side.jpg",
          "before": {}, "after": {"decision": "accepted"}}, "Confirmed the photo side.jpg"),
        ({"entity_type": "product_status", "action": "approve", "before": {},
          "after": {"profile": "shopify"}}, "Approved it for shopify"),
        ({"entity_type": "product_status", "action": "reject", "before": {},
          "after": {"reason": "Discontinued"}}, "Rejected it: “Discontinued”"),
        ({"entity_type": "generated_content", "action": "edit", "before": {},
          "after": {"version": 3}}, "Edited the copy by hand (version 3)"),
    ],
)  # fmt: skip
def test_history_is_written_in_words(event: dict, text: str) -> None:
    assert describe(event) == text


# Checks ------------------------------------------------------------------------------------


def test_hand_written_copy_is_checked_as_warnings() -> None:
    row = {
        "attribute_id": uuid4(), "code": "material", "name": "Material", "unit_family": None,
        "state": "verified", "shown": "Stainless Steel", "value_number": None,
        "reviewer_value": None, "selected_value_id": uuid4(),
    }  # fmt: skip
    sheet = build_facts([row], None, get_enrichment_config())
    grounding = Grounding(get_enrichment_config(), get_normalization_config())
    problems = grounding.unsupported(
        ["Stainless steel bottle", "Dishwasher safe and holds 2 L", "The best bottle"],
        sheet.facts,
    )
    joined = " ".join(problems)
    assert '"dishwasher safe"' in joined and "states a volume" in joined and '"best"' in joined
    assert not any("Line 1" in problem for problem in problems)


def test_fix_links_name_the_value_and_rejected_products_leave_duplicate_checks() -> None:
    gtin = Attribute("gtin", "EAN", "verified", None, "4006381333931", None, "4006381333931", ())
    a = _product(key="AK-1")
    a.attributes["gtin"] = gtin
    b = _product(key="AK-2", status="rejected")
    b.attributes["gtin"] = gtin
    del a.attributes["brand"]
    report = evaluate(BatchContext(batch_id=uuid4(), products=[a, b]), "generic", [])
    issues = {(i.rule, i.subject): i for i in report.product(a.id).issues}
    assert issues[("required_attribute", "brand")].attribute == "brand"
    assert not any(rule == "duplicate_gtin" for rule, _ in issues)
    b.row["status"] = "draft"
    report = evaluate(BatchContext(batch_id=uuid4(), products=[a, b]), "generic", [])
    duplicate = next(i for i in report.product(a.id).issues if i.rule == "duplicate_gtin")
    assert duplicate.attribute == "gtin"
