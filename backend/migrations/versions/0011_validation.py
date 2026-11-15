"""Validation: warnings a person set aside, and each batch's marketplace profile.

Expand-only: a new table, and a new batch column with a default.

Revision ID: 0011
Revises: 0010
"""

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create validation_overrides, and give batches a marketplace profile."""
    op.execute(
        """
        CREATE TABLE validation_overrides (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            product_id UUID NOT NULL REFERENCES products (id) ON DELETE CASCADE,
            batch_id UUID NOT NULL REFERENCES batches (id) ON DELETE CASCADE,
            owner_user_id TEXT NOT NULL REFERENCES user_profiles (id) ON DELETE CASCADE,
            rule_code TEXT NOT NULL,
            -- The attribute code, the other product's ID, or '' for the product itself.
            subject TEXT NOT NULL DEFAULT '',
            -- What the check saw. The warning stays aside only while this still matches.
            fingerprint TEXT NOT NULL,
            reason TEXT NOT NULL CHECK (length(reason) BETWEEN 1 AND 500),
            created_by TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (product_id, rule_code, subject)
        )
        """
    )
    op.execute("CREATE INDEX validation_overrides_by_batch ON validation_overrides (batch_id)")
    # Checked in code against the profiles in config, so a new profile needs no migration.
    op.execute("ALTER TABLE batches ADD COLUMN target_marketplace TEXT NOT NULL DEFAULT 'shopify'")


def downgrade() -> None:
    """Drop them."""
    op.execute("ALTER TABLE batches DROP COLUMN target_marketplace")
    op.execute("DROP TABLE validation_overrides")
