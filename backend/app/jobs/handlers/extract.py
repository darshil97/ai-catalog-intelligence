"""Extract jobs: turn a batch's spreadsheets into products and what each cell said about them.

extract_select payload {"batch_id"}: map every sheet's columns, choose which products are kept,
    and create one extract_rows chunk per block of rows that holds a kept product.
extract_rows   payload {"batch_id", "sheet_id", "start_row", "end_row"}: store the rows of kept
    products in that block, and one observation per mapped cell.

Products are kept in file order: upload order, then sheet order, then row order. The first N
distinct identifiers win (D13), and everything else is counted, never stored.

Both jobs can run again safely. extract_select clears what an earlier run of the batch stored,
and extract_rows derives row IDs from the sheet and row number, so nothing is stored twice. A
row that can't be used is reported and the rest of its chunk carries on. Only failures on our
side fail a job.
"""

import json
import tempfile
import uuid
from collections.abc import Iterator, Sequence
from dataclasses import asdict, dataclass, field
from itertools import islice
from pathlib import Path
from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb

from app.catalog.identifiers import Identifier, canonicalize
from app.config import get_extraction_config, get_ingest_config
from app.db import get_pool, query
from app.ingest.spreadsheet import (
    ReadLimits,
    Sheet,
    SheetRow,
    SpreadsheetUnreadableError,
    column_letter,
    get_reader,
)
from app.ingest.spreadsheet.base import UNUSABLE_FLAGS
from app.ingest.spreadsheet.headers import build_known_names
from app.ingest.spreadsheet.mapping import (
    AttributeDef,
    LlmAnswer,
    LlmMappingError,
    MappedColumn,
    MappingAnswer,
    SheetMapping,
    map_sheet,
)
from app.jobs import queue
from app.jobs.base import JobContext, register_handler
from app.jobs.errors import PermanentJobError, TransientJobError
from app.jobs.models import ChunkSpec, Job, JobOutcome
from app.llm import LlmBudgetError, LlmTaskError, run_task
from app.storage import ObjectTooLargeError, StorageError, get_object_store
from app.usage import effective_limits
from app.utils.logger import get_logger

logger = get_logger(__name__)

Row = dict[str, Any]

MAPPING_TASK = "column_mapping"
_INTERRUPT_CHECK_ROWS = 1000

# Why a sheet wasn't used. Stored on the sheet with a message for the user.
SKIP_EMPTY = "empty"
SKIP_NO_HEADER = "no_header"
SKIP_NO_IDENTIFIER = "no_identifier"
SKIP_UNREADABLE = "unreadable"
_SKIP_MESSAGES = {
    SKIP_EMPTY: "This sheet is empty.",
    SKIP_NO_HEADER: "No row of column names could be found in this sheet.",
    SKIP_NO_IDENTIFIER: (
        "No column was recognised as a SKU, model number or barcode, so its rows can't be "
        "told apart as products."
    ),
    SKIP_UNREADABLE: "This sheet couldn't be read.",
}

# Why a row or cell wasn't used. Stored in processing_errors with its location.
NOTE_ROW_UNREADABLE = "row_unreadable"
NOTE_ROW_TOO_LARGE = "row_too_large"
NOTE_MORE = "more_row_problems"
_CELL_NOTE_MESSAGES = {
    "formula_without_value": "This cell holds a formula with no saved result, so it has no value.",
    "error_value": "This cell holds a spreadsheet error, not a value.",
}


def _read_limits() -> ReadLimits:
    settings = get_extraction_config()
    ingest = get_ingest_config()
    return ReadLimits(
        max_columns=settings["max_columns"],
        max_sheets=settings["max_sheets_per_file"],
        max_merged_ranges=settings["max_merged_ranges"],
        csv_sniff_bytes=ingest["csv_sniff_bytes"],
        max_file_bytes=ingest["zip_max_entry_bytes"],
    )


def _download(file: Row, destination: Path) -> None:
    """Fetch a file that ingest already checked.

    Raises:
        TransientJobError: if storage can't be reached.
        PermanentJobError: if the stored object is no longer what ingest checked.
    """
    try:
        sha256 = get_object_store().download(file["s3_key"], destination, file["size_bytes"])
    except ObjectTooLargeError as exc:
        raise PermanentJobError(f"File {file['id']} changed after it was checked") from exc
    except StorageError as exc:
        raise TransientJobError(f"Download failed for file {file['id']}: {exc}") from exc
    if sha256 != file["sha256"]:
        raise PermanentJobError(f"File {file['id']} changed after it was checked")


