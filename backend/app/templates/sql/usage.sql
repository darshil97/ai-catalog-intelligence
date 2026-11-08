-- Usage ledger and limits. Each "-- name:" line starts a query. Values are always bound parameters.

-- name: lock_user
-- Every submission for one user takes this lock first, so simultaneous submissions are checked
-- one at a time and can't both pass a limit.
SELECT id FROM user_profiles WHERE id = %(user_id)s FOR UPDATE;

-- name: get_limits_and_usage
-- One round trip for everything the account page shows. Restored entries don't count.
SELECT
    (SELECT overrides FROM user_limits WHERE user_id = %(user_id)s) AS overrides,
    count(*) FILTER (WHERE u.kind = 'submission') AS submissions_lifetime,
    count(*) FILTER (
        WHERE u.kind = 'submission' AND u.created_at > now() - interval '24 hours'
    ) AS submissions_per_24_hours
FROM usage_ledger u
WHERE u.user_id = %(user_id)s
  AND NOT u.is_restoration
  AND NOT EXISTS (SELECT 1 FROM usage_ledger r WHERE r.restores_entry_id = u.id);

-- name: insert_entry
INSERT INTO usage_ledger (user_id, kind, quantity, cost_usd, pipeline_run_id)
VALUES (%(user_id)s, %(kind)s, %(quantity)s, %(cost_usd)s, %(pipeline_run_id)s)
RETURNING id;

-- name: insert_restoration
-- Copies the original entry, so a restoration can only ever cancel exactly what was charged.
INSERT INTO usage_ledger (user_id, kind, quantity, cost_usd, pipeline_run_id, is_restoration,
                          restores_entry_id)
SELECT user_id, kind, quantity, cost_usd, pipeline_run_id, true, id
FROM usage_ledger
WHERE id = %(entry_id)s AND user_id = %(user_id)s AND NOT is_restoration
ON CONFLICT (restores_entry_id) WHERE restores_entry_id IS NOT NULL DO NOTHING
RETURNING id;
