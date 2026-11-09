"""Source files, and the batch statuses processing needs.

Expand-only: the new table is unused by older code, and batches.status only gains values.

Revision ID: 0004
Revises: 0003
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create source_files and widen the batch status check."""
    op.execute("ALTER TABLE batches DROP CONSTRAINT batches_status_check")
    op.execute(
        "ALTER TABLE batches ADD CONSTRAINT batches_status_check "
        "CHECK (status IN ('draft', 'queued', 'processing', 'completed', 'failed'))"
    )
    op.execute(
        """
        CREATE TABLE source_files (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            batch_id UUID NOT NULL REFERENCES batches (id) ON DELETE CASCADE,
            owner_user_id TEXT NOT NULL REFERENCES user_profiles (id) ON DELETE CASCADE,
            -- Set for a file found inside an uploaded ZIP.
            parent_file_id UUID REFERENCES source_files (id) ON DELETE CASCADE,
            archive_member TEXT,
            -- Upload order, then order inside the archive. The image limit keeps the first ones.
            position INTEGER NOT NULL,
            member_index INTEGER NOT NULL DEFAULT 0,
            -- No object is stored for an archive entry that was rejected.
            s3_key TEXT UNIQUE,
            original_name TEXT NOT NULL,
            extension TEXT NOT NULL,
            -- other: an archive entry of an unsupported type, kept only to report it.
            kind TEXT NOT NULL CHECK (kind IN ('spreadsheet', 'pdf', 'image', 'zip', 'other')),
            content_type TEXT NOT NULL,
            size_bytes BIGINT NOT NULL CHECK (size_bytes >= 0),
            sha256 TEXT CHECK (sha256 ~ '^[0-9a-f]{64}$'),
            status TEXT NOT NULL
                CHECK (status IN ('pending_upload', 'uploaded', 'ingested', 'rejected')),
            rejection_reason TEXT,
            rejection_message TEXT,
            details JSONB NOT NULL DEFAULT '{}',
            upload_expires_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CHECK ((parent_file_id IS NULL) = (archive_member IS NULL)),
            CHECK (s3_key IS NOT NULL OR status = 'rejected'),
            CHECK (sha256 IS NOT NULL OR status = 'rejected'),
            CHECK ((status = 'rejected') = (rejection_reason IS NOT NULL))
        )
        """
    )
    # The same content once per batch. Rejected files don't count, so a failed upload can be
    # tried again.
    op.execute(
        "CREATE UNIQUE INDEX source_files_batch_sha256 ON source_files (batch_id, sha256) "
        "WHERE status <> 'rejected'"
    )
    op.execute(
        "CREATE UNIQUE INDEX source_files_archive_member ON source_files "
        "(parent_file_id, archive_member) WHERE parent_file_id IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX source_files_by_batch ON source_files (batch_id, position, member_index)"
    )
    op.execute("CREATE INDEX source_files_by_owner ON source_files (owner_user_id)")
    op.execute("CREATE INDEX pipeline_runs_by_batch ON pipeline_runs (batch_id, created_at)")


def downgrade() -> None:
    """Drop source_files and restore the draft-only status check."""
    op.execute("DROP INDEX pipeline_runs_by_batch")
    op.execute("DROP TABLE source_files")
    op.execute("ALTER TABLE batches DROP CONSTRAINT batches_status_check")
    op.execute("UPDATE batches SET status = 'draft'")
    op.execute(
        "ALTER TABLE batches ADD CONSTRAINT batches_status_check CHECK (status IN ('draft'))"
    )