def _sheet_id(file_id: UUID, sheet_index: int) -> UUID:
    """The same sheet always gets the same ID, so chunks can name it before it is stored."""
    return uuid.uuid5(file_id, f"sheet:{sheet_index}")


def _record_id(sheet_id: UUID, row_number: int) -> UUID:
    return uuid.uuid5(sheet_id, f"row:{row_number}")


def _identifier_columns(
    columns: Sequence[MappedColumn], identifier_attributes: Sequence[str]
) -> list[tuple[str, int]]:
    """Return (attribute, column index) for each identifier a sheet has, most preferred first.

    Where two columns map to the same identifier, the leftmost is used.
    """
    found = []
    for attribute in identifier_attributes:
        indexes = [column.index for column in columns if column.attribute == attribute]
        if indexes:
            found.append((attribute, min(indexes)))
    return found


def row_identifier(
    row: SheetRow, identifier_columns: Sequence[tuple[str, int]]
) -> tuple[str, Identifier] | None:
    """Return the attribute and cleaned identifier that say which product a row belongs to.

    The first identifier column with a usable value decides, so a row with no SKU can still be
    placed by its model number or barcode. Both extract jobs use this, so they always agree.
    """
    for attribute, index in identifier_columns:
        cell = row.cell(index)
        if not cell.usable:
            continue
        identifier = canonicalize(attribute, cell.text, cell.flags)
        if identifier is not None:
            return attribute, identifier
    return None


@dataclass
class _Selection:
    """Chooses the first products in file order, across every sheet of the batch."""

    max_products: int | None
    order: dict[str, int] = field(default_factory=dict)
    kept: dict[str, Row] = field(default_factory=dict)

    def see(self, attribute: str, identifier: Identifier, supplier_sku: str | None) -> bool:
        """Count a row's product, and return True if the product is kept."""
        key = identifier.key
        if key not in self.order:
            self.order[key] = len(self.order) + 1
            if self.max_products is None or len(self.kept) < self.max_products:
                self.kept[key] = {
                    "product_key": key,
                    "key_source": attribute,
                    "supplier_sku": supplier_sku,
                    "match_key": identifier.match_key,
                    "identifier_flags": list(identifier.flags),
                    "source_order": self.order[key],
                }
        return key in self.kept


@dataclass
class _SheetPlan:
    """What extract_select decided for one sheet. Stored as a spreadsheet_sheets row."""

    id: UUID
    source_file_id: UUID
    sheet_index: int
    sheet_name: str | None
    hidden: bool
    status: str = "used"
    skip_reason: str | None = None
    skip_message: str | None = None
    header_row: int | None = None
    header_rows: int | None = None
    columns: list[dict[str, Any]] = field(default_factory=list)
    mapping_source: str = "none"
    mapping_message: str | None = None
    row_count: int = 0
    rows_without_identifier: int = 0
    duplicate_rows: int = 0
    rows_over_cap: int = 0
    unreadable_rows: int = 0
    chunk_starts: list[int] = field(default_factory=list)
    chunk_rows: int = 0

    def skip(self, reason: str, detail: str | None = None) -> "_SheetPlan":
        """Mark the sheet as not used, with a message for the user."""
        self.status = "skipped"
        self.skip_reason = reason
        self.skip_message = _SKIP_MESSAGES[reason] + (f" ({detail})" if detail else "")
        self.chunk_starts = []
        return self

    def to_row(self) -> Row:
        """Return the plan as JSON for the insert."""
        return {**asdict(self), "id": str(self.id), "source_file_id": str(self.source_file_id)}


