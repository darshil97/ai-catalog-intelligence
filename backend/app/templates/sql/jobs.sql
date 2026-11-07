-- Job queue queries. Each "-- name:" line starts a query. Values are always bound parameters.

-- name: insert_run
INSERT INTO pipeline_runs (pipeline, owner_user_id, request_id)
VALUES (%(pipeline)s, %(owner)s, %(request_id)s)
RETURNING id;

-- name: insert_stage
INSERT INTO pipeline_stage_runs (pipeline_run_id, stage, position, job_type, payload)
VALUES (%(run_id)s, %(stage)s, %(position)s, %(job_type)s, %(payload)s)
RETURNING id;

-- name: insert_job
INSERT INTO jobs (pipeline_run_id, stage_run_id, owner_user_id, job_type, payload,
                  idempotency_key, max_attempts, contract_version, request_id)
VALUES (%(run_id)s, %(stage_run_id)s, %(owner)s, %(job_type)s, %(payload)s,
        %(idempotency_key)s, %(max_attempts)s, %(contract_version)s, %(request_id)s)
ON CONFLICT (idempotency_key) DO NOTHING
RETURNING id;

-- name: start_stage
UPDATE pipeline_stage_runs
SET status = 'running', started_at = now(), total_chunks = total_chunks + %(added)s
WHERE id = %(stage_run_id)s;

-- name: add_chunks
UPDATE pipeline_stage_runs
SET total_chunks = total_chunks + %(added)s
WHERE id = %(stage_run_id)s;

-- name: claim
WITH next AS (
    SELECT id FROM jobs
    WHERE status = 'pending' AND run_after <= now()
      AND contract_version BETWEEN %(min_contract)s AND %(max_contract)s
    ORDER BY priority, created_at
    FOR UPDATE SKIP LOCKED
    LIMIT 1
)
UPDATE jobs j
SET status = 'running',
    lease_token = gen_random_uuid(),
    lease_owner = %(worker_id)s,
    lease_expires_at = now() + make_interval(secs => %(lease_seconds)s),
    attempts = attempts + 1,
    code_sha = %(code_sha)s,
    started_at = COALESCE(j.started_at, now())
FROM next
WHERE j.id = next.id
RETURNING j.*;

-- name: mark_run_running
UPDATE pipeline_runs
SET status = 'running', started_at = now()
WHERE id = %(run_id)s AND status = 'queued';

-- name: renew_lease
UPDATE jobs
SET lease_expires_at = now() + make_interval(secs => %(lease_seconds)s)
WHERE id = %(job_id)s AND lease_token = %(lease_token)s AND status = 'running';

-- name: fence
UPDATE jobs
SET lease_token = lease_token
WHERE id = %(job_id)s AND lease_token = %(lease_token)s AND status = 'running';

-- name: mark_succeeded
UPDATE jobs
SET status = 'succeeded', finished_at = now(), lease_token = NULL, lease_owner = NULL,
    lease_expires_at = NULL, last_error = NULL, error_class = NULL
WHERE id = %(job_id)s;

-- name: mark_retry
UPDATE jobs
SET status = 'pending', run_after = now() + make_interval(secs => %(delay_seconds)s),
    lease_token = NULL, lease_owner = NULL, lease_expires_at = NULL,
    last_error = %(error)s, error_class = %(error_class)s
WHERE id = %(job_id)s;

-- name: mark_failed
UPDATE jobs
SET status = 'failed', finished_at = now(), lease_token = NULL, lease_owner = NULL,
    lease_expires_at = NULL, last_error = %(error)s, error_class = %(error_class)s
WHERE id = %(job_id)s;

-- name: mark_released
UPDATE jobs
SET status = 'pending', attempts = attempts - 1, lease_token = NULL, lease_owner = NULL,
    lease_expires_at = NULL
WHERE id = %(job_id)s;

-- name: insert_error
INSERT INTO processing_errors (pipeline_run_id, job_id, error_code, message)
VALUES (%(run_id)s, %(job_id)s, %(error_code)s, %(message)s);

-- name: expired_jobs
SELECT * FROM jobs
WHERE status = 'running' AND lease_expires_at < now()
FOR UPDATE SKIP LOCKED;

-- name: record_chunk_result
UPDATE pipeline_stage_runs
SET completed_chunks = completed_chunks + %(completed)s,
    failed_chunks = failed_chunks + %(failed)s
WHERE id = %(stage_run_id)s
RETURNING id, pipeline_run_id, position, total_chunks, completed_chunks, failed_chunks;

-- name: finish_stage
UPDATE pipeline_stage_runs
SET status = %(status)s, finished_at = now()
WHERE id = %(stage_run_id)s;

-- name: next_stage
SELECT id, stage, job_type, payload FROM pipeline_stage_runs
WHERE pipeline_run_id = %(run_id)s AND position = %(position)s;

-- name: finish_run
UPDATE pipeline_runs
SET status = %(status)s, finished_at = now()
WHERE id = %(run_id)s;

-- name: cancel_remaining_jobs
UPDATE jobs
SET status = 'cancelled', finished_at = now()
WHERE pipeline_run_id = %(run_id)s AND status = 'pending';

-- name: cancel_remaining_stages
UPDATE pipeline_stage_runs
SET status = 'cancelled', finished_at = now()
WHERE pipeline_run_id = %(run_id)s AND status = 'pending';

-- name: get_run
SELECT id, pipeline, owner_user_id, status, request_id, created_at, started_at, finished_at
FROM pipeline_runs
WHERE id = %(run_id)s;

-- name: get_stages
SELECT stage, position, status, total_chunks, completed_chunks, failed_chunks
FROM pipeline_stage_runs
WHERE pipeline_run_id = %(run_id)s
ORDER BY position;

-- name: count_jobs_by_status
SELECT status, count(*) AS count FROM jobs
WHERE pipeline_run_id = %(run_id)s
GROUP BY status;
