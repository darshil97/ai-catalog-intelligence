# Database Schema

**Status: planned, except these tables:**
- **Job queue** (`pipeline_runs`, `pipeline_stage_runs`, `jobs`, `processing_errors`, `worker_control`, `worker_instances`, `worker_sessions`): migration `0002`, applied to the Neon `production`, `dev` and `test` branches.
- **Accounts and catalog structure** (`user_profiles`, `user_limits`, `usage_ledger`, `suppliers`, `batches`, plus owner foreign keys on `pipeline_runs` and `jobs`): migration `0003` (M1), applied to `production`, `dev` and `test`. It refuses to run while queue rows exist whose owners have no profile.

- **Spreadsheet extraction** (`categories`, `attribute_definitions`, `model_runs`, `column_mappings`, `spreadsheet_sheets`, `source_records`, `products`, `attribute_observations`): migration `0005` (M3), applied to `production`, `dev` and `test`.
- **Document extraction** (`ocr_results`, `document_files`, `products.origin`): migration `0006` (M4), applied to `production` and `test`. `dev` is still at `0005`.
- **Images** (`embedding_models`, `images`, `image_duplicate_groups`, `image_embeddings`, `pipeline_runs.image_duplicate_groups`): migration `0007` (M5A), applied to `test` only so far.

This design guides the migrations written in each module, and it may be refined when each module is proposed. Tables are added only by the module that needs them.

PostgreSQL on Neon (Sydney), with the `pgvector` extension.

## Conventions

- **Primary keys:** `id UUID DEFAULT gen_random_uuid()`, except where noted.
- **Timestamps:** `TIMESTAMPTZ`. Most tables have `created_at`, and mutable tables also have `updated_at`.
- **Status values:** `TEXT` with a `CHECK` constraint, kept in sync with config.
- **Large content** (raw PDF text, OCR dumps, big LLM payloads) lives in S3, with only references here. Neon's free 1 GB is shared by all users.
- **Foreign keys** use `ON DELETE CASCADE` from `batches` downward so retention purges work, except for append-only audit and usage records.

## Relationships

```
 user_profiles ─1:N─ suppliers ─1:N─ batches ─1:N─ source_files ─1:N─ source_records
      │                               │               │                     │
      ├─1:1─ user_limits              │ 1:N           │ 1:N                 │
      └─1:N─ usage_ledger             ▼               ▼                     │
                                   products         images ─1:N─ image_embeddings ─N:1─ embedding_models
                                      │               │  └─1:N─ image_ocr_results
                                      │               │  └─N:1─ image_duplicate_groups
                                      │ 1:N           │ 1:N
                                      ├──── image_match_candidates ─(approved)─▶ product_images
                                      │
                                      ├─1:N─ attribute_observations ◀── source_records, model_runs
                                      │        │ (immutable: raw value + location + extractor)
                                      │        └─1:N─ normalized_values (value, unit, rule version)
                                      ├─1:N─ product_attributes (current value + state)
                                      ├─1:N─ generated_content ─▶ model_runs (+ attributes cited)
                                      ├─1:N─ validation_issues
                                      └─N:M─ variant_candidates (flagged, never auto-merged)

 Cross-cutting: review_tasks, review_events, model_runs, processing_errors, exports, worker_sessions
 Processing:    pipeline_runs ─1:N─ pipeline_stage_runs ─1:N─ jobs
                worker_control (single row), worker_instances
 Managed:       neon_auth.*  (owned by Neon Auth; we don't add foreign keys into it)
```

---

## Accounts and usage

### `user_profiles`

| Column | Type | Notes |
|---|---|---|
| `id` | TEXT PK | The token's `sub` |
| `email` | TEXT NOT NULL | |
| `role` | TEXT NOT NULL | `user`, `admin`. Copied from the token, which stays the source of truth. |
| `status` | TEXT NOT NULL | `active`, `waitlisted`, `disabled` |
| `created_at`, `updated_at` | TIMESTAMPTZ | |

There is no foreign key into `neon_auth`, so the managed schema can change without breaking ours.

**Admission:** the first request with a valid token creates the profile. An allowlisted, verified email is `active`. Otherwise it's `active` only with `signups_enabled` on and fewer active profiles than `max_total_users`, and `waitlisted` if not. Admissions take `LOCK TABLE user_profiles IN SHARE ROW EXCLUSIVE MODE` for their transaction, so two sign-ups can't both take the last place. A waitlisted profile is re-checked on each request. Only an admin sets `disabled`, directly in the database.

