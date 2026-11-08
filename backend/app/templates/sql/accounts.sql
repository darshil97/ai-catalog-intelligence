-- Profiles and access. Each "-- name:" line starts a query. Values are always bound parameters.

-- name: lock_profile
SELECT id, email, role, status FROM user_profiles WHERE id = %(user_id)s FOR UPDATE;

-- name: lock_profiles_for_admission
-- Serializes admissions, so two simultaneous sign-ups can't both take the last place under
-- the user cap. Only first sign-ins and waitlist checks take it. Transaction-scoped, so it's
-- safe with Neon's transaction pooling.
LOCK TABLE user_profiles IN SHARE ROW EXCLUSIVE MODE;

-- name: count_active_profiles
SELECT count(*) AS count FROM user_profiles WHERE status = 'active';

-- name: insert_profile
INSERT INTO user_profiles (id, email, role, status)
VALUES (%(user_id)s, %(email)s, %(role)s, %(status)s)
ON CONFLICT (id) DO UPDATE SET updated_at = user_profiles.updated_at
RETURNING id, email, role, status;

-- name: update_profile
UPDATE user_profiles
SET email = %(email)s, role = %(role)s, status = %(status)s, updated_at = now()
WHERE id = %(user_id)s
RETURNING id, email, role, status;
