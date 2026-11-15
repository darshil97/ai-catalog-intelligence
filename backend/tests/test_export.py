"""Covers the export files: cell safety, CSV, Excel, Shopify's format and variants (M10, D41).

No database.
"""

import csv
import io
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from app.export.data import ExportData, Item, grams, price_number, where
from app.export.formats import SHOPIFY_COLUMNS, build_csv, build_shopify, build_xlsx, handle
from app.export.safety import safe_cell, shopify_text
from app.export.variants import group, group_title
from app.validation.base import Attribute, ProductData

MADE = datetime(2026, 11, 15, 10, 30, tzinfo=UTC)


def _attr(code: str, shown: str, number: Decimal | None = None) -> Attribute:
    return Attribute(code, code.replace("_", " ").capitalize(), "verified", None, shown, number,
                     None, ())  # fmt: skip


def _item(key: str = "AK-1", photos: int = 1, **values: str) -> Item:
    values = {"product_name": "Glass bottle", "brand": "Acme", "price": "1299 INR", **values}
    title = values.pop("title", f"Acme {values['product_name']}")
    return Item(
        product=ProductData(
            id=uuid4(),
            key=key,
            row={
                "generated_title": title,
                "generated_description": "A sturdy bottle.",
                "generated_bullets": ["Holds 1 litre", "Dishwasher safe"],
                "approved_at": MADE,
                "approved_by": "user-1",
            },
            attributes={code: _attr(code, shown) for code, shown in values.items()},
        ),
        category_path="Home › Kitchen › Bottles",
        photos=[{"image_id": uuid4(), "s3_key": f"k/{key}/{n}"} for n in range(photos)],
        sources=[{"attribute": "Price", "raw_value": "1,299", "file": "list.xlsx",
                  "location": {"sheet": "Sheet1", "row": 4, "column": "C", "header": "MRP"}}],
    )  # fmt: skip


def _data(*items: Item, pairs: dict[frozenset[UUID], str] | None = None) -> ExportData:
    return ExportData(
        batch={"name": "Vardhan Nov", "supplier_name": "Vardhan"},
        profile="shopify",
        profile_name="Shopify",
        attributes=[
            {"code": "brand", "name": "Brand"},
            {"code": "color", "name": "Colour"},
            {"code": "capacity", "name": "Capacity"},
        ],
        items=list(items),
        left_out=[{"product_key": "AK-9", "reason": "Not approved yet."}],
        variant_pairs=pairs or {},
    )


def _links(*items: Item) -> dict[UUID, str]:
    return {p["image_id"]: f"https://s3.example/{p['s3_key']}?sig" for i in items for p in i.photos}


def _rows(body: bytes) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(body.decode("utf-8-sig"))))


# Safety ------------------------------------------------------------------------------------


@pytest.mark.parametrize("text", ["=SUM(A1)", "+91 98", "-5", "@cmd", "\tx", "\rx"])
def test_risky_cells_are_made_inert(text: str) -> None:
    assert safe_cell(text) == "'" + text
    notes: list[str] = []
    cleaned = shopify_text(text, "AK-1 title", notes)
    assert not cleaned.startswith(("=", "+", "-", "@", "\t", "\r"))
    assert len(notes) == 1 and notes[0].startswith("AK-1 title")


def test_ordinary_values_are_unchanged() -> None:
    assert safe_cell("Glass bottle") == "Glass bottle"
    assert safe_cell(12) == 12
    notes: list[str] = []
    assert shopify_text("Glass bottle", "x", notes) == "Glass bottle" and notes == []


# Reading values ----------------------------------------------------------------------------


def test_price_weight_and_source_location_are_read() -> None:
    item = _item(price="1,299.50 INR", net_weight="1.2 kg")
    assert price_number(item) == (Decimal("1299.50"), "INR")
    assert grams(item) == 1200
    assert where(item.sources[0]) == "sheet Sheet1, row 4, column C (MRP)"
    assert where({"location": {"page": 2, "quote": "1 L"}}) == "page 2, “1 L”"


# CSV and Excel -----------------------------------------------------------------------------


def test_csv_has_a_bom_every_attribute_and_safe_cells() -> None:
    risky = _item("AK-2", product_name="=HYPERLINK(1)", title="=HYPERLINK(1)")
    items = (_item(color="Red"), risky)
    body, notes = build_csv(_data(*items), _links(*items))
    assert body.startswith(b"\xef\xbb\xbf") and notes == []
    rows = _rows(body)
    assert list(rows[0])[:5] == ["Product code", "Name", "Brand", "Category", "Variant group"]
    assert {"Colour", "Capacity", "Title", "Bullet 5", "Photo links"} <= set(rows[0])
    header = body.decode("utf-8-sig").splitlines()[0].split(",")
    assert len(header) == len(set(header)), "a column appears twice"
    assert rows[0]["Colour"] == "Red" and rows[0]["Photo links"].startswith("https://s3.example")
    assert rows[1]["Name"] == "'=HYPERLINK(1)" and rows[1]["Title"] == "'=HYPERLINK(1)"