### `user_limits`

| Column | Type | Notes |
|---|---|---|
| `user_id` | TEXT PK, FK | |
| `overrides` | JSONB NOT NULL | Limit name to value. Absent means use the config default, and null means unlimited. Unknown names and invalid values are ignored with a warning. |
| `updated_at` | TIMESTAMPTZ | |

### `usage_ledger`

Append-only. A restoration is a new row, never an update.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `user_id` | TEXT FK | |
| `kind` | TEXT NOT NULL | For example `submission`, `llm_tokens`, `worker_seconds` |
| `quantity` | NUMERIC NOT NULL | Positive |
| `cost_usd` | NUMERIC | When known |
| `pipeline_run_id` | UUID FK, nullable | `ON DELETE SET NULL`, so usage outlives purged runs |
| `is_restoration` | BOOLEAN NOT NULL DEFAULT false | The "restored" flag |
| `restores_entry_id` | UUID FK self, nullable | The entry being reversed. Set exactly when `is_restoration` is true. |
| `created_at` | TIMESTAMPTZ | |

**Index:** `(user_id, kind, created_at)`, and partial `UNIQUE(restores_entry_id)`, so an entry is restored at most once.

**Counting:** an entry counts unless a restoration points at it. The rolling window uses the original entry's `created_at`.

**Concurrency rule:** the submission transaction locks the user's `user_profiles` row with `SELECT … FOR UPDATE`, checks the ledger, then writes the ledger row and the job together. Simultaneous submissions can't both pass.

---

## Catalog structure

### `suppliers`

`id`, `owner_user_id` TEXT FK NOT NULL, `name` TEXT NOT NULL, `created_at`, `updated_at`.

**Constraints:** `UNIQUE(owner_user_id, lower(name))`, so names are unique per owner ignoring case. `UNIQUE(id, owner_user_id)`, which batches reference.

### `batches`

`id`, `supplier_id`, `owner_user_id` TEXT FK NOT NULL, `name`, `status` (`draft` for now; later modules add values), `expires_at` (creation plus `retention_days`), `created_at`, `updated_at`.

**Constraints:** `FOREIGN KEY (supplier_id, owner_user_id) REFERENCES suppliers (id, owner_user_id)`, so a batch always belongs to its supplier's owner. It's `NO ACTION`, which refuses deleting a supplier that has batches yet lets deleting a user cascade to both.

**Index:** `(owner_user_id, created_at)`, `(supplier_id, created_at)`, and `(expires_at)` for the purge job.

### `source_files`

Created in migration `0004` (M2). An uploaded file, or a file found inside an uploaded ZIP.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | ZIP entries get an ID derived from the archive and member, so a rerun replaces them |
| `batch_id` | UUID FK | Cascade |
| `owner_user_id` | TEXT FK | Cascade |
| `parent_file_id` | UUID FK self | The ZIP it came from. Cascade |
| `archive_member` | TEXT | Path inside that ZIP. Set exactly when `parent_file_id` is |
| `position`, `member_index` | INTEGER | Upload order, then order inside the ZIP. The image limit keeps the first ones |
| `s3_key` | TEXT UNIQUE | `{env}/raw/...` or `{env}/derived/...` (D29). Null only for a rejected ZIP entry, which isn't stored |
| `original_name` | TEXT NOT NULL | Cleaned: no folders or control characters. Never used in keys |
| `extension` | TEXT NOT NULL | Lowercase, without the dot |
| `kind` | TEXT NOT NULL | `spreadsheet`, `pdf`, `image`, `zip`, or `other` (an unsupported ZIP entry) |
| `content_type` | TEXT NOT NULL | Fixed per extension by the upload policy |
| `size_bytes` | BIGINT NOT NULL | |
| `sha256` | TEXT | Hex. From the browser, checked by S3, the API and the worker (D30) |
| `status` | TEXT NOT NULL | `pending_upload`, `uploaded`, `ingested`, `rejected` |
| `rejection_reason`, `rejection_message` | TEXT | Set exactly when rejected |
| `details` | JSONB | What the checks found, such as page count, image size, or ZIP entry counts |
| `upload_expires_at` | TIMESTAMPTZ | An unfinished upload past this is treated as rejected |
| `created_at`, `updated_at` | TIMESTAMPTZ | |

