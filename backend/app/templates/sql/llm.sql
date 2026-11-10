-- LLM call records. Each "-- name:" line starts a query. Values are always bound parameters.

-- name: find_succeeded
-- A reply is reused only by the user it was produced for.
SELECT id, response FROM model_runs
WHERE owner_user_id = %(owner)s AND input_hash = %(input_hash)s AND status = 'succeeded';

-- name: month_spend
-- Every user's recorded LLM cost since the start of this calendar month (UTC).
SELECT COALESCE(sum(cost_usd), 0)::float AS spent
FROM model_runs
WHERE created_at >= date_trunc('month', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC';

-- name: insert_run
-- Inserts nothing when the same request already succeeded, so the caller reads that one.
INSERT INTO model_runs (owner_user_id, job_id, task, provider, model_id, prompt_version,
                        input_hash, status, response, input_tokens, output_tokens, cost_usd,
                        latency_ms, error)
VALUES (%(owner)s, %(job_id)s, %(task)s, %(provider)s, %(model_id)s, %(prompt_version)s,
        %(input_hash)s, %(status)s, %(response)s, %(input_tokens)s, %(output_tokens)s,
        %(cost_usd)s, %(latency_ms)s, %(error)s)
ON CONFLICT (owner_user_id, input_hash) WHERE status = 'succeeded' DO NOTHING
RETURNING id;
