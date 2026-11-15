-- Normalization, categories and variants. Each "-- name:" line starts a query. Values are
-- always bound parameters. Worker queries trust the job's batch ID; API queries filter on owner.

-- name: attributes
SELECT id, code, name, data_type, unit_family FROM attribute_definitions ORDER BY code;

-- name: observations
-- Every value read for the batch's products, in file order.
SELECT o.id, o.product_id, a.code, o.raw_value, o.extractor, o.location ->> 'header' AS header,
       row_number() OVER (ORDER BY f.position, f.member_index, f.id,
                          (o.location ->> 'sheet_index')::int NULLS FIRST,
                          (o.location ->> 'row')::int NULLS FIRST,
                          (o.location ->> 'page')::int NULLS FIRST, o.id) AS position
FROM attribute_observations o
JOIN products p ON p.id = o.product_id
JOIN attribute_definitions a ON a.id = o.attribute_id
JOIN source_files f ON f.id = o.source_file_id
WHERE p.batch_id = %(batch_id)s;

-- name: insert_values
-- A value already normalized under this rule version is left as it is.
INSERT INTO normalized_values (observation_id, product_id, attribute_id, value_text,
                               value_number, unit, comparable, display, flags, source_rank,
                               rule_version)
SELECT v.observation_id, v.product_id, v.attribute_id, v.value_text, v.value_number, v.unit,
       v.comparable, v.display, v.flags, v.source_rank, %(rule_version)s
FROM jsonb_to_recordset(%(values)s::jsonb) AS v(
    observation_id uuid, product_id uuid, attribute_id uuid, value_text text,
    value_number numeric, unit text, comparable text, display text, flags jsonb,
    source_rank int)
ON CONFLICT (observation_id, attribute_id, rule_version) DO NOTHING;

-- name: batch_values
SELECT v.id, v.product_id, v.attribute_id, v.value_number, v.unit, v.comparable, v.flags,
       v.source_rank, v.observation_id
FROM normalized_values v
JOIN products p ON p.id = v.product_id
WHERE p.batch_id = %(batch_id)s AND v.rule_version = %(rule_version)s;

-- name: batch_products
SELECT id, product_key, category_source FROM products
WHERE batch_id = %(batch_id)s ORDER BY source_order;

-- name: person_decided
SELECT product_id, attribute_id FROM product_attributes pa
JOIN products p ON p.id = pa.product_id
WHERE p.batch_id = %(batch_id)s AND pa.decided_by <> 'system';

-- name: upsert_attributes
-- A person's decision stands: their rows aren't changed.
INSERT INTO product_attributes (product_id, attribute_id, selected_value_id, state, decided_by,
                                updated_at)
SELECT a.product_id, a.attribute_id, a.selected_value_id, a.state, 'system', now()
FROM jsonb_to_recordset(%(attributes)s::jsonb) AS a(
    product_id uuid, attribute_id uuid, selected_value_id uuid, state text)
ON CONFLICT (product_id, attribute_id) DO UPDATE
SET selected_value_id = EXCLUDED.selected_value_id, state = EXCLUDED.state,
    updated_at = now()
WHERE product_attributes.decided_by = 'system';

-- name: open_conflict_tasks
INSERT INTO review_tasks (owner_user_id, batch_id, product_id, kind, subject_ref, status)
SELECT %(owner)s, %(batch_id)s, pa.product_id, 'attribute_conflict',
       jsonb_build_object('product_id', pa.product_id, 'attribute_id', pa.attribute_id), 'open'
FROM product_attributes pa
JOIN products p ON p.id = pa.product_id
WHERE p.batch_id = %(batch_id)s AND pa.state = 'conflict' AND pa.decided_by = 'system'
-- A task closed because the sources came to agree opens again if they disagree again. A person's
-- decision never reaches here: their rows aren't decided_by 'system'.
ON CONFLICT ((subject_ref ->> 'product_id'), (subject_ref ->> 'attribute_id'))
    WHERE kind = 'attribute_conflict'
DO UPDATE SET status = 'open', resolved_at = NULL WHERE review_tasks.status = 'resolved';

