-- Human review (M9, D40): value and copy edits, approval, rejection, duplicates and history.
-- Each "-- name:" line starts a query. Values are always bound parameters.

-- name: lock_product
SELECT p.id, p.batch_id, p.product_key, p.status, p.revision, p.approval_fingerprint,
       p.duplicate_of, p.rejection_reason
FROM products p
WHERE p.id = %(product_id)s AND p.owner_user_id = %(owner)s
FOR UPDATE;

-- name: bump_revision
UPDATE products SET revision = revision + 1, updated_at = now()
WHERE id = %(product_id)s
RETURNING revision;

-- name: attribute
SELECT id, code, name, data_type, unit_family FROM attribute_definitions WHERE code = %(code)s;

-- name: current_value
SELECT pa.id, pa.state, COALESCE(s.display, pa.reviewer_value ->> 'text') AS shown
FROM product_attributes pa
LEFT JOIN normalized_values s ON s.id = pa.selected_value_id
WHERE pa.product_id = %(product_id)s AND pa.attribute_id = %(attribute_id)s
FOR UPDATE OF pa;

-- name: set_value
-- A person's value (or a cleared one: state missing), which processing never overwrites.
INSERT INTO product_attributes (product_id, attribute_id, selected_value_id, reviewer_value,
                                state, decided_by, decided_at)
VALUES (%(product_id)s, %(attribute_id)s, NULL, %(reviewer_value)s, %(state)s, %(actor)s, now())
ON CONFLICT (product_id, attribute_id) DO UPDATE
SET selected_value_id = NULL, reviewer_value = EXCLUDED.reviewer_value, state = EXCLUDED.state,
    decided_by = EXCLUDED.decided_by, decided_at = now(), updated_at = now()
RETURNING id;

-- name: resolve_conflict
UPDATE review_tasks SET status = 'resolved', resolved_at = now()
WHERE kind = 'attribute_conflict' AND status = 'open'
  AND subject_ref ->> 'product_id' = %(product_id)s::text
  AND subject_ref ->> 'attribute_id' = %(attribute_id)s::text
RETURNING id;

-- name: insert_edited_copy
INSERT INTO generated_content (product_id, batch_id, owner_user_id, version, title, description,
                               bullets, claims, cited_attribute_ids, input_fingerprint,
                               rules_version, model_run_id, status, created_by)
SELECT %(product_id)s, %(batch_id)s, %(owner)s,
       COALESCE((SELECT max(version) FROM generated_content WHERE product_id = %(product_id)s), 0)
           + 1,
       %(title)s, %(description)s, %(bullets)s, '[]'::jsonb, '{}'::uuid[], %(fingerprint)s,
       'edited', NULL, 'edited', %(owner)s
RETURNING id, version;

-- name: copy_written
UPDATE products SET enrichment_state = 'generated', enrichment_message = NULL,
                    enrichment_at = now()
WHERE id = %(product_id)s;

-- name: set_status
UPDATE products
SET status = %(status)s,
    approved_at = CASE WHEN %(status)s = 'approved' THEN now() END,
    approved_by = CASE WHEN %(status)s = 'approved' THEN %(actor)s END,
    approval_fingerprint = %(fingerprint)s,
    rejected_at = CASE WHEN %(status)s = 'rejected' THEN now() END,
    rejected_by = CASE WHEN %(status)s = 'rejected' THEN %(actor)s END,
    rejection_reason = %(reason)s,
    duplicate_of = %(duplicate_of)s,
    revision = revision + 1, updated_at = now()
WHERE id = %(product_id)s
RETURNING revision;

-- name: history
-- Every recorded change to one product, newest first.
SELECT e.created_at, e.actor_user_id, e.entity_type, e.action, e.before, e.after,
       a.name AS attribute, f.original_name AS image
FROM review_events e
LEFT JOIN product_attributes pa
       ON e.entity_type = 'product_attribute' AND pa.id = e.entity_id
LEFT JOIN attribute_definitions a ON a.id = pa.attribute_id
LEFT JOIN image_match_candidates c
       ON e.entity_type = 'image_match_candidate' AND c.id = e.entity_id
LEFT JOIN images i ON i.id = c.image_id
LEFT JOIN source_files f ON f.id = i.source_file_id
WHERE e.batch_id = %(batch_id)s
  AND (pa.product_id = %(product_id)s
       OR c.product_id = %(product_id)s
       OR (e.entity_type IN ('product_category', 'validation_issue', 'generated_content',
                             'product_status', 'duplicate', 'variant')
           AND e.entity_id = %(product_id)s))
ORDER BY e.created_at DESC
LIMIT 200;

-- name: copy_versions
SELECT version, status, created_by, created_at FROM generated_content
WHERE product_id = %(product_id)s
ORDER BY version DESC;

-- name: review_summary
-- Batches with review items waiting, newest activity first.
SELECT b.id AS batch_id, b.name AS batch_name, s.name AS supplier_name, count(*) AS open
FROM review_tasks t
JOIN batches b ON b.id = t.batch_id
JOIN suppliers s ON s.id = b.supplier_id
WHERE t.owner_user_id = %(owner)s AND t.status = 'open'
GROUP BY b.id, b.name, s.name
ORDER BY max(t.created_at) DESC;