**Constraints:** `UNIQUE(batch_id, sha256) WHERE status <> 'rejected'`; `UNIQUE(parent_file_id, archive_member)` for ZIP entries.

`batches.status` gained `queued`, `processing`, `completed` and `failed` in `0004`. A run's status updates its batch.

### `source_records`

One spreadsheet row or one PDF page or block.

`id`, `source_file_id` FK, `record_type` (`row`, `page`, `block`), `location` JSONB, `raw_ref` (S3 key if large), `raw_data` JSONB (small rows only), `created_at`.

**Constraint:** `UNIQUE(source_file_id, record_type, location)`.

**M4:** `page` rows hold one PDF page or photo: `location` is `{"page": n}` (null for a photo), and `raw_data` holds `method` (`text` or `ocr`), `width`, `height`, `confidence` and `lines` (text with a box each).

**As built (M3):** a spreadsheet row's `location` is `{"sheet", "sheet_index", "row"}`, with `sheet` null for a CSV. Its `raw_data` holds every non-empty cell by column letter under `cells`, mapped or not, plus `formats`, `flags` and `merged_from` where they apply. Only rows of kept products are stored. A row's ID is derived from its sheet and row number, so reading it twice stores it once.

### `spreadsheet_sheets`

One row per sheet of each spreadsheet (one per CSV), written by `extract_select`. Added in M3.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | Derived from the file and sheet index |
| `source_file_id`, `batch_id` | UUID FK | `ON DELETE CASCADE` |
| `sheet_index`, `sheet_name`, `hidden` | | `sheet_name` is null for a CSV |
| `status` | TEXT NOT NULL | `used`, `skipped` |
| `skip_reason`, `skip_message` | TEXT | `empty`, `no_header`, `no_identifier`, `unreadable` |
| `header_row`, `header_rows` | INTEGER | `header_rows` is 2 for a two-row header |
| `columns` | JSONB NOT NULL | Per column: letter, header, attribute, source, confidence, reason, suggestion, model run |
| `mapping_source` | TEXT NOT NULL | `known_names`, `saved`, `llm`, `llm_failed`, `llm_capped`, `none` |
| `mapping_message` | TEXT | Why the LLM wasn't used when it was needed |
| `row_count`, `rows_without_identifier`, `duplicate_rows`, `rows_over_cap`, `unreadable_rows` | INTEGER | |
| `chunk_starts`, `chunk_rows` | JSONB, INTEGER | Which row blocks hold kept products |

**Constraint:** `UNIQUE(source_file_id, sheet_index)`.

### `column_mappings`

A supplier's remembered layouts (D32). Added in M3.

`id`, `supplier_id` FK, `owner_user_id` FK, `header_fingerprint` (SHA-256 of the normalized headers, the attribute list and the mapping version), `mapping` JSONB, `created_at`, `updated_at`.

**Constraint:** `UNIQUE(supplier_id, header_fingerprint)`.

### `categories` and `attribute_definitions`

These make the schema category-independent. Home & Kitchen is seed data, not code.

**`categories`:** `id`, `code` UNIQUE, `name`, `parent_id` FK self, `created_at`.

**`attribute_definitions`:**

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `code` | TEXT UNIQUE NOT NULL | For example `net_weight` |
| `data_type` | TEXT NOT NULL | `text`, `number`, `boolean`, `enum` |
| `unit_family` | TEXT | For example `mass`, `length` |
| `category_scope` | UUID FK, nullable | Null means all categories |
| `rules` | JSONB | Required rules, allowed values. `description` tells the LLM what belongs in the attribute. |
| `version` | INTEGER NOT NULL | |

**As built (M3):** also `name` (shown to people) and `created_at`. Seventeen attributes are seeded by the migration. The column names that map to each one live in `config.yaml` (`extraction.header_names`), so they can grow without a migration. `categories` is empty until M6.

