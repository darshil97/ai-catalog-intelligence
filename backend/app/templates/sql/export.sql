-- Exports (M10, D41). Each "-- name:" line starts a query. Values are always bound parameters.

-- name: batch
SELECT b.id, b.name, b.target_marketplace, s.name AS supplier_name
FROM batches b JOIN suppliers s ON s.id = b.supplier_id
WHERE b.id = %(batch_id)s AND b.owner_user_id = %(owner)s;

-- name: attributes
SELECT code, name, unit_family, data_type FROM attribute_definitions ORDER BY name;

-- name: photos
-- Accepted photos of a batch's products, in their order, with the stored original.
SELECT pi.product_id, pi.image_id, pi.position, f.s3_key, f.original_name, i.width, i.height
FROM product_images pi
JOIN products p ON p.id = pi.product_id
JOIN images i ON i.id = pi.image_id
JOIN source_files f ON f.id = i.source_file_id
WHERE p.batch_id = %(batch_id)s AND p.owner_user_id = %(owner)s AND f.s3_key IS NOT NULL
ORDER BY pi.product_id, pi.position, (COALESCE(i.width, 0) * COALESCE(i.height, 0)) DESC;

-- name: sources
-- Every value read for a batch's products, with where it came from.
SELECT o.product_id, a.code, a.name AS attribute, o.raw_value, f.original_name AS file,
       o.location, o.extractor
FROM attribute_observations o
JOIN products p ON p.id = o.product_id
JOIN attribute_definitions a ON a.id = o.attribute_id
JOIN source_files f ON f.id = o.source_file_id
WHERE p.batch_id = %(batch_id)s AND p.owner_user_id = %(owner)s
ORDER BY p.source_order, a.name, f.position, o.id;

-- name: insert_export
INSERT INTO exports (id, batch_id, owner_user_id, format, status, s3_key, profile, product_ids,
                     product_count, left_out_count, links_expire_at, size_bytes, sha256, notes,
                     created_by)
VALUES (%(id)s, %(batch_id)s, %(owner)s, %(format)s, 'ready', %(s3_key)s, %(profile)s,
        %(product_ids)s::uuid[], %(product_count)s, %(left_out_count)s, %(links_expire_at)s,
        %(size_bytes)s, %(sha256)s, %(notes)s, %(owner)s)
RETURNING id, batch_id, format, status, profile, product_count, left_out_count,
          links_expire_at, size_bytes, notes, created_at;

-- name: list_exports
SELECT e.id, e.batch_id, e.format, e.status, e.profile, e.product_count, e.left_out_count,
       e.links_expire_at, e.size_bytes, e.notes, e.created_at
FROM exports e
WHERE e.batch_id = %(batch_id)s AND e.owner_user_id = %(owner)s
ORDER BY e.created_at DESC
LIMIT 50;

-- name: get_export
SELECT e.id, e.s3_key, e.format, e.batch_id FROM exports e
WHERE e.id = %(export_id)s AND e.owner_user_id = %(owner)s AND e.status = 'ready';

-- name: lock_variant
SELECT v.id, v.batch_id, v.product_a_id, v.product_b_id, v.status
FROM variant_candidates v
JOIN batches b ON b.id = v.batch_id
WHERE v.id = %(variant_id)s AND b.owner_user_id = %(owner)s
FOR UPDATE OF v;

-- name: set_variant
UPDATE variant_candidates SET status = %(status)s WHERE id = %(variant_id)s;
