-- Suppliers and batches. Each "-- name:" line starts a query. Values are always bound parameters.
-- Every query filters on owner_user_id, so another user's rows look exactly like missing ones.

-- name: list_suppliers
SELECT s.id, s.name, s.created_at, s.updated_at,
       (SELECT count(*) FROM batches b WHERE b.supplier_id = s.id) AS batch_count
FROM suppliers s
WHERE s.owner_user_id = %(owner)s
ORDER BY lower(s.name), s.id
LIMIT %(limit)s;

-- name: insert_supplier
INSERT INTO suppliers (owner_user_id, name)
VALUES (%(owner)s, %(name)s)
RETURNING id, name, created_at, updated_at, 0::bigint AS batch_count;

-- name: get_supplier
SELECT s.id, s.name, s.created_at, s.updated_at,
       (SELECT count(*) FROM batches b WHERE b.supplier_id = s.id) AS batch_count
FROM suppliers s
WHERE s.id = %(supplier_id)s AND s.owner_user_id = %(owner)s;

-- name: rename_supplier
UPDATE suppliers s
SET name = %(name)s, updated_at = now()
WHERE s.id = %(supplier_id)s AND s.owner_user_id = %(owner)s
RETURNING s.id, s.name, s.created_at, s.updated_at,
          (SELECT count(*) FROM batches b WHERE b.supplier_id = s.id) AS batch_count;

-- name: delete_supplier
-- Refuses a supplier with batches. The foreign key also refuses it if a batch appears
-- between this check and the delete.
WITH target AS (
    SELECT s.id,
           EXISTS (SELECT 1 FROM batches b WHERE b.supplier_id = s.id) AS has_batches
    FROM suppliers s
    WHERE s.id = %(supplier_id)s AND s.owner_user_id = %(owner)s
),
deleted AS (
    DELETE FROM suppliers s
    USING target t
    WHERE s.id = t.id AND NOT t.has_batches
    RETURNING s.id
)
SELECT t.has_batches, (SELECT count(*) FROM deleted) AS deleted
FROM target t;

-- name: list_batches
-- One round trip: the supplier, plus its batches. No row means no such supplier for this owner.
SELECT s.id AS supplier_id, s.name AS supplier_name,
       COALESCE(
           (SELECT json_agg(b ORDER BY b.created_at DESC, b.id)
            FROM (SELECT id, supplier_id, name, status, expires_at, created_at, updated_at
                  FROM batches
                  WHERE supplier_id = s.id AND owner_user_id = %(owner)s
                  ORDER BY created_at DESC, id
                  LIMIT %(limit)s) b),
           '[]'::json
       ) AS batches
FROM suppliers s
WHERE s.id = %(supplier_id)s AND s.owner_user_id = %(owner)s;

-- name: insert_batch
-- Inserts nothing when the supplier isn't this owner's.
INSERT INTO batches (supplier_id, owner_user_id, name, expires_at)
SELECT s.id, s.owner_user_id, %(name)s, now() + make_interval(days => %(retention_days)s)
FROM suppliers s
WHERE s.id = %(supplier_id)s AND s.owner_user_id = %(owner)s
RETURNING id, supplier_id, name, status, expires_at, created_at, updated_at,
          (SELECT name FROM suppliers WHERE id = supplier_id) AS supplier_name;

-- name: get_batch
SELECT b.id, b.supplier_id, s.name AS supplier_name, b.name, b.status, b.expires_at,
       b.created_at, b.updated_at
FROM batches b
JOIN suppliers s ON s.id = b.supplier_id
WHERE b.id = %(batch_id)s AND b.owner_user_id = %(owner)s;

-- name: rename_batch
UPDATE batches b
SET name = %(name)s, updated_at = now()
FROM suppliers s
WHERE b.id = %(batch_id)s AND b.owner_user_id = %(owner)s AND s.id = b.supplier_id
RETURNING b.id, b.supplier_id, s.name AS supplier_name, b.name, b.status, b.expires_at,
          b.created_at, b.updated_at;

-- name: delete_batch
-- Refuses a batch that is queued or processing. Returns its stored keys so the caller can delete
-- the objects; the subquery sees the files as they were before the cascade.
WITH target AS (
    SELECT id, status FROM batches
    WHERE id = %(batch_id)s AND owner_user_id = %(owner)s
    FOR UPDATE
),
deleted AS (
    DELETE FROM batches b
    USING target t
    WHERE b.id = t.id AND t.status NOT IN ('queued', 'processing')
    RETURNING b.id
)
SELECT t.status, (SELECT count(*) FROM deleted) AS deleted,
       ARRAY(SELECT f.s3_key FROM source_files f
             WHERE f.batch_id = t.id AND f.s3_key IS NOT NULL) AS keys
FROM target t;
