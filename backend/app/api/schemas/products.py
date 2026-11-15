"""Schemas for products, their observations, and how each sheet's columns were mapped."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from app.api.schemas.enrichment import GeneratedContent
from app.api.schemas.validation import ProductValidation


class Product(BaseModel):
    """A product found in a batch's spreadsheets."""

    id: UUID = Field(..., description="Product ID.")
    batch_id: UUID = Field(..., description="The batch it belongs to.")
    product_key: str = Field(
        ..., description="The cleaned identifier that makes rows one product.", examples=["AB-12"]
    )
    key_source: str = Field(
        ..., description="Which identifier the key came from: sku, model_number or gtin."
    )
    supplier_sku: str | None = Field(None, description="The SKU exactly as the supplier wrote it.")
    identifier_flags: list[str] = Field(
        ...,
        description="Problems with the identifier: scientific_notation, precision_lost or "
        "invalid_gtin.",
    )
    source_order: int = Field(..., description="Position in file order. The first product is 1.")
    status: str = Field(..., description="draft, in_review, approved or rejected.")
    created_at: datetime = Field(..., description="When it was created.")


class ProductSummary(Product):
    """A product in a list."""

    name: str | None = Field(None, description="Its name as the supplier wrote it, if given.")
    observation_count: int = Field(..., description="How many values were read for it.")


class ProductList(BaseModel):
    """A batch's products, in file order."""

    batch_id: UUID = Field(..., description="The batch.")
    items: list[ProductSummary] = Field(..., description="Products, first in the file first.")


class Observation(BaseModel):
    """One value exactly as a source gave it, with where it was read from."""

    id: UUID = Field(..., description="Observation ID.")
    attribute_code: str = Field(..., description="The attribute's code.", examples=["net_weight"])
    attribute_name: str = Field(..., description="The attribute's name.")
    raw_value: str = Field(..., description="The value exactly as found. Never changed.")
    source_file_id: UUID = Field(..., description="The file it came from.")
    source_file_name: str = Field(..., description="That file's name.")
    source_record_id: UUID | None = Field(None, description="The row it came from.")
    location: dict[str, Any] = Field(
        ...,
        description="Where in the file. Spreadsheets: sheet (null for a CSV), sheet_index, row, "
        "column and header, and merged_from for a merged cell. PDFs and photos: page (null for "
        "a photo), method (text or ocr), quote (the text the value was read from), bbox (left, "
        "top, right, bottom) and the page_width and page_height it is measured against.",
    )
    extractor: str = Field(..., description="What read it, with its version.")
    confidence: float | None = Field(
        None, description="How sure the column's mapping is, from 0 to 1."
    )
    mapped_by: str = Field(
        ...,
        description="How it was read: known_name or llm for a spreadsheet column, llm for a "
        "value read from a PDF or photo.",
    )


class ProductDetail(Product):
    """A product with everything read about it."""

    observations: list[Observation] = Field(
        ..., description="Values grouped by attribute, then in file order."
    )
    catalog_values: list["CatalogValue"] = Field(
        default_factory=list,
        description="Every attribute: its state, the value it shows, and the standard values "
        "behind it.",
    )
    category: "ProductCategory | None" = Field(None, description="Its category.")
    generated_content: "GeneratedContent | None" = Field(
        None, description="Its generated title, description and bullets, with their citations."
    )
    validation: "ProductValidation | None" = Field(
        None, description="Its checks under the batch's marketplace profile."
    )
    images: list["ProductImage"] = Field(
        default_factory=list, description="Photos accepted as showing it, in file order."
    )
    differing_attributes: list[str] = Field(
        ...,
        description="Attribute codes whose sources don't all give the same value (ignoring "
        "case and spacing). Every value is kept; none is chosen here.",
    )


class SheetColumn(BaseModel):
    """One column of a sheet and what it was mapped to."""

    index: int = Field(..., description="Position, counting from 0.")
    letter: str = Field(..., description="The column's letters.", examples=["C"])
    header: str = Field(..., description="Its name as written. Empty if it has none.")
    attribute: str | None = Field(None, description="The attribute code, or null if unmapped.")
    source: str | None = Field(None, description="known_name or llm. Null if unmapped.")
    confidence: float | None = Field(None, description="How sure the mapping is, from 0 to 1.")
    reason: str | None = Field(None, description="The LLM's few words on why.")
    suggestion: str | None = Field(
        None, description="An attribute the LLM proposed without enough confidence to use."
    )


