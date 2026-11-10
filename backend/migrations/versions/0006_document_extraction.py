"""Text read from PDFs and photos, and a summary of each document.

Expand-only: two new tables, a column with a default, and one new allowed value in a check.

Revision ID: 0006
Revises: 0005
"""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create ocr_results and document_files, and allow the llm_budget mapping state."""
    op.execute(
        """
        CREATE TABLE ocr_results (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            source_file_id UUID NOT NULL REFERENCES source_files (id) ON DELETE CASCADE,
            -- The PDF page read, from 1. Null for a photo.
            page INTEGER CHECK (page >= 1),
            engine TEXT NOT NULL,
            engine_version TEXT NOT NULL,
            text TEXT NOT NULL,
            line_count INTEGER NOT NULL,
            -- Mean confidence of the lines kept, from 0 to 1.
            confidence NUMERIC(4, 3) CHECK (confidence BETWEEN 0 AND 1),
            duration_ms INTEGER,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX ocr_results_one_per_page ON ocr_results "
        "(source_file_id, COALESCE(page, 0), engine)"
    )

    op.execute(
        """
        CREATE TABLE document_files (
            source_file_id UUID PRIMARY KEY REFERENCES source_files (id) ON DELETE CASCADE,
            batch_id UUID NOT NULL REFERENCES batches (id) ON DELETE CASCADE,
            kind TEXT NOT NULL CHECK (kind IN ('pdf', 'image')),
            status TEXT NOT NULL CHECK (status IN ('read', 'skipped')),
            skip_reason TEXT,
            skip_message TEXT,
            pages INTEGER NOT NULL DEFAULT 0,
            pages_with_text INTEGER NOT NULL DEFAULT 0,
            pages_ocr INTEGER NOT NULL DEFAULT 0,
            pages_unreadable INTEGER NOT NULL DEFAULT 0,
            characters INTEGER NOT NULL DEFAULT 0,
            -- Filled in by the values job.
            product_keys JSONB NOT NULL DEFAULT '[]',
            pages_without_product INTEGER NOT NULL DEFAULT 0,
            values_kept INTEGER NOT NULL DEFAULT 0,
            values_dropped INTEGER NOT NULL DEFAULT 0,
            llm_source TEXT NOT NULL DEFAULT 'none'
                CHECK (llm_source IN ('none', 'llm', 'llm_failed', 'llm_capped', 'llm_budget')),
            llm_message TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CHECK ((status = 'skipped') = (skip_reason IS NOT NULL))
        )
        """
    )
    op.execute("CREATE INDEX document_files_by_batch ON document_files (batch_id)")

    # A batch with no spreadsheet makes its products from codes found in PDFs and photos.
    # Existing products all came from spreadsheets.
    op.execute(
        "ALTER TABLE products ADD COLUMN origin TEXT NOT NULL DEFAULT 'spreadsheet' "
        "CHECK (origin IN ('spreadsheet', 'document'))"
    )

    # Column mapping can now be skipped because the monthly LLM budget is used.
    op.execute(
        "ALTER TABLE spreadsheet_sheets DROP CONSTRAINT spreadsheet_sheets_mapping_source_check"
    )
    op.execute(
        "ALTER TABLE spreadsheet_sheets ADD CONSTRAINT spreadsheet_sheets_mapping_source_check "
        "CHECK (mapping_source IN ('none', 'known_names', 'saved', 'llm', 'llm_failed', "
        "'llm_capped', 'llm_budget'))"
    )


def downgrade() -> None:
    """Drop the document tables and the llm_budget mapping state."""
    op.execute(
        "UPDATE spreadsheet_sheets SET mapping_source = 'llm_failed' "
        "WHERE mapping_source = 'llm_budget'"
    )
    op.execute(
        "ALTER TABLE spreadsheet_sheets DROP CONSTRAINT spreadsheet_sheets_mapping_source_check"
    )
    op.execute(
        "ALTER TABLE spreadsheet_sheets ADD CONSTRAINT spreadsheet_sheets_mapping_source_check "
        "CHECK (mapping_source IN ('none', 'known_names', 'saved', 'llm', 'llm_failed', "
        "'llm_capped'))"
    )
    op.execute("ALTER TABLE products DROP COLUMN origin")
    op.execute("DROP TABLE document_files")
    op.execute("DROP TABLE ocr_results")