class ExtractSelectHandler:
    """Maps columns and chooses products for a whole batch, then plans the row chunks."""

    name = "extract_select"

    def run(self, context: JobContext) -> JobOutcome:
        """Plan the batch's extraction.

        Raises:
            TransientJobError: if storage or the database can't be reached.
            PermanentJobError: if config names an attribute that doesn't exist.
        """
        job = context.job
        batch_id = UUID(job.payload["batch_id"])
        settings = get_extraction_config()
        with get_pool().connection() as conn:
            batch: Row | None = conn.execute(
                query("extraction", "batch_context"), {"batch_id": batch_id}
            ).fetchone()
            files = conn.execute(
                query("extraction", "spreadsheet_files"), {"batch_id": batch_id}
            ).fetchall()
            attributes = [
                AttributeDef(**row)
                for row in conn.execute(query("extraction", "attributes")).fetchall()
            ]
        if batch is None:
            logger.info("Extraction skipped: batch_id=%s reason=batch_gone", batch_id)
            return JobOutcome()
        known = self._known_names(settings, attributes)
        limits = effective_limits(batch["overrides"])
        selection = _Selection(max_products=limits["max_products_per_submission"])
        mapper = _Mapper(job, batch, attributes, known, settings)
        plans: list[_SheetPlan] = []

        with tempfile.TemporaryDirectory(prefix="extract-") as workdir:
            for file in files:
                context.check_interrupted()
                local = Path(workdir) / f"{file['id']}.{file['extension']}"
                _download(file, local)
                plans.extend(self._plan_file(context, file, local, mapper, selection, settings))
                local.unlink(missing_ok=True)

        found, kept = len(selection.order), len(selection.kept)
        with get_pool().connection() as conn:
            queue.fence(conn, job)
            conn.execute(query("extraction", "clear_batch_products"), {"batch_id": batch_id})
            conn.execute(query("extraction", "clear_batch_sheets"), {"batch_id": batch_id})
            conn.execute(query("extraction", "clear_batch_rows"), {"batch_id": batch_id})
            if plans:
                conn.execute(
                    query("extraction", "insert_sheets"),
                    {"batch_id": batch_id, "sheets": Jsonb([plan.to_row() for plan in plans])},
                )
            if selection.kept:
                conn.execute(
                    query("extraction", "insert_products"),
                    {
                        "batch_id": batch_id,
                        "owner": batch["owner_user_id"],
                        "products": Jsonb(list(selection.kept.values())),
                    },
                )
            for fingerprint, mapping in mapper.to_save.items():
                conn.execute(
                    query("extraction", "save_mapping"),
                    {
                        "supplier_id": batch["supplier_id"],
                        "owner": batch["owner_user_id"],
                        "fingerprint": fingerprint,
                        "mapping": Jsonb(mapping),
                    },
                )
            conn.execute(
                query("extraction", "record_product_counts"),
                {
                    "run_id": job.pipeline_run_id,
                    "found": found,
                    "kept": kept,
                    "discarded": found - kept,
                },
            )
        logger.info(
            "Extraction planned: batch_id=%s files=%d sheets=%d products_found=%d "
            "products_kept=%d llm_calls=%d",
            batch_id,
            len(files),
            len(plans),
            found,
            kept,
            mapper.llm_calls,
        )
        return JobOutcome(
            chunks=tuple(
                ChunkSpec(
                    key=f"rows-{plan.id}-{start}",
                    job_type="extract_rows",
                    payload={
                        "batch_id": str(batch_id),
                        "sheet_id": str(plan.id),
                        "start_row": start,
                        "end_row": start + plan.chunk_rows,
                    },
                )
                for plan in plans
                if plan.status == "used"
                for start in plan.chunk_starts
            )
        )

    def _known_names(
        self, settings: dict[str, Any], attributes: Sequence[AttributeDef]
    ) -> dict[str, str]:
        codes = {attribute.code for attribute in attributes}
        unknown = sorted(
            (set(settings["header_names"]) | set(settings["identifier_attributes"])) - codes
        )
        if unknown:
            raise PermanentJobError(
                f"config.yaml names attributes that don't exist: {', '.join(unknown)}"
            )
        return build_known_names(settings["header_names"])

    def _plan_file(
        self,
        context: JobContext,
        file: Row,
        local: Path,
        mapper: "_Mapper",
        selection: _Selection,
        settings: dict[str, Any],
    ) -> list[_SheetPlan]:
        """Plan every sheet of one file. A file that can't be opened becomes one skipped sheet."""
        reader = get_reader(file["extension"])
        plans: list[_SheetPlan] = []
        try:
            with reader.open(local, _read_limits()) as workbook:
                for sheet in workbook.sheets:
                    context.check_interrupted()
                    plan = _SheetPlan(
                        id=_sheet_id(file["id"], sheet.index),
                        source_file_id=file["id"],
                        sheet_index=sheet.index,
                        sheet_name=sheet.name,
                        hidden=sheet.hidden,
                        chunk_rows=settings["chunk_rows"],
                    )
                    plans.append(plan)
                    try:
                        self._plan_sheet(context, sheet, plan, mapper, selection, settings)
                    except SpreadsheetUnreadableError as exc:
                        logger.warning(
                            "Sheet unreadable: file_id=%s sheet_index=%d error=%s",
                            file["id"],
                            sheet.index,
                            exc,
                        )
                        plan.skip(SKIP_UNREADABLE)
        except SpreadsheetUnreadableError as exc:
            logger.warning("Spreadsheet unreadable: file_id=%s error=%s", file["id"], exc)
            if not plans:
                plans.append(
                    _SheetPlan(
                        id=_sheet_id(file["id"], 0),
                        source_file_id=file["id"],
                        sheet_index=0,
                        sheet_name=None,
                        hidden=False,
                    ).skip(SKIP_UNREADABLE)
                )
        return plans

    def _plan_sheet(
        self,
        context: JobContext,
        sheet: Sheet,
        plan: _SheetPlan,
        mapper: "_Mapper",
        selection: _Selection,
        settings: dict[str, Any],
    ) -> None:
        # Enough rows for the header search and the samples shown to the LLM, allowing for
        # blank rows between them.
        top_count = settings["header_scan_rows"] + settings["sample_rows"] * 3
        top_rows = list(islice(sheet.rows(), top_count))
        if all(row.empty for row in top_rows):
            plan.skip(SKIP_EMPTY)
            return

        mapping = mapper.map(top_rows, merged=sheet.name is not None)
        plan.mapping_source = mapping.source
        plan.mapping_message = mapping.message
        plan.columns = [asdict(column) for column in mapping.columns]
        if mapping.header_row is None:
            plan.skip(SKIP_NO_HEADER)
            return
        plan.header_row = mapping.header_row
        plan.header_rows = mapping.header_rows
        identifier_columns = _identifier_columns(mapping.columns, settings["identifier_attributes"])
        if not identifier_columns:
            plan.skip(SKIP_NO_IDENTIFIER)
            return

        sku_index = next((index for name, index in identifier_columns if name == "sku"), None)
        last_row = mapping.header_row + settings["max_rows_per_sheet"]
        chunk_starts: set[int] = set()
        for row in sheet.rows():
            if row.number <= mapping.header_row:
                continue
            if row.number % _INTERRUPT_CHECK_ROWS == 0:
                context.check_interrupted()
            if row.error is not None:
                plan.unreadable_rows += 1
                continue
            if row.empty:
                continue
            if row.number > last_row:
                plan.rows_over_cap += 1
                continue
            plan.row_count += 1
            found = row_identifier(row, identifier_columns)
            if found is None:
                plan.rows_without_identifier += 1
                continue
            attribute, identifier = found
            if identifier.key in selection.order:
                plan.duplicate_rows += 1
            sku_cell = row.cell(sku_index) if sku_index is not None else None
            supplier_sku = sku_cell.text if sku_cell is not None and sku_cell.usable else None
            if selection.see(attribute, identifier, supplier_sku):
                offset = (row.number - mapping.header_row - 1) // plan.chunk_rows
                chunk_starts.add(mapping.header_row + 1 + offset * plan.chunk_rows)
        plan.chunk_starts = sorted(chunk_starts)