class Sheet(BaseModel):
    """One sheet of a spreadsheet, and whether and how it was used."""

    id: UUID = Field(..., description="Sheet ID.")
    source_file_id: UUID = Field(..., description="The file it is in.")
    source_file_name: str = Field(..., description="That file's name.")
    sheet_index: int = Field(..., description="Position in the file, counting from 0.")
    sheet_name: str | None = Field(None, description="The sheet's name. Null for a CSV.")
    hidden: bool = Field(..., description="True if the sheet is hidden in the workbook.")
    status: str = Field(..., description="used or skipped.")
    skip_reason: str | None = Field(
        None, description="Why it wasn't used: empty, no_header, no_identifier or unreadable."
    )
    skip_message: str | None = Field(None, description="Why it wasn't used, for people.")
    header_row: int | None = Field(None, description="The row holding the column names.")
    header_rows: int | None = Field(None, description="1, or 2 for a two-row header.")
    columns: list[SheetColumn] = Field(..., description="Every column, mapped or not.")
    mapping_source: str = Field(
        ...,
        description="How columns were mapped: known_names, saved (reused for this supplier), "
        "llm, llm_failed, llm_capped, llm_budget (monthly LLM budget used) or none.",
    )
    mapping_message: str | None = Field(
        None, description="Why the LLM wasn't used when it was needed."
    )
    row_count: int = Field(..., description="Data rows read.")
    rows_without_identifier: int = Field(
        ..., description="Rows skipped for having no SKU, model number or barcode."
    )
    duplicate_rows: int = Field(
        ..., description="Rows repeating a product already seen. They add values to it."
    )
    rows_over_cap: int = Field(..., description="Rows past the per-sheet limit, not read.")
    unreadable_rows: int = Field(..., description="Rows that couldn't be parsed.")


class SheetList(BaseModel):
    """A batch's sheets, in file order."""

    batch_id: UUID = Field(..., description="The batch.")
    items: list[Sheet] = Field(..., description="Sheets, in upload then sheet order.")


class Document(BaseModel):
    """A PDF or photo, how it was read, and what was found in it."""

    source_file_id: UUID = Field(..., description="The file.")
    source_file_name: str = Field(..., description="Its name.")
    parent_file_id: UUID | None = Field(None, description="The ZIP or PDF it came from, if any.")
    kind: str = Field(..., description="pdf or image.")
    status: str = Field(..., description="read or skipped.")
    skip_reason: str | None = Field(None, description="Why it wasn't read: unreadable or no_text.")
    skip_message: str | None = Field(None, description="Why it wasn't read, for people.")
    pages: int = Field(..., description="Pages in the file. A photo is one page.")
    pages_with_text: int = Field(..., description="Pages where text was found.")
    pages_ocr: int = Field(..., description="Pages read with OCR, such as scans and photos.")
    pages_unreadable: int = Field(..., description="Pages that couldn't be read.")
    characters: int = Field(..., description="Characters of text found.")
    product_keys: list[str] = Field(..., description="Products whose values it gave.")
    pages_without_product: int = Field(
        ...,
        description="Pages that named none of the batch's products, so weren't read for values.",
    )
    values_kept: int = Field(..., description="Values found on the page and kept.")
    values_dropped: int = Field(
        ..., description="Values the LLM gave that weren't on the page, or had no clear product."
    )
    llm_source: str = Field(
        ...,
        description="none, llm, llm_failed, llm_capped (the run's calls were used) or llm_budget "
        "(this month's LLM budget was used).",
    )
    llm_message: str | None = Field(None, description="Why values weren't read, for people.")


class DocumentList(BaseModel):
    """A batch's PDFs and photos, in file order."""

    batch_id: UUID = Field(..., description="The batch.")
    items: list[Document] = Field(..., description="Files, in upload order.")


class BatchImage(BaseModel):
    """A kept image: its size, thumbnail, and the near-duplicates it was grouped with."""

    id: UUID = Field(..., description="Image ID.")
    source_file_id: UUID = Field(..., description="The file it is.")
    source_file_name: str = Field(..., description="That file's name.")
    parent_file_id: UUID | None = Field(None, description="The ZIP or PDF it came from, if any.")
    parent_file_name: str | None = Field(None, description="That ZIP or PDF's name.")
    status: str = Field(..., description="processed or skipped.")
    skip_reason: str | None = Field(None, description="Why it wasn't processed: unreadable.")
    skip_message: str | None = Field(None, description="Why it wasn't processed, for people.")
    width: int | None = Field(None, description="Width in pixels, upright.")
    height: int | None = Field(None, description="Height in pixels, upright.")
    thumbnail_url: str | None = Field(
        None, description="A short-lived link to a small JPEG of it. Expires after 15 minutes."
    )
    embedded: bool = Field(..., description="True if it can be compared with others by content.")
    embedding_note: str | None = Field(None, description="Why it can't be compared, if not.")
    duplicate_group_id: UUID | None = Field(
        None, description="Its near-duplicate group. Images in a group show the same picture."
    )
    duplicate_kind: str | None = Field(None, description="exact or near.")
    group_size: int = Field(..., description="Images in its group, itself included. 1 if none.")
    is_representative: bool = Field(..., description="True for the group's largest image.")


