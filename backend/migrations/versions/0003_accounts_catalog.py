"""Accounts, usage, suppliers and batches, plus owner foreign keys on the queue tables.

Expand-only: every new column and constraint is compatible with the code already running.
Queue rows whose owner has no profile (such as the M0B test runs owned by "system") must be
removed first, with the owner's approval. This migration refuses to run while they exist,
rather than deleting anything itself.

Revision ID: 0003
Revises: 0002
"""

from alembic import context, op
from sqlalchemy import text

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def _refuse_orphaned_queue_rows() -> None:
    """Fail with a clear message, instead of a bare foreign key error, if queue rows exist."""
    if context.is_offline_mode():
        return
    row = (
        op.get_bind()
        .execute(
            text(
                "SELECT (SELECT count(*) FROM pipeline_runs) AS runs, "
                "(SELECT count(*) FROM jobs) AS jobs"
            )
        )
        .one()
    )
    if row.runs or row.jobs:
        raise RuntimeError(
            f"Migration 0003 needs empty queue tables, but found {row.runs} pipeline runs and "
            f"{row.jobs} jobs whose owners have no profile. Delete them with the owner's "
            "approval, then run the migration again."
        )


def upgrade() -> None:
    """Create the account and catalog tables, then link the queue tables to owners."""
    _refuse_orphaned_queue_rows()
    op.execute(
        """
        CREATE TABLE user_profiles (
            id TEXT PRIMARY KEY,
            email TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('user', 'admin')),
            status TEXT NOT NULL CHECK (status IN ('active', 'waitlisted', 'disabled')),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    # The user cap counts active profiles.
    op.execute("CREATE INDEX user_profiles_status ON user_profiles (status)")
    op.execute(
        """
        CREATE TABLE user_limits (
            user_id TEXT PRIMARY KEY REFERENCES user_profiles (id) ON DELETE CASCADE,
            overrides JSONB NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(overrides) = 'object'),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    # Append-only. Usage records outlive the data they describe, so there's no cascade from
    # pipeline runs, which retention purges.
    op.execute(
        """
        CREATE TABLE usage_ledger (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id TEXT NOT NULL REFERENCES user_profiles (id) ON DELETE CASCADE,
            kind TEXT NOT NULL,
            quantity NUMERIC NOT NULL CHECK (quantity > 0),
            cost_usd NUMERIC,
            pipeline_run_id UUID REFERENCES pipeline_runs (id) ON DELETE SET NULL,
            is_restoration BOOLEAN NOT NULL DEFAULT false,
            restores_entry_id UUID REFERENCES usage_ledger (id),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CHECK (is_restoration = (restores_entry_id IS NOT NULL))
        )
        """
    )
    op.execute("CREATE INDEX usage_ledger_by_user ON usage_ledger (user_id, kind, created_at)")
    # An entry can be restored only once.
    op.execute(
        "CREATE UNIQUE INDEX usage_ledger_one_restoration ON usage_ledger (restores_entry_id) "
        "WHERE restores_entry_id IS NOT NULL"
    )
    op.execute(
        """
        CREATE TABLE suppliers (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            owner_user_id TEXT NOT NULL REFERENCES user_profiles (id) ON DELETE CASCADE,
            name TEXT NOT NULL CHECK (length(btrim(name)) > 0),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            -- Lets batches reference the supplier and its owner together.
            UNIQUE (id, owner_user_id)
        )
        """
    )
    # Names are unique per owner, ignoring case, so "Acme" and "acme" can't both exist.
    op.execute("CREATE UNIQUE INDEX suppliers_owner_name ON suppliers (owner_user_id, lower(name))")
    op.execute(
        """
        CREATE TABLE batches (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            supplier_id UUID NOT NULL,
            owner_user_id TEXT NOT NULL REFERENCES user_profiles (id) ON DELETE CASCADE,
            name TEXT NOT NULL CHECK (length(btrim(name)) > 0),
            status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft')),
            expires_at TIMESTAMPTZ NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            -- A batch always belongs to the owner of its supplier. NO ACTION (not RESTRICT)
            -- refuses deleting a supplier that has batches, yet still lets one statement
            -- that deletes a user cascade to both.
            FOREIGN KEY (supplier_id, owner_user_id)
                REFERENCES suppliers (id, owner_user_id) ON DELETE NO ACTION
        )
        """
    )
    op.execute("CREATE INDEX batches_by_owner ON batches (owner_user_id, created_at)")
    op.execute("CREATE INDEX batches_by_supplier ON batches (supplier_id, created_at)")
    op.execute("CREATE INDEX batches_expiry ON batches (expires_at)")

    op.execute(
        "ALTER TABLE pipeline_runs ADD CONSTRAINT pipeline_runs_owner_fk "
        "FOREIGN KEY (owner_user_id) REFERENCES user_profiles (id) ON DELETE CASCADE"
    )
    op.execute(
        "ALTER TABLE pipeline_runs ADD CONSTRAINT pipeline_runs_batch_fk "
        "FOREIGN KEY (batch_id) REFERENCES batches (id) ON DELETE CASCADE"
    )
    op.execute(
        "ALTER TABLE jobs ADD CONSTRAINT jobs_owner_fk "
        "FOREIGN KEY (owner_user_id) REFERENCES user_profiles (id) ON DELETE CASCADE"
    )


def downgrade() -> None:
    """Remove the foreign keys, then the tables."""
    op.execute("ALTER TABLE jobs DROP CONSTRAINT jobs_owner_fk")
    op.execute("ALTER TABLE pipeline_runs DROP CONSTRAINT pipeline_runs_batch_fk")
    op.execute("ALTER TABLE pipeline_runs DROP CONSTRAINT pipeline_runs_owner_fk")
    for table in ("batches", "suppliers", "usage_ledger", "user_limits", "user_profiles"):
        op.execute(f"DROP TABLE {table}")  # noqa: S608
