-- PDF and photo reading, run by the worker. Each "-- name:" line starts a query. Values are
-- always bound parameters. The worker trusts the job's batch ID, which only the API can set.

-- name: files_to_read
-- Every kept PDF and image in file order, including those found inside ZIPs and PDFs.
SELECT id, batch_id, owner_user_id, s3_key, original_name, extension, kind, size_bytes, sha256
FROM source_files
WHERE batch_id = %(batch_id)s AND kind IN ('pdf', 'image') AND status = 'ingested'
ORDER BY position, member_index, id;

-- name: get_file
SELECT id, batch_id, owner_user_id, s3_key, original_name, extension, kind, size_bytes, sha256,
       status
FROM source_files
WHERE id = %(file_id)s AND batch_id = %(batch_id)s;

-- name: clear_batch_documents
DELETE FROM document_files WHERE batch_id = %(batch_id)s;

-- name: clear_file_pages
-- A file read again replaces its pages. Values read from them go with them.
DELETE FROM source_records WHERE source_file_id = %(file_id)s AND record_type = 'page';

-- name: clear_file_ocr
DELETE FROM ocr_results WHERE source_file_id = %(file_id)s;

-- name: insert_pages
INSERT INTO source_records (id, source_file_id, record_type, location, raw_data)
SELECT p.id, %(file_id)s, 'page', p.location, p.raw_data
FROM jsonb_to_recordset(%(pages)s::jsonb) AS p(id uuid, location jsonb, raw_data jsonb);

-- name: insert_ocr
INSERT INTO ocr_results (source_file_id, page, engine, engine_version, text, line_count,
                         confidence, duration_ms)
SELECT %(file_id)s, o.page, o.engine, o.engine_version, o.text, o.line_count, o.confidence,
       o.duration_ms
FROM jsonb_to_recordset(%(results)s::jsonb) AS o(
    page int, engine text, engine_version text, text text, line_count int, confidence numeric,
    duration_ms int);

-- name: upsert_document
INSERT INTO document_files (source_file_id, batch_id, kind, status, skip_reason, skip_message,
                            pages, pages_with_text, pages_ocr, pages_unreadable, characters)
VALUES (%(file_id)s, %(batch_id)s, %(kind)s, %(status)s, %(skip_reason)s, %(skip_message)s,
        %(pages)s, %(pages_with_text)s, %(pages_ocr)s, %(pages_unreadable)s, %(characters)s)
ON CONFLICT (source_file_id) DO UPDATE
SET status = EXCLUDED.status, skip_reason = EXCLUDED.skip_reason,
    skip_message = EXCLUDED.skip_message, pages = EXCLUDED.pages,
    pages_with_text = EXCLUDED.pages_with_text, pages_ocr = EXCLUDED.pages_ocr,
    pages_unreadable = EXCLUDED.pages_unreadable, characters = EXCLUDED.characters,
    product_keys = '[]', pages_without_product = 0, values_kept = 0, values_dropped = 0,
    llm_source = 'none', llm_message = NULL, updated_at = now();

-- name: has_spreadsheet_products
SELECT EXISTS (
    SELECT 1 FROM products WHERE batch_id = %(batch_id)s AND origin = 'spreadsheet'
) AS linked;

-- name: products_with_codes
-- Each product with every code that names it: its key, and each identifier a spreadsheet gave.
SELECT p.id, p.product_key, p.source_order,
       COALESCE(array_agg(DISTINCT o.raw_value) FILTER (WHERE o.raw_value IS NOT NULL), '{}')
           AS codes
FROM products p
LEFT JOIN attribute_observations o
       ON o.product_id = p.id
      AND o.attribute_id IN (SELECT id FROM attribute_definitions WHERE code = ANY(%(codes)s))
WHERE p.batch_id = %(batch_id)s
GROUP BY p.id
ORDER BY p.source_order;

-- name: pages_in_order
SELECT r.id, r.location, r.raw_data, f.id AS file_id, f.original_name, f.kind
FROM source_records r
JOIN source_files f ON f.id = r.source_file_id
JOIN document_files d ON d.source_file_id = f.id
WHERE f.batch_id = %(batch_id)s AND r.record_type = 'page' AND d.status = 'read'
ORDER BY f.position, f.member_index, f.id, (r.location ->> 'page')::int NULLS FIRST;

-- name: clear_document_values
-- Values read from documents are read afresh each time. Spreadsheet values stay.
DELETE FROM attribute_observations o
USING products p
WHERE p.id = o.product_id AND p.batch_id = %(batch_id)s AND o.extractor = ANY(%(extractors)s);

-- name: clear_document_products
DELETE FROM products WHERE batch_id = %(batch_id)s AND origin = 'document';

-- name: insert_products
INSERT INTO products (batch_id, owner_user_id, product_key, key_source, supplier_sku, match_key,
                      identifier_flags, source_order, origin)
SELECT %(batch_id)s, %(owner)s, p.product_key, p.key_source, p.supplier_sku, p.match_key,
       p.identifier_flags, p.source_order, 'document'
FROM jsonb_to_recordset(%(products)s::jsonb) AS p(
    product_key text, key_source text, supplier_sku text, match_key text,
    identifier_flags jsonb, source_order int)
RETURNING id, product_key;

-- name: insert_observations
-- Never updates: an observation that is already there is left exactly as it was.
INSERT INTO attribute_observations (product_id, attribute_id, raw_value, source_file_id,
                                    source_record_id, location, extractor, model_run_id,
                                    confidence)
SELECT o.product_id, o.attribute_id, o.raw_value, o.source_file_id, o.source_record_id,
       o.location, o.extractor, m.id, o.confidence
FROM jsonb_to_recordset(%(observations)s::jsonb) AS o(
    product_id uuid, attribute_id uuid, raw_value text, source_file_id uuid,
    source_record_id uuid, location jsonb, extractor text, model_run_id uuid,
    confidence numeric)
LEFT JOIN model_runs m ON m.id = o.model_run_id
ON CONFLICT DO NOTHING;

-- name: update_document_values
UPDATE document_files d
SET product_keys = u.product_keys, pages_without_product = u.pages_without_product,
    values_kept = u.values_kept, values_dropped = u.values_dropped,
    llm_source = u.llm_source, llm_message = u.llm_message, updated_at = now()
FROM jsonb_to_recordset(%(files)s::jsonb) AS u(
    source_file_id uuid, product_keys jsonb, pages_without_product int, values_kept int,
    values_dropped int, llm_source text, llm_message text)
WHERE d.source_file_id = u.source_file_id;

-- name: clear_job_notes
DELETE FROM processing_errors WHERE job_id = %(job_id)s;

-- name: insert_notes
INSERT INTO processing_errors (pipeline_run_id, job_id, source_file_id, location, error_code,
                               message)
SELECT %(run_id)s, %(job_id)s, n.source_file_id, n.location, n.error_code, n.message
FROM jsonb_to_recordset(%(notes)s::jsonb) AS n(
    source_file_id uuid, location jsonb, error_code text, message text);

-- name: record_product_counts
UPDATE pipeline_runs
SET products_found = %(found)s, products_kept = %(kept)s, products_discarded = %(discarded)s
WHERE id = %(run_id)s;