class _Mapper:
    """Maps sheets for one run: holds the LLM budget and the mappings to save afterwards."""

    def __init__(
        self,
        job: Job,
        batch: Row,
        attributes: Sequence[AttributeDef],
        known: dict[str, str],
        settings: dict[str, Any],
    ) -> None:
        self._job = job
        self._batch = batch
        self._attributes = attributes
        self._known = known
        self._settings = settings
        self.llm_calls = 0
        self.to_save: dict[str, dict[str, Any]] = {}

    def map(self, top_rows: Sequence[SheetRow], *, merged: bool) -> SheetMapping:
        """Map one sheet, asking the LLM only while the run has calls left."""
        has_budget = self.llm_calls < self._settings["max_llm_calls_per_run"]
        mapping = map_sheet(
            top_rows,
            merged=merged,
            attributes=self._attributes,
            known=self._known,
            settings=self._settings,
            find_saved=self._find_saved,
            ask_llm=self._ask if has_budget else None,
        )
        self.llm_calls += mapping.llm_calls
        if mapping.save_as is not None:
            self.to_save[mapping.save_as] = mapping.to_saved()
        return mapping

    def _find_saved(self, fingerprint: str) -> dict[str, Any] | None:
        # A layout mapped earlier in this run is reused before it has been stored.
        if fingerprint in self.to_save:
            return self.to_save[fingerprint]
        with get_pool().connection() as conn:
            row = conn.execute(
                query("extraction", "find_saved_mapping"),
                {"supplier_id": self._batch["supplier_id"], "fingerprint": fingerprint},
            ).fetchone()
        return dict(row["mapping"]) if row else None

    def _ask(self, variables: dict[str, Any], parser: Any) -> LlmAnswer:
        try:
            result = run_task(
                MAPPING_TASK,
                owner=self._job.owner_user_id,
                job_id=self._job.id,
                variables=variables,
                parse=parser,
                to_record=MappingAnswer.model_dump,
            )
        except LlmTaskError as exc:
            budget = isinstance(exc, LlmBudgetError)
            raise LlmMappingError(str(exc), exc.calls, budget=budget) from exc
        return LlmAnswer(answer=result.value, model_run_id=result.model_run_id, calls=result.calls)