### `products`

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `batch_id` | UUID FK | |
| `owner_user_id` | TEXT FK | |
| `product_key` | TEXT NOT NULL | Canonicalized SKU or generated key |
| `supplier_sku` | TEXT | As provided |
| `category_id` | UUID FK | |
| `status` | TEXT NOT NULL | `draft`, `in_review`, `approved`, `rejected` |
| `created_at`, `updated_at` | TIMESTAMPTZ | |

**Constraint:** `UNIQUE(batch_id, product_key)`.

Products beyond the POC limit get no row. Their counts are stored on `pipeline_runs`.

**M4:** `origin` (`spreadsheet` or `document`) says where a product came from. Document products exist only in batches without spreadsheet products.

**As built (M3):** also `key_source` (`sku`, `model_number` or `gtin`), `match_key` (the key without punctuation, for M5B), `identifier_flags` JSONB (`scientific_notation`, `precision_lost`, `invalid_gtin`) and `source_order` (position in file order). `category_id` stays null until M6.

---

## Provenance

Three layers keep the original, the cleaned value and the chosen value separate:

```
 attribute_observations  →  normalized_values  →  product_attributes
 "what the source said"     "what we computed"    "what the product currently shows"
 immutable                  versioned by rule     pointer + state
```

### `attribute_observations`

**Immutable. Never updated.**

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `product_id` | UUID FK | |
| `attribute_id` | UUID FK | |
| `raw_value` | TEXT NOT NULL | Exactly as found |
| `source_file_id` | UUID FK | |
| `source_record_id` | UUID FK, nullable | |
| `location` | JSONB NOT NULL | Sheet/row/column, or page/bbox |
| `extractor` | TEXT NOT NULL | Name and version |
| `model_run_id` | UUID FK, nullable | Set when an LLM was involved |
| `confidence` | NUMERIC | |
| `created_at` | TIMESTAMPTZ | |

**Constraint:** `UNIQUE(product_id, attribute_id, source_file_id, location, extractor)`, which makes re-runs idempotent.

**As built (M3):** a spreadsheet observation's `location` is `{"sheet", "sheet_index", "row", "column", "header"}`, plus `merged_from` for a cell filled from a merged range. `extractor` is `spreadsheet@1`. `model_run_id` and `confidence` are set when the LLM decided what the column means; a known column name gives confidence 1 and no model run. The value is never the LLM's.

**M4:** an observation from a PDF or photo has `extractor` `pdf_text+llm@1` or `ocr+llm@1`, its page record as `source_record_id`, and a `location` of `{"page", "method", "quote", "bbox", "page_width", "page_height"}`. `raw_value` and `quote` are the page's own text, checked to contain what the LLM reported. `confidence` is the page's mean OCR confidence, or null for a PDF's own text.

### `normalized_values`

`id`, `observation_id` FK, `value_text`, `value_number` NUMERIC, `unit`, `rule_version` TEXT NOT NULL, `created_at`.

**Constraint:** `UNIQUE(observation_id, rule_version)`.

### `product_attributes`

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `product_id` | UUID FK | |
| `attribute_id` | UUID FK | |
| `selected_value_id` | UUID FK to `normalized_values`, nullable | |
| `reviewer_value` | JSONB, nullable | A reviewer's entry, kept apart from sources |
| `state` | TEXT NOT NULL | `verified`, `conflict`, `missing`, `reviewer_set` |
| `updated_at` | TIMESTAMPTZ | |

**Constraint:** `UNIQUE(product_id, attribute_id)`.

**Rule:** different normalized values for one attribute set `state = conflict` and create a review task. Nothing resolves a conflict automatically.

---

## Images and embeddings

### `images`

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `batch_id` | UUID FK | |
| `source_file_id` | UUID FK | |
| `archive_member` | TEXT | Path inside a ZIP or location in a PDF |
| `sha256` | TEXT NOT NULL | |
| `phash` | BIGINT | |
| `width`, `height` | INTEGER | |
| `s3_key`, `thumbnail_s3_key` | TEXT | |
| `duplicate_group_id` | UUID FK, nullable | |
| `created_at` | TIMESTAMPTZ | |

**Constraint:** `UNIQUE(source_file_id, archive_member)`. **Index:** `(batch_id, sha256)`.

