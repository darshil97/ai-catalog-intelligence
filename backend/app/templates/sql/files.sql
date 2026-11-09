-- Source files. Each "-- name:" line starts a query. Values are always bound parameters.
-- API queries filter on owner_user_id, so another user's files look exactly like missing ones.

-- name: lock_batch
-- Serializes file changes and submission for one batch, and reads the owner's limit overrides
-- in the same round trip.
SELECT b.id, b.status,
       (SELECT l.overrides FROM user_limits l WHERE l.user_id = b.owner_user_id) AS overrides
FROM batches b
WHERE b.id = %(batch_id)s AND b.owner_user_id = %(owner)s
FOR UPDATE OF b;

-- name: expire_pending
-- Uploads nobody finished stop counting toward limits. With all_pending, unfinished uploads are
-- given up even if their link hasn't expired, because the batch is being submitted.
UPDATE source_files
SET status = 'rejected', rejection_reason = 'upload_not_completed',
    rejection_message = %(message)s, updated_at = now()
WHERE batch_id = %(batch_id)s AND status = 'pending_upload'
  AND (%(all_pending)s OR upload_expires_at < now());

-- name: batch_totals
-- Uploaded files only: ZIP entries count toward the image limit, not the file limit.
SELECT count(*) AS files, COALESCE(sum(size_bytes), 0)::bigint AS bytes
FROM source_files
WHERE batch_id = %(batch_id)s AND parent_file_id IS NULL AND status <> 'rejected';

-- name: find_by_sha256
SELECT id, status, original_name, parent_file_id FROM source_files
WHERE batch_id = %(batch_id)s AND sha256 = %(sha256)s AND status <> 'rejected';

-- name: insert_upload
INSERT INTO source_files (id, batch_id, owner_user_id, position, s3_key, original_name,
                          extension, kind, content_type, size_bytes, sha256, status,
                          upload_expires_at)
VALUES (%(id)s, %(batch_id)s, %(owner)s,
        (SELECT COALESCE(max(position), 0) + 1 FROM source_files WHERE batch_id = %(batch_id)s),
        %(s3_key)s, %(original_name)s, %(extension)s, %(kind)s, %(content_type)s,
        %(size_bytes)s, %(sha256)s, 'pending_upload',
        now() + make_interval(secs => %(expires_seconds)s))
RETURNING id, batch_id, parent_file_id, archive_member, position, member_index, original_name,
          extension, kind, size_bytes, sha256, status, rejection_reason, rejection_message,
          details, upload_expires_at, created_at, updated_at, s3_key, content_type;

-- name: renew_upload
-- The same content registered again before its upload finished: hand out a fresh link.
UPDATE source_files
SET original_name = %(original_name)s,
    upload_expires_at = now() + make_interval(secs => %(expires_seconds)s), updated_at = now()
WHERE id = %(id)s
RETURNING id, batch_id, parent_file_id, archive_member, position, member_index, original_name,
          extension, kind, size_bytes, sha256, status, rejection_reason, rejection_message,
          details, upload_expires_at, created_at, updated_at, s3_key, content_type;

-- name: lock_file
SELECT f.id, f.batch_id, f.parent_file_id, f.s3_key, f.size_bytes, f.sha256, f.status,
       f.upload_expires_at, b.status AS batch_status
FROM source_files f
JOIN batches b ON b.id = f.batch_id
WHERE f.id = %(file_id)s AND f.owner_user_id = %(owner)s
FOR UPDATE OF f, b;

-- name: mark_uploaded
UPDATE source_files
SET status = 'uploaded', upload_expires_at = NULL, updated_at = now()
WHERE id = %(file_id)s
RETURNING id, batch_id, parent_file_id, archive_member, position, member_index, original_name,
          extension, kind, size_bytes, sha256, status, rejection_reason, rejection_message,
          details, upload_expires_at, created_at, updated_at;

