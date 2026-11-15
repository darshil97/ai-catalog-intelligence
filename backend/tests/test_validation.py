"""Covers the catalog check rules, the registry and the engine (M8, D38). No database."""

from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.config import get_config, get_validation_config
from app.validation import check_settings
from app.validation.base import Attribute, BatchContext, ProductData, registered_rules
from app.validation.engine import BatchReport, ProductReport, evaluate


def _attr(code: str, shown: str | None, state: str = "verified", number: str | None = None,
          family: str | None = None, flags: tuple[str, ...] = (),
          comparable: str | None = None) -> Attribute:  # fmt: skip
    return Attribute(
        code=code,
        name=code.replace("_", " ").capitalize(),
        state=state,
        family=family,
        shown=shown,
        number=Decimal(number) if number else None,
        comparable=comparable,
        flags=flags,
    )


def _product(key: str = "AK-1001", *, attributes: list[Attribute] | None = None,
             photos: list[dict] | None = None, reviews: list[str] | None = None,
             text_state: str = "generated", **row: Any) -> ProductData:  # fmt: skip
    base = [
        _attr("product_name", "Glass jug"),
        _attr("brand", "Acme"),
        _attr("price", "1299 INR", number="1299"),
    ]
    by_code = {a.code: a for a in base}
    for attribute in attributes or []:
        by_code[attribute.code] = attribute
    return ProductData(
        id=uuid4(),
        key=key,
        row={
            "key_source": "sku",
            "identifier_flags": [],
            "category_id": uuid4(),
            "category_state": "assigned",
            "category_source": "llm",
            "generated_title": "Acme Glass jug",
            "generated_bullets": ["a", "b", "c"],
            "input_fingerprint": "f",
            **row,
        },
        attributes=by_code,
        # Each product's own photo unless a test says otherwise.
        photos=[{"image_id": uuid4(), "duplicate_group_id": None}] if photos is None else photos,
        open_reviews=reviews or [],
        text_state=text_state,
    )


def _run(*products: ProductData, profile: str = "shopify", pairs: dict | None = None,
         overrides: list[dict] | None = None) -> BatchReport:  # fmt: skip
    ctx = BatchContext(batch_id=uuid4(), products=list(products), variant_pairs=pairs or {})
    return evaluate(ctx, profile, overrides)


def _report(report: BatchReport, product: ProductData) -> ProductReport:
    found = report.product(product.id)
    assert found is not None
    return found


def _issues(report: BatchReport, product: ProductData) -> dict[tuple[str, str], Any]:
    return {(i.rule, i.subject): i for i in _report(report, product).issues}


# Registry and settings ---------------------------------------------------------------------


def test_every_rule_is_described_versioned_and_configured() -> None:
    rules = registered_rules()
    assert len(rules) == 17
    defaults = get_validation_config()["severities"]
    for code, rule in rules.items():
        assert rule.code == code and rule.version >= 1 and rule.description
        assert defaults[code] in ("error", "warning", "info")
    check_settings()


def test_unknown_rules_and_attributes_in_config_are_refused() -> None:
    get_config()["validation"]["severities"]["no_such_rule"] = "error"
    with pytest.raises(ValueError, match="unknown rules"):
        check_settings()


def test_an_unknown_attribute_in_a_profile_is_refused() -> None:
    get_config()["validation"]["profiles"]["shopify"]["required"].append("sparkle")
    with pytest.raises(ValueError, match="unknown attributes"):
        get_validation_config()


def test_a_clean_product_is_ready() -> None:
    product = _product()
    report = _run(product)
    item = _report(report, product)
    assert item.issues == [] and item.can_approve
    assert (report.profile, report.profile_name, report.rules_version) == (
        "shopify", "Shopify", "checks@1",
    )  # fmt: skip


def test_an_unknown_profile_falls_back_to_the_default() -> None:
    assert _run(_product(), profile="ebay").profile == "shopify"


# Attribute rules ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("price", "expected"),
    [
        (None, "is required for Shopify, but no source gives it"),
        (_attr("price", None, "conflict"), "sources disagree; choose one in review"),
        (_attr("price", None, "unclear"), "can't be relied on"),
    ],
)
def test_a_required_attribute_must_be_settled(price: Attribute | None, expected: str) -> None:
    product = _product()
    if price is None:
        del product.attributes["price"]
    else:
        product.attributes["price"] = price
    issue = _issues(_run(product), product)[("required_attribute", "price")]
    assert issue.severity == "error" and expected in issue.message
    assert not _report(_run(product), product).can_approve


def test_profiles_require_different_attributes() -> None:
    product = _product()
    del product.attributes["brand"]
    assert ("required_attribute", "brand") not in _issues(_run(product), product)
    for profile in ("generic", "amazon_in"):
        assert ("required_attribute", "brand") in _issues(_run(product, profile=profile), product)
    assert ("required_attribute", "gtin") in _issues(_run(product, profile="amazon_in"), product)