class BatchImageList(BaseModel):
    """A batch's kept images, in file order."""

    batch_id: UUID = Field(..., description="The batch.")
    items: list[BatchImage] = Field(..., description="Images, in upload order.")


class ProductImage(BaseModel):
    """A photo accepted as showing a product."""

    image_id: UUID = Field(..., description="The image.")
    image_name: str = Field(..., description="Its file name.")
    position: int = Field(..., description="Order among the product's photos.")
    thumbnail_url: str | None = Field(None, description="A short-lived link to a small JPEG.")
    decision: str = Field(..., description="auto_accepted or accepted (by a person).")
    decided_by: str = Field(..., description="'system', or the person who accepted it.")


class StandardValue(BaseModel):
    """One source's value in standard form."""

    id: UUID = Field(..., description="Standard value ID, for choosing it in a conflict.")
    display: str = Field(..., description='The value as shown, such as "1.5 L".')
    unit: str | None = Field(None, description="Standard unit (g, ml, mm) or currency code.")
    flags: list[str] = Field(
        ...,
        description="How it was read, and why it can't be relied on if so: unit_missing, "
        "range, approximate, two_values, wrong_unit, not_a_number, currency_unknown, "
        "not_a_whole_number (blocking); currency_assumed, decimal_comma, split_from_size, "
        "fluid_ounce_assumed, unit_from_header.",
    )
    raw_value: str = Field(..., description="The value exactly as the source gave it.")
    source_file_name: str = Field(..., description="The file it came from.")
    location: dict[str, Any] = Field(..., description="Where in the file.")
    rule_version: str = Field(..., description="The rules that made it, such as units@1.")


class CatalogValue(BaseModel):
    """What a product shows for one attribute."""

    code: str = Field(..., description="Attribute code.")
    name: str = Field(..., description="Attribute name.")
    state: str = Field(
        ...,
        description="verified (sources agree), conflict (they don't; a person decides), "
        "unclear (none can be relied on), missing, or reviewer_set (a person decided).",
    )
    shown: str | None = Field(None, description="The value shown, if any.")
    decided_by: str | None = Field(None, description="'system', or the person who decided.")
    values: list[StandardValue] = Field(..., description="Every source's standard value.")


class CategoryRef(BaseModel):
    """A category."""

    id: UUID = Field(..., description="Category ID.")
    code: str = Field(..., description="Category code.")
    name: str = Field(..., description="Category name.")


class ProductCategory(BaseModel):
    """A product's category, or what was suggested."""

    state: str = Field(..., description="none, assigned, or needs_review.")
    source: str | None = Field(None, description="llm or person.")
    confidence: float | None = Field(None, description="How sure the LLM was.")
    reason: str | None = Field(None, description="Why, in a few words.")
    category: CategoryRef | None = Field(None, description="The category, once assigned.")
    suggested: CategoryRef | None = Field(None, description="The LLM's suggestion, if unsure.")


class CategoryNode(BaseModel):
    """One category of the tree."""

    id: UUID = Field(..., description="Category ID.")
    code: str = Field(..., description="Category code.")
    name: str = Field(..., description="Category name.")
    path: str = Field(..., description='Its full path, such as "Dining › Drinkware".')


class CategoryList(BaseModel):
    """The category tree, in order."""

    items: list[CategoryNode] = Field(..., description="Categories, parents before children.")


class VariantPair(BaseModel):
    """Two products that may be variants of one. Flagged only, never merged."""

    id: UUID = Field(..., description="Pair ID.")
    status: str = Field(..., description="flagged, confirmed or dismissed.")
    product_a_id: UUID = Field(..., description="One product.")
    product_a_key: str = Field(..., description="Its key.")
    product_b_id: UUID = Field(..., description="The other.")
    product_b_key: str = Field(..., description="Its key.")
    reasons: list[dict[str, Any]] = Field(
        ...,
        description="Why: rule (same_name_but_colour_or_size, same_base_code, "
        "similar_photos) and text.",
    )


class VariantList(BaseModel):
    """A batch's possible variants."""

    batch_id: UUID = Field(..., description="The batch.")
    items: list[VariantPair] = Field(..., description="Pairs, in file order.")


ProductDetail.model_rebuild()
