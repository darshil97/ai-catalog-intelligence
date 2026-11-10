-- Spreadsheet extraction, run by the worker. Each "-- name:" line starts a query. Values are
-- always bound parameters. The worker trusts the job's batch ID, which only the API can set.

-- name: batch_context
SELECT b.id, b.supplier_id, b.owner_user_id,
       (SELECT l.overrides FROM user_limits l WHERE l.user_id = b.owner_user_id) AS overrides
FROM batches b
WHERE b.id = %(batch_id)s;

-- name: spreadsheet_files
-- File order decides which products are kept: upload order, then order inside a ZIP.
SELECT id, batch_id, owner_user_id, s3_key, original_name, extension, size_bytes, sha256
FROM source_files
WHERE batch_id = %(batch_id)s AND kind = 'spreadsheet' AND status = 'ingested'
ORDER BY position, member_index, id;

-- name: attributes
SELECT id, code, name, data_type, COALESCE(rules ->> 'description', name) AS description
FROM attribute_definitions
ORDER BY code;

-- name: find_saved_mapping
SELECT mapping FROM column_mappings
WHERE supplier_id = %(supplier_id)s AND header_fingerprint = %(fingerprint)s;

-- name: save_mapping
INSERT INTO column_mappings (supplier_id, owner_user_id, header_fingerprint, mapping)
VALUES (%(supplier_id)s, %(owner)s, %(fingerprint)s, %(mapping)s)
ON CONFLICT (supplier_id, header_fingerprint)
DO UPDATE SET mapping = EXCLUDED.mapping, updated_at = now();

-- name: clear_batch_products
-- A batch that is processed again starts from nothing. Observations go with their products.
DELETE FROM products WHERE batch_id = %(batch_id)s;

-- name: clear_batch_sheets
DELETE FROM spreadsheet_sheets WHERE batch_id = %(batch_id)s;

-- name: clear_batch_rows
DELETE FROM source_records r
USING source_files f
WHERE f.id = r.source_file_id AND f.batch_id = %(batch_id)s AND r.record_type = 'row';

-- name: insert_sheets
INSERT INTO spreadsheet_sheets (id, source_file_id, batch_id, sheet_index, sheet_name, hidden,
                                status, skip_reason, skip_message, header_row, header_rows,
                                columns, mapping_source, mapping_message, row_count,
                                rows_without_identifier, duplicate_rows, rows_over_cap,
                                unreadable_rows, chunk_starts, chunk_rows)
SELECT s.id, s.source_file_id, %(batch_id)s, s.sheet_index, s.sheet_name, s.hidden, s.status,
       s.skip_reason, s.skip_message, s.header_row, s.header_rows, s.columns, s.mapping_source,
       s.mapping_message, s.row_count, s.rows_without_identifier, s.duplicate_rows,
       s.rows_over_cap, s.unreadable_rows, s.chunk_starts, s.chunk_rows
FROM jsonb_to_recordset(%(sheets)s::jsonb) AS s(
    id uuid, source_file_id uuid, sheet_index int, sheet_name text, hidden boolean, status text,
    skip_reason text, skip_message text, header_row int, header_rows int, columns jsonb,
    mapping_source text, mapping_message text, row_count int, rows_without_identifier int,
    duplicate_rows int, rows_over_cap int, unreadable_rows int, chunk_starts jsonb,
    chunk_rows int);

-- name: insert_products
INSERT INTO products (batch_id, owner_user_id, product_key, key_source, supplier_sku, match_key,
                      identifier_flags, source_order)
SELECT %(batch_id)s, %(owner)s, p.product_key, p.key_source, p.supplier_sku, p.match_key,
       p.identifier_flags, p.source_order
FROM jsonb_to_recordset(%(products)s::jsonb) AS p(
    product_key text, key_source text, supplier_sku text, match_key text,
    identifier_flags jsonb, source_order int);

-- name: record_product_counts
UPDATE pipeline_runs
SET products_found = %(found)s, products_kept = %(kept)s, products_discarded = %(discarded)s
WHERE id = %(run_id)s;

-- name: sheet_for_rows
SELECT s.id, s.sheet_index, s.sheet_name, s.status, s.header_row, s.columns,
       f.id AS file_id, f.batch_id, f.s3_key, f.extension, f.size_bytes, f.sha256
FROM spreadsheet_sheets s
JOIN source_files f ON f.id = s.source_file_id
WHERE s.id = %(sheet_id)s AND s.batch_id = %(batch_id)s;

-- name: sheets_to_extract
SELECT id, chunk_starts, chunk_rows FROM spreadsheet_sheets
WHERE batch_id = %(batch_id)s AND status = 'used'
ORDER BY id;

-- name: batch_product_keys
SELECT id, product_key FROM products WHERE batch_id = %(batch_id)s;

-- name: insert_rows
-- A row read twice is stored once: its ID comes from its sheet and row number.
INSERT INTO source_records (id, source_file_id, record_type, location, raw_data)
SELECT r.id, %(file_id)s, 'row', r.location, r.raw_data
FROM jsonb_to_recordset(%(rows)s::jsonb) AS r(id uuid, location jsonb, raw_data jsonb)
ON CONFLICT DO NOTHING;

-- name: insert_observations
-- Never updates: an observation that is already there is left exactly as it was.
INSERT INTO attribute_observations (product_id, attribute_id, raw_value, source_file_id,
                                    source_record_id, location, extractor, model_run_id,
                                    confidence)
SELECT o.product_id, o.attribute_id, o.raw_value, %(file_id)s, o.source_record_id, o.location,
       %(extractor)s, m.id, o.confidence
FROM jsonb_to_recordset(%(observations)s::jsonb) AS o(
    product_id uuid, attribute_id uuid, raw_value text, source_record_id uuid, location jsonb,
    model_run_id uuid, confidence numeric)
LEFT JOIN model_runs m ON m.id = o.model_run_id
ON CONFLICT DO NOTHING;

-- name: clear_job_notes
-- A job that runs again reports its rows afresh.
DELETE FROM processing_errors WHERE job_id = %(job_id)s;

-- name: insert_notes
INSERT INTO processing_errors (pipeline_run_id, job_id, source_file_id, location, error_code,
                               message)
SELECT %(run_id)s, %(job_id)s, %(file_id)s, n.location, n.error_code, n.message
FROM jsonb_to_recordset(%(notes)s::jsonb) AS n(location jsonb, error_code text, message text);