def test_excel_has_products_sources_and_about_sheets() -> None:
    from openpyxl import load_workbook

    item = _item(color="Red")
    body, _ = build_xlsx(_data(item), _links(item), MADE)
    book = load_workbook(io.BytesIO(body))
    assert book.sheetnames == ["Products", "Sources", "About"]
    assert book["Products"]["A2"].value == "AK-1"
    sources = [[c.value for c in row] for row in book["Sources"].iter_rows(min_row=2)]
    place = "sheet Sheet1, row 4, column C (MRP)"
    assert sources == [["AK-1", "Price", "1,299", "list.xlsx", place]]
    about = {row[0].value: row[1].value for row in book["About"].iter_rows(min_row=2)}
    assert about["Products left out"] == 1 and about["Left out: AK-9"] == "Not approved yet."


# Shopify -----------------------------------------------------------------------------------


def test_shopify_file_has_its_columns_drafts_and_photo_rows() -> None:
    item = _item(photos=3, gtin="8901234567890", net_weight="500 g")
    body, notes = build_shopify(_data(item), _links(item))
    assert not body.startswith(b"\xef\xbb\xbf")
    rows = _rows(body)
    assert list(rows[0]) == SHOPIFY_COLUMNS
    first = rows[0]
    assert first["Status"] == "draft" and first["Published on online store"] == "false"
    assert first["Option1 name"] == "Title" and first["Option1 value"] == "Default Title"
    assert first["Price"] == "1299.00" and first["Barcodes"] == "gtin:8901234567890"
    assert first["Weight value (grams)"] == "500" and first["Weight unit for display"] == "g"
    assert first["Type"] == "Bottles" and first["Tags"] == "Home, Kitchen, Bottles"
    assert "<ul><li>Holds 1 litre</li>" in first["Description"]
    assert [r["Image position"] for r in rows] == ["1", "2", "3"]
    assert {r["URL handle"] for r in rows} == {"acme-glass-bottle"}
    assert all(r["Title"] == "" for r in rows[1:])
    assert notes == []


def test_shopify_strips_formula_characters_and_says_so() -> None:
    item = _item(title="=cmd bottle", brand="+Acme")
    body, notes = build_shopify(_data(item), _links(item))
    row = _rows(body)[0]
    assert row["Title"] == "cmd bottle" and row["Vendor"] == "Acme"
    assert any("AK-1 title" in note for note in notes)
    assert any("AK-1 brand" in note for note in notes)


def test_handles_are_unique_in_a_file() -> None:
    taken: set[str] = set()
    assert handle("Acme Glass Bottle, 1 L", "AK-1", taken) == "acme-glass-bottle-1-l"
    assert handle("Acme Glass Bottle, 1 L", "AK-2", taken) == "acme-glass-bottle-1-l-ak-2"
    assert handle("", "AK 3", taken) == "ak-3"


def test_a_price_in_another_currency_is_noted() -> None:
    item = _item(price="15 USD")
    _, notes = build_shopify(_data(item), _links(item))
    assert any("USD" in note for note in notes)


# Variants ----------------------------------------------------------------------------------


def _pairs(*pairs: tuple[Item, Item], status: str = "confirmed") -> dict[frozenset[UUID], str]:
    return {frozenset((a.id, b.id)): status for a, b in pairs}


def test_confirmed_colour_variants_become_one_shopify_product() -> None:
    red = _item("AK-1", title="Acme Glass bottle, Red", color="Red")
    blue = _item("AK-2", title="Acme Glass bottle, Blue", color="Blue")
    green = _item("AK-3", title="Acme Glass bottle, Green", color="Green")
    data = _data(red, blue, green, pairs=_pairs((red, blue), (blue, green)))
    groups, notes = group(data.items, data.variant_pairs)
    assert len(groups) == 1 and notes == []
    assert groups[0].option == "Colour" and groups[0].values == ("Red", "Blue", "Green")
    assert group_title(groups[0]) == "Acme Glass bottle"

    rows = _rows(build_shopify(data, _links(red, blue, green))[0])
    variants = [r for r in rows if r["SKU"]]
    assert [r["Option1 value"] for r in variants] == ["Red", "Blue", "Green"]
    assert variants[0]["Option1 name"] == "Colour" and variants[1]["Option1 name"] == ""
    assert all(r["Variant image URL"] for r in variants)
    assert len({r["URL handle"] for r in rows}) == 1

    csv_rows = _rows(build_csv(data, _links(red, blue, green))[0])
    assert {r["Variant group"] for r in csv_rows} == {"AK-1 (Colour)"}


def test_flagged_or_dismissed_pairs_are_not_grouped() -> None:
    a, b = _item("AK-1", color="Red"), _item("AK-2", color="Blue")
    for status in ("flagged", "dismissed"):
        groups, _ = group([a, b], _pairs((a, b), status=status))
        assert [g.option for g in groups] == [None, None]


def test_size_variants_use_a_size_option() -> None:
    small, large = _item("AK-1", capacity="500 ml"), _item("AK-2", capacity="1 L")
    groups, _ = group([small, large], _pairs((small, large)))
    assert groups[0].option == "Size" and groups[0].values == ("500 ml", "1 L")


def test_variants_differing_in_colour_and_size_stay_separate_with_a_note() -> None:
    a = _item("AK-1", color="Red", capacity="500 ml")
    b = _item("AK-2", color="Blue", capacity="1 L")
    groups, notes = group([a, b], _pairs((a, b)))
    assert [g.option for g in groups] == [None, None]
    assert len(notes) == 1 and "AK-1, AK-2" in notes[0]
