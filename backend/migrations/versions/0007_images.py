"""Images: one record per kept image, near-duplicate groups, and embeddings (D20, D34).

Expand-only: new tables, one new column with no default rows to fill, and one seed row.

Revision ID: 0007
Revises: 0006
"""

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the image tables and record the embedding model."""
    op.execute(
        """
        CREATE TABLE embedding_models (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            provider TEXT NOT NULL,
            name TEXT NOT NULL,
            version TEXT NOT NULL,
            dimension INTEGER NOT NULL CHECK (dimension > 0),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (provider, name, version)
        )
        """
    )
    # Titan's multimodal model in Sydney, at its largest output size (D34).
    op.execute(
        "INSERT INTO embedding_models (provider, name, version, dimension) "
        "VALUES ('bedrock', 'amazon.titan-embed-image-v1', '1', 1024)"
    )

    op.execute(
        """
        CREATE TABLE image_duplicate_groups (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            batch_id UUID NOT NULL REFERENCES batches (id) ON DELETE CASCADE,
            -- exact: the same pixels. near: the same photo resized, re-saved or lightly edited.
            kind TEXT NOT NULL CHECK (kind IN ('exact', 'near')),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX image_duplicate_groups_by_batch ON image_duplicate_groups (batch_id)")

    op.execute(
        """
        CREATE TABLE images (
            id UUID PRIMARY KEY,
            batch_id UUID NOT NULL REFERENCES batches (id) ON DELETE CASCADE,
            owner_user_id TEXT NOT NULL REFERENCES user_profiles (id) ON DELETE CASCADE,
            source_file_id UUID NOT NULL UNIQUE REFERENCES source_files (id) ON DELETE CASCADE,
            status TEXT NOT NULL CHECK (status IN ('processed', 'skipped')),
            skip_reason TEXT,
            skip_message TEXT,
            sha256 TEXT NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
            -- Perceptual hash: 64 bits, stored signed.
            phash BIGINT,
            -- How much of each of 12 hues the image holds, for telling colour variants apart.
            colour_signature JSONB,
            width INTEGER,
            height INTEGER,
            thumbnail_s3_key TEXT,
            duplicate_group_id UUID REFERENCES image_duplicate_groups (id) ON DELETE SET NULL,
            -- The image a duplicate group is best shown by: its largest.
            is_representative BOOLEAN NOT NULL DEFAULT false,
            -- Why the embedding is missing, when it is.
            embedding_note TEXT,
            -- Milliseconds per step, for the throughput benchmark.
            timings JSONB NOT NULL DEFAULT '{}',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CHECK ((status = 'skipped') = (skip_reason IS NOT NULL))
        )
        """
    )
    op.execute("CREATE INDEX images_by_batch ON images (batch_id)")
    op.execute("CREATE INDEX images_by_sha256 ON images (batch_id, sha256)")

    op.execute(
        """
        CREATE TABLE image_embeddings (
            image_id UUID NOT NULL REFERENCES images (id) ON DELETE CASCADE,
            embedding_model_id UUID NOT NULL REFERENCES embedding_models (id),
            -- No index: comparisons are within one batch, so exact search is accurate (D20).
            embedding vector(1024) NOT NULL,
            model_run_id UUID REFERENCES model_runs (id) ON DELETE SET NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (image_id, embedding_model_id)
        )
        """
    )

    op.execute("ALTER TABLE pipeline_runs ADD COLUMN image_duplicate_groups INTEGER")


def downgrade() -> None:
    """Drop the image tables."""
    op.execute("ALTER TABLE pipeline_runs DROP COLUMN image_duplicate_groups")
    for table in ("image_embeddings", "images", "image_duplicate_groups", "embedding_models"):
        op.execute(f"DROP TABLE {table}")  # noqa: S608