def test_other_conflicts_warn_and_unclear_values_inform() -> None:
    product = _product(attributes=[_attr("color", None, "conflict"),
                                   _attr("capacity", None, "unclear")])  # fmt: skip
    issues = _issues(_run(product), product)
    assert issues[("attribute_conflict", "color")].severity == "warning"
    assert issues[("attribute_unclear", "capacity")].severity == "info"
    assert _report(_run(product), product).can_approve


def test_price_must_be_positive_and_an_assumed_currency_warns() -> None:
    zero = _product(attributes=[_attr("price", "0 INR", number="0")])
    assert _issues(_run(zero), zero)[("price_positive", "price")].severity == "error"
    assumed = _product(attributes=[_attr("price", "1299 INR", number="1299",
                                         flags=("currency_assumed",))])  # fmt: skip
    issue = _issues(_run(assumed), assumed)[("currency_assumed", "price")]
    assert issue.severity == "warning" and "assumed" in issue.message


def test_implausible_measurements_warn() -> None:
    product = _product(attributes=[
        _attr("net_weight", "1500 kg", number="1500000", family="mass"),
        _attr("capacity", "1.5 L", number="1500", family="volume"),
        _attr("length", "0 cm", number="0", family="length"),
    ])  # fmt: skip
    issues = _issues(_run(product), product)
    assert "unusually large weight" in issues[("measurements", "net_weight")].message
    assert "zero or less" in issues[("measurements", "length")].message
    assert ("measurements", "capacity") not in issues


# Product rules -----------------------------------------------------------------------------


def test_category_must_be_settled() -> None:
    unsure = _product(category_id=None, category_state="needs_review")
    issue = _issues(_run(unsure), unsure)[("category", "")]
    assert issue.severity == "error" and issue.fix == "review"
    chosen = _product(category_state="needs_review", category_source="person")
    assert ("category", "") not in _issues(_run(chosen), chosen)


def test_identifier_flags_are_errors() -> None:
    product = _product(identifier_flags=["invalid_gtin"])
    issue = _issues(_run(product), product)[("identifier", "invalid_gtin")]
    assert issue.severity == "error" and "check digit" in issue.message


def test_a_photo_is_a_warning_for_shopify_and_an_error_for_amazon() -> None:
    product = _product(photos=[])
    assert _issues(_run(product), product)[("photo", "")].severity == "warning"
    amazon = _issues(_run(product, profile="amazon_in"), product)
    assert amazon[("photo", "")].severity == "error"


def test_open_review_items_warn() -> None:
    product = _product(reviews=["attribute_conflict", "image_match"])
    issue = _issues(_run(product), product)[("open_review", "")]
    assert "2 review items are waiting" in issue.message and "a photo to confirm" in issue.message


# Text rules --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("state", "expected"),
    [("none", "No title"), ("out_of_date", "out of date"), ("failed", "failed"),
     ("skipped", "not enough")],
)  # fmt: skip
def test_missing_or_stale_text_warns(state: str, expected: str) -> None:
    product = _product(text_state=state)
    issue = _issues(_run(product), product)[("generated_text", "")]
    assert issue.severity == "warning" and expected in issue.message and issue.fix == "generate"


def test_title_length_uses_the_name_without_generated_text() -> None:
    long_name = "Jug " * 70
    product = _product(generated_title=None, attributes=[_attr("product_name", long_name)])
    issue = _issues(_run(product), product)[("title_length", "")]
    assert issue.severity == "error" and "Shopify allows 255" in issue.message
    short = _product(generated_title="A" * 210)
    assert ("title_length", "") not in _issues(_run(short), short)
    assert ("title_length", "") in _issues(_run(short, profile="amazon_in"), short)


def test_amazon_expects_bullets() -> None:
    product = _product(generated_bullets=["one"])
    assert ("bullets", "") not in _issues(_run(product), product)
    issue = _issues(_run(product, profile="amazon_in"), product)[("bullets", "")]
    assert "at least 3 bullet points; there is 1" in issue.message


# Duplicates --------------------------------------------------------------------------------


def test_a_shared_barcode_is_an_error_on_both_products() -> None:
    gtin = _attr("gtin", "4006381333931", comparable="4006381333931")
    a = _product("AK-1", attributes=[gtin])
    b = _product("4006381333931", key_source="gtin")
    report = _run(a, b)
    on_a = _issues(report, a)[("duplicate_gtin", str(b.id))]
    on_b = _issues(report, b)[("duplicate_gtin", str(a.id))]
    assert on_a.severity == "error" and on_a.other_product_id == b.id
    assert "4006381333931" in on_a.message and on_b.other_product_id == a.id


