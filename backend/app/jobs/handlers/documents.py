"""Document jobs: read the text of every PDF and photo, then the product values it states.

documents_plan  payload {"batch_id"}: one documents_read chunk per kept PDF or image.
documents_read  payload {"batch_id", "file_id"}: store each page's text with positions. PDF
    pages with their own text are read directly; scanned pages and photos go through OCR.
document_values payload {"batch_id"}: in file order, ask the LLM for each page's values, keep
    only what is found word for word on the page, and store it against a product.

Which products values go to depends on the batch. With products from spreadsheets, a page is
only read if it names one of them, so discarded products cost no LLM calls (D13). Without
spreadsheet products, codes found on pages make products, first N in file order.

Every job can run again safely: reading replaces a file's pages, and values are cleared and
read afresh. A page or value that can't be used is reported, and the job carries on.
"""

import tempfile
import time
import uuid
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb

from app.config import get_documents_config, get_extraction_config
from app.db import get_pool, query
from app.ingest.documents.pages import METHOD_OCR, METHOD_TEXT, Line, Page
from app.ingest.documents.pdf import PdfUnreadableError, ocr_page, open_pdf, text_page
from app.ingest.documents.values import (
    MODE_CREATE,
    MODE_LINK,
    DocumentAnswer,
    KeptValue,
    PageResult,
    check_create,
    check_link,
    occurrences,
    parse_answer,
)
from app.jobs import queue
from app.jobs.base import JobContext, register_handler
from app.jobs.errors import PermanentJobError, TransientJobError
from app.jobs.models import ChunkSpec, Job, JobOutcome
from app.llm import LlmBudgetError, LlmTaskError, run_task
from app.ocr import get_ocr_engine
from app.storage import ObjectTooLargeError, StorageError, get_object_store
from app.usage import effective_limits
from app.utils.logger import get_logger

logger = get_logger(__name__)

Row = dict[str, Any]

VALUES_TASK = "document_attributes"

SKIP_UNREADABLE = "unreadable"
SKIP_NO_TEXT = "no_text"
_SKIP_MESSAGES = {
    SKIP_UNREADABLE: "This file couldn't be read.",
    SKIP_NO_TEXT: "No text was found in this file.",
}

NOTE_PAGE_UNREADABLE = "page_unreadable"
NOTE_PAGE_CUT = "page_text_cut"
NOTE_MORE = "more_value_problems"

LLM_NONE = "none"
LLM_USED = "llm"
LLM_FAILED = "llm_failed"
LLM_CAPPED = "llm_capped"
LLM_BUDGET = "llm_budget"
_LLM_MESSAGES = {
    LLM_FAILED: "The LLM couldn't be used for some of this file, so values weren't read there.",
    LLM_CAPPED: "This run had already used its LLM calls, so some of this file wasn't read.",
    LLM_BUDGET: "This month's LLM budget has been used, so values weren't read from this file.",
}
# A file's LLM state only gets worse as its pages are read.
_LLM_RANK = {LLM_NONE: 0, LLM_USED: 1, LLM_FAILED: 2, LLM_CAPPED: 3, LLM_BUDGET: 4}


def _page_id(file_id: UUID, number: int | None) -> UUID:
    """The same page always gets the same ID."""
    return uuid.uuid5(file_id, f"page:{number or 0}")


def _round_box(box: tuple[float, ...] | None) -> list[float] | None:
    return [round(value, 1) for value in box] if box else None


def page_to_raw(page: Page) -> Row:
    """Serialise a page for source_records.raw_data."""
    return {
        "method": page.method,
        "width": round(page.width, 1),
        "height": round(page.height, 1),
        "confidence": round(page.confidence, 3) if page.confidence is not None else None,
        "lines": [[line.text, _round_box(line.box)] for line in page.lines],
    }


