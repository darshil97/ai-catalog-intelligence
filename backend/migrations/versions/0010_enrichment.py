"""Enrichment: generated titles, descriptions and bullets, and runs that pause at the spend cap.

Expand-only: a new table, new product and run columns with defaults, and a wider status check
on pipeline_runs (old code never writes the new value).

Revision ID: 0010
Revises: 0009
"""

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create generated_content, and let runs pause."""
    op.execute(
        """
        CREATE TABLE generated_content (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            product_id UUID NOT NULL REFERENCES products (id) ON DELETE CASCADE,
            batch_id UUID NOT NULL REFERENCES batches (id) ON DELETE CASCADE,
            owner_user_id TEXT NOT NULL REFERENCES user_profiles (id) ON DELETE CASCADE,
            version INTEGER NOT NULL CHECK (version > 0),
            is_current BOOLEAN NOT NULL DEFAULT true,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            bullets TEXT[] NOT NULL,
            -- Every title, sentence and bullet, with the facts it cites as they were then.
            claims JSONB NOT NULL,
            cited_attribute_ids UUID[] NOT NULL,
            -- Hash of the facts given; differs from the product's current facts when out of date.
            input_fingerprint TEXT NOT NULL,
            rules_version TEXT NOT NULL,
            model_run_id UUID REFERENCES model_runs (id) ON DELETE SET NULL,
            status TEXT NOT NULL DEFAULT 'generated'
                CHECK (status IN ('generated', 'edited', 'approved', 'rejected')),
            -- 'system', or the person who wrote it (M9).
            created_by TEXT NOT NULL DEFAULT 'system',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (product_id, version)
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX generated_content_one_current ON generated_content (product_id) "
        "WHERE is_current"
    )
    op.execute("CREATE INDEX generated_content_by_batch ON generated_content (batch_id)")

    op.execute(
        "ALTER TABLE products ADD COLUMN enrichment_state TEXT NOT NULL DEFAULT 'none' "
        "CHECK (enrichment_state IN ('none', 'generated', 'skipped', 'failed'))"
    )
    op.execute("ALTER TABLE products ADD COLUMN enrichment_message TEXT")
    op.execute("ALTER TABLE products ADD COLUMN enrichment_at TIMESTAMPTZ")

    # paused: stopped by a global cap, with its jobs blocked_quota. Not an active run, so the
    # owner can process other batches meanwhile.
    op.execute("ALTER TABLE pipeline_runs DROP CONSTRAINT pipeline_runs_status_check")
    op.execute(
        "ALTER TABLE pipeline_runs ADD CONSTRAINT pipeline_runs_status_check "
        "CHECK (status IN ('queued', 'running', 'paused', 'succeeded', 'failed', 'cancelled'))"
    )
    op.execute("ALTER TABLE pipeline_runs ADD COLUMN pause_reason TEXT")


def downgrade() -> None:
    """Drop enrichment, and turn paused runs into cancelled ones."""
    op.execute("ALTER TABLE pipeline_runs DROP COLUMN pause_reason")
    op.execute(
        "UPDATE jobs SET status = 'cancelled', finished_at = now() WHERE status = 'blocked_quota'"
    )
    op.execute(
        "UPDATE pipeline_runs SET status = 'cancelled', finished_at = now() WHERE status = 'paused'"
    )
    op.execute("ALTER TABLE pipeline_runs DROP CONSTRAINT pipeline_runs_status_check")
    op.execute(
        "ALTER TABLE pipeline_runs ADD CONSTRAINT pipeline_runs_status_check "
        "CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled'))"
    )
    for column in ("enrichment_at", "enrichment_message", "enrichment_state"):
        op.execute(f"ALTER TABLE products DROP COLUMN {column}")  # noqa: S608 - constant names
    op.execute("DROP TABLE generated_content")
