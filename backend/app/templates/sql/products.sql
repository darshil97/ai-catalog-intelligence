-- Products and where their values came from, read by the API. Each "-- name:" line starts a
-- query. Values are always bound parameters. Every query filters on owner_user_id, so another
-- user's rows look exactly like missing ones.

-- name: list_products
-- In file order, which is the order the product limit kept them in.
SELECT p.id, p.batch_id, p.product_key, p.key_source, p.supplier_sku, p.identifier_flags,
       p.source_order, p.status, p.created_at,
       (SELECT o.raw_value
        FROM attribute_observations o
        JOIN attribute_definitions a ON a.id = o.attribute_id
        WHERE o.product_id = p.id AND a.code = 'product_name'
        ORDER BY o.created_at, (o.location ->> 'row')::int NULLS LAST, o.id
        LIMIT 1) AS name,
       (SELECT count(*) FROM attribute_observations o WHERE o.product_id = p.id)
           AS observation_count
FROM products p
WHERE p.batch_id = %(batch_id)s AND p.owner_user_id = %(owner)s
ORDER BY p.source_order
LIMIT %(limit)s;

-- name: get_product
SELECT p.id, p.batch_id, p.product_key, p.key_source, p.supplier_sku, p.identifier_flags,
       p.source_order, p.status, p.created_at
FROM products p
WHERE p.id = %(product_id)s AND p.owner_user_id = %(owner)s;

-- name: product_observations
-- Grouped by attribute, then in file order, so differing values for one attribute sit together.
SELECT o.id, a.code AS attribute_code, a.name AS attribute_name, o.raw_value,
       o.source_file_id, f.original_name AS source_file_name, o.source_record_id, o.location,
       o.extractor, o.confidence,
       CASE WHEN o.model_run_id IS NULL THEN 'known_name' ELSE 'llm' END AS mapped_by
FROM attribute_observations o
JOIN products p ON p.id = o.product_id
JOIN attribute_definitions a ON a.id = o.attribute_id
JOIN source_files f ON f.id = o.source_file_id
WHERE o.product_id = %(product_id)s AND p.owner_user_id = %(owner)s
ORDER BY a.name, f.position, f.member_index,
         (o.location ->> 'sheet_index')::int NULLS LAST, (o.location ->> 'row')::int NULLS LAST,
         o.id
LIMIT %(limit)s;

-- name: list_sheets
SELECT s.id, s.source_file_id, f.original_name AS source_file_name, s.sheet_index, s.sheet_name,
       s.hidden, s.status, s.skip_reason, s.skip_message, s.header_row, s.header_rows, s.columns,
       s.mapping_source, s.mapping_message, s.row_count, s.rows_without_identifier,
       s.duplicate_rows, s.rows_over_cap, s.unreadable_rows
FROM spreadsheet_sheets s
JOIN source_files f ON f.id = s.source_file_id
JOIN batches b ON b.id = s.batch_id
WHERE s.batch_id = %(batch_id)s AND b.owner_user_id = %(owner)s
ORDER BY f.position, f.member_index, s.sheet_index
LIMIT %(limit)s;

-- name: list_documents
SELECT d.source_file_id, f.original_name AS source_file_name, f.parent_file_id, d.kind,
       d.status, d.skip_reason, d.skip_message, d.pages, d.pages_with_text, d.pages_ocr,
       d.pages_unreadable, d.characters, d.product_keys, d.pages_without_product,
       d.values_kept, d.values_dropped, d.llm_source, d.llm_message
FROM document_files d
JOIN source_files f ON f.id = d.source_file_id
JOIN batches b ON b.id = d.batch_id
WHERE d.batch_id = %(batch_id)s AND b.owner_user_id = %(owner)s
ORDER BY f.position, f.member_index, f.id
LIMIT %(limit)s;