def page_from_raw(number: int | None, raw: Row) -> Page:
    """Rebuild a page stored by page_to_raw."""
    return Page(
        number=number,
        method=raw["method"],
        lines=[Line(text=text, box=tuple(box)) for text, box in raw["lines"]],
        width=float(raw["width"]),
        height=float(raw["height"]),
        confidence=raw.get("confidence"),
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


class DocumentsPlanHandler:
    """Creates one read job per kept PDF and image."""

    name = "documents_plan"

    def run(self, context: JobContext) -> JobOutcome:
        """Clear the batch's document summaries and plan a read per file."""
        job = context.job
        batch_id = job.payload["batch_id"]
        with get_pool().connection() as conn:
            queue.fence(conn, job)
            conn.execute(query("documents", "clear_batch_documents"), {"batch_id": batch_id})
            files = conn.execute(
                query("documents", "files_to_read"), {"batch_id": batch_id}
            ).fetchall()
        logger.info("Documents planned: batch_id=%s files=%d", batch_id, len(files))
        return JobOutcome(
            chunks=tuple(
                ChunkSpec(
                    key=f"read-{file['id']}",
                    job_type="documents_read",
                    payload={"batch_id": batch_id, "file_id": str(file["id"])},
                )
                for file in files
            )
        )


@dataclass
class _ReadSummary:
    pages: list[Page] = field(default_factory=list)
    ocr: list[Row] = field(default_factory=list)
    unreadable_pages: list[int] = field(default_factory=list)
    skip_reason: str | None = None


class DocumentsReadHandler:
    """Reads one PDF or photo into pages of text with positions."""

    name = "documents_read"

    def run(self, context: JobContext) -> JobOutcome:
        """Read the file and replace its stored pages.

        Raises:
            TransientJobError: if storage or the database can't be reached.
        """
        job = context.job
        settings = get_documents_config()
        with get_pool().connection() as conn:
            file: Row | None = conn.execute(
                query("documents", "get_file"),
                {"file_id": job.payload["file_id"], "batch_id": job.payload["batch_id"]},
            ).fetchone()
        if file is None or file["status"] != "ingested":
            logger.info("Read skipped: file_id=%s", job.payload["file_id"])
            return JobOutcome()

        with tempfile.TemporaryDirectory(prefix="documents-") as workdir:
            local = Path(workdir) / f"source.{file['extension']}"
            _download(file, local)
            if file["kind"] == "pdf":
                summary = self._read_pdf(context, local, settings)
            else:
                summary = self._read_image(local, settings)
        self._store(job, file, summary)
        return JobOutcome()

    def _ocr_record(
        self,
        page: int | None,
        engine: Any,
        text: str,
        lines: int,
        confidence: float | None,
        started: float,
    ) -> Row:
        return {
            "page": page,
            "engine": engine.name,
            "engine_version": engine.version,
            "text": text,
            "line_count": lines,
            "confidence": round(confidence, 3) if confidence is not None else None,
            "duration_ms": int((time.monotonic() - started) * 1000),
        }

    def _read_pdf(self, context: JobContext, local: Path, settings: Row) -> _ReadSummary:
        summary = _ReadSummary()
        engine = get_ocr_engine(settings["ocr_engine"])
        try:
            with open_pdf(local) as pdf:
                for number, plumber_page in enumerate(pdf.pages, start=1):
                    context.check_interrupted()
                    try:
                        page = text_page(plumber_page, number)
                        if len(page.text.strip()) < settings["min_page_chars"]:
                            # Too little of its own text: most likely scanned, so read the pixels.
                            started = time.monotonic()
                            for dpi in (settings["render_dpi"], settings["render_dpi"] // 2):
                                # A nearly empty page at full resolution can defeat the text
                                # detector, so an empty first reading is tried once smaller.
                                scanned, confidence = ocr_page(
                                    local, number, engine, dpi, settings["ocr_min_confidence"]
                                )
                                if scanned.text.strip():
                                    break
                            summary.ocr.append(
                                self._ocr_record(
                                    number,
                                    engine,
                                    scanned.text,
                                    len(scanned.lines),
                                    confidence,
                                    started,
                                )
                            )
                            # A short page of real text stays as it is unless OCR found more.
                            if len(scanned.text.strip()) > len(page.text.strip()):
                                page = scanned
                    except PdfUnreadableError as exc:
                        logger.warning("PDF page unreadable: page=%d error=%s", number, exc)
                        summary.unreadable_pages.append(number)
                        continue
                    summary.pages.append(page)
        except PdfUnreadableError as exc:
            logger.warning("PDF unreadable: error=%s", exc)
            summary.skip_reason = SKIP_UNREADABLE
            return summary
        if not any(page.text.strip() for page in summary.pages) and not summary.unreadable_pages:
            summary.skip_reason = SKIP_NO_TEXT
        return summary

    def _read_image(self, local: Path, settings: Row) -> _ReadSummary:
        from PIL import Image, ImageOps, UnidentifiedImageError

        summary = _ReadSummary()
        engine = get_ocr_engine(settings["ocr_engine"])
        try:
            with Image.open(local) as opened:
                # Phones store photos sideways and record the turn; apply it before reading.
                image = ImageOps.exif_transpose(opened)
                image.load()
        except (UnidentifiedImageError, OSError) as exc:
            logger.warning("Image unreadable: error=%s", exc)
            summary.skip_reason = SKIP_UNREADABLE
            return summary
        started = time.monotonic()
        result = engine.read(image, settings["ocr_min_confidence"])
        confidence = (
            sum(line.confidence for line in result.lines) / len(result.lines)
            if result.lines
            else None
        )
        summary.ocr.append(
            self._ocr_record(None, engine, result.text, len(result.lines), confidence, started)
        )
        page = Page(
            number=None,
            method=METHOD_OCR,
            lines=[Line(text=line.text, box=line.box) for line in result.lines],
            width=float(result.width),
            height=float(result.height),
            confidence=confidence,
        )
        summary.pages.append(page)
        if not page.text.strip():
            summary.skip_reason = SKIP_NO_TEXT
        return summary

    def _store(self, job: Job, file: Row, summary: _ReadSummary) -> None:
        pages = [
            {
                "id": str(_page_id(file["id"], page.number)),
                "location": {"page": page.number},
                "raw_data": page_to_raw(page),
            }
            for page in summary.pages
            if page.text.strip()
        ]
        notes = [
            {
                "source_file_id": str(file["id"]),
                "location": {"page": number},
                "error_code": NOTE_PAGE_UNREADABLE,
                "message": "This page couldn't be read.",
            }
            for number in summary.unreadable_pages
        ]
        with get_pool().connection() as conn:
            queue.fence(conn, job)
            conn.execute(query("documents", "clear_file_pages"), {"file_id": file["id"]})
            conn.execute(query("documents", "clear_file_ocr"), {"file_id": file["id"]})
            conn.execute(query("documents", "clear_job_notes"), {"job_id": job.id})
            if pages:
                conn.execute(
                    query("documents", "insert_pages"),
                    {"file_id": file["id"], "pages": Jsonb(pages)},
                )
            if summary.ocr:
                conn.execute(
                    query("documents", "insert_ocr"),
                    {"file_id": file["id"], "results": Jsonb(summary.ocr)},
                )
            if notes:
                conn.execute(
                    query("documents", "insert_notes"),
                    {"run_id": job.pipeline_run_id, "job_id": job.id, "notes": Jsonb(notes)},
                )
            skipped = summary.skip_reason is not None
            conn.execute(
                query("documents", "upsert_document"),
                {
                    "file_id": file["id"],
                    "batch_id": file["batch_id"],
                    "kind": file["kind"],
                    "status": "skipped" if skipped else "read",
                    "skip_reason": summary.skip_reason,
                    "skip_message": _SKIP_MESSAGES.get(summary.skip_reason or ""),
                    "pages": len(summary.pages) + len(summary.unreadable_pages),
                    "pages_with_text": len(pages),
                    "pages_ocr": sum(1 for page in summary.pages if page.method == METHOD_OCR),
                    "pages_unreadable": len(summary.unreadable_pages),
                    "characters": sum(len(page.text) for page in summary.pages),
                },
            )
        logger.info(
            "Document read: file_id=%s kind=%s pages=%d ocr_pages=%d unreadable=%d skipped=%s",
            file["id"],
            file["kind"],
            len(summary.pages),
            len(summary.ocr),
            len(summary.unreadable_pages),
            summary.skip_reason,
        )


@dataclass
class _FileTally:
    product_keys: list[str] = field(default_factory=list)
    pages_without_product: int = 0
    values_kept: int = 0
    values_dropped: int = 0
    llm_source: str = LLM_NONE

    def worsen(self, state: str) -> None:
        """Record an LLM outcome, keeping the worst one seen in this file."""
        if _LLM_RANK[state] > _LLM_RANK[self.llm_source]:
            self.llm_source = state


@dataclass
class _ValuesRun:
    """Everything one document_values job collects before writing it in one transaction."""

    mode: str
    max_notes: int
    files: dict[UUID, _FileTally] = field(default_factory=lambda: defaultdict(_FileTally))
    observations: list[Row] = field(default_factory=list)
    notes: list[Row] = field(default_factory=list)
    notes_dropped: int = 0
    new_products: dict[str, Row] = field(default_factory=dict)
    products_seen: list[str] = field(default_factory=list)
    llm_calls: int = 0
    budget_reached: bool = False

    def note(self, file_id: UUID, location: Row, code: str, message: str) -> None:
        """Report a page or value that wasn't used, up to the limit."""
        if len(self.notes) < self.max_notes:
            self.notes.append(
                {
                    "source_file_id": str(file_id),
                    "location": location,
                    "error_code": code,
                    "message": message,
                }
            )
        else:
            self.notes_dropped += 1


class DocumentValuesHandler:
    """Reads values from every stored page, in file order, and stores what checks out."""

    name = "document_values"

    def run(self, context: JobContext) -> JobOutcome:
        """Read the batch's values.

        Raises:
            TransientJobError: if the database can't be reached.
        """
        job = context.job
        batch_id = UUID(job.payload["batch_id"])
        settings = get_documents_config()
        extraction = get_extraction_config()
        identifier_attributes = extraction["identifier_attributes"]
        with get_pool().connection() as conn:
            linked = conn.execute(
                query("documents", "has_spreadsheet_products"), {"batch_id": batch_id}
            ).fetchone()
            mode = MODE_LINK if linked and linked["linked"] else MODE_CREATE
            products = (
                conn.execute(
                    query("documents", "products_with_codes"),
                    {"batch_id": batch_id, "codes": identifier_attributes},
                ).fetchall()
                if mode == MODE_LINK
                else []
            )
            pages = conn.execute(
                query("documents", "pages_in_order"), {"batch_id": batch_id}
            ).fetchall()
            attributes = conn.execute(query("extraction", "attributes")).fetchall()
            batch = conn.execute(
                query("extraction", "batch_context"), {"batch_id": batch_id}
            ).fetchone()
        if batch is None:
            return JobOutcome()

        run = _ValuesRun(mode=mode, max_notes=settings["max_notes_per_run"])
        attribute_ids = {row["code"]: row["id"] for row in attributes}
        prompt_attributes = [
            {"code": row["code"], "data_type": row["data_type"], "description": row["description"]}
            for row in attributes
        ]
        product_codes = {
            str(row["id"]): sorted({row["product_key"], *row["codes"]}) for row in products
        }
        product_names = {str(row["id"]): row["product_key"] for row in products}
        for record in pages:
            context.check_interrupted()
            self._read_page(
                job,
                record,
                run,
                settings,
                identifier_attributes,
                prompt_attributes,
                set(attribute_ids),
                product_codes,
                product_names,
            )

        max_products = effective_limits(batch["overrides"])["max_products_per_submission"]
        self._store(job, batch, run, attribute_ids, settings, max_products)
        logger.info(
            "Document values read: batch_id=%s mode=%s pages=%d llm_calls=%d values=%d notes=%d",
            batch_id,
            mode,
            len(pages),
            run.llm_calls,
            len(run.observations),
            len(run.notes) + run.notes_dropped,
        )
        return JobOutcome()

    def _read_page(
        self,
        job: Job,
        record: Row,
        run: _ValuesRun,
        settings: Row,
        identifier_attributes: list[str],
        prompt_attributes: list[Row],
        allowed: set[str],
        product_codes: dict[str, list[str]],
        product_names: dict[str, str],
    ) -> None:
        number = record["location"].get("page")
        page = page_from_raw(number, record["raw_data"])
        file_id = record["file_id"]
        tally = run.files[file_id]
        text = page.text
        location = {"page": number}

        on_page: dict[str, list[str]] = {}
        if run.mode == MODE_LINK:
            on_page = {
                product_id: codes
                for product_id, codes in product_codes.items()
                if occurrences(text, codes, settings["min_key_chars_for_loose_match"])
            }
            if not on_page:
                # No kept product named here, so no LLM call: discarded products cost nothing.
                tally.pages_without_product += 1
                return
        if run.budget_reached:
            tally.worsen(LLM_BUDGET)
            return
        if run.llm_calls >= settings["max_llm_calls_per_run"]:
            tally.worsen(LLM_CAPPED)
            return

        truncated = len(text) > settings["max_llm_chars"]
        if truncated:
            run.note(
                file_id,
                location,
                NOTE_PAGE_CUT,
                f"Only the first {settings['max_llm_chars']} characters of this page were read "
                "for values.",
            )
        source = (
            f"Page {number} of the PDF {record['original_name']}"
            if number is not None
            else f"The photo {record['original_name']}, read by OCR"
        )
        variables = {
            "attributes": prompt_attributes,
            "mode": run.mode,
            "products": sorted(product_names[product_id] for product_id in on_page),
            "source": source,
            "truncated": truncated,
            "text": text[: settings["max_llm_chars"]],
        }
        try:
            result = run_task(
                VALUES_TASK,
                owner=job.owner_user_id,
                job_id=job.id,
                variables=variables,
                parse=parse_answer,
                to_record=DocumentAnswer.model_dump,
            )
        except LlmBudgetError:
            run.budget_reached = True
            tally.worsen(LLM_BUDGET)
            return
        except LlmTaskError as exc:
            run.llm_calls += exc.calls
            tally.worsen(LLM_FAILED)
            logger.warning(
                "Page values not read: file_id=%s page=%s error=%s", file_id, number, exc
            )
            return
        run.llm_calls += result.calls
        tally.worsen(LLM_USED)

        if run.mode == MODE_LINK:
            checked = check_link(
                page,
                result.value,
                on_page,
                allowed,
                settings["min_key_chars_for_loose_match"],
            )
            for product_id in on_page:
                if product_names[product_id] not in tally.product_keys:
                    tally.product_keys.append(product_names[product_id])
        else:
            checked = check_create(
                page,
                result.value,
                allowed,
                identifier_attributes,
                settings["min_key_chars_for_loose_match"],
            )
            if not checked.new_products:
                tally.pages_without_product += 1
            for new in checked.new_products:
                key = new.identifier.key
                if key not in run.products_seen:
                    run.products_seen.append(key)
                    run.new_products[key] = {
                        "product_key": key,
                        "key_source": new.key_source,
                        "supplier_sku": new.supplier_sku,
                        "match_key": new.identifier.match_key,
                        "identifier_flags": list(new.identifier.flags),
                    }
                if key not in tally.product_keys:
                    tally.product_keys.append(key)
        self._collect(record, page, checked, run, result.model_run_id, tally, settings)

    def _collect(
        self,
        record: Row,
        page: Page,
        checked: PageResult,
        run: _ValuesRun,
        model_run_id: UUID,
        tally: _FileTally,
        settings: Row,
    ) -> None:
        extractor = (
            settings["extractor_text"] if page.method == METHOD_TEXT else settings["extractor_ocr"]
        )
        for dropped in checked.dropped:
            tally.values_dropped += 1
            run.note(record["file_id"], {"page": page.number}, dropped.code, dropped.message)
        for kept in checked.kept:
            tally.values_kept += 1
            run.observations.append(self._observation(record, page, kept, model_run_id, extractor))

    def _observation(
        self, record: Row, page: Page, kept: KeptValue, model_run_id: UUID, extractor: str
    ) -> Row:
        found = kept.found
        return {
            # Resolved to an ID when stored: a product key in create mode.
            "product": kept.product,
            "attribute": kept.attribute,
            "raw_value": found.raw_value,
            "source_file_id": str(record["file_id"]),
            "source_record_id": str(record["id"]),
            "location": {
                "page": page.number,
                "method": page.method,
                "quote": found.quote,
                "bbox": _round_box(found.box),
                "page_width": round(page.width, 1),
                "page_height": round(page.height, 1),
            },
            "extractor": extractor,
            "model_run_id": str(model_run_id),
            "confidence": round(page.confidence, 3) if page.confidence is not None else None,
        }

    def _store(
        self,
        job: Job,
        batch: Row,
        run: _ValuesRun,
        attribute_ids: dict[str, UUID],
        settings: Row,
        max_products: int | None,
    ) -> None:
        kept_keys = run.products_seen if max_products is None else run.products_seen[:max_products]
        notes = list(run.notes)
        if run.notes_dropped:
            notes.append(
                {
                    "source_file_id": None,
                    "location": None,
                    "error_code": NOTE_MORE,
                    "message": f"{run.notes_dropped} more values or pages had problems.",
                }
            )
        files = [
            {
                "source_file_id": str(file_id),
                **{k: v for k, v in asdict(tally).items() if k != "product_keys"},
                "product_keys": [
                    key for key in tally.product_keys if run.mode == MODE_LINK or key in kept_keys
                ],
                "llm_message": _LLM_MESSAGES.get(tally.llm_source),
            }
            for file_id, tally in run.files.items()
        ]
        with get_pool().connection() as conn:
            queue.fence(conn, job)
            conn.execute(query("documents", "clear_job_notes"), {"job_id": job.id})
            conn.execute(
                query("documents", "clear_document_values"),
                {
                    "batch_id": batch["id"],
                    "extractors": [settings["extractor_text"], settings["extractor_ocr"]],
                },
            )
            product_ids: dict[str, str] = {}
            if run.mode == MODE_CREATE:
                conn.execute(
                    query("documents", "clear_document_products"), {"batch_id": batch["id"]}
                )
                rows = [
                    {**run.new_products[key], "source_order": order}
                    for order, key in enumerate(kept_keys, start=1)
                ]
                if rows:
                    inserted = conn.execute(
                        query("documents", "insert_products"),
                        {
                            "batch_id": batch["id"],
                            "owner": batch["owner_user_id"],
                            "products": Jsonb(rows),
                        },
                    ).fetchall()
                    product_ids = {row["product_key"]: str(row["id"]) for row in inserted}
                found = len(run.products_seen)
                conn.execute(
                    query("documents", "record_product_counts"),
                    {
                        "run_id": job.pipeline_run_id,
                        "found": found,
                        "kept": len(kept_keys),
                        "discarded": found - len(kept_keys),
                    },
                )
            observations = []
            for item in run.observations:
                product_id = (
                    item["product"] if run.mode == MODE_LINK else product_ids.get(item["product"])
                )
                if product_id is None:
                    # A product past the limit: nothing is stored for it (D13).
                    continue
                observations.append(
                    {
                        **{k: v for k, v in item.items() if k not in ("product", "attribute")},
                        "product_id": product_id,
                        "attribute_id": str(attribute_ids[item["attribute"]]),
                    }
                )
            if observations:
                conn.execute(
                    query("documents", "insert_observations"), {"observations": Jsonb(observations)}
                )
            if files:
                conn.execute(query("documents", "update_document_values"), {"files": Jsonb(files)})
            if notes:
                conn.execute(
                    query("documents", "insert_notes"),
                    {"run_id": job.pipeline_run_id, "job_id": job.id, "notes": Jsonb(notes)},
                )


register_handler(DocumentsPlanHandler())
register_handler(DocumentsReadHandler())
register_handler(DocumentValuesHandler())
