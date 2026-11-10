"""Products and what spreadsheets said about them, plus the record of LLM calls.

Expand-only: every table is new, and nothing existing changes.

Revision ID: 0005
Revises: 0004
"""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

# code, name, data_type, unit_family, description. The description tells the LLM what belongs in
# the attribute. Column names that map to each one live in config.yaml, so they can grow without
# a migration.
_ATTRIBUTES = (
    ("sku", "SKU", "text", None, "The supplier's own code for the product."),
    ("model_number", "Model number", "text", None, "The manufacturer's model or part number."),
    ("gtin", "Barcode (GTIN)", "text", None, "The barcode number, also called GTIN, EAN or UPC."),
    ("product_name", "Product name", "text", None, "The product's name or short title."),
    ("brand", "Brand", "text", None, "The brand or manufacturer name."),
    ("description", "Description", "text", None, "Longer text describing the product."),
    ("color", "Colour", "text", None, "The product's colour."),
    ("material", "Material", "text", None, "What the product is made of."),
    ("price", "Price", "number", None, "The price of one unit, as the supplier lists it."),
    ("currency", "Currency", "text", None, "The currency of the price."),
    (
        "pack_quantity",
        "Pack quantity",
        "number",
        None,
        "How many units come in one pack or carton.",
    ),
    ("net_weight", "Net weight", "number", "mass", "The product's weight without packaging."),
    ("capacity", "Capacity", "number", "volume", "How much the product holds."),
    ("length", "Length", "number", "length", "The product's length or depth."),
    ("width", "Width", "number", "length", "The product's width."),
    ("height", "Height", "number", "length", "The product's height."),
    ("supplier_category", "Supplier's category", "text", None, "The supplier's own category."),
)


