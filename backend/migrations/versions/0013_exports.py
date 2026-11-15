"""Exports: files built from a batch's approved products (M10, D41).

Expand-only: a new table.

Revision ID: 0013
Revises: 0012
"""

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create exports."""
    op.execute(
        """
        CREATE TABLE exports (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            batch_id UUID NOT NULL REFERENCES batches (id) ON DELETE CASCADE,
            owner_user_id TEXT NOT NULL REFERENCES user_profiles (id) ON DELETE CASCADE,
            format TEXT NOT NULL CHECK (format IN ('csv', 'xlsx', 'shopify_csv')),
            status TEXT NOT NULL CHECK (status IN ('ready', 'failed')),
            error TEXT,
            s3_key TEXT,
            -- The marketplace profile the products were approved under.
            profile TEXT NOT NULL,
            -- Exactly which products went in.
            product_ids UUID[] NOT NULL,
            product_count INTEGER NOT NULL,
            left_out_count INTEGER NOT NULL,
            -- When the photo links in the file stop working.
            links_expire_at TIMESTAMPTZ,
            size_bytes INTEGER,
            sha256 TEXT,
            -- Changes made to make cells safe, such as a leading "=" removed, for people.
            notes JSONB NOT NULL DEFAULT '[]',
            created_by TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX exports_by_batch ON exports (batch_id, created_at)")


def downgrade() -> None:
    """Drop exports."""
    op.execute("DROP TABLE exports")
