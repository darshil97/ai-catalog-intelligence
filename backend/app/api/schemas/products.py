"""Schemas for products, their observations, and how each sheet's columns were mapped."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


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
        description="Where in the file: sheet (null for a CSV), sheet_index, row, column and "
        "header. merged_from names the cell holding the value, for a merged cell.",
    )
    extractor: str = Field(..., description="What read it, with its version.")
    confidence: float | None = Field(
        None, description="How sure the column's mapping is, from 0 to 1."
    )
    mapped_by: str = Field(..., description="How its column was recognised: known_name or llm.")


class ProductDetail(Product):
    """A product with everything read about it."""

    observations: list[Observation] = Field(
        ..., description="Values grouped by attribute, then in file order."
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
        "llm, llm_failed, llm_capped or none.",
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
