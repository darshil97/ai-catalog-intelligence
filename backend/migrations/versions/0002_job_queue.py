"""Job queue and worker tables.

The owner and batch columns have no foreign keys yet, because their tables arrive in M1.

Revision ID: 0002
Revises: 0001
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the queue and worker tables."""
    op.execute(
        """
        CREATE TABLE pipeline_runs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            pipeline TEXT NOT NULL,
            batch_id UUID,
            owner_user_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'queued'
                CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')),
            products_found INTEGER,
            products_kept INTEGER,
            products_discarded INTEGER,
            files_found INTEGER,
            files_kept INTEGER,
            files_discarded INTEGER,
            images_found INTEGER,
            images_kept INTEGER,
            images_discarded INTEGER,
            request_id TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            started_at TIMESTAMPTZ,
            finished_at TIMESTAMPTZ
        )
        """
    )
    # Fairness: one active run per user.
    op.execute(
        """
        CREATE UNIQUE INDEX pipeline_runs_one_active_per_owner
            ON pipeline_runs (owner_user_id)
            WHERE status IN ('queued', 'running')
        """
    )
    op.execute(
        """
        CREATE TABLE pipeline_stage_runs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            pipeline_run_id UUID NOT NULL REFERENCES pipeline_runs (id) ON DELETE CASCADE,
            stage TEXT NOT NULL,
            position INTEGER NOT NULL,
            job_type TEXT NOT NULL,
            payload JSONB NOT NULL DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'running', 'succeeded', 'failed', 'cancelled')),
            total_chunks INTEGER NOT NULL DEFAULT 0,
            completed_chunks INTEGER NOT NULL DEFAULT 0,
            failed_chunks INTEGER NOT NULL DEFAULT 0,
            started_at TIMESTAMPTZ,
            finished_at TIMESTAMPTZ,
            UNIQUE (pipeline_run_id, stage),
            UNIQUE (pipeline_run_id, position)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE jobs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            pipeline_run_id UUID NOT NULL REFERENCES pipeline_runs (id) ON DELETE CASCADE,
            stage_run_id UUID NOT NULL REFERENCES pipeline_stage_runs (id) ON DELETE CASCADE,
            owner_user_id TEXT NOT NULL,
            job_type TEXT NOT NULL,
            payload JSONB NOT NULL DEFAULT '{}',
            idempotency_key TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'running', 'succeeded', 'failed', 'cancelled',
                                  'blocked_quota')),
            priority INTEGER NOT NULL DEFAULT 100,
            attempts INTEGER NOT NULL DEFAULT 0,
            max_attempts INTEGER NOT NULL,
            run_after TIMESTAMPTZ NOT NULL DEFAULT now(),
            lease_owner TEXT,
            lease_token UUID,
            lease_expires_at TIMESTAMPTZ,
            contract_version INTEGER NOT NULL,
            request_id TEXT,
            code_sha TEXT,
            last_error TEXT,
            error_class TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            started_at TIMESTAMPTZ,
            finished_at TIMESTAMPTZ
        )
        """
    )
    op.execute(
        "CREATE INDEX jobs_claimable ON jobs (priority, created_at) WHERE status = 'pending'"
    )
    op.execute("CREATE INDEX jobs_leases ON jobs (lease_expires_at) WHERE status = 'running'")
    op.execute("CREATE INDEX jobs_by_run ON jobs (pipeline_run_id)")
    op.execute(
        """
        CREATE TABLE processing_errors (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            pipeline_run_id UUID NOT NULL REFERENCES pipeline_runs (id) ON DELETE CASCADE,
            job_id UUID REFERENCES jobs (id) ON DELETE CASCADE,
            source_file_id UUID,
            location JSONB,
            error_code TEXT NOT NULL,
            message TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE worker_control (
            id INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
            state TEXT NOT NULL DEFAULT 'stopped'
                CHECK (state IN ('stopped', 'starting', 'running', 'paused', 'shutting_down',
                                 'unavailable')),
            last_start_requested_at TIMESTAMPTZ,
            min_contract INTEGER,
            max_contract INTEGER,
            pause_reason TEXT,
            pause_expires_at TIMESTAMPTZ,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("INSERT INTO worker_control (id) VALUES (1)")
    op.execute(
        """
        CREATE TABLE worker_instances (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            ec2_instance_id TEXT,
            code_sha TEXT,
            state TEXT NOT NULL CHECK (state IN ('running', 'stopped')),
            started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_heartbeat_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            stopped_at TIMESTAMPTZ
        )
        """
    )
    op.execute(
        "CREATE INDEX worker_instances_running ON worker_instances (state, last_heartbeat_at)"
    )
    op.execute(
        """
        CREATE TABLE worker_sessions (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            ec2_instance_id TEXT NOT NULL,
            started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            ended_at TIMESTAMPTZ
        )
        """
    )
    op.execute("CREATE INDEX worker_sessions_started ON worker_sessions (started_at)")


def downgrade() -> None:
    """Drop the queue and worker tables."""
    for table in (
        "worker_sessions",
        "worker_instances",
        "worker_control",
        "processing_errors",
        "jobs",
        "pipeline_stage_runs",
        "pipeline_runs",
    ):
        op.execute(f"DROP TABLE {table}")  # noqa: S608
