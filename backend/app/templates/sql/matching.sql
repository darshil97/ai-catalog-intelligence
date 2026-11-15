-- Image association and review. Each "-- name:" line starts a query. Values are always bound
-- parameters. Worker queries trust the job's batch ID; API queries filter on the owner.

-- name: images_for_matching
-- Each image with its name and path, OCR text, near-duplicate group, and the products whose
-- values came from the PDF page it was found on.
SELECT i.id, f.original_name AS name,
       concat_ws(' / ', p.original_name, f.archive_member, f.original_name) AS path,
       COALESCE((SELECT o.text FROM ocr_results o
                 WHERE o.source_file_id = f.id AND o.page IS NULL
                 ORDER BY o.created_at DESC LIMIT 1), '') AS text,
       i.duplicate_group_id,
       COALESCE((SELECT array_agg(DISTINCT ob.product_id)
                 FROM attribute_observations ob
                 JOIN source_records r ON r.id = ob.source_record_id
                 WHERE p.kind = 'pdf' AND r.source_file_id = p.id AND r.record_type = 'page'
                   AND (r.location ->> 'page')::int
                       = (regexp_match(f.archive_member, '^page-(\d+)-image'))[1]::int),
                '{}') AS page_products
FROM images i
JOIN source_files f ON f.id = i.source_file_id
LEFT JOIN source_files p ON p.id = f.parent_file_id
WHERE i.batch_id = %(batch_id)s AND i.status = 'processed'
ORDER BY f.position, f.member_index, f.id;

-- name: products_for_matching
SELECT p.id, p.product_key,
       COALESCE(array_agg(DISTINCT o.raw_value)
                FILTER (WHERE a.code = ANY(%(identifiers)s)), '{}') AS codes,
       COALESCE(array_agg(DISTINCT o.raw_value)
                FILTER (WHERE a.code IN ('brand', 'product_name')), '{}') AS words
FROM products p
LEFT JOIN attribute_observations o ON o.product_id = p.id
LEFT JOIN attribute_definitions a ON a.id = o.attribute_id
WHERE p.batch_id = %(batch_id)s
GROUP BY p.id
ORDER BY p.source_order;

-- name: human_decisions
SELECT image_id, product_id, decision FROM image_match_candidates
WHERE batch_id = %(batch_id)s AND decided_by <> 'system';

-- name: clear_system_candidates
DELETE FROM image_match_candidates WHERE batch_id = %(batch_id)s AND decided_by = 'system';

-- name: upsert_candidates
-- A person's decision is kept: only the score and evidence of their rows are refreshed.
INSERT INTO image_match_candidates (batch_id, image_id, product_id, score, signals, decision,
                                    decided_by)
SELECT %(batch_id)s, c.image_id, c.product_id, c.score, c.signals, c.decision, 'system'
FROM jsonb_to_recordset(%(candidates)s::jsonb) AS c(
    image_id uuid, product_id uuid, score numeric, signals jsonb, decision text)
ON CONFLICT (image_id, product_id) DO UPDATE
SET score = EXCLUDED.score, signals = EXCLUDED.signals;

-- name: clear_product_images
DELETE FROM product_images pi USING images i
WHERE i.id = pi.image_id AND i.batch_id = %(batch_id)s;

-- name: rebuild_product_images
-- Accepted links, in file order per product.
INSERT INTO product_images (product_id, image_id, position, candidate_id)
SELECT c.product_id, c.image_id,
       row_number() OVER (PARTITION BY c.product_id ORDER BY f.position, f.member_index, f.id),
       c.id
FROM image_match_candidates c
JOIN images i ON i.id = c.image_id
JOIN source_files f ON f.id = i.source_file_id
WHERE c.batch_id = %(batch_id)s AND c.decision IN ('auto_accepted', 'accepted');

-- name: open_review_tasks
-- One task per image that has candidates to review and no decision by a person yet.
INSERT INTO review_tasks (owner_user_id, batch_id, kind, subject_ref, status)
SELECT %(owner)s, %(batch_id)s, 'image_match', jsonb_build_object('image_id', c.image_id),
       'open'
FROM image_match_candidates c
WHERE c.batch_id = %(batch_id)s AND c.decision = 'needs_review'
  AND NOT EXISTS (SELECT 1 FROM image_match_candidates h
                  WHERE h.image_id = c.image_id AND h.decided_by <> 'system')
GROUP BY c.image_id
ON CONFLICT (kind, (subject_ref ->> 'image_id')) DO NOTHING;

-- name: close_stale_tasks
-- An open task whose image no longer has anything to review is closed by the system.
UPDATE review_tasks t
SET status = 'resolved', resolved_at = now()
WHERE t.batch_id = %(batch_id)s AND t.status = 'open' AND t.kind = 'image_match'
  AND NOT EXISTS (SELECT 1 FROM image_match_candidates c
                  WHERE c.image_id = (t.subject_ref ->> 'image_id')::uuid
                    AND c.decision = 'needs_review');