-- name: reject
UPDATE source_files
SET status = 'rejected', rejection_reason = %(reason)s, rejection_message = %(message)s,
    upload_expires_at = NULL, updated_at = now()
WHERE id = %(file_id)s
RETURNING id, batch_id, parent_file_id, archive_member, position, member_index, original_name,
          extension, kind, size_bytes, sha256, status, rejection_reason, rejection_message,
          details, upload_expires_at, created_at, updated_at;

-- name: delete_file
-- Returns every stored key, including ZIP entries the cascade removes. The subquery sees the
-- rows as they were before this statement.
DELETE FROM source_files f
WHERE f.id = %(file_id)s AND f.owner_user_id = %(owner)s
RETURNING f.id,
          ARRAY(SELECT c.s3_key FROM source_files c
                WHERE (c.id = f.id OR c.parent_file_id = f.id) AND c.s3_key IS NOT NULL) AS keys;

-- name: list_files
-- An unfinished upload whose link has expired is shown as rejected, as it will be treated.
SELECT f.id, f.batch_id, f.parent_file_id, f.archive_member, f.position, f.member_index,
       f.original_name, f.extension, f.kind, f.size_bytes, f.sha256,
       CASE WHEN f.status = 'pending_upload' AND f.upload_expires_at < now()
            THEN 'rejected' ELSE f.status END AS status,
       CASE WHEN f.status = 'pending_upload' AND f.upload_expires_at < now()
            THEN 'upload_not_completed' ELSE f.rejection_reason END AS rejection_reason,
       CASE WHEN f.status = 'pending_upload' AND f.upload_expires_at < now()
            THEN %(expired_message)s ELSE f.rejection_message END AS rejection_message,
       f.details, f.upload_expires_at, f.created_at, f.updated_at
FROM source_files f
WHERE f.batch_id = %(batch_id)s AND f.owner_user_id = %(owner)s
ORDER BY f.position, f.member_index
LIMIT %(limit)s;

-- name: reset_for_processing
-- A retry checks every file again, including images the last run left out by the image limit.
UPDATE source_files
SET status = 'uploaded', rejection_reason = NULL, rejection_message = NULL, details = '{}',
    updated_at = now()
WHERE batch_id = %(batch_id)s AND parent_file_id IS NULL
  AND (status = 'ingested' OR (status = 'rejected' AND rejection_reason = 'image_limit'));

-- name: set_batch_status
UPDATE batches SET status = %(status)s, updated_at = now()
WHERE id = %(batch_id)s;

-- name: latest_run
SELECT r.id, r.status, r.created_at, r.started_at, r.finished_at,
       r.files_found, r.files_kept, r.files_discarded,
       r.images_found, r.images_kept, r.images_discarded,
       COALESCE((SELECT json_agg(s ORDER BY s.position)
                 FROM (SELECT stage, position, status, total_chunks, completed_chunks,
                              failed_chunks
                       FROM pipeline_stage_runs WHERE pipeline_run_id = r.id) s), '[]'::json)
           AS stages,
       COALESCE((SELECT json_agg(e ORDER BY e.created_at)
                 FROM (SELECT error_code, message, source_file_id, created_at
                       FROM processing_errors WHERE pipeline_run_id = r.id
                       ORDER BY created_at LIMIT 50) e), '[]'::json) AS errors
FROM pipeline_runs r
JOIN batches b ON b.id = r.batch_id
WHERE r.batch_id = %(batch_id)s AND b.owner_user_id = %(owner)s
ORDER BY r.created_at DESC
LIMIT 1;

-- Worker queries. The worker trusts the job's batch ID, which only the API can set.

-- name: files_to_ingest
SELECT id FROM source_files
WHERE batch_id = %(batch_id)s AND parent_file_id IS NULL AND status IN ('uploaded', 'ingested')
ORDER BY position;

