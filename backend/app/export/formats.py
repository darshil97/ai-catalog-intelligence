"""The three export files (D41): CSV, Excel and Shopify's product CSV. Pure.

Each builder takes an ExportData and the signed photo links (image ID to URL) and returns the
file's bytes and any notes for people (such as cells changed for safety).

Shopify's columns follow its help page "Using CSV files to import and export products"
(help.shopify.com/en/manual/products/import-export/using-csv), read on 2026-11-15. Extra
photos are rows that repeat the URL handle, and extra variants are rows with their own option
value. Products are imported as drafts and not published.
"""

import csv
import html
import io
import re
from datetime import datetime
from typing import Any
from uuid import UUID

from app.export.data import ExportData, Item, grams, price_number, where
from app.export.safety import safe_cell, shopify_text
from app.export.variants import Group, group, group_title

BULLETS = 5
_HANDLE = re.compile(r"[^a-z0-9]+")
_OWN_COLUMNS = {"product_name", "brand"}
_GTIN = re.compile(r"^\d{8}$|^\d{12,14}$")

# Shopify's product CSV columns, in the help page's order, as far as this export fills them.
SHOPIFY_COLUMNS = [
    "Title", "URL handle", "Description", "Vendor", "Product category", "Type", "Tags",
    "Published on online store", "Status", "SKU", "Barcodes", "Option1 name", "Option1 value",
    "Price", "Charge tax", "Weight value (grams)", "Weight unit for display",
    "Requires shipping", "Fulfillment service", "Product image URL", "Image position",
    "Image alt text", "Variant image URL", "Gift card", "SEO title", "SEO description",
]  # fmt: skip


def _photo_links(item: Item, links: dict[UUID, str]) -> list[str]:
    return [links[p["image_id"]] for p in item.photos if p["image_id"] in links]


def catalog_rows(data: ExportData, links: dict[UUID, str]) -> tuple[list[str], list[list[Any]]]:
    """The header and rows shared by the CSV and Excel files."""
    groups, _ = group(data.items, data.variant_pairs)
    variant_of: dict[UUID, str] = {}
    for g in groups:
        if g.option:
            for item in g.items:
                variant_of[item.id] = f"{g.lead.key} ({g.option})"
    # Name and brand have their own columns near the front.
    attributes = [a for a in data.attributes if a["code"] not in _OWN_COLUMNS]
    names = [a["name"] for a in attributes]
    header = [
        "Product code", "Name", "Brand", "Category", "Variant group", *names, "Title",
        "Description", *[f"Bullet {n}" for n in range(1, BULLETS + 1)], "Photo links",
        "Approved at", "Approved by",
    ]  # fmt: skip
    rows = []
    for item in data.items:
        bullets = item.bullets[:BULLETS] + [""] * (BULLETS - len(item.bullets[:BULLETS]))
        approved = item.product.row.get("approved_at")
        rows.append(
            [
                item.key,
                item.value("product_name") or "",
                item.value("brand") or "",
                item.category_path or "",
                variant_of.get(item.id, ""),
                *[item.value(a["code"]) or "" for a in attributes],
                item.title,
                item.description,
                *bullets,
                " | ".join(_photo_links(item, links)),
                approved.isoformat(timespec="minutes") if isinstance(approved, datetime) else "",
                "you" if item.product.row.get("approved_by") else "",
            ]
        )
    return header, rows


def build_csv(data: ExportData, links: dict[UUID, str]) -> tuple[bytes, list[str]]:
    """CSV, UTF-8 with a byte-order mark so Excel shows ₹ and Indian scripts correctly."""
    header, rows = catalog_rows(data, links)
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(header)
    writer.writerows([[safe_cell(v) for v in row] for row in rows])
    return out.getvalue().encode("utf-8-sig"), []


def build_xlsx(
    data: ExportData, links: dict[UUID, str], made_at: datetime
) -> tuple[bytes, list[str]]:  # noqa: E501
    """Excel with Products, Sources (where every value came from) and About sheets."""
    from openpyxl import Workbook
    from openpyxl.styles import Font
    from openpyxl.utils import get_column_letter

    book = Workbook()
    bold = Font(bold=True)

    def sheet(title: str, header: list[str], rows: list[list[Any]], first: bool = False) -> None:
        ws = book.active if first else book.create_sheet()
        assert ws is not None  # noqa: S101
        ws.title = title
        ws.append(header)
        for cell in ws[1]:
            cell.font = bold
        for row in rows:
            ws.append([safe_cell(v) for v in row])
        ws.freeze_panes = "A2"
        for index, name in enumerate(header, start=1):
            longest = max([len(str(name)), *(len(str(r[index - 1])) for r in rows)] or [10])
            ws.column_dimensions[get_column_letter(index)].width = min(max(longest + 2, 10), 60)

    header, rows = catalog_rows(data, links)
    sheet("Products", header, rows, first=True)

    sources: list[list[Any]] = []
    for item in data.items:
        for source in item.sources:
            sources.append([item.key, source["attribute"], source["raw_value"],
                            source["file"], where(source)])  # fmt: skip
        for attribute in item.product.attributes.values():
            if attribute.state == "reviewer_set" and attribute.shown:
                sources.append([item.key, attribute.name, attribute.shown, "", "Set by you"])
    sheet("Sources", ["Product code", "Attribute", "Value in the source", "File", "Where"],
          sources)  # fmt: skip

    about = [
        ["Batch", data.batch["name"]],
        ["Supplier", data.batch["supplier_name"]],
        ["Checked for", data.profile_name],
        ["Made at", made_at.isoformat(timespec="minutes")],
        ["Products included", len(data.items)],
        ["Products left out", len(data.left_out)],
        *[[f"Left out: {row['product_key']}", row["reason"]] for row in data.left_out],
        ["Photo links", "Signed links that stop working after 7 days."],
    ]
    sheet("About", ["", ""], about)
    out = io.BytesIO()
    book.save(out)
    return out.getvalue(), []


