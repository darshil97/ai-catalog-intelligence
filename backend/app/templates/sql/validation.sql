-- Catalog checks (M8, D38): what the rules read about one batch. Each "-- name:" line starts a
-- query. Values are always bound parameters.

-- name: batch
SELECT b.id, b.target_marketplace FROM batches b
WHERE b.id = %(batch_id)s AND b.owner_user_id = %(owner)s;

-- name: set_profile
UPDATE batches SET target_marketplace = %(profile)s, updated_at = now()
WHERE id = %(batch_id)s AND owner_user_id = %(owner)s
RETURNING id, target_marketplace;

-- name: products
SELECT p.id, p.product_key, p.key_source, p.identifier_flags, p.category_id, p.category_state,
       p.category_source, p.enrichment_state, p.enrichment_message, p.status, p.revision,
       p.approval_fingerprint, p.approved_at, p.approved_by, p.rejection_reason, p.duplicate_of,
       g.title AS generated_title, g.bullets AS generated_bullets, g.input_fingerprint,
       g.description AS generated_description, g.version AS content_version,
       g.status AS content_status
FROM products p
LEFT JOIN generated_content g ON g.product_id = p.id AND g.is_current
WHERE p.batch_id = %(batch_id)s AND p.owner_user_id = %(owner)s
ORDER BY p.source_order NULLS LAST, p.product_key;

-- name: attributes
-- Every attribute of every product, settled or not, with the shown value's standard form.
SELECT p.id AS product_id, a.id AS attribute_id, a.code, a.name, a.unit_family, pa.state,
       s.display AS shown, s.value_number, s.comparable, s.flags, pa.reviewer_value,
       pa.selected_value_id
FROM products p
CROSS JOIN attribute_definitions a
LEFT JOIN product_attributes pa ON pa.product_id = p.id AND pa.attribute_id = a.id
LEFT JOIN normalized_values s ON s.id = pa.selected_value_id
WHERE p.batch_id = %(batch_id)s AND p.owner_user_id = %(owner)s
ORDER BY a.name;

-- name: photos
SELECT pi.product_id, pi.image_id, i.duplicate_group_id, f.original_name AS image_name
FROM product_images pi
JOIN products p ON p.id = pi.product_id
JOIN images i ON i.id = pi.image_id
JOIN source_files f ON f.id = i.source_file_id
WHERE p.batch_id = %(batch_id)s AND p.owner_user_id = %(owner)s;

-- name: variant_pairs
SELECT product_a_id, product_b_id, status FROM variant_candidates
WHERE batch_id = %(batch_id)s;

-- name: open_reviews
-- Open decisions per product: conflicts and categories by product, photos by candidate.
SELECT t.product_id, t.kind FROM review_tasks t
WHERE t.batch_id = %(batch_id)s AND t.owner_user_id = %(owner)s AND t.status = 'open'
  AND t.kind <> 'image_match' AND t.product_id IS NOT NULL
UNION ALL
SELECT c.product_id, 'image_match' AS kind FROM review_tasks t
JOIN image_match_candidates c ON c.image_id = (t.subject_ref ->> 'image_id')::uuid
WHERE t.batch_id = %(batch_id)s AND t.owner_user_id = %(owner)s AND t.status = 'open'
  AND t.kind = 'image_match' AND c.decision = 'needs_review';

-- name: overrides
SELECT o.product_id, o.rule_code, o.subject, o.fingerprint, o.reason, o.created_by,
       o.created_at
FROM validation_overrides o
WHERE o.batch_id = %(batch_id)s AND o.owner_user_id = %(owner)s;

-- name: upsert_override
INSERT INTO validation_overrides (product_id, batch_id, owner_user_id, rule_code, subject,
                                  fingerprint, reason, created_by)
VALUES (%(product_id)s, %(batch_id)s, %(owner)s, %(rule_code)s, %(subject)s, %(fingerprint)s,
        %(reason)s, %(owner)s)
ON CONFLICT (product_id, rule_code, subject)
DO UPDATE SET fingerprint = EXCLUDED.fingerprint, reason = EXCLUDED.reason,
              created_by = EXCLUDED.created_by, created_at = now()
RETURNING id;

-- name: delete_override
DELETE FROM validation_overrides
WHERE product_id = %(product_id)s AND owner_user_id = %(owner)s AND rule_code = %(rule_code)s
  AND subject = %(subject)s
RETURNING rule_code, subject, fingerprint, reason, created_by, created_at;

-- name: product_batch
SELECT p.batch_id FROM products p WHERE p.id = %(product_id)s AND p.owner_user_id = %(owner)s;