-- name: close_stale_conflicts
UPDATE review_tasks t
SET status = 'resolved', resolved_at = now()
WHERE t.batch_id = %(batch_id)s AND t.kind = 'attribute_conflict' AND t.status = 'open'
  AND NOT EXISTS (SELECT 1 FROM product_attributes pa
                  WHERE pa.product_id = (t.subject_ref ->> 'product_id')::uuid
                    AND pa.attribute_id = (t.subject_ref ->> 'attribute_id')::uuid
                    AND pa.state = 'conflict');

-- name: product_summaries
SELECT p.id, p.product_key,
       COALESCE(array_agg(DISTINCT o.raw_value) FILTER (WHERE a.code = 'product_name'), '{}')
           AS names,
       COALESCE(array_agg(DISTINCT o.raw_value) FILTER (WHERE a.code = 'brand'), '{}')
           AS brands
FROM products p
LEFT JOIN attribute_observations o ON o.product_id = p.id
LEFT JOIN attribute_definitions a ON a.id = o.attribute_id
WHERE p.batch_id = %(batch_id)s
GROUP BY p.id
ORDER BY p.source_order;

-- name: product_photo_similarity
-- The highest similarity between any accepted photo of one product and any of another.
SELECT pa.product_id AS a, pb.product_id AS b,
       max(1 - (ea.embedding <=> eb.embedding)) AS similarity
FROM product_images pa
JOIN product_images pb ON pb.product_id > pa.product_id
JOIN image_embeddings ea ON ea.image_id = pa.image_id AND ea.embedding_model_id = %(model_id)s
JOIN image_embeddings eb ON eb.image_id = pb.image_id AND eb.embedding_model_id = %(model_id)s
JOIN products x ON x.id = pa.product_id
JOIN products y ON y.id = pb.product_id
WHERE x.batch_id = %(batch_id)s AND y.batch_id = %(batch_id)s
GROUP BY pa.product_id, pb.product_id;

-- name: clear_flagged_variants
-- A person's confirmation or dismissal is kept.
DELETE FROM variant_candidates WHERE batch_id = %(batch_id)s AND status = 'flagged';

-- name: insert_variants
INSERT INTO variant_candidates (batch_id, product_a_id, product_b_id, reason)
SELECT %(batch_id)s, v.a, v.b, v.reason
FROM jsonb_to_recordset(%(pairs)s::jsonb) AS v(a uuid, b uuid, reason jsonb)
ON CONFLICT (product_a_id, product_b_id) DO NOTHING;

-- name: categories
SELECT c.id, c.code, c.name, c.parent_id, c.position FROM categories c ORDER BY c.position;

-- name: product_for_category
SELECT p.id, p.batch_id, p.owner_user_id, p.product_key, p.category_source,
       COALESCE(json_object_agg(a.code, d.display) FILTER (WHERE a.code IS NOT NULL), '{}')
           AS shown
FROM products p
LEFT JOIN product_attributes pa ON pa.product_id = p.id
LEFT JOIN normalized_values d ON d.id = pa.selected_value_id
LEFT JOIN attribute_definitions a ON a.id = pa.attribute_id
WHERE p.id = %(product_id)s AND p.batch_id = %(batch_id)s
GROUP BY p.id;

-- name: product_texts
-- Every name, description and supplier category a source gave, for choosing a category.
SELECT a.code, array_agg(DISTINCT o.raw_value) AS values
FROM attribute_observations o
JOIN attribute_definitions a ON a.id = o.attribute_id
WHERE o.product_id = %(product_id)s
  AND a.code IN ('product_name', 'description', 'supplier_category', 'brand', 'material')
GROUP BY a.code;

-- name: set_category
UPDATE products
SET category_id = %(category_id)s, suggested_category_id = %(suggested_id)s,
    category_state = %(state)s, category_source = %(source)s,
    category_confidence = %(confidence)s, category_reason = %(reason)s, updated_at = now()
WHERE id = %(product_id)s AND (category_source IS DISTINCT FROM 'person' OR %(source)s = 'person');

-- name: open_category_task
INSERT INTO review_tasks (owner_user_id, batch_id, product_id, kind, subject_ref, status)
VALUES (%(owner)s, %(batch_id)s, %(product_id)s, 'category',
        jsonb_build_object('product_id', %(product_id)s::text), 'open')
-- Only reached for products a person hasn't categorized, so a closed task here is stale.
ON CONFLICT ((subject_ref ->> 'product_id')) WHERE kind = 'category'
DO UPDATE SET status = 'open', resolved_at = NULL WHERE review_tasks.status = 'resolved';

