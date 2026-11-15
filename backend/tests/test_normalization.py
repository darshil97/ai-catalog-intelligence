"""Covers the normalization rules: standard values, which value a product shows, and variants.

No database: these are the pure rules the normalize_batch job applies.
"""

from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.config import get_normalization_config
from app.jobs.handlers.normalize import category_paths
from app.normalization import ProductSummary, Rules, choose, find_variants, normalize
from app.normalization.values import parse_number


@pytest.fixture
def rules() -> Rules:
    return Rules(get_normalization_config())


def _attribute(code: str, data_type: str = "number", family: str | None = None) -> dict:
    return {"code": code, "data_type": data_type, "unit_family": family}


WEIGHT = _attribute("net_weight", family="mass")
CAPACITY = _attribute("capacity", family="volume")
LENGTH = _attribute("length", family="length")
PRICE = _attribute("price")


def _one(rules: Rules, attribute: dict, raw: str, **kwargs: Any) -> tuple:
    [value] = normalize(rules, attribute, raw, **kwargs)
    return value.value_number, value.unit, value.display, value.flags


@pytest.mark.parametrize(
    ("attribute", "raw", "kwargs", "expected"),
    [
        (WEIGHT, "113 gm", {}, (Decimal(113), "g", "113 g", ())),
        (WEIGHT, "1.2 kg", {}, (Decimal(1200), "g", "1.2 kg", ())),
        (
            WEIGHT,
            "500",
            {"header": "Weight (g)"},
            (Decimal(500), "g", "500 g", ("unit_from_header",)),
        ),
        (CAPACITY, "1.5 L", {}, (Decimal(1500), "ml", "1.5 L", ())),
        (CAPACITY, "1,5 l", {}, (Decimal(1500), "ml", "1.5 L", ("decimal_comma",))),
        (_attribute("height", family="length"), "24 cm", {}, (Decimal(240), "mm", "24 cm", ())),
        # India defaults: rupees when nothing is said, lakh grouping, and "$" read as USD.
        (PRICE, "₹1,99,999", {}, (Decimal(199999), "INR", "199999 INR", ())),
        (PRICE, "1,00,000", {}, (Decimal(100000), "INR", "100000 INR", ("currency_assumed",))),
        (PRICE, "$12.50", {}, (Decimal("12.50"), "USD", "12.5 USD", ("currency_assumed",))),
        # The product's own currency, when its sources give one, wins over the default.
        (PRICE, "499", {"product_currency": "EUR"}, (Decimal(499), "EUR", "499 EUR", ())),
        (_attribute("pack_quantity"), "Pack of 6", {}, (Decimal(6), None, "6", ())),
    ],
)
def test_values_are_put_in_standard_units(
    rules: Rules, attribute: dict, raw: str, kwargs: dict, expected: tuple
) -> None:
    assert _one(rules, attribute, raw, **kwargs) == expected


@pytest.mark.parametrize(
    ("attribute", "raw", "flag"),
    [
        (WEIGHT, "500", "unit_missing"),
        (WEIGHT, "approx 500 g", "approximate"),
        (CAPACITY, "1-2 L", "range"),
        (CAPACITY, "500 g", "wrong_unit"),
        (WEIGHT, "500 g / 600 g", "two_values"),
        (WEIGHT, "heavy", "not_a_number"),
        (_attribute("pack_quantity"), "2.5", "not_a_whole_number"),
    ],
)
def test_values_that_cant_be_relied_on_are_flagged(
    rules: Rules, attribute: dict, raw: str, flag: str
) -> None:
    [value] = normalize(rules, attribute, raw)
    assert flag in value.flags
    assert not value.usable


def test_a_combined_size_gives_length_width_and_height(rules: Rules) -> None:
    values = normalize(rules, LENGTH, "10cm x 6cm x 3cm")
    assert [(v.attribute, v.value_number, v.display) for v in values] == [
        ("length", Decimal(100), "10 cm"),
        ("width", Decimal(60), "6 cm"),
        ("height", Decimal(30), "3 cm"),
    ]
    assert all("split_from_size" in v.flags and v.usable for v in values)


def test_ounces_for_a_capacity_are_fluid_ounces(rules: Rules) -> None:
    [value] = normalize(rules, CAPACITY, "12 oz")
    assert value.unit == "ml" and value.usable
    assert value.value_number is not None and round(value.value_number) == 355
    assert value.flags == ("fluid_ounce_assumed",)


def test_codes_and_text_are_cleaned_but_kept(rules: Rules) -> None:
    [sku] = normalize(rules, _attribute("sku", "text"), " ak-1003 ")
    assert (sku.value_text, sku.comparable) == ("AK-1003", "AK-1003")
    [colour] = normalize(rules, _attribute("color", "text"), "  Red\n Glass ")
    assert (colour.value_text, colour.comparable) == ("Red Glass", "red glass")
    [currency] = normalize(rules, _attribute("currency", "text"), "Rs.")
    assert currency.value_text == "INR"


