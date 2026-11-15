"""Image association: candidate links with their evidence, accepted links, and review.

Expand-only: new tables and new nullable columns.

Revision ID: 0008
Revises: 0007
"""

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the association and review tables."""
    op.execute(
        """
        CREATE TABLE image_match_candidates (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            batch_id UUID NOT NULL REFERENCES batches (id) ON DELETE CASCADE,
            image_id UUID NOT NULL REFERENCES images (id) ON DELETE CASCADE,
            product_id UUID NOT NULL REFERENCES products (id) ON DELETE CASCADE,
            score NUMERIC(4, 3) NOT NULL CHECK (score BETWEEN 0 AND 1),
            -- One entry per signal that fired, with what it found.
            signals JSONB NOT NULL,
            decision TEXT NOT NULL
                CHECK (decision IN ('auto_accepted', 'needs_review', 'accepted', 'rejected')),
            -- 'system', or the ID of the person who decided.
            decided_by TEXT NOT NULL,
            decided_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            -- Set once values have been read from the photo for this product.
            values_read_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (image_id, product_id)
        )
        """
    )
    op.execute("CREATE INDEX image_match_candidates_by_batch ON image_match_candidates (batch_id)")

    op.execute(
        """
        CREATE TABLE product_images (
            product_id UUID NOT NULL REFERENCES products (id) ON DELETE CASCADE,
            image_id UUID NOT NULL REFERENCES images (id) ON DELETE CASCADE,
            position INTEGER NOT NULL,
            candidate_id UUID NOT NULL REFERENCES image_match_candidates (id) ON DELETE CASCADE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (product_id, image_id)
        )
        """
    )
    op.execute("CREATE INDEX product_images_by_image ON product_images (image_id)")

    op.execute(
        """
        CREATE TABLE review_tasks (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            owner_user_id TEXT NOT NULL REFERENCES user_profiles (id) ON DELETE CASCADE,
            batch_id UUID NOT NULL REFERENCES batches (id) ON DELETE CASCADE,
            product_id UUID REFERENCES products (id) ON DELETE CASCADE,
            kind TEXT NOT NULL CHECK (kind IN ('image_match')),
            -- What the task is about, such as {"image_id": ...}.
            subject_ref JSONB NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('open', 'resolved')),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            resolved_at TIMESTAMPTZ,
            CHECK ((status = 'resolved') = (resolved_at IS NOT NULL))
        )
        """
    )
    op.execute(
        "CREATE INDEX review_tasks_open ON review_tasks (owner_user_id, created_at) "
        "WHERE status = 'open'"
    )
    op.execute("CREATE INDEX review_tasks_by_batch ON review_tasks (batch_id)")
    # One task per image: a rerun finds the existing one instead of opening another.
    op.execute(
        "CREATE UNIQUE INDEX review_tasks_one_per_image ON review_tasks "
        "(kind, (subject_ref ->> 'image_id'))"
    )

    op.execute(
        """
        CREATE TABLE review_events (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            -- Kept when the person is deleted, because the record must outlive them.
            actor_user_id TEXT NOT NULL,
            batch_id UUID REFERENCES batches (id) ON DELETE CASCADE,
            entity_type TEXT NOT NULL,
            entity_id UUID NOT NULL,
            action TEXT NOT NULL,
            before JSONB,
            after JSONB,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX review_events_by_entity ON review_events (entity_type, entity_id)")
    # Append-only: rows can be added, never changed or removed, except with their batch.
    op.execute(
        """
        CREATE FUNCTION review_events_append_only() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'review_events is append-only';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        "CREATE TRIGGER review_events_no_update BEFORE UPDATE ON review_events "
        "FOR EACH ROW EXECUTE FUNCTION review_events_append_only()"
    )

    op.execute("ALTER TABLE pipeline_runs ADD COLUMN images_matched INTEGER")
    op.execute("ALTER TABLE pipeline_runs ADD COLUMN images_to_review INTEGER")
    op.execute("ALTER TABLE pipeline_runs ADD COLUMN images_unmatched INTEGER")


def downgrade() -> None:
    """Drop the association and review tables."""
    for column in ("images_unmatched", "images_to_review", "images_matched"):
        op.execute(f"ALTER TABLE pipeline_runs DROP COLUMN {column}")
    op.execute("DROP TRIGGER review_events_no_update ON review_events")
    op.execute("DROP FUNCTION review_events_append_only()")
    for table in ("review_events", "review_tasks", "product_images", "image_match_candidates"):
        op.execute(f"DROP TABLE {table}")  # noqa: S608