-- name: get_file
SELECT id, batch_id, owner_user_id, parent_file_id, position, s3_key, original_name, extension,
       kind, content_type, size_bytes, sha256, status
FROM source_files
WHERE id = %(file_id)s;

-- name: delete_children
DELETE FROM source_files WHERE parent_file_id = %(file_id)s;

-- name: insert_child
-- Nothing is inserted when the batch already has this content, so the caller records the
-- entry as a duplicate instead.
INSERT INTO source_files (id, batch_id, owner_user_id, parent_file_id, archive_member, position,
                          member_index, s3_key, original_name, extension, kind, content_type,
                          size_bytes, sha256, status)
VALUES (%(id)s, %(batch_id)s, %(owner)s, %(parent_id)s, %(member)s, %(position)s,
        %(member_index)s, %(s3_key)s, %(original_name)s, %(extension)s, %(kind)s,
        %(content_type)s, %(size_bytes)s, %(sha256)s, 'ingested')
ON CONFLICT (batch_id, sha256) WHERE status <> 'rejected' DO NOTHING
RETURNING id;

-- name: insert_rejected_child
INSERT INTO source_files (id, batch_id, owner_user_id, parent_file_id, archive_member, position,
                          member_index, original_name, extension, kind, content_type,
                          size_bytes, sha256, status, rejection_reason, rejection_message)
VALUES (%(id)s, %(batch_id)s, %(owner)s, %(parent_id)s, %(member)s, %(position)s,
        %(member_index)s, %(original_name)s, %(extension)s, %(kind)s, %(content_type)s,
        %(size_bytes)s, %(sha256)s, 'rejected', %(reason)s, %(message)s);

-- name: mark_ingested
UPDATE source_files
SET status = 'ingested', details = %(details)s, updated_at = now()
WHERE id = %(file_id)s;

-- name: reject_by_worker
UPDATE source_files
SET status = 'rejected', rejection_reason = %(reason)s, rejection_message = %(message)s,
    details = %(details)s, updated_at = now()
WHERE id = %(file_id)s;

-- name: apply_image_limit
-- Keeps the first images in upload order, then archive order, and leaves out the rest.
WITH ranked AS (
    SELECT id, row_number() OVER (ORDER BY position, member_index, id) AS n
    FROM source_files
    WHERE batch_id = %(batch_id)s AND kind = 'image' AND status = 'ingested'
)
UPDATE source_files f
SET status = 'rejected', rejection_reason = 'image_limit', rejection_message = %(message)s,
    updated_at = now()
FROM ranked
WHERE f.id = ranked.id AND %(max_images)s::int IS NOT NULL AND ranked.n > %(max_images)s::int
RETURNING f.id;

-- name: record_run_counts
-- A ZIP that was opened is only a container, so it isn't counted; a ZIP that was refused is a
-- file that wasn't used.
WITH counted AS (
    SELECT kind, status FROM source_files
    WHERE batch_id = %(batch_id)s
      AND NOT (kind = 'zip' AND parent_file_id IS NULL AND status = 'ingested')
),
counts AS (
    SELECT count(*) AS files_found,
           count(*) FILTER (WHERE status = 'ingested') AS files_kept,
           count(*) FILTER (WHERE status = 'rejected') AS files_discarded,
           count(*) FILTER (WHERE kind = 'image') AS images_found,
           count(*) FILTER (WHERE kind = 'image' AND status = 'ingested') AS images_kept,
           count(*) FILTER (WHERE kind = 'image' AND status = 'rejected') AS images_discarded
    FROM counted
)
UPDATE pipeline_runs r
SET files_found = c.files_found, files_kept = c.files_kept,
    files_discarded = c.files_discarded, images_found = c.images_found,
    images_kept = c.images_kept, images_discarded = c.images_discarded
FROM counts c
WHERE r.id = %(run_id)s
RETURNING r.files_found, r.files_kept, r.files_discarded, r.images_found, r.images_kept,
          r.images_discarded;
