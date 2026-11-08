-- Worker state queries. Each "-- name:" line starts a query. Values are always bound parameters.

-- name: lock_control
SELECT state, pause_reason,
       (pause_reason IS NOT NULL AND pause_expires_at > now()) AS paused
FROM worker_control
WHERE id = 1
FOR UPDATE;

-- name: other_live_workers
SELECT count(*) AS count FROM worker_instances
WHERE state = 'running'
  AND last_heartbeat_at > now() - make_interval(secs => %(stale_after_seconds)s);

-- name: stop_stale_instances
UPDATE worker_instances
SET state = 'stopped', stopped_at = last_heartbeat_at
WHERE state = 'running';

-- name: insert_instance
INSERT INTO worker_instances (ec2_instance_id, code_sha, state)
VALUES (%(ec2_instance_id)s, %(code_sha)s, 'running')
RETURNING id;

-- name: insert_session
INSERT INTO worker_sessions (ec2_instance_id)
VALUES (%(ec2_instance_id)s);

-- name: set_running
UPDATE worker_control
SET state = 'running', min_contract = %(min_contract)s, max_contract = %(max_contract)s,
    updated_at = now()
WHERE id = 1;

-- name: instance_heartbeat
UPDATE worker_instances
SET last_heartbeat_at = now()
WHERE id = %(instance_id)s;

-- name: session_heartbeat
UPDATE worker_sessions
SET last_seen_at = now()
WHERE ec2_instance_id = %(ec2_instance_id)s AND ended_at IS NULL;

-- name: pause_state
SELECT (pause_reason IS NOT NULL AND pause_expires_at > now()) AS paused
FROM worker_control
WHERE id = 1;

-- name: set_state
UPDATE worker_control
SET state = %(state)s, updated_at = now()
WHERE id = 1 AND state <> %(state)s;

-- name: outstanding_jobs
SELECT count(*) AS count FROM jobs
WHERE status = 'running'
   OR (%(count_pending)s AND status = 'pending'
       AND contract_version BETWEEN %(min_contract)s AND %(max_contract)s);

-- name: stop_instance
UPDATE worker_instances
SET state = 'stopped', stopped_at = now()
WHERE id = %(instance_id)s;

-- name: end_session
UPDATE worker_sessions
SET ended_at = now(), last_seen_at = now()
WHERE ec2_instance_id = %(ec2_instance_id)s AND ended_at IS NULL;

-- name: claimable_pending
SELECT count(*) AS count
FROM jobs j CROSS JOIN worker_control c
WHERE j.status = 'pending'
  AND j.contract_version BETWEEN COALESCE(c.min_contract, j.contract_version)
                             AND COALESCE(c.max_contract, j.contract_version);

-- name: ec2_seconds_this_month
SELECT COALESCE(SUM(EXTRACT(EPOCH FROM COALESCE(ended_at, last_seen_at) - started_at)), 0) AS seconds
FROM worker_sessions
WHERE started_at >= date_trunc('month', now());

-- name: take_start_cooldown
UPDATE worker_control
SET last_start_requested_at = now(), updated_at = now()
WHERE id = 1
  AND (last_start_requested_at IS NULL
       OR last_start_requested_at < now() - make_interval(secs => %(cooldown_seconds)s))
RETURNING id;