-- API queries.

-- name: product_attributes
SELECT a.id AS attribute_id, a.code, a.name, a.unit_family, pa.state, pa.decided_by,
       pa.decided_at, pa.reviewer_value, s.display AS shown, s.id AS selected_value_id
FROM attribute_definitions a
JOIN products p ON p.id = %(product_id)s AND p.owner_user_id = %(owner)s
LEFT JOIN product_attributes pa ON pa.product_id = p.id AND pa.attribute_id = a.id
LEFT JOIN normalized_values s ON s.id = pa.selected_value_id
ORDER BY a.name;

-- name: product_value_list
SELECT v.id, v.attribute_id, v.display, v.unit, v.flags, v.rule_version, o.raw_value,
       f.original_name AS source_file_name, o.location, o.extractor
FROM normalized_values v
JOIN products p ON p.id = v.product_id
JOIN attribute_observations o ON o.id = v.observation_id
JOIN source_files f ON f.id = o.source_file_id
WHERE v.product_id = %(product_id)s AND p.owner_user_id = %(owner)s
  AND v.rule_version = %(rule_version)s
ORDER BY v.source_rank, f.position, f.member_index, v.id;

-- name: product_category
SELECT p.category_state, p.category_source, p.category_confidence, p.category_reason,
       c.id AS category_id, c.code AS category_code, c.name AS category_name,
       s.id AS suggested_id, s.code AS suggested_code, s.name AS suggested_name
FROM products p
LEFT JOIN categories c ON c.id = p.category_id
LEFT JOIN categories s ON s.id = p.suggested_category_id
WHERE p.id = %(product_id)s AND p.owner_user_id = %(owner)s;

-- name: list_variants
SELECT v.id, v.status, v.reason, v.product_a_id, a.product_key AS product_a_key,
       v.product_b_id, b.product_key AS product_b_key, v.created_at
FROM variant_candidates v
JOIN products a ON a.id = v.product_a_id
JOIN products b ON b.id = v.product_b_id
JOIN batches t ON t.id = v.batch_id
WHERE v.batch_id = %(batch_id)s AND t.owner_user_id = %(owner)s
ORDER BY a.source_order, b.source_order
LIMIT %(limit)s;

-- name: list_other_tasks
SELECT t.id, t.kind, t.status, t.created_at, t.resolved_at, t.product_id, p.product_key,
       (t.subject_ref ->> 'attribute_id')::uuid AS attribute_id, a.name AS attribute_name,
       a.code AS attribute_code
FROM review_tasks t
JOIN products p ON p.id = t.product_id
LEFT JOIN attribute_definitions a ON a.id = (t.subject_ref ->> 'attribute_id')::uuid
WHERE t.batch_id = %(batch_id)s AND t.owner_user_id = %(owner)s
  AND t.kind IN ('attribute_conflict', 'category')
ORDER BY t.status DESC, p.source_order, a.name
LIMIT %(limit)s;

-- name: lock_other_task
SELECT t.id, t.kind, t.status, t.batch_id, t.product_id,
       (t.subject_ref ->> 'attribute_id')::uuid AS attribute_id
FROM review_tasks t
WHERE t.id = %(task_id)s AND t.owner_user_id = %(owner)s
FOR UPDATE;

-- name: lock_product_attribute
SELECT pa.id, pa.state, pa.selected_value_id, pa.reviewer_value, pa.decided_by
FROM product_attributes pa
WHERE pa.product_id = %(product_id)s AND pa.attribute_id = %(attribute_id)s
FOR UPDATE;

-- name: value_for_attribute
SELECT v.id, v.display FROM normalized_values v
WHERE v.id = %(value_id)s AND v.product_id = %(product_id)s
  AND v.attribute_id = %(attribute_id)s;

-- name: set_attribute_by_person
UPDATE product_attributes
SET selected_value_id = %(selected_value_id)s, reviewer_value = %(reviewer_value)s,
    state = 'reviewer_set', decided_by = %(actor)s, decided_at = now(), updated_at = now()
WHERE id = %(id)s;

-- name: lock_product_category
SELECT p.category_id, p.category_state, p.category_source FROM products p
WHERE p.id = %(product_id)s FOR UPDATE;