def handle(title: str, key: str, taken: set[str]) -> str:
    """Shopify's URL handle: lowercase letters, numbers and dashes, unique in the file."""
    base = _HANDLE.sub("-", title.lower()).strip("-")[:80] or _HANDLE.sub("-", key.lower())
    candidate = base
    if candidate in taken:
        candidate = f"{base}-{_HANDLE.sub('-', key.lower()).strip('-')}"
    taken.add(candidate)
    return candidate


def _body(item: Item) -> str:
    parts = [f"<p>{html.escape(item.description)}</p>"] if item.description else []
    if item.bullets:
        parts.append("<ul>" + "".join(f"<li>{html.escape(b)}</li>" for b in item.bullets) + "</ul>")
    return "".join(parts)


def _barcode(item: Item) -> str:
    value = (item.value("gtin") or "").replace(" ", "")
    return f"gtin:{value}" if _GTIN.match(value) else value


def build_shopify(data: ExportData, links: dict[UUID, str]) -> tuple[bytes, list[str]]:
    """Shopify's product CSV: plain UTF-8, one row per variant and per extra photo."""
    groups, notes = group(data.items, data.variant_pairs)
    rows: list[dict[str, Any]] = []
    taken: set[str] = set()
    for g in groups:
        rows.extend(_shopify_group(g, links, taken, notes))
    for item in data.items:
        _, currency = price_number(item)
        if currency and currency != "INR":
            notes.append(f"{item.key}: its price is in {currency}; Shopify uses your store's "
                         "currency.")  # fmt: skip
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=SHOPIFY_COLUMNS, lineterminator="\r\n")
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue().encode("utf-8"), notes


def _shopify_group(g: Group, links: dict[UUID, str], taken: set[str],
                   notes: list[str]) -> list[dict[str, Any]]:  # fmt: skip
    lead = g.lead
    title = shopify_text(group_title(g), f"{lead.key} title", notes)
    url = handle(title, lead.key, taken)
    path = lead.category_path or ""
    rows: list[dict[str, Any]] = []
    photos = [(item, link) for item in g.items for link in _photo_links(item, links)]
    for index, item in enumerate(g.items):
        price, _ = price_number(item)
        weight = grams(item)
        own = _photo_links(item, links)
        row: dict[str, Any] = {
            "URL handle": url,
            "Option1 name": g.option or "Title" if index == 0 else "",
            "Option1 value": g.values[index] if g.option else "Default Title",
            "SKU": item.key,
            "Barcodes": _barcode(item),
            "Price": f"{price:.2f}" if price is not None else "",
            "Charge tax": "true",
            "Weight value (grams)": weight if weight is not None else "",
            "Weight unit for display": "g" if weight is not None else "",
            "Requires shipping": "true",
            "Fulfillment service": "manual",
            "Variant image URL": own[0] if own and g.option else "",
        }
        if index == 0:
            description = shopify_text(lead.description, f"{lead.key} description", notes)
            row.update(
                {
                    "Title": title,
                    "Description": _body(lead)
                    if description == lead.description
                    else f"<p>{html.escape(description)}</p>",
                    "Vendor": shopify_text(lead.value("brand") or "", f"{lead.key} brand", notes),
                    "Product category": "",
                    "Type": path.split(" › ")[-1] if path else "",
                    "Tags": ", ".join(path.split(" › ")) if path else "",
                    "Published on online store": "false",
                    "Status": "draft",
                    "Gift card": "false",
                    "SEO title": title[:70],
                    "SEO description": re.sub(r"\s+", " ", lead.description)[:320],
                }
            )
            if photos:
                row.update({"Product image URL": photos[0][1], "Image position": 1,
                            "Image alt text": title[:512]})  # fmt: skip
        rows.append(row)
    for position, (item, link) in enumerate(photos[1:], start=2):
        rows.append({"URL handle": url, "Product image URL": link, "Image position": position,
                     "Image alt text": f"{title} ({item.key})"[:512]})  # fmt: skip
    return rows