@pytest.mark.parametrize(
    ("text", "number"),
    [
        ("1,00,000", Decimal(100000)),
        ("12,34,567", Decimal(1234567)),
        ("1,234,567", Decimal(1234567)),
        ("1,5", Decimal("1.5")),
        ("2.75", Decimal("2.75")),
    ],
)
def test_numbers_are_read_with_indian_and_international_grouping(
    text: str, number: Decimal
) -> None:
    assert parse_number(text)[0] == number


def _value(number: str | None, unit: str | None = "g", *, rank: int = 1, order: int = 0,
           flags: tuple[str, ...] = (), comparable: str | None = None) -> dict:  # fmt: skip
    return {
        "id": uuid4(),
        "value_number": None if number is None else Decimal(number),
        "unit": unit,
        "comparable": comparable or f"{number} {unit}",
        "flags": list(flags),
        "source_rank": rank,
        "order": order,
    }


def test_agreeing_sources_show_the_best_source() -> None:
    sheet = _value("500", rank=1, order=2)
    photo = _value("503", rank=3, order=1)  # Within 1%.
    choice = choose([photo, sheet], 0.01)
    assert (choice.state, choice.selected_id) == ("verified", sheet["id"])


def test_disagreeing_sources_wait_for_a_person() -> None:
    choice = choose([_value("500"), _value("600", rank=2)], 0.01)
    assert (choice.state, choice.selected_id) == ("conflict", None)
    # Different units never agree, even with the same number.
    assert choose([_value("5", "ml"), _value("5", "g")], 0.01).state == "conflict"


def test_unreliable_values_dont_count() -> None:
    # A value with no unit is set aside, so the other one stands alone.
    usable = _value("500")
    choice = choose([usable, _value("600", None, flags=("unit_missing",))], 0.01)
    assert (choice.state, choice.selected_id) == ("verified", usable["id"])
    assert choose([_value(None, None, flags=("range",))], 0.01).state == "unclear"
    assert choose([], 0.01).state == "missing"


def test_text_agrees_when_its_folded_form_matches() -> None:
    red = _value(None, None, comparable="red")
    assert choose([red, _value(None, None, comparable="red", rank=2)], 0.01).state == "verified"
    assert choose([red, _value(None, None, comparable="blue")], 0.01).state == "conflict"


def _product(key: str, name: str, brand: str = "Acme") -> ProductSummary:
    return ProductSummary(id=uuid4(), key=key, names=(name,), brands=(brand,))


def _variant_settings() -> dict:
    return get_normalization_config()["variants"]


def test_names_differing_only_in_colour_or_size_are_variants() -> None:
    red = _product("JUG-0145", "Glass Jug Red 1L")
    blue = _product("JUG-7788", "Glass Jug Blue 1L")
    bowl = _product("BWL-02", "Steel Bowl Red")
    [pair] = find_variants([red, blue, bowl], _variant_settings(), {})
    assert {pair.a, pair.b} == {red.id, blue.id}
    assert [r["rule"] for r in pair.reasons] == ["same_name_but_colour_or_size"]
    # The table needs the smaller ID first.
    assert str(pair.a) < str(pair.b)


def test_other_brands_or_identical_names_are_not_variants() -> None:
    acme = _product("JUG-01", "Glass Jug Red", "Acme")
    other = _product("JUG-77", "Glass Jug Blue", "Borosil")
    same = _product("JUG-88", "Glass Jug Red", "Acme")
    assert find_variants([acme, other], _variant_settings(), {}) == []
    assert find_variants([acme, same], _variant_settings(), {}) == []


def test_codes_sharing_a_base_are_variants_and_photos_only_support() -> None:
    red = _product("AK-1002-RED", "Water Jug")
    blue = _product("AK-1002-BLU", "Pitcher")
    plain = _product("AK-1003", "Bowl")
    # Sequential codes are different products.
    lookalike = _product("AK-1004", "Plate")
    similarity = {
        frozenset({red.id, blue.id}): 0.93,
        # Very similar photos, but nothing else in common: not a variant.
        frozenset({plain.id, lookalike.id}): 0.99,
    }
    [pair] = find_variants([red, blue, plain, lookalike], _variant_settings(), similarity)
    assert {pair.a, pair.b} == {red.id, blue.id}
    assert [r["rule"] for r in pair.reasons] == ["same_base_code", "similar_photos"]
    assert pair.reasons[0]["base"] == "AK-1002"


def test_category_paths_leave_out_the_root() -> None:
    root, dining, mugs = UUID(int=1), UUID(int=2), UUID(int=3)
    rows = [
        {"id": root, "code": "home-kitchen", "name": "Home & Kitchen", "parent_id": None},
        {"id": dining, "code": "dining", "name": "Dining", "parent_id": root},
        {"id": mugs, "code": "dining.mugs", "name": "Mugs", "parent_id": dining},
    ]
    assert [(row["code"], row["path"]) for row in category_paths(rows)] == [
        ("dining", "Dining"),
        ("dining.mugs", "Dining › Mugs"),
    ]
