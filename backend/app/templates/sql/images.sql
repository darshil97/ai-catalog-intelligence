-- Images, embeddings and near-duplicate groups. Each "-- name:" line starts a query. Values are
-- always bound parameters. Every comparison is limited to one batch, one owner and one model.

-- name: images_to_process
-- Every kept image in file order, including those found inside ZIPs and PDFs.
SELECT id, batch_id, owner_user_id, s3_key, original_name, size_bytes, sha256
FROM source_files
WHERE batch_id = %(batch_id)s AND kind = 'image' AND status = 'ingested'
ORDER BY position, member_index, id;

-- name: clear_batch_groups
DELETE FROM image_duplicate_groups WHERE batch_id = %(batch_id)s;

-- name: clear_batch_images
DELETE FROM images WHERE batch_id = %(batch_id)s;

-- name: get_file
SELECT id, batch_id, owner_user_id, s3_key, original_name, size_bytes, sha256, status
FROM source_files
WHERE id = %(file_id)s AND batch_id = %(batch_id)s AND kind = 'image';

-- name: embedding_model
SELECT id, dimension FROM embedding_models
WHERE provider = %(provider)s AND name = %(name)s AND version = %(version)s;

-- name: existing_embedding
SELECT 1 FROM image_embeddings WHERE image_id = %(image_id)s AND embedding_model_id = %(model_id)s;

-- name: upsert_image
INSERT INTO images (id, batch_id, owner_user_id, source_file_id, status, skip_reason,
                    skip_message, sha256, phash, colour_signature, width, height,
                    thumbnail_s3_key, embedding_note, timings)
VALUES (%(id)s, %(batch_id)s, %(owner)s, %(file_id)s, %(status)s, %(skip_reason)s,
        %(skip_message)s, %(sha256)s, %(phash)s, %(colour)s, %(width)s, %(height)s,
        %(thumbnail)s, %(embedding_note)s, %(timings)s)
ON CONFLICT (source_file_id) DO UPDATE
SET status = EXCLUDED.status, skip_reason = EXCLUDED.skip_reason,
    skip_message = EXCLUDED.skip_message, phash = EXCLUDED.phash,
    colour_signature = EXCLUDED.colour_signature, width = EXCLUDED.width,
    height = EXCLUDED.height, thumbnail_s3_key = EXCLUDED.thumbnail_s3_key,
    embedding_note = EXCLUDED.embedding_note, timings = EXCLUDED.timings,
    duplicate_group_id = NULL, is_representative = false;

-- name: insert_embedding
-- An image is embedded once per model; a rerun keeps the first.
INSERT INTO image_embeddings (image_id, embedding_model_id, embedding, model_run_id)
VALUES (%(image_id)s, %(model_id)s, %(embedding)s::vector, %(model_run_id)s)
ON CONFLICT (image_id, embedding_model_id) DO NOTHING;

-- name: batch_images
SELECT i.id, i.status, i.sha256, i.colour_signature, i.width, i.height, f.position,
       f.member_index
FROM images i
JOIN source_files f ON f.id = i.source_file_id
WHERE i.batch_id = %(batch_id)s AND i.owner_user_id = %(owner)s
ORDER BY f.position, f.member_index, f.id;

-- name: similar_pairs
-- Exact cosine similarity between every two embedded images of the batch, for one model.
SELECT a.image_id AS a, b.image_id AS b,
       1 - (a.embedding <=> b.embedding) AS similarity
FROM image_embeddings a
JOIN images ia ON ia.id = a.image_id
JOIN image_embeddings b ON b.embedding_model_id = a.embedding_model_id AND b.image_id > a.image_id
JOIN images ib ON ib.id = b.image_id
WHERE a.embedding_model_id = %(model_id)s
  AND ia.batch_id = %(batch_id)s AND ib.batch_id = %(batch_id)s
  AND ia.owner_user_id = %(owner)s AND ib.owner_user_id = %(owner)s
  AND 1 - (a.embedding <=> b.embedding) >= %(min_similarity)s;

-- name: compare_two
-- One comparison, refused (no row) unless both images belong to the owner and the model.
SELECT 1 - (a.embedding <=> b.embedding) AS similarity
FROM image_embeddings a
JOIN images ia ON ia.id = a.image_id
JOIN image_embeddings b ON b.image_id = %(b)s AND b.embedding_model_id = a.embedding_model_id
JOIN images ib ON ib.id = b.image_id
WHERE a.image_id = %(a)s AND a.embedding_model_id = %(model_id)s
  AND ia.owner_user_id = %(owner)s AND ib.owner_user_id = %(owner)s;

-- name: insert_group
INSERT INTO image_duplicate_groups (batch_id, kind) VALUES (%(batch_id)s, %(kind)s)
RETURNING id;

-- name: set_group
UPDATE images
SET duplicate_group_id = %(group_id)s, is_representative = (id = %(representative)s)
WHERE id = ANY(%(members)s);

-- name: record_group_count
UPDATE pipeline_runs SET image_duplicate_groups = %(groups)s WHERE id = %(run_id)s;

-- API queries.

-- name: list_images
SELECT i.id, i.source_file_id, f.original_name AS source_file_name, f.parent_file_id,
       p.original_name AS parent_file_name, i.status, i.skip_reason, i.skip_message,
       i.width, i.height, i.thumbnail_s3_key, i.duplicate_group_id, g.kind AS duplicate_kind,
       i.is_representative, i.embedding_note,
       EXISTS (SELECT 1 FROM image_embeddings e WHERE e.image_id = i.id) AS embedded,
       (SELECT count(*) FROM images o WHERE o.duplicate_group_id = i.duplicate_group_id)
           AS group_size
FROM images i
JOIN source_files f ON f.id = i.source_file_id
LEFT JOIN source_files p ON p.id = f.parent_file_id
LEFT JOIN image_duplicate_groups g ON g.id = i.duplicate_group_id
JOIN batches b ON b.id = i.batch_id
WHERE i.batch_id = %(batch_id)s AND b.owner_user_id = %(owner)s
ORDER BY f.position, f.member_index, f.id
LIMIT %(limit)s;
