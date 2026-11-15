-- Enrichment: generated titles, descriptions and bullets (M7). Each "-- name:" line starts a
-- query. Values are always bound parameters.

-- name: categories
SELECT id, code, name, parent_id FROM categories;

-- name: products
-- A batch's products (or one of them) with their current text, if any.
SELECT p.id, p.batch_id, p.owner_user_id, p.product_key, p.category_id, p.category_state,
       p.category_source, p.enrichment_state, p.enrichment_message, p.enrichment_at,
       g.version, g.input_fingerprint, g.title, g.created_at AS generated_at,
       (SELECT COALESCE(max(v.version), 0) FROM generated_content v WHERE v.product_id = p.id)
           AS versions
FROM products p
LEFT JOIN generated_content g ON g.product_id = p.id AND g.is_current
WHERE p.batch_id = %(batch_id)s AND p.owner_user_id = %(owner)s
  AND (%(product_id)s::uuid IS NULL OR p.id = %(product_id)s::uuid)
ORDER BY p.source_order NULLS LAST, p.product_key;

-- name: attributes
-- Every attribute of a batch's products (or one product), settled or not.
SELECT p.id AS product_id, a.id AS attribute_id, a.code, a.name, a.unit_family, pa.state,
       s.display AS shown, s.value_number, pa.reviewer_value, pa.selected_value_id
FROM products p
CROSS JOIN attribute_definitions a
LEFT JOIN product_attributes pa ON pa.product_id = p.id AND pa.attribute_id = a.id
LEFT JOIN normalized_values s ON s.id = pa.selected_value_id
WHERE p.batch_id = %(batch_id)s AND p.owner_user_id = %(owner)s
  AND (%(product_id)s::uuid IS NULL OR p.id = %(product_id)s::uuid)
ORDER BY a.name;

-- name: lock_product
SELECT id FROM products WHERE id = %(product_id)s FOR UPDATE;

-- name: clear_current
UPDATE generated_content SET is_current = false
WHERE product_id = %(product_id)s AND is_current;

-- name: insert_content
INSERT INTO generated_content (product_id, batch_id, owner_user_id, version, title, description,
                               bullets, claims, cited_attribute_ids, input_fingerprint,
                               rules_version, model_run_id)
SELECT %(product_id)s, %(batch_id)s, %(owner)s,
       COALESCE((SELECT max(version) FROM generated_content WHERE product_id = %(product_id)s), 0)
           + 1,
       %(title)s, %(description)s, %(bullets)s, %(claims)s, %(cited)s::uuid[], %(fingerprint)s,
       %(rules_version)s, %(model_run_id)s
RETURNING id, version;

-- name: set_state
UPDATE products
SET enrichment_state = %(state)s, enrichment_message = %(message)s, enrichment_at = now(),
    updated_at = now()
WHERE id = %(product_id)s;

-- API queries.

-- name: current_content
SELECT g.id, g.version, g.title, g.description, g.bullets, g.claims, g.input_fingerprint,
       g.rules_version, g.status, g.created_by, g.created_at, g.model_run_id,
       m.model_id, m.prompt_version
FROM generated_content g
JOIN products p ON p.id = g.product_id
LEFT JOIN model_runs m ON m.id = g.model_run_id
WHERE g.product_id = %(product_id)s AND p.owner_user_id = %(owner)s AND g.is_current;

-- name: latest_run
-- The batch's latest enrich run. Enrich runs aren't tied to the batch, so it's in the payload.
SELECT r.id, r.status, r.pause_reason, r.created_at, r.started_at, r.finished_at,
       count(j.id) FILTER (WHERE j.status = 'blocked_quota') AS blocked,
       count(j.id) FILTER (WHERE j.status IN ('pending', 'running')) AS waiting,
       count(j.id) FILTER (WHERE j.job_type = 'enrich_product' AND j.status = 'succeeded')
           AS done,
       count(j.id) FILTER (WHERE j.job_type = 'enrich_product') AS total
FROM pipeline_runs r
JOIN pipeline_stage_runs s ON s.pipeline_run_id = r.id AND s.position = 0
LEFT JOIN jobs j ON j.pipeline_run_id = r.id
WHERE r.pipeline = 'enrich' AND r.owner_user_id = %(owner)s
  AND s.payload ->> 'batch_id' = %(batch_id)s::text
GROUP BY r.id
ORDER BY r.created_at DESC
LIMIT 1;

-- name: open_reviews
SELECT count(*) AS count FROM review_tasks
WHERE batch_id = %(batch_id)s AND owner_user_id = %(owner)s AND status = 'open'
  AND kind IN ('attribute_conflict', 'category');