def test_same_brand_and_name_warns_unless_they_are_variants() -> None:
    a = _product("AK-1")
    b = _product("AK-2", attributes=[_attr("product_name", "GLASS  jug")])
    assert ("duplicate_name", str(b.id)) in _issues(_run(a, b), a)
    pair = frozenset((a.id, b.id))
    for status in ("flagged", "confirmed"):
        assert ("duplicate_name", str(b.id)) not in _issues(_run(a, b, pairs={pair: status}), a)
    dismissed = _issues(_run(a, b, pairs={pair: "dismissed"}), a)
    assert ("duplicate_name", str(b.id)) in dismissed


def test_different_brands_and_sequential_codes_are_not_duplicates() -> None:
    a = _product("AK-1003", attributes=[_attr("model_number", "AK-1003", comparable="AK-1003")])
    model = _attr("model_number", "AK-1004", comparable="AK-1004")
    b = _product("AK-1004", attributes=[_attr("brand", "Other"), model])
    assert not [i for i in _report(_run(a, b), a).issues if i.rule.startswith("duplicate")]


def test_shared_and_near_duplicate_photos_warn() -> None:
    group = uuid4()
    a = _product("AK-1", photos=[{"image_id": uuid4(), "duplicate_group_id": group}])
    b = _product("AK-2", attributes=[_attr("product_name", "Steel bowl")],
                 photos=[{"image_id": uuid4(), "duplicate_group_id": group}])  # fmt: skip
    issue = _issues(_run(a, b), b)[("duplicate_photo", str(a.id))]
    assert issue.severity == "warning" and "AK-1" in issue.message


def test_a_shared_model_number_warns() -> None:
    model = _attr("model_number", "TG-500", comparable="TG500")
    a = _product("AK-1", attributes=[model])
    b = _product("AK-2", attributes=[model, _attr("product_name", "Steel bowl")])
    assert _issues(_run(a, b), a)[("duplicate_model_number", str(b.id))].severity == "warning"


# Engine ------------------------------------------------------------------------------------


def _override(product: ProductData, issue: Any, reason: str = "Fine") -> dict:
    return {
        "product_id": product.id,
        "rule_code": issue.rule,
        "subject": issue.subject,
        "fingerprint": issue.fingerprint,
        "reason": reason,
        "created_by": "alice",
        "created_at": "2026-11-15T00:00:00Z",
    }


def test_errors_come_first_and_a_set_aside_warning_goes_last() -> None:
    product = _product(photos=[], category_id=None, category_state="none", text_state="none")
    report = _run(product)
    item = _report(report, product)
    assert [i.severity for i in item.issues] == ["error", "warning", "warning"]
    photo = _issues(report, product)[("photo", "")]
    after = _report(_run(product, overrides=[_override(product, photo)]), product)
    assert after.issues[-1].rule == "photo" and after.issues[-1].set_aside["reason"] == "Fine"
    assert after.count("warning") == 1


def test_a_set_aside_warning_returns_when_what_it_checked_changes() -> None:
    product = _product(attributes=[_attr("color", None, "conflict")])
    conflict = _issues(_run(product), product)[("attribute_conflict", "color")]
    overrides = [_override(product, conflict)]
    assert _issues(_run(product, overrides=overrides), product)[
        ("attribute_conflict", "color")
    ].set_aside
    product.attributes["color"] = _attr("color", None, "conflict", flags=("x",))
    assert _issues(_run(product, overrides=overrides), product)[
        ("attribute_conflict", "color")
    ].set_aside  # flags aren't part of what a conflict checks
    product.attributes["color"] = _attr("color", "Red", "unclear")
    assert ("attribute_conflict", "color") not in _issues(
        _run(product, overrides=overrides), product
    )


def test_an_error_cannot_be_set_aside() -> None:
    product = _product(category_id=None, category_state="none")
    issue = _issues(_run(product), product)[("category", "")]
    item = _report(_run(product, overrides=[_override(product, issue)]), product)
    assert item.issues[0].set_aside is None and not item.can_approve


def test_a_profile_raising_a_warning_to_an_error_ignores_its_set_aside() -> None:
    product = _product(photos=[])
    photo = _issues(_run(product), product)[("photo", "")]
    item = _report(_run(product, profile="amazon_in", overrides=[_override(product, photo)]),
                   product)  # fmt: skip
    assert item.issues[0].rule == "photo" and item.issues[0].set_aside is None


def test_a_broken_rule_is_reported_and_the_others_still_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rule = registered_rules()["photo"]

    def broken(*args: Any) -> Any:
        raise RuntimeError("boom")

    monkeypatch.setattr(rule, "check", broken)
    product = _product(category_id=None, category_state="none")
    issues = _issues(_run(product), product)
    assert issues[("check_failed", "photo")].severity == "info"
    assert ("category", "") in issues


def test_ids_are_uuids() -> None:
    a, b = _product("A"), _product("B")
    issue = _issues(_run(a, b), a)[("duplicate_name", str(b.id))]
    assert UUID(issue.subject) == b.id