def upgrade() -> None:
    """Create the extraction tables and seed the core attributes."""
    op.execute(
        """
        CREATE TABLE categories (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            code TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            parent_id UUID REFERENCES categories (id) ON DELETE SET NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE attribute_definitions (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            code TEXT NOT NULL UNIQUE CHECK (code ~ '^[a-z][a-z0-9_]*$'),
            name TEXT NOT NULL,
            data_type TEXT NOT NULL CHECK (data_type IN ('text', 'number', 'boolean', 'enum')),
            unit_family TEXT,
            -- Null means the attribute applies to every category.
            category_scope UUID REFERENCES categories (id) ON DELETE CASCADE,
            rules JSONB NOT NULL DEFAULT '{}',
            version INTEGER NOT NULL DEFAULT 1,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    for code, name, data_type, unit_family, description in _ATTRIBUTES:
        # Written as literals so the migration also renders as plain SQL. The values are the
        # constants above, with quotes doubled.
        values = ", ".join(
            "NULL" if value is None else "'" + value.replace("'", "''") + "'"
            for value in (code, name, data_type, unit_family, description)
        )
        op.execute(
            "INSERT INTO attribute_definitions (code, name, data_type, unit_family, rules) "  # noqa: S608
            "SELECT v.code, v.name, v.data_type, v.unit_family, "
            "jsonb_build_object('description', v.description) "
            f"FROM (VALUES ({values})) AS v(code, name, data_type, unit_family, description)"
        )

    op.execute(
        """
        CREATE TABLE model_runs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            owner_user_id TEXT NOT NULL REFERENCES user_profiles (id) ON DELETE CASCADE,
            -- Kept after its job is purged, because the cost still counts.
            job_id UUID REFERENCES jobs (id) ON DELETE SET NULL,
            task TEXT NOT NULL,
            provider TEXT NOT NULL,
            model_id TEXT NOT NULL,
            prompt_version TEXT,
            input_hash TEXT NOT NULL CHECK (input_hash ~ '^[0-9a-f]{64}$'),
            status TEXT NOT NULL CHECK (status IN ('succeeded', 'failed')),
            -- The validated reply, when it is small. Large replies go to storage instead.
            response JSONB,
            response_ref TEXT,
            input_tokens INTEGER,
            output_tokens INTEGER,
            cost_usd NUMERIC(12, 6),
            latency_ms INTEGER,
            error TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    # A retry reuses the stored reply, and replies never cross users.
    op.execute(
        "CREATE UNIQUE INDEX model_runs_reuse ON model_runs (owner_user_id, input_hash) "
        "WHERE status = 'succeeded'"
    )
    op.execute("CREATE INDEX model_runs_by_job ON model_runs (job_id)")
    op.execute("CREATE INDEX model_runs_by_time ON model_runs (created_at)")

    op.execute(
        """
        CREATE TABLE column_mappings (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            supplier_id UUID NOT NULL REFERENCES suppliers (id) ON DELETE CASCADE,
            owner_user_id TEXT NOT NULL REFERENCES user_profiles (id) ON DELETE CASCADE,
            -- Identifies a header layout, the attribute list and the mapping rules' version.
            header_fingerprint TEXT NOT NULL CHECK (header_fingerprint ~ '^[0-9a-f]{64}$'),
            mapping JSONB NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (supplier_id, header_fingerprint)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE spreadsheet_sheets (
            id UUID PRIMARY KEY,
            source_file_id UUID NOT NULL REFERENCES source_files (id) ON DELETE CASCADE,
            batch_id UUID NOT NULL REFERENCES batches (id) ON DELETE CASCADE,
            sheet_index INTEGER NOT NULL,
            -- Null for a CSV, which has no sheets.
            sheet_name TEXT,
            hidden BOOLEAN NOT NULL DEFAULT false,
            status TEXT NOT NULL CHECK (status IN ('used', 'skipped')),
            skip_reason TEXT,
            skip_message TEXT,
            header_row INTEGER,
            header_rows INTEGER,
            -- One entry per column: letter, header, attribute, source, confidence, reason.
            columns JSONB NOT NULL DEFAULT '[]',
            -- How the columns were mapped, and why the LLM wasn't used if it wasn't.
            mapping_source TEXT NOT NULL
                CHECK (mapping_source IN ('none', 'known_names', 'saved', 'llm', 'llm_failed',
                                          'llm_capped')),
            mapping_message TEXT,
            row_count INTEGER NOT NULL DEFAULT 0,
            rows_without_identifier INTEGER NOT NULL DEFAULT 0,
            duplicate_rows INTEGER NOT NULL DEFAULT 0,
            rows_over_cap INTEGER NOT NULL DEFAULT 0,
            unreadable_rows INTEGER NOT NULL DEFAULT 0,
            -- First rows of the chunks that hold kept products, and the chunk size used.
            chunk_starts JSONB NOT NULL DEFAULT '[]',
            chunk_rows INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (source_file_id, sheet_index),
            CHECK ((status = 'skipped') = (skip_reason IS NOT NULL))
        )
        """
    )
    op.execute("CREATE INDEX spreadsheet_sheets_by_batch ON spreadsheet_sheets (batch_id)")

    op.execute(
        """
        CREATE TABLE source_records (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            source_file_id UUID NOT NULL REFERENCES source_files (id) ON DELETE CASCADE,
            record_type TEXT NOT NULL CHECK (record_type IN ('row', 'page', 'block')),
            location JSONB NOT NULL,
            raw_ref TEXT,
            raw_data JSONB,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (source_file_id, record_type, location)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE products (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            batch_id UUID NOT NULL REFERENCES batches (id) ON DELETE CASCADE,
            owner_user_id TEXT NOT NULL REFERENCES user_profiles (id) ON DELETE CASCADE,
            -- The cleaned identifier that makes rows one product.
            product_key TEXT NOT NULL CHECK (length(product_key) > 0),
            key_source TEXT NOT NULL CHECK (key_source IN ('sku', 'model_number', 'gtin')),
            -- The SKU exactly as the supplier wrote it, when there is one.
            supplier_sku TEXT,
            -- The key without punctuation, for matching file names and photos later.
            match_key TEXT NOT NULL,
            -- Problems found with the identifier, such as lost digits.
            identifier_flags JSONB NOT NULL DEFAULT '[]',
            -- Position in file order: the first product found is 1.
            source_order INTEGER NOT NULL,
            category_id UUID REFERENCES categories (id) ON DELETE SET NULL,
            status TEXT NOT NULL DEFAULT 'draft'
                CHECK (status IN ('draft', 'in_review', 'approved', 'rejected')),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (batch_id, product_key)
        )
        """
    )
    op.execute("CREATE INDEX products_by_owner ON products (owner_user_id)")
    op.execute("CREATE INDEX products_by_match_key ON products (batch_id, match_key)")

    op.execute(
        """
        CREATE TABLE attribute_observations (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            product_id UUID NOT NULL REFERENCES products (id) ON DELETE CASCADE,
            attribute_id UUID NOT NULL REFERENCES attribute_definitions (id),
            -- Exactly as found. Rows here are never updated.
            raw_value TEXT NOT NULL,
            source_file_id UUID NOT NULL REFERENCES source_files (id) ON DELETE CASCADE,
            source_record_id UUID REFERENCES source_records (id) ON DELETE CASCADE,
            location JSONB NOT NULL,
            extractor TEXT NOT NULL,
            -- Set when an LLM decided what the column means. The value itself is never the LLM's.
            model_run_id UUID REFERENCES model_runs (id) ON DELETE SET NULL,
            confidence NUMERIC(4, 3) CHECK (confidence BETWEEN 0 AND 1),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (product_id, attribute_id, source_file_id, location, extractor)
        )
        """
    )
    op.execute(
        "CREATE INDEX attribute_observations_by_file ON attribute_observations (source_file_id)"
    )
    op.execute(
        "CREATE INDEX attribute_observations_by_record ON attribute_observations (source_record_id)"
    )


def downgrade() -> None:
    """Drop the extraction tables."""
    for table in (
        "attribute_observations",
        "products",
        "source_records",
        "spreadsheet_sheets",
        "column_mappings",
        "model_runs",
        "attribute_definitions",
        "categories",
    ):
        op.execute(f"DROP TABLE {table}")  # noqa: S608