**As built (M5A):** one row per kept image's source file (`source_file_id` UNIQUE; the ZIP or PDF it came from is on `source_files`, so `archive_member` isn't repeated here), with `owner_user_id`, `status` (`processed`, `skipped`), `skip_reason`, `skip_message`, `colour_signature` (12-bin hue histogram), `is_representative`, `embedding_note` (why there's no embedding) and `timings` (milliseconds per step). The ID is derived from the source file, so a rerun replaces it.

### `image_duplicate_groups`

`id`, `batch_id` FK, `kind` (`exact`, `near`), `created_at`.

### `ocr_results` (planned as `image_ocr_results`)

Built in M4 as `ocr_results`, keyed by file rather than image, because scanned PDF pages need OCR too and the `images` table arrives in M5A.

`id`, `source_file_id` FK, `page` (null for a photo), `engine`, `engine_version`, `text`, `line_count`, `confidence` (mean of lines kept), `duration_ms`, `created_at`.

**Constraint:** unique `(source_file_id, COALESCE(page, 0), engine)`. Lines with their positions are stored once, on the page's `source_records` row.

### `document_files`

One row per PDF or image read by the `documents` stage (M4): `source_file_id` PK, `batch_id`, `kind` (`pdf`, `image`), `status` (`read`, `skipped`), `skip_reason`, `skip_message`, `pages`, `pages_with_text`, `pages_ocr`, `pages_unreadable`, `characters`, `product_keys` JSONB, `pages_without_product`, `values_kept`, `values_dropped`, `llm_source` (`none`, `llm`, `llm_failed`, `llm_capped`, `llm_budget`), `llm_message`, `created_at`, `updated_at`.

### `embedding_models`

`id`, `provider`, `name`, `version`, `dimension` INTEGER NOT NULL, `created_at`. Built in M5A with `provider` in place of the planned `pretrained_tag`, since the model is called on Bedrock (D34). Seeded with `bedrock` / `amazon.titan-embed-image-v1` / `1` / 1024.

**Constraint:** `UNIQUE(provider, name, version)`.

### `image_embeddings`

| Column | Type | Notes |
|---|---|---|
| `image_id` | UUID FK | Part of the primary key |
| `embedding_model_id` | UUID FK | Part of the primary key |
| `embedding` | `vector(1024)` NOT NULL | Titan's size (D34); planned as 512 for OpenCLIP |
| `model_run_id` | UUID FK, nullable | The call that produced it, with its cost (M5A) |
| `created_at` | TIMESTAMPTZ | |

**Rules:**
- Every comparison filters on `embedding_model_id` and on batch or owner, so incompatible vectors and other users' data can't mix.
- A model with a different dimension requires a new table through a migration.
- **No HNSW index initially.** Comparisons are batch-scoped, so exact search is accurate and smaller. Add one only if measurements justify it.

### `image_match_candidates`

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `image_id`, `product_id` | UUID FK | |
| `score` | NUMERIC | |
| `signals` | JSONB NOT NULL | Evidence from each signal |
| `decision` | TEXT NOT NULL | `auto_accepted`, `needs_review`, `accepted`, `rejected` |
| `decided_by` | TEXT | User ID or `system` |
| `decided_at` | TIMESTAMPTZ | |

**Constraint:** `UNIQUE(image_id, product_id)`. Auto-accept needs an exact identifier hit with no competing candidate.

### `product_images`

`product_id` FK, `image_id` FK, `position` INTEGER, `candidate_id` FK, `created_at`. **Primary key:** `(product_id, image_id)`.

---

## Enrichment, validation and review

### `model_runs`

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `owner_user_id` | TEXT FK | |
| `job_id` | UUID FK | |
| `provider`, `model_id` | TEXT NOT NULL | |
| `prompt_version` | TEXT | |
| `input_hash` | TEXT NOT NULL | |
| `status` | TEXT NOT NULL | `succeeded`, `failed` |
| `response_ref` | TEXT | S3 key |
| `input_tokens`, `output_tokens` | INTEGER | |
| `cost_usd` | NUMERIC | Feeds the global spend cap |
| `latency_ms` | INTEGER | |
| `error` | TEXT | |
| `created_at` | TIMESTAMPTZ | |

**Constraint:** partial `UNIQUE(owner_user_id, input_hash) WHERE status = 'succeeded'`, so a retry reuses a stored response and results never cross users.

**As built (M3):** also `task` (such as `column_mapping`) and `response` JSONB, which holds the checked reply when it's small. `job_id` is set to null when its job is purged, so costs outlive the batch. Failed attempts are recorded too.

### `generated_content`

`id`, `product_id` FK, `field` (`title`, `description`, `bullet`), `text`, `version` INTEGER, `model_run_id` FK, `cited_attribute_ids` UUID[] NOT NULL, `status` (`generated`, `edited`, `approved`, `rejected`), `created_at`.

**Constraint:** `UNIQUE(product_id, field, version)`.

### `validation_issues`

`id`, `product_id` FK, `rule_code`, `rule_version`, `severity`, `attribute_id` FK nullable, `details` JSONB, `status` (`open`, `resolved`, `ignored`), `created_at`, `resolved_at`.

### `variant_candidates`

`id`, `product_a_id` FK, `product_b_id` FK, `reason` JSONB, `status` (`flagged`, `confirmed`, `dismissed`), `created_at`.

**Constraint:** `CHECK (product_a_id < product_b_id)` and `UNIQUE(product_a_id, product_b_id)`. Flagged only, never merged.

### `review_tasks`

`id`, `owner_user_id` FK, `product_id` FK nullable, `kind` (`image_match`, `attribute_conflict`, `category`, `llm_output`, ...), `subject_ref` JSONB, `status` (`open`, `resolved`), `created_at`, `resolved_at`.

**Index:** partial `(owner_user_id, created_at) WHERE status = 'open'`.

### `review_events`

Append-only audit trail: `id`, `actor_user_id` FK, `entity_type`, `entity_id`, `action`, `before` JSONB, `after` JSONB, `created_at`.

### `processing_errors`

`id`, `pipeline_run_id` FK, `job_id` FK, `source_file_id` FK nullable, `location` JSONB, `error_code`, `message`, `created_at`.

Since M3 this also holds rows and cells that couldn't be used in a run that otherwise succeeded, each with its `location`: `row_unreadable`, `row_too_large`, `formula_without_value`, `error_value` and `more_row_problems`. At most 20 are stored per extract job, and the rest are counted.

### `exports`

`id`, `batch_id` FK, `owner_user_id` FK, `format` (`csv`, `xlsx`, `shopify_csv`), `status`, `s3_key`, `product_count`, `created_at`.

---

## Job queue

### `pipeline_runs`

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `pipeline` | TEXT NOT NULL | Pipeline name from config |
| `batch_id` | UUID FK, nullable | `ON DELETE CASCADE` (M1) |
| `owner_user_id` | TEXT FK NOT NULL | `ON DELETE CASCADE` (M1) |
| `status` | TEXT NOT NULL | `queued`, `running`, `succeeded`, `failed`, `cancelled` |
| `products_found`, `products_kept`, `products_discarded` | INTEGER | |
| `files_found`, `files_kept`, `files_discarded` | INTEGER | |
| `images_found`, `images_kept`, `images_discarded` | INTEGER | |
| `request_id` | TEXT | |
| `created_at`, `started_at`, `finished_at` | TIMESTAMPTZ | |

**Constraint:** partial `UNIQUE(owner_user_id) WHERE status IN ('queued', 'running')`, enforcing one active run per user.

### `pipeline_stage_runs`

`id`, `pipeline_run_id` FK, `stage` TEXT, `position` INTEGER, `job_type`, `payload` JSONB, `status`, `total_chunks`, `completed_chunks`, `failed_chunks`, `started_at`, `finished_at`.

`position`, `job_type` and `payload` say which stage comes next, and which job starts it.

**Constraints:** `UNIQUE(pipeline_run_id, stage)`, `UNIQUE(pipeline_run_id, position)`.

The transaction completing a stage's last chunk also enqueues the next stage.

### `jobs`

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `pipeline_run_id` | UUID FK | |
| `stage_run_id` | UUID FK | |
| `owner_user_id` | TEXT FK | |
| `job_type` | TEXT NOT NULL | Validated against the handler registry |
| `payload` | JSONB NOT NULL | References only, no large content |
| `idempotency_key` | TEXT NOT NULL UNIQUE | Hash of run + stage + chunk |
| `status` | TEXT NOT NULL | `pending`, `running`, `succeeded`, `failed`, `cancelled`, `blocked_quota` |
| `priority` | INTEGER NOT NULL DEFAULT 100 | |
| `attempts`, `max_attempts` | INTEGER NOT NULL | |
| `run_after` | TIMESTAMPTZ NOT NULL DEFAULT now() | Backoff |
| `lease_owner` | TEXT | Worker ID |
| `lease_token` | UUID | Blocks stale writers |
| `lease_expires_at` | TIMESTAMPTZ | |
| `contract_version` | INTEGER NOT NULL | |
| `request_id` | TEXT | Traces back to the API request |
| `code_sha` | TEXT | Worker version that ran it |
| `last_error`, `error_class` | TEXT | |
| `created_at`, `started_at`, `finished_at` | TIMESTAMPTZ | |

**Indexes:**
```sql
CREATE INDEX jobs_claimable ON jobs (priority, created_at) WHERE status = 'pending';
CREATE INDEX jobs_leases    ON jobs (lease_expires_at)     WHERE status = 'running';
```

**Claiming:**
```sql
WITH next AS (
  SELECT id FROM jobs
  WHERE status = 'pending' AND run_after <= now()
    AND contract_version BETWEEN :min_contract AND :max_contract
  ORDER BY priority, created_at
  FOR UPDATE SKIP LOCKED
  LIMIT 1
)
UPDATE jobs j SET status = 'running', lease_token = gen_random_uuid(),
       lease_owner = :worker_id, lease_expires_at = now() + :lease_ttl,
       attempts = attempts + 1, started_at = now()
FROM next WHERE j.id = next.id
RETURNING j.*;
```

The claim commits immediately. Ownership is held by the lease, not an open transaction.

**Heartbeat:** `UPDATE jobs SET lease_expires_at = now() + :ttl WHERE id = :id AND lease_token = :token`. On 0 rows, the worker aborts the job.

**Stale writers:** the transaction storing results begins with a guarded update on `lease_token`. On 0 rows it rolls back.

**Reaper:** `status = 'running' AND lease_expires_at < now()` returns to `pending` with backoff, or to `failed` if attempts are exhausted.

**Shutdown check:** counts pending jobs within the supported contract range, running jobs (including expired leases) and retries due soon.

### `worker_control`

A single row, enforced with `id INTEGER PRIMARY KEY CHECK (id = 1)`.

| Column | Type | Notes |
|---|---|---|
| `state` | TEXT NOT NULL | `stopped`, `starting`, `running`, `paused`, `shutting_down`, `unavailable` |
| `last_start_requested_at` | TIMESTAMPTZ | Cooldown for start attempts |
| `min_contract`, `max_contract` | INTEGER | Published by the worker |
| `pause_reason` | TEXT | `deploy`, or null |
| `pause_expires_at` | TIMESTAMPTZ | |
| `updated_at` | TIMESTAMPTZ | |

### `worker_instances`

A row, not an advisory lock, because session features conflict with transaction pooling.

`id`, `ec2_instance_id` (null locally), `code_sha`, `state`, `started_at`, `last_heartbeat_at`, `stopped_at`.

### `worker_sessions`

Runtime for the global EC2 hour cap: `id`, `ec2_instance_id`, `started_at`, `last_seen_at`, `ended_at`. An unexpected stop is counted up to `last_seen_at`.

---

## Migration approach

- **Tool:** Alembic, in `backend/migrations/`, running over Neon's **direct** endpoint. Runtime uses the pooled endpoint.
- **Run by:** GitHub Actions, before any deploy, in the order migrate → worker → API.
- **Expand/contract only:**
  1. *Expand:* add new columns or tables, compatible with running code.
  2. *Migrate:* deploy code that uses them, and backfill if needed.
  3. *Contract:* remove old columns in a later release.
- **Never** make a destructive change in the same release as the code that stops using the column.
- **Branches:** Neon's default branch is `production`. Local development uses `dev`, tests use `test`, and CI uses a Postgres + pgvector container. Postgres never runs locally.
- **`neon_auth` is managed by Neon.** We never migrate it.
- **Direct database changes:** Claude asks before any schema change or destructive action on Neon.

## Storage budget

Neon's free tier allows 1 GB per project, shared by all users and by `neon_auth`.

- A 512-dimension vector is about 2 KB.
- With the example limits, 25 users produce about 250 products, well within this.
- Retention purges and a storage metric ship early, so the limit is visible before it's reached.