@dataclass
class _Chunk:
    """What one extract_rows job collected, written in one transaction."""

    max_notes: int
    rows: list[Row] = field(default_factory=list)
    observations: list[Row] = field(default_factory=list)
    notes: list[Row] = field(default_factory=list)
    notes_dropped: int = 0

    def note(self, code: str, message: str, location: Row) -> None:
        """Report a row or cell that wasn't used, up to the limit."""
        if len(self.notes) < self.max_notes:
            self.notes.append({"location": location, "error_code": code, "message": message})
        else:
            self.notes_dropped += 1


class ExtractRowsHandler:
    """Stores one block of a sheet's rows, for kept products only."""

    name = "extract_rows"

    def run(self, context: JobContext) -> JobOutcome:
        """Read the block and store its records and observations.

        Raises:
            TransientJobError: if storage or the database can't be reached.
            PermanentJobError: if the file can no longer be read as it was when planned.
        """
        job = context.job
        payload = job.payload
        settings = get_extraction_config()
        with get_pool().connection() as conn:
            sheet: Row | None = conn.execute(
                query("extraction", "sheet_for_rows"),
                {"sheet_id": payload["sheet_id"], "batch_id": payload["batch_id"]},
            ).fetchone()
            products = {
                row["product_key"]: row["id"]
                for row in conn.execute(
                    query("extraction", "batch_product_keys"), {"batch_id": payload["batch_id"]}
                ).fetchall()
            }
            attribute_ids = {
                row["code"]: row["id"]
                for row in conn.execute(query("extraction", "attributes")).fetchall()
            }
        if sheet is None or sheet["status"] != "used" or not products:
            logger.info("Rows skipped: sheet_id=%s reason=nothing_to_extract", payload["sheet_id"])
            return JobOutcome()

        chunk = _Chunk(max_notes=settings["max_row_notes_per_chunk"])
        with tempfile.TemporaryDirectory(prefix="extract-") as workdir:
            local = Path(workdir) / f"source.{sheet['extension']}"
            _download({**sheet, "id": sheet["file_id"]}, local)
            try:
                for row in self._block(local, sheet, payload["start_row"], payload["end_row"]):
                    if row.number % _INTERRUPT_CHECK_ROWS == 0:
                        context.check_interrupted()
                    self._read_row(row, sheet, products, attribute_ids, settings, chunk)
            except SpreadsheetUnreadableError as exc:
                raise PermanentJobError(
                    f"Sheet {sheet['id']} could be read when planned, but not now: {exc}"
                ) from exc

        self._store(job, sheet, settings, chunk)
        logger.info(
            "Rows extracted: sheet_id=%s start_row=%d rows=%d observations=%d notes=%d",
            sheet["id"],
            payload["start_row"],
            len(chunk.rows),
            len(chunk.observations),
            len(chunk.notes) + chunk.notes_dropped,
        )
        return JobOutcome()

    def _block(self, local: Path, sheet: Row, start_row: int, end_row: int) -> Iterator[SheetRow]:
        """Yield the sheet's rows from start_row up to, not including, end_row."""
        with get_reader(sheet["extension"]).open(local, _read_limits()) as workbook:
            match = [item for item in workbook.sheets if item.index == sheet["sheet_index"]]
            if not match:
                raise SpreadsheetUnreadableError("The sheet is no longer in the file")
            for row in match[0].rows():
                if row.number >= end_row:
                    return
                if row.number >= start_row:
                    yield row

    def _read_row(
        self,
        row: SheetRow,
        sheet: Row,
        products: dict[str, UUID],
        attribute_ids: dict[str, UUID],
        settings: dict[str, Any],
        chunk: _Chunk,
    ) -> None:
        """Collect one row's record and observations. A row that can't be used is reported."""
        location = {
            "sheet": sheet["sheet_name"],
            "sheet_index": sheet["sheet_index"],
            "row": row.number,
        }
        if row.error is not None:
            chunk.note(NOTE_ROW_UNREADABLE, f"This row couldn't be read: {row.error}", location)
            return
        if row.empty:
            return
        columns = [MappedColumn(**column) for column in sheet["columns"]]
        try:
            found = row_identifier(
                row, _identifier_columns(columns, settings["identifier_attributes"])
            )
            product_id = products.get(found[1].key) if found else None
            if product_id is None:
                # No identifier, or a product past the limit: counted by extract_select.
                return
            raw_data = self._raw_data(row)
            if len(json.dumps(raw_data, ensure_ascii=False)) > settings["max_row_bytes"]:
                chunk.note(
                    NOTE_ROW_TOO_LARGE,
                    "This row holds too much text to store, so it wasn't used.",
                    location,
                )
                return
            record_id = str(_record_id(sheet["id"], row.number))
            observations, notes = [], []
            for column in columns:
                cell = row.cell(column.index)
                if column.attribute is None or cell.empty:
                    continue
                cell_location = {**location, "column": column.letter, "header": column.header}
                problem = next((flag for flag in cell.flags if flag in UNUSABLE_FLAGS), None)
                if problem is not None:
                    notes.append((problem, _CELL_NOTE_MESSAGES[problem], cell_location))
                    continue
                if cell.merged_from is not None:
                    cell_location["merged_from"] = cell.merged_from
                observations.append(
                    {
                        "product_id": str(product_id),
                        "attribute_id": str(attribute_ids[column.attribute]),
                        "raw_value": cell.text,
                        "source_record_id": record_id,
                        "location": cell_location,
                        "model_run_id": column.model_run_id,
                        "confidence": column.confidence,
                    }
                )
        except Exception as exc:  # noqa: BLE001 - one bad row must not fail its chunk
            logger.warning(
                "Row skipped: sheet_id=%s row=%d error_class=%s",
                sheet["id"],
                row.number,
                type(exc).__name__,
            )
            chunk.note(
                NOTE_ROW_UNREADABLE,
                f"This row couldn't be processed ({type(exc).__name__}).",
                location,
            )
            return
        chunk.rows.append({"id": record_id, "location": location, "raw_data": raw_data})
        chunk.observations.extend(observations)
        for code, message, cell_location in notes:
            chunk.note(code, message, cell_location)

    def _raw_data(self, row: SheetRow) -> Row:
        """Return every non-empty cell of the row by column letter, mapped or not."""
        cells: dict[str, str] = {}
        formats: dict[str, str] = {}
        flags: dict[str, list[str]] = {}
        merged: dict[str, str] = {}
        for index, cell in enumerate(row.cells):
            letter = column_letter(index)
            if cell.flags:
                flags[letter] = list(cell.flags)
            if cell.empty:
                continue
            cells[letter] = cell.text
            if cell.number_format:
                formats[letter] = cell.number_format
            if cell.merged_from:
                merged[letter] = cell.merged_from
        raw: Row = {"cells": cells}
        for name, value in (("formats", formats), ("flags", flags), ("merged_from", merged)):
            if value:
                raw[name] = value
        return raw

    def _store(self, job: Job, sheet: Row, settings: dict[str, Any], chunk: _Chunk) -> None:
        notes = list(chunk.notes)
        if chunk.notes_dropped:
            notes.append(
                {
                    "location": {
                        "sheet": sheet["sheet_name"],
                        "sheet_index": sheet["sheet_index"],
                    },
                    "error_code": NOTE_MORE,
                    "message": f"{chunk.notes_dropped} more rows or cells in this part of the "
                    "sheet had problems.",
                }
            )
        with get_pool().connection() as conn:
            queue.fence(conn, job)
            conn.execute(query("extraction", "clear_job_notes"), {"job_id": job.id})
            if chunk.rows:
                conn.execute(
                    query("extraction", "insert_rows"),
                    {"file_id": sheet["file_id"], "rows": Jsonb(chunk.rows)},
                )
            if chunk.observations:
                conn.execute(
                    query("extraction", "insert_observations"),
                    {
                        "file_id": sheet["file_id"],
                        "extractor": settings["extractor"],
                        "observations": Jsonb(chunk.observations),
                    },
                )
            if notes:
                conn.execute(
                    query("extraction", "insert_notes"),
                    {
                        "run_id": job.pipeline_run_id,
                        "job_id": job.id,
                        "file_id": sheet["file_id"],
                        "notes": Jsonb(notes),
                    },
                )


register_handler(ExtractSelectHandler())
register_handler(ExtractRowsHandler())