-- name: record_match_counts
WITH counts AS (
    SELECT count(*) FILTER (WHERE EXISTS (
               SELECT 1 FROM image_match_candidates c
               WHERE c.image_id = i.id AND c.decision IN ('auto_accepted', 'accepted')))
               AS matched,
           count(*) FILTER (WHERE EXISTS (
               SELECT 1 FROM review_tasks t
               WHERE t.kind = 'image_match' AND t.status = 'open'
                 AND (t.subject_ref ->> 'image_id')::uuid = i.id)) AS to_review,
           count(*) FILTER (WHERE NOT EXISTS (
               SELECT 1 FROM image_match_candidates c
               WHERE c.image_id = i.id AND c.decision <> 'rejected')) AS unmatched
    FROM images i
    WHERE i.batch_id = %(batch_id)s AND i.status = 'processed'
)
UPDATE pipeline_runs r
SET images_matched = c.matched, images_to_review = c.to_review, images_unmatched = c.unmatched
FROM counts c
WHERE r.id = %(run_id)s;

-- name: photos_needing_values
-- Accepted links whose photo hasn't been read for the product yet.
SELECT c.id FROM image_match_candidates c
WHERE c.batch_id = %(batch_id)s AND c.decision IN ('auto_accepted', 'accepted')
  AND c.values_read_at IS NULL
ORDER BY c.created_at, c.id;

-- name: photo_for_values
SELECT c.id, c.decision, c.values_read_at, c.product_id, c.batch_id, p.product_key,
       i.source_file_id, f.original_name,
       r.id AS record_id, r.raw_data,
       EXISTS (SELECT 1 FROM attribute_observations o
               WHERE o.product_id = c.product_id AND o.source_file_id = i.source_file_id)
           AS already_read
FROM image_match_candidates c
JOIN products p ON p.id = c.product_id
JOIN images i ON i.id = c.image_id
JOIN source_files f ON f.id = i.source_file_id
LEFT JOIN source_records r ON r.source_file_id = i.source_file_id AND r.record_type = 'page'
WHERE c.id = %(candidate_id)s AND c.batch_id = %(batch_id)s;

-- name: mark_values_read
UPDATE image_match_candidates SET values_read_at = now() WHERE id = %(candidate_id)s;

-- API queries.

-- name: list_matches
SELECT c.id, c.image_id, i.source_file_id, f.original_name AS image_name, c.product_id,
       p.product_key, c.score, c.signals, c.decision, c.decided_by, c.decided_at,
       c.values_read_at
FROM image_match_candidates c
JOIN images i ON i.id = c.image_id
JOIN source_files f ON f.id = i.source_file_id
JOIN products p ON p.id = c.product_id
JOIN batches b ON b.id = c.batch_id
WHERE c.batch_id = %(batch_id)s AND b.owner_user_id = %(owner)s
ORDER BY f.position, f.member_index, f.id, c.score DESC
LIMIT %(limit)s;

-- name: list_review_tasks
SELECT t.id, t.status, t.created_at, t.resolved_at, (t.subject_ref ->> 'image_id')::uuid
           AS image_id,
       i.source_file_id, f.original_name AS image_name, i.thumbnail_s3_key
FROM review_tasks t
JOIN images i ON i.id = (t.subject_ref ->> 'image_id')::uuid
JOIN source_files f ON f.id = i.source_file_id
WHERE t.batch_id = %(batch_id)s AND t.owner_user_id = %(owner)s AND t.kind = 'image_match'
ORDER BY t.status DESC, f.position, f.member_index, f.id
LIMIT %(limit)s;

-- name: task_candidates
SELECT c.id, c.image_id, c.product_id, p.product_key, c.score, c.signals, c.decision,
       c.decided_by
FROM image_match_candidates c
JOIN products p ON p.id = c.product_id
WHERE c.image_id = ANY(%(image_ids)s)
ORDER BY c.score DESC, p.source_order;

-- name: lock_task
SELECT t.id, t.batch_id, t.status, (t.subject_ref ->> 'image_id')::uuid AS image_id
FROM review_tasks t
WHERE t.id = %(task_id)s AND t.owner_user_id = %(owner)s
FOR UPDATE;

-- name: lock_image_candidates
SELECT id, product_id, decision, decided_by, score FROM image_match_candidates
WHERE image_id = %(image_id)s
FOR UPDATE;

-- name: set_decision
UPDATE image_match_candidates
SET decision = %(decision)s, decided_by = %(actor)s, decided_at = now()
WHERE id = %(candidate_id)s;

-- name: resolve_task
UPDATE review_tasks SET status = 'resolved', resolved_at = now() WHERE id = %(task_id)s;

-- name: insert_event
INSERT INTO review_events (actor_user_id, batch_id, entity_type, entity_id, action, before,
                           after)
VALUES (%(actor)s, %(batch_id)s, %(entity_type)s, %(entity_id)s, %(action)s, %(before)s,
        %(after)s);

-- name: product_images
SELECT pi.image_id, pi.position, i.thumbnail_s3_key, f.original_name AS image_name,
       c.decision, c.decided_by
FROM product_images pi
JOIN products p ON p.id = pi.product_id
JOIN images i ON i.id = pi.image_id
JOIN source_files f ON f.id = i.source_file_id
JOIN image_match_candidates c ON c.id = pi.candidate_id
WHERE pi.product_id = %(product_id)s AND p.owner_user_id = %(owner)s
ORDER BY pi.position;

-- name: pending_photo_values
SELECT count(*) AS pending FROM image_match_candidates c
JOIN batches b ON b.id = c.batch_id
WHERE c.batch_id = %(batch_id)s AND b.owner_user_id = %(owner)s
  AND c.decision IN ('auto_accepted', 'accepted') AND c.values_read_at IS NULL;
