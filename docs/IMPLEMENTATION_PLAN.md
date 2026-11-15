# Implementation Plan

Each module's status is in the table below. Status values: Not Started, In Progress, Completed.

Each module gets its own proposal and approval before work starts, and review after it ends. See the process in [PROJECT_CONTEXT.md](PROJECT_CONTEXT.md#13-development-process).

## Status

| Module | Name | Depends on | Status |
|---|---|---|---|
| M0A | Foundation | — | Completed |
| M0B | Processing platform | M0A | Completed |
| M1 | Accounts, suppliers and batches | M0A | Completed |
| M2 | File ingestion and storage | M0B, M1 | Completed |
| M3 | Spreadsheet extraction | M2 | Completed |
| M4 | Document and image text extraction | M3 | Completed |
| M5A | Image processing | M2, M0B | Completed |
| M5B | Image association | M3, M4, M5A | Completed |
| M6 | Product normalization | M3, M4 | Completed |
| M7 | Catalog enrichment | M6 | Completed |
| M8 | Catalog validation | M6, M7 | Completed |
| M9 | Human review | M5B, M8 | In Progress |
| M10 | Export | M9 | Not Started |

**Delivery order:** M0A → M0B → M1 → M2 → M3 → M4 → M5A → M5B → M6 → M7 → M8 → M9 → M10.

## Why this sequence differs from the original list

Approved in [D10](DECISIONS.md#d10-module-sequencing):

1. **M0 is split.** The queue, EC2 lifecycle and worker CI/CD are the riskiest parts, and every processing module depends on them. A no-op job proves them end to end before features are built on top.
2. **M5 is split.** Duplicate detection and embeddings need only images. Association needs product data from M3 and M4, plus review.
3. **A review backbone arrives at M5B.** Uncertain matches need a person from that point on. M9 builds the complete experience.

---

## M0A Foundation

**Scope**
- `docs/` and root `.gitignore`.
- `backend/` skeleton: config, logging, request IDs, `GET /health`, Alembic baseline.
- `frontend/` skeleton on Next.js + TypeScript.
- CI, and deployment to Render (created manually) and Vercel.
- **Model review:** list Bedrock models available in Sydney with current pricing, and decide together (D2).
- Check how Claude accesses Neon (MCP or connection string).

**Acceptance criteria**
- Deployed `/health` reports the code SHA and touches no database.
- The API stays within a memory ceiling with worker dependencies absent.
- API↔Neon latency is measured and recorded.
- The model table is delivered to the owner.
- `.env` files are confirmed ignored before any secret is added.

**Results (2026-11-07)**
- Live: `/health` on Render reports the commit with no database access, and the Vercel site reaches it.
- API imports use about 74 MB, against a 150 MB test ceiling.
- API↔Neon: about 95 ms per query, about 580 ms for a new connection.
- Models chosen: gpt-oss-120b for text, Claude Haiku 4.5 for images (D2).

**Tests**
- Missing required config fails at startup.
- Import boundary: the API loads with torch, OpenCV and OCR blocked.
- Request ID middleware, including generated and caller-provided IDs.

---

## M0B Processing platform

**Stages:** B1 queue and worker (local, no AWS), B2 EC2 lifecycle, B3 safe deployment. Each is reviewed before the next begins.

**Approved changes from the original plan:** S3 and Bedrock permissions wait for M2 and M4/M7. Deployment jobs go in `ci.yml`, with no separate `deploy.yml`. Queue tables have no foreign key to users until M1. Temporary test-run endpoints use an admin token, and are removed in M1.

**B3 results (2026-11-08)**, from the merge of PR #6 (`65bd09e`):
- `migrate` ran against production through the GitHub key and Parameter Store, with nothing new to apply.
- `deploy-worker` started EC2 at 10:20:54 UTC. The old worker booted paused and claimed nothing, and the deploy was confirmed at 10:21:28 with the new commit running.
- Render switched the API to `65bd09e` at 10:22:30, after `deploy-worker` finished. This confirms "After CI Checks Pass" waits for the deploy jobs.
- EC2 stopped itself at 10:26:33.
- Deploy during a running job (PR #7, `754ae8d`): the deploy paused the worker at 10:51:22, while a 10-minute job ran. The job succeeded on its first attempt at 10:55:05, then the worker updated at 10:56:27, and Render followed.
- Broken deploy (PR #8, `a0c342f`, a deliberately invalid setting): the new worker never came up, `deploy.sh` rolled back to `754ae8d` at 12:31:04, the GitHub run failed, and Render stayed on `754ae8d`. Reverted in PR #9 (`213f61d`), which deployed normally.
- Not exercised on EC2: five quick submissions causing one start. Covered by tests.

**B2 results (2026-11-08)**
- First boot: setup finished, but the worker couldn't connect, because hiding `/home` blocked Postgres's optional certificate check. With no heartbeat, the watchdog powered the instance off within 20 minutes, as designed. Fixed with `ProtectHome=tmpfs`.
- Idle cycle: the worker started on `ea12b11`, registered, and powered off after exactly 5 idle minutes.
- Full cycle: a test run was submitted at 07:14:01 UTC, and the API asked AWS to start EC2 a second later. The worker claimed the first job 24 seconds after submission, finished all 3 jobs on their first attempt by 07:15:24, and powered off at 07:20:25.
- Not yet exercised on EC2: force-stopping mid-job, and a deploy during a job. These are covered by tests and the local real-process run, and the deploy case comes with B3.

**Scope**
- Job queue tables, claiming, leases, heartbeat, retries and reaper.
- Pipeline runs and stages.
- Worker loop, graceful stop, idle shutdown, `WorkerController` and `PowerController` with `noop` implementations.
- CloudFormation stack, IAM identities, EC2 setup, systemd units, watchdog, AWS Budgets.
- Deployment jobs in `ci.yml`, including migrations and the safe worker deploy.
- A no-op job, and the global EC2 hour cap.
- Verify cost figures in the AWS calculator.

**Acceptance criteria**
- Submit a no-op job: EC2 starts, the job runs, EC2 stops.
- Killing EC2 mid-job recovers, and the job completes after restart.
- Deploying during a job doesn't interrupt it.
- Multiple submissions cause one start and never a second instance.
- A crashed worker can't leave EC2 running indefinitely.

**Tests**
- Concurrent claims never double-claim (real Postgres).
- Stale writers are blocked by the lease token.
- Retries, backoff limits, reaper and max attempts.
- The shutdown race: a job arriving during the shutdown check.
- Contract version handling: unsupported jobs wait and don't cause boot loops.
- EC2 state handling with stubs: every state in the start table.
- A local-mode worker refuses the production database.

---

## M1 Accounts, suppliers and batches

**Status:** proposal approved by the owner on 2026-11-08. **Completed on 2026-11-08.** Merged and deployed, migration `0003` applied to `production` (by CI), `dev` and `test`, and live acceptance passed (owner, 2026-11-08). One open issue: no one can be an admin yet (see below). See [Implementation notes](#m1-implementation-notes).

**Approved additions to the scope below:**
- Migration `0003`: `user_profiles`, `user_limits`, `usage_ledger`, `suppliers`, `batches`. Adds foreign keys from `pipeline_runs.owner_user_id` and `jobs.owner_user_id` to `user_profiles`. Expand-only.
- The API verifies Neon Auth tokens locally: EdDSA signature via cached JWKS, issuer, audience, expiry, `banned` and `role`. It creates the profile on first use. There's no database query per check.
- `ADMIN_TOKEN` is removed. Test runs stay available, but only to signed-in users with the `admin` role.
- Neon Auth trusted domains for the Vercel URL and `localhost`. Neon's shared Google credentials are used until M7.
- Render and Vercel get the Neon Auth settings, and `ADMIN_TOKEN` is removed from Render.

**Owner decisions:**
1. Delete the M0B test-run rows in production (`noop` runs with owners such as `system`) before adding the foreign keys. This is approved, but confirm with the owner right before running it.
2. While `signups_enabled` is false, only allowlisted emails can use the app. Start with `owner@example.com`. Anyone else sees a waitlist message.
3. Keep admin-only test runs.

**Live acceptance:** the owner signs in with Google on Vercel and creates a supplier and a batch. A second Google account can't see them. A non-allowlisted account gets the waitlist message.

**Edge cases to cover:** expired, wrongly signed, wrong-audience and rotated-key tokens (refetch once, rate-limited); a user banned mid-session; a changed email; the user cap reached; duplicate supplier names per owner; deleting a supplier that has batches (refused); Render cold starts; about one database query per request, since each costs about 95 ms.

**Scope**
- Neon Auth with Google sign-in in the frontend.
- API token verification, profiles, roles and bans.
- Ownership on all data.
- Usage ledger, limits framework, per-user overrides, `signups_enabled`, total-user cap, `processing_enabled`.
- Supplier and batch CRUD with UI.

**Acceptance criteria**
- A signed-in user manages only their own data.
- `signups_enabled` is respected.
- Lifetime and rolling 24-hour submission limits block correctly.
- An admin override allows an exempt account.

**Tests**
- Forged, expired and wrong-audience tokens are rejected, and so are banned users.
- **Cross-user access is attempted on every endpoint.**
- Simultaneous submissions can't both pass a limit.
- Key rotation: an unknown `kid` triggers a key refetch.

### M1 implementation notes

**Built:**
- Migration `0003` as approved, plus a foreign key from `pipeline_runs.batch_id` to `batches` (planned in the schema). It stops with a clear message, rather than deleting anything, if queue rows without profiles exist.
- `app/auth`: a verifier registry with the `neon_auth` verifier (EdDSA via cached JWKS, issuer, audience, expiry, 30-second leeway), profiles, and admission rules. Active profiles are cached for 60 seconds, so most requests make one database query.
- `app/usage`: effective limits with per-user overrides (null means unlimited), `record_submission` (locks the profile row, then checks lifetime and rolling 24-hour limits), and append-only restorations. Nothing calls `record_submission` yet: the first submission endpoint arrives with processing in M2/M3.
- `app/catalog` and endpoints: `GET /me`, `GET|POST /suppliers`, `GET|PATCH|DELETE /suppliers/{id}`, `GET|POST /suppliers/{id}/batches`, `GET|PATCH|DELETE /batches/{id}`. Each is one query.
- Test runs require an active admin. Anyone else gets 404, and runs are owned by the calling admin. `ADMIN_TOKEN` is gone.
- Frontend: Google sign-in through the app's own `/api/auth` route (first-party cookies), a waitlist message, and supplier and batch pages.

**Differences from the proposal:**
- Tokens without a verified email are refused (D27), because Neon Auth also allows unverified email-and-password sign-up.
- Neon Auth gives ordinary users the role `authenticated`. Only `admin` grants admin.
- A ban takes effect with the next token (Neon issues 15-minute tokens). Setting a profile to `disabled` takes effect within 60 seconds.
- Frontend types are hand-written for now (D28).

**Tests (on Neon `test`):** 76 new tests in `test_auth.py`, `test_access.py`, `test_catalog.py`, `test_usage.py` and `test_test_runs.py`, plus migration tests. Every catalog endpoint is attempted cross-user, unauthenticated and waitlisted.

**Done before merging (2026-11-08, with the owner's approval):**
1. Deleted the M0B test-run rows: `production` 2 runs and 5 jobs (owner `system`), `dev` 6 runs and 15 jobs (M0B end-to-end owners). Both queues are empty, so migration `0003` can run.
2. Render: `NEON_AUTH_BASE_URL` set to the `production` Neon Auth URL. `ADMIN_TOKEN` set to empty, which hides the old test-run endpoints. Render's tool can't delete a single variable, so the owner can remove it in the dashboard.
3. Vercel: `NEON_AUTH_BASE_URL` and `NEON_AUTH_COOKIE_SECRET` set for production and preview. They take effect with the next deployment.
4. Neon Auth on `production`: `https://ai-catalog-intelligence.vercel.app` added as a trusted domain. `localhost` was already allowed on `production` and `dev`.
5. D27 approved: email-and-password sign-in turned off (and sign-up disabled) on `production` and `dev`.

Preview deployments use other URLs, which aren't trusted, so sign-in works only on the production URL.

**Fixed after the first deploy:** the frontend sent Better Auth's opaque session token instead of the JWT, so the API answered 401 (`malformed`) and sign-in looped back to the sign-in button. The proxy's cached `get-session` response carries no `set-auth-jwt` header. The frontend now fetches the JWT from `/token`.

**Open issue: no one can be an admin yet (owner chose to leave it, 2026-11-08).** Neon Auth always issues `"role": "authenticated"` in its tokens, whatever `neon_auth."user".role` says, because Neon uses that claim to pick the database role for its Data API. So the token-based admin check never passes, and admin-only test runs return 404 for everyone. The proposed fix is to make `user_profiles.role` the source of truth, set in SQL and cached for 60 seconds like the rest of the profile. That needs a new decision. The owner's `neon_auth."user".role` was set to `admin` on `production` while this was being found, and is harmless.

**Live acceptance (2026-11-08):** the owner signed in with Google on Vercel, created suppliers and batches, and confirmed a second account can't see them and a non-allowlisted account gets the waitlist message. `ADMIN_TOKEN` was deleted from Render.

---

## M2 File ingestion and storage

**Status:** proposal approved by the owner on 2026-11-09. **Completed on 2026-11-09.** Merged and deployed, migration `0004` applied to `production` (by CI) and `test`, and live acceptance passed (owner, 2026-11-09). `dev` is still at `0003`. See the implementation notes below.

**Scope**
- Presigned uploads directly to S3, and completion checks.
- File records, type and hash checks.
- ZIP expansion with safety limits.
- File count and size limits, with clear messages.

**Acceptance criteria**
- All supported types upload directly to S3.
- Re-uploading the same file in a batch is deduplicated by hash.
- Malicious ZIPs are rejected.
- Over-limit files are rejected or reported, never silently dropped.

**Tests**
- Zip bomb, zip slip and wrong magic bytes.
- Abandoned uploads.
- Over-limit files and over-limit totals.
- Presigning another user's batch returns 404.

**Approved decisions (2026-11-09):** one bucket, `ai-catalog-intelligence`, with `prod/`, `dev/` and `test/` prefixes (D29); browser hashing checked by S3 (D30); process once, retry only after a platform failure (D31); ZIP limits of 200 entries, 100 MB total, 10 MB per entry and 100:1 compression (in `config.yaml`); infrastructure applied by Claude through the connectors.

**Implementation notes**
- Migration `0004`: `source_files` (with ZIP entries as child rows), unique content per batch, and batch statuses `queued`, `processing`, `completed` and `failed`. A run's batch follows its status, and a failed run restores its submission.
- API: `POST/GET /batches/{id}/files`, `POST /files/{id}/complete`, `DELETE /files/{id}`, `POST /batches/{id}/process`, `GET /batches/{id}/run`. Deleting a batch is refused while it's processing, and removes its objects.
- `app/storage` (S3 and in-memory backends), `app/files` (registration, limits, submission), `app/ingest` (file-kind registry, signatures, safe ZIP expansion).
- Worker pipeline `ingest`: `ingest_plan`, one `ingest_file` per file, then `ingest_finalize` (image limit, run counts).
- Frontend: a batch page with drag-and-drop uploads, file statuses and reasons, Process, and run progress.
- CloudFormation: bucket, S3 gateway endpoint, IAM for the API, worker and a `catalog-dev` user.
- Verified against real S3 (`tests/test_storage_live.py`): the bucket refuses a form upload whose size or SHA-256 differs from its link.
- Fix after release: Process and Delete asked with `window.confirm`, which the desktop app's browser pane blocks silently, so they did nothing. They now confirm on the page (`ConfirmButton`).

**Live acceptance (2026-11-09):** the owner uploaded four JPEGs on Vercel and processed the batch. The EC2 worker checked all four within seconds and kept them, and the batch showed completed. Other file types, ZIPs and rejections are covered by the automated tests, not yet by a live run.

---

## M3 Spreadsheet extraction

**Status:** proposal approved by the owner on 2026-11-10, with LLM column mapping in place of the fixed list first proposed (D32). **Completed on 2026-11-10.** Merged and deployed, migration `0005` applied to `production` (by CI), `dev` and `test`, and live acceptance passed (owner, 2026-11-10). See the implementation notes below.

**Scope**
- CSV and XLSX parsers.
- Column mapping, source records and attribute observations.
- Identifier canonicalization for SKU and model numbers.
- **Product limit:** first N products in file order, with discard counts stored and shown.

**Acceptance criteria**
- Every value traces to its file, sheet, row and column.
- Only the first N products are processed, and the discarded count is shown.
- Large files are chunked.

**Tests**
- Encodings, merged headers, empty rows and formula cells.
- Duplicate SKUs.
- Partial failure: a bad row doesn't fail the chunk.
- Idempotent re-run.
- Exactly N and N+1 products.

**Approved decisions (2026-11-10):** columns are mapped by known names first and the LLM for the rest, with each supplier's layout remembered (D32); the LLM is gpt-oss-120b on Bedrock, called by the worker through its instance role; rows with no identifier are skipped and counted; unmapped columns are kept with each row but produce no observations; the seed attributes are SKU, model number, barcode, name, brand, description, colour, material, price, currency, pack quantity, net weight, capacity, length, width, height and the supplier's category.

**Implementation notes**
- Migration `0005`: `categories` (empty until M6), `attribute_definitions` (17 seeded), `model_runs`, `column_mappings`, `spreadsheet_sheets`, `source_records`, `products` and `attribute_observations`. Expand-only.
- The `ingest` pipeline gains a third stage, `extract`. `extract_select` maps every sheet, chooses the first N products in file order, and stores the sheets, products and counts. It then creates one `extract_rows` job per 1,000-row block that holds a kept product, so a 50,000-row sheet under a limit of 5 usually needs one job.
- `app/ingest/spreadsheet`: a reader registry (CSV and XLSX), header detection, and column mapping. XLSX is streamed with openpyxl. Merged ranges are read from the sheet XML and filled across their range.
- `app/llm`: a backend registry (`bedrock` through LangChain, `fake` for tests), Jinja2 prompts under `app/templates/prompts`, and a runner that records every call in `model_runs` and reuses a stored reply for the same question. LLM setup moved here from M7, as the M7 scope allows. The global spend cap stays in M7. M3 caps mapping calls at 10 per run.
- `app/catalog/identifiers.py`: identifier cleanup. `app/products`: read access for the API.
- API: `GET /batches/{id}/products`, `GET /products/{id}`, `GET /batches/{id}/sheets`. `GET /batches/{id}/run` adds product counts and lists rows and cells that couldn't be used.
- Frontend: the batch page shows products, how each sheet was mapped, and unusable rows. A product page shows every value with its file, sheet, row and column.
- CloudFormation: the worker role gains `bedrock:InvokeModel` on gpt-oss-120b in Sydney (policy `worker-llm`), applied through change set `m3-bedrock`.
- Verified against the real model (`tests/test_llm_live.py`): a German price list with a title row mapped correctly, and gross weight and stock were left unmapped.

**Behaviour worth knowing**
- Values shown as text follow the cell's format where it changes meaning: decimal places, leading zeros, percentages and dates. Thousands separators and currency symbols are left out, and the format is kept with the row.
- A formula cell uses the result Excel last saved. A formula that was never calculated, or a spreadsheet error such as `#N/A`, has no value: it's reported with its cell, and no observation is made.
- A row is one product by its SKU, or its model number if it has no SKU, or its barcode. Case and surrounding spaces are ignored. Dashes and slashes are kept.
- A product whose only row couldn't be stored (too large) still exists, with nothing read for it, and the row is reported.
- An image-only batch completes with no products. Products from images come with M4 and M5B.

**Release notes**
- The first CI run failed one check: loading the API peaked at 165 MB on Linux, over the test's 150 MB guard, so the guard was raised to 200 MB. M4 found the real cause (see M4's release notes): the test measured the pytest process's memory, not the API's. The guard is back at 150 MB.
- The fix after it changed only a test, so CI skipped the worker deploy (it compared with the previous push). The worker was deployed by hand over Systems Manager, and CI now compares with the last fully successful run.
- `dev` was migrated through `run_sql` with the SQL Alembic generates, because local Alembic runs were blocked in this session.

**Live acceptance (2026-11-10):** the owner processed `acme-price-list.csv` (a title row, 15 columns, 8 products, a repeated SKU and a row without one) on Vercel. The run took 6 seconds on EC2 and kept 5 of 8 products in file order. The header was found in row 3, and 12 columns were recognised by name. One gpt-oss-120b call ($0.00045, 4 seconds) mapped "Höhe (cm)" to height and left gross weight, stock and lead time unmapped. The repeated SKU became one product with both rows' values. Workbooks, merged headers and the LLM fallback are covered by automated tests, not yet by a live run.

---

## M4 Document and image text extraction

**Status:** proposal approved by the owner on 2026-11-10. **Completed on 2026-11-10,** by the owner's decision, without a live acceptance run. Merged and deployed (CI run 35): migration `0006` applied to `production` (by CI) and `test`, and the worker and API run `dde1f89`. `dev` is still at `0005`. The pipeline was exercised on the owner's real photos locally, against the real model (below). See the implementation notes below.

**Scope**
- Text PDF extraction with page limits.
- Image extraction from PDFs.
- OCR behind a registry.
- Optional LLM-assisted extraction with provenance.

**Acceptance criteria**
- PDF values record page and location.
- Conflicts with spreadsheet values are flagged, never overwritten.

**Tests**
- A conflict is preserved with both sources.
- No value exists without a source.
- Malformed PDFs.
- RapidOCR compared with Tesseract on sample images, with results recorded.

**Approved decisions (2026-11-10):**
- PDFs and photos add values to the batch's spreadsheet products. A batch with no spreadsheet products takes its products from codes found in its PDFs and photos, first N in file order (D33).
- Photos are read with OCR (RapidOCR), then gpt-oss-120b reads values from the text. Claude Haiku isn't used: OCR is needed anyway, and checking values against OCR text keeps the no-invention rule. Switching photos to a vision model stays open if real photos show OCR missing packaging text.
- The global LLM spend cap ($10 a month) moves forward from M7.
- Tesseract is installed in the local conda environment only, for the comparison.

**Implementation notes**
- Migration `0006`: `ocr_results`, `document_files`, `products.origin` (`spreadsheet` or `document`), and an `llm_budget` mapping state for sheets. Expand-only.
- The `ingest` pipeline gains two stages. `documents` reads every kept PDF and image, one job each: PDF pages with their own text are read with pdfplumber, and pages with under 40 characters are rendered and read with OCR (keeping whichever reading found more, and retrying an empty OCR reading at half resolution). `document_values` then goes through the pages in file order.
- In a batch with spreadsheet products, a page is sent to the LLM only if it names one of them (by its key, SKU, model number or barcode, however punctuated), so discarded products cost no LLM calls (D13). In a batch without, every page is read, since codes only show up by reading.
- Every value the LLM reports must quote text that is on the page, with the value inside the quote; what's stored is the page's own text. Each observation's location holds the page, the quote, and its box. Values that fail are dropped and reported.
- Images inside PDFs become files of the batch, like ZIP entries (`page-1-image-1.jpg`): icons under 150 px are skipped and repeats kept once. They count toward the image limit in upload order.
- `app/ocr`: an engine registry with `rapidocr` (production) and `tesseract` (comparison only). `app/ingest/documents`: page model, quote check, PDF reading. `app/llm/runner.py`: the monthly spend cap.
- API: `GET /batches/{id}/documents`. `GET /products/{id}` adds `differing_attributes`: attributes whose sources disagree, ignoring only case and spacing. Nothing is resolved.
- Frontend: a "PDFs and photos" panel on the batch page. The product page shows each value's page and quote, and a "differs" badge.
- `backend/tools/compare_ocr.py` compares the engines.

**OCR comparison (D7), 2026-11-10, generated images:** 32 images (4 texts in 8 conditions), scored exactly.

| Condition | RapidOCR | Tesseract |
|---|---|---|
| Clean | 100% | 97% |
| Small type | 100% | 97% |
| Rotated 6–7° | 100% | 63% |
| Blurred | 100% | 98% |
| Low contrast | 100% | 98% |
| Light on dark | 100% | 98% |
| Noisy | 99% | 16% |
| Heavy JPEG compression | 100% | 97% |
| **All** | **100%** | **83%** |
| Product codes read exactly | 32 of 32 | 27 of 32 |
| Median time per image (Mac) | 174 ms | 111 ms |

**Owner's photos, 2026-11-10:** four photos of a selfie-stick box (front, features side, spec panel with a printed label, and instructions).

| Photo | RapidOCR | Tesseract |
|---|---|---|
| Spec panel and label | All 6 spec lines exact, plus the label: brand, colour, MRP, dimensions and the code `B094YFFSMY` | Spec lines garbled ("Solfie", "Hoight", "18,5 om"); the label missed entirely |
| Features side | All 11 lines exact | Readable, with stray symbols |
| Instructions (small type) | All 27 lines exact | 16 lines, many words wrong, some lines missing |
| Front | 6 lines; the logo read as "TY-G-O-T" | 2 lines; the logo read as "Yrreot" |

RapidOCR stays (D7).

**Release notes**
- The first CI run reported the API loading in 907 MB on Linux. The cause was the test, not the API: on Linux, `ru_maxrss` carries over from the process that starts a program, here the pytest process, which earlier tests had filled with OCR models. The same flaw explains M3's 165 MB. The test now reads the program's own peak (`VmHWM` in `/proc/self/status`), and the guard is back at 150 MB. On a Mac the API loads in about 78 MB, and the live API uses about 110 MB of Render's 512 MB.

**Live acceptance:** not run. The owner marked M4 complete on 2026-11-10 before trying it on Vercel. Suggested check when convenient: the four selfie-stick photos alone (expect product `B094YFFSMY` with about 8 values, and no product from the logo), and `acme-price-list.csv` with `acme-specs.pdf` (expect capacity on `AK-1003` to show "differs").

**Real photos through the whole extraction (gpt-oss-120b, image-only batch):** the label photo made product `B094YFFSMY` with 8 values, all found on the photo (name, net weight "113 gm", colour, brand, price, currency, dimensions, code). On the front photo the model called the logo "TY-G-O-T" a SKU; since the text is really on the box, the quote check let it through. A rule now stops it: a code found on a page makes a product only if it has a digit and at least four letters and digits, or is a barcode with a valid check digit. Photos with no code (the features side, the instructions) give no values in a batch without a spreadsheet; grouping them with their product is M5B. The owner chose on 2026-11-10 to leave them for M5B, rather than attach them whenever a batch names a single product, which could put one product's specs on another.

---

## M5A Image processing

**Status:** proposal approved by the owner on 2026-11-10, with Titan embeddings on Bedrock in place of OpenCLIP (D34). **Completed on 2026-11-15.** Merged and deployed (CI run 37), migration `0007` applied to `production` (by CI) and `test`, and live acceptance passed (owner, 2026-11-15). `dev` is still at `0005`.

**Scope**
- CPU throughput benchmark for CLIP and OCR, which informs chunk sizes.
- pHash, thumbnails, duplicate grouping.
- OpenCLIP embeddings in pgvector, with image limits.

**Acceptance criteria**
- Duplicates are grouped.
- Embeddings are tagged with their model.
- Throughput is documented, and the EC2 cap is checked against it.

**Tests**
- Resized and slightly edited duplicates are detected.
- Comparing across models or across users is refused.

**Approved decisions (2026-11-10):**
- Embeddings from Titan Multimodal Embeddings G1 on Bedrock, chosen over OpenCLIP after test calls (D34). No PyTorch and no model on the worker, so the owner's choices about where to keep the model no longer apply.
- Thumbnails shown in the app, through signed links; the API user gains read access to `prod/derived/`.
- The simpler near-duplicate rule: similar embeddings and close colours. pHash is stored but not part of the rule.

**Implementation notes**
- Migration `0007`: `embedding_models` (Titan seeded), `images`, `image_duplicate_groups`, `image_embeddings` (`vector(1024)`, no index), and `pipeline_runs.image_duplicate_groups`.
- The `ingest` pipeline gains two stages. `images` processes every kept image in chunks of 8: download, upright and RGB, pHash, a 12-bin hue histogram, a 320 px thumbnail under `derived/`, and an embedding. `image_groups` then groups near-duplicates across the batch with one exact SQL comparison, and marks each group's largest image as its representative. Nothing is removed.
- `app/vision`: features and an embedder registry (`bedrock` Titan, `fake` for tests). `app/images`: similarity queries that always name the batch or images, the owner and the model; comparing across models or users raises an error.
- API: `GET /batches/{id}/images`, with 15-minute signed thumbnail links. Run status adds `image_duplicate_groups`. Deleting a batch also deletes its thumbnails.
- Frontend: an Images grid on the batch page, with each duplicate group outlined, largest first.
- `tools/benchmark_images.py` times each step; `tests/test_embeddings_live.py` checks Titan's threshold (opt-in).
- The colour check started as a 4x4 grid of average colours. On the owner's photos it split a brightened copy (19.7 against a limit of 18) and barely noticed a recolour (6.7), so it was replaced by the hue histogram before release.

**Throughput (D20's benchmark), 2026-11-10, on a Mac, the owner's photos as uploaded and scaled to 12 megapixels:**

| Step | Median | Slowest |
|---|---|---|
| Load | 0.02 s | 0.04 s |
| Hash and colour | 0.01–0.03 s | 0.03 s |
| Thumbnail | 0.01 s | 0.01 s |
| OCR (M4) | 0.45–0.61 s | 1.16 s |
| Titan embedding | 0.69–1.06 s | 2.63 s |

About 1.5–2 seconds per image, almost all OCR and the Titan call, so a 25-image batch needs about a minute of image work here, and an estimated 2–3 minutes on the t3a.medium. With about a minute to boot, a batch costs well under 10 EC2 minutes, so the 30-hour monthly cap allows hundreds of batches. Real worker timings are stored per image (`images.timings`).

**On the worker (first production run, 2026-11-15):** Titan embedding 0.17–0.24 s per image (in-region, faster than from the Mac); hash and colour 6–105 ms; thumbnail 28–67 ms; OCR jobs 2.4–8.0 s per photo, the first including model loading. The whole four-photo batch took 38 seconds from start to finish, of which reading values with the LLM took 16 s.

**Live acceptance (2026-11-15):** the owner processed the four selfie-stick box photos on Vercel. All four were processed, embedded and shown with thumbnails, and none was grouped as a duplicate, as expected for four different sides of a box. The label photo made product `B094YFFSMY` with 8 values, the logo on the front made no product (its 4 values were set aside with reasons), and the run cost about $0.003 (LLM $0.0024, embeddings $0.0003). The API logs showed no errors. Near-duplicate grouping is covered by the automated tests, not yet by a live run.

---

## M5B Image association

**Status:** proposal approved by the owner on 2026-11-15 (D35). **Completed on 2026-11-15.** Merged and deployed, migration `0008` applied to `production` (by CI) and `test`, and live acceptance passed (owner, 2026-11-15). `dev` is still at `0005`.

**Scope**
- Signal registry: SKU/model number, filename, OCR text, brand and attributes, supplier identifiers, visual similarity.
- Scoring and candidates, with evidence stored.
- `review_tasks` backbone and a minimal review UI.

**Acceptance criteria**
- A visual-only match is never auto-accepted.
- Uncertain matches go to review with visible evidence.

**Tests**
- Adversarial lookalike products.
- Conflicting signals.
- Threshold configuration, tuned on a small labelled sample.

**Approved decisions (2026-11-15):** accepted photos are read for values; near-duplicates of an accepted photo go to review with its product suggested; a minimal review panel on the batch page, with the full queue in M9 (D35).

**Implementation notes**
- Migration `0008`: `image_match_candidates`, `product_images`, `review_tasks` (one per image), `review_events` (append-only, enforced by a trigger), and image match counts on `pipeline_runs`.
- The `ingest` pipeline gains an `associate` stage: `associate_images` scores every image against every product, auto-accepts single-code matches, opens review tasks, and queues `photo_value` jobs for accepted photos not yet read. A new `photo_values` pipeline (`photo_values_plan`, then `photo_value` per photo) reads photos a person accepts later.
- `app/matching`: a signal registry with seven signals, weights in config, and the decision rule. `app/review`: tasks, decisions and the audit trail.
- API: `GET /batches/{id}/matches`, `GET /batches/{id}/review-tasks`, `POST /review-tasks/{id}/decision`, `POST /batches/{id}/photo-values`. `GET /products/{id}` adds the product's photos. Run status adds images matched, to review and unmatched.
- Frontend: a "Needs review" panel (photo, candidates with evidence, Accept or None of these), "Shows:" on each image in the grid, and a photo strip on the product page.
- Added during the build: the `only_product` signal. Without it, a code-less photo in a one-product batch (the owner's features side) had no evidence at all and could never be confirmed.

**Labelled sample (2026-11-15):** the owner's four box photos, with real OCR text and real Titan similarities, and one product, `B094YFFSMY`.

| Photo | Decision | Score | Evidence |
|---|---|---|---|
| Label | Auto-accepted | 1.00 | Its code is on it |
| Front | Review | 0.52 | "TYGOT" on it, only product, looks like the label (0.75) |
| Instructions | Review | 0.45 | "TYGOT" on it, only product |
| Features side | Review | 0.20 | Only product |

**Live acceptance (2026-11-15):** the owner processed the four box photos on Vercel. Processing took 23 seconds: the label photo was accepted automatically and the other three went to review, with the scores above. The owner accepted all three; each decision was recorded, and a separate run read the three photos in 10 seconds. `B094YFFSMY` gained "18.5 cm", "Stainless Steel" and "113 Grams" from the features side, and brand, name and description from the front. The API logs showed no errors.

**Fix after release:** reading the confirmed front photo stored its logo, "TY-G-O-T", as the product's model number. Stored codes are matching evidence, so a later photo of the logo would have matched on a "code". A SKU or model number read from a page or photo must now look like a code (a digit, and at least four letters and digits), as for making a product; others are dropped and reported. The value already stored in production is left as recorded.

Generated cases in the tests cover lookalike products, conflicting codes, two codes on one photo, near-duplicates and file names.

---

## M6 Product normalization

**Status:** proposal approved by the owner on 2026-11-15 (D36). **Completed on 2026-11-15.** Shipped as `99a4423`, migration `0009` applied to `production` (by CI) and `test`, and the owner's live test is done. `dev` is still at `0005`.

**Scope**
- Attribute mapping onto definitions.
- Deterministic unit conversion.
- Category assignment.
- Variant flagging.

**Acceptance criteria**
- Conversions are deterministic.
- Original values are retained.
- Variants are flagged and never merged.

**Tests**
- Unit edge cases, such as mixed and missing units.
- Ambiguous categories go to review.
- The rule version is recorded on each normalized value.

**Approved decisions (2026-11-15):** India defaults (g, ml, mm with lengths shown in cm; INR when no currency is given and a lone "$" as USD, both marked assumed; lakh grouping); combined sizes split into length, width and height; numbers agree within 1%; an LLM chooses a category from a fixed Home & Kitchen tree, with "other" or confidence below 0.7 going to review; variants flagged by name and code rules, with photo similarity as support only (D36). Both earlier production batches were deleted first, at the owner's request.

**Implementation notes**
- Migration `0009`: `normalized_values` (one or more per observation, under a rule version), `product_attributes` (the value each product shows and its state), a seeded category tree of 29 categories with `position`, category columns on `products` (state, source, suggestion, confidence, reason), `variant_candidates`, and the `attribute_conflict` and `category` review kinds, one task per product attribute and per product's category. Expand-only.
- The `ingest` and `photo_values` pipelines end with a `normalize` stage. `normalize_batch` stores standard values, chooses what each product shows, opens or closes conflict tasks and flags variants; then one `categorize_product` job per product a person hasn't categorized. A person's decision always stands, and every job can run again safely.
- `app/normalization`: unit and number rules (`values.py`), choosing the shown value (`select.py`) and variant rules (`variants.py`). Units, aliases, currencies, tolerance and source ranks live in `config.yaml` (`normalization`).
- States: `verified` (every reliable source agrees; the best source is shown: spreadsheet, then PDF text, then photos), `conflict` (they disagree; a person decides), `unclear` (values exist but none can be relied on, such as no unit or a range), `missing`, and `reviewer_set`.
- API: `GET /categories`, `GET /batches/{id}/variants`; `GET /products/{id}` adds catalog values and the category; review tasks and decisions cover the new kinds (`choose` a value or a category, or `set` your own value).
- Frontend: a "Catalog values" table and the category on the product page, conflict and category items in the review panel, and "Possible variants" on the batch page.
- Found while testing: the code rule would have flagged sequential codes such as AK-1003 and AK-1004 as variants (base "AK"). A code base must keep a digit.

---

## M7 Catalog enrichment

**Status:** proposal approved by the owner on 2026-11-15 (D37), all eleven decisions as recommended. **Completed on 2026-11-15,** by the owner's decision, without a live acceptance run. Shipped as `8f0b2d0`: migration `0010` applied to `production` (by CI) and `test`, and the worker and API run `8f0b2d0`. `dev` is still at `0005`. A check of `production` afterwards found no batches and no enrich runs, so the feature has run against the real model only in the opt-in live test, not through the deployed app.

**Scope**
- Titles, descriptions and bullet points from verified attributes.
- LLM calls through LangChain, using gpt-oss-120b for text, with a per-task config entry. If LLM calls first arrive in M4, the setup moves there.
- Prompts in template files.
- `blocked_quota` for enrichment jobs. The global LLM spend cap itself arrived in M4.

**Acceptance criteria**
- Generated claims cite attributes.
- Missing data stays missing.
- Model metadata is logged.
- The spend cap stops new calls.
- After this module, the owner can decide whether to open sign-ups.

**Tests**
- Stubbed Bedrock only; no live calls in CI.
- Fabrication checks using products with missing fields.
- Malformed and truncated output.
- A retry reuses the stored result.
- The cap is reached mid-run, and the job pauses without being lost.

**Implementation notes**
- Migration `0010`: `generated_content` (one row per product version, citations frozen per claim), `products.enrichment_state`, `enrichment_message` and `enrichment_at`, and `paused` runs with `pause_reason`. Expand-only. Applied to `test` on 2026-11-15 with the owner's OK, then to `production` by CI; `dev` is still at `0005`.
- New `enrich` pipeline: `enrich_plan` picks products whose text is missing, failed or out of date, then one `enrich_product` per product (one gpt-oss-120b call). It uses no submission and isn't tied to the batch's status, like `photo_values`.
- `app/enrichment`: `facts.py` (settled facts, left-out reasons, fingerprint; shared with the API), `grounding.py` (the checks in D37, using M6's unit rules), `models.py` (reply shape), `repository.py`. Prompt `catalog/enrichment`, settings under `enrichment:` and `llm.tasks.enrichment` in `config.yaml`.
- `blocked_quota` is used for the first time: `QuotaBlockedError` from a handler makes `queue.block` set that job and the run's other pending jobs to `blocked_quota` (no attempt counted) and the run to `paused`; `queue.resume_run` puts them back. Monthly spend is now readable by the API (`app/usage/spend.py`).
- API: `GET`/`POST /batches/{id}/enrichment`, `POST /batches/{id}/enrichment/resume`; `GET /products/{id}` adds `generated_content`.
- Frontend: a "Generated content" panel on the batch page (Generate, paused banner with Resume, each product's state) and a section on the product page (text with a chip per cited fact, Left out, Regenerate).
- Found while building: a product code such as "TG-500X" was read as the number 50. Codes are now checked as copied text from a cited fact before numbers are read.

**Tests:** 28 fast tests in `test_enrichment.py` (facts, every fabrication case, citations, lengths, malformed replies) and 13 database tests in `test_enrichment_worker.py` on Neon `test` (end to end, fabrication retried then refused, truncated replies, reuse after a lost lease, the cap pausing mid-run and resuming, a paused run not blocking other work, out of date after a review decision, skipped products, cross-user, signed-out and waitlisted access). All pass. **Live check (decision 11, 2026-11-15, `tests/test_enrichment_live.py`):** one real gpt-oss-120b product (a 1 L steel bottle with a supplier description, a colour in conflict and a price). The first reply was refused because it put the model number in the description and a bullet. The retry, told why, passed every check: "Milton Insulated Water Bottle THERMOSTEEL-1000, 1 L", three description sentences and five bullets. No price or colour appeared, and "leak-proof" and "24 hours" were allowed because the supplier's description says them. 782–870 input and 1,101–1,333 output tokens per call (more output than estimated, from reasoning), $0.0018 for both calls, so about $0.0009 per call. The prompt's title-only rule was made firmer afterwards; that wording hasn't had a live call.

Unrelated and pre-existing: running `test_ingest_kinds` before `test_vision` in one process fails the vision tests, because it sets Pillow's global pixel limit.

**Live acceptance:** not run. Suggested check when convenient: process `acme-price-list.csv`, settle its review items, press Generate, then change a value in review and confirm the product's text shows "Out of date" and regenerates as version 2.

---

## M8 Catalog validation

**Status:** proposal approved by the owner on 2026-11-15 (D38), all nine decisions as recommended. **Completed on 2026-11-15,** by the owner's decision, without a live acceptance run. Shipped as `c21edc4` (CI run 42): migration `0011` applied to `production` (by CI) and `test`, and the worker and API run `c21edc4`. `dev` is still at `0005`.

**Scope**
- Rule registry and a small configurable marketplace ruleset.
- Duplicate detection across products in a batch.
- Validation issues.

**Acceptance criteria**
- Missing, conflicting and duplicate data is flagged.
- Rules come from config.

**Tests**
- Registry contract, each rule, and severity handling.

**Implementation notes**
- Migration `0011`: `validation_overrides` (warnings set aside) and `batches.target_marketplace` (default `shopify`). Expand-only. Applied to `test` on 2026-11-15 with the owner's OK, then to `production` by CI.
- `app/validation`: `base.py` (findings, the rule registry), `context.py` (loads one batch in seven queries), `engine.py` (severities from config and the profile, set-asides by fingerprint, errors first), `repository.py` (set aside and undo, each recorded in `review_events`). 17 rules in four modules under `rules/` (attributes, product, text, duplicates) rather than one module per rule.
- Settings under `validation:` in `config.yaml`; the API refuses to start if they name an unknown rule or attribute, or leave a rule without a default severity. Marketplace numbers are marked unverified (D38, decision 8).
- API: `GET /batches/{id}/validation`, `PUT /batches/{id}/validation/profile` (in place of the proposed `PATCH /batches/{id}`, which requires a name), `POST` and `DELETE /products/{id}/validation/set-aside`; `GET /products/{id}` adds `validation`.
- Frontend: a "Checks" panel on the batch page (marketplace, "N of M ready", each product's summary) and a section on the product page (issues by severity, where to fix them, Set aside with a reason, Undo).

**Tests:** 33 fast tests in `test_validation.py` (registry and settings, every rule, every profile, duplicates and variants, set-asides and fingerprints, a broken rule) and 5 database tests in `test_validation_worker.py` on Neon `test` (checks and profiles end to end, current after a change, set aside until it changes with undo and audit, cross-user, signed-out and waitlisted access, an empty batch). All pass.

**Live acceptance:** not run. Suggested check when convenient: process `acme-price-list.csv`, open the Checks panel, switch to Amazon India, and set a warning aside and undo it on a product page.

---

## UI redesign (between M8 and M9)

**Status:** owner's request on 2026-11-15, before M9. Design chosen from options in `ui-options/` (D39). **Completed on 2026-11-15:** shipped as `6ada9e1`, with fixes in `7cd31b8` and `34054e6`; the owner reviewed the signed-in pages on the live site and asked for the follow-ups below. The latest copy change wasn't tried live before the owner marked it done.

- **Design system:** Bazaar · Indigo tokens, Fraunces and DM Sans, cards, badges, tabs, stepper and stats in `globals.css`; dark mode follows the system.
- **New pages:** a landing page (what it does, an example of a traced value, the eight pipeline steps, six features, sign-in) and `/how-it-works` (six steps with tips, and the preview limits). Shared text in `src/lib/content.ts`.
- **Shell:** a header with pill navigation (Suppliers, How it works) and the account once signed in, a Sign in button when not, and a footer on every page.
- **App pages:** suppliers as cards; a supplier's batches with status badges; a batch page with breadcrumb, progress steps, a Files card, and results in tabs; a product page in two columns of cards.
- **After the owner's first review (2026-11-15):** a highlighted "Next step" card on a processed batch (decide review items, then write copy, then fix errors, then "ready"); counts on the Review, Checks and Sources tabs; a processed batch's Files folded away at the bottom. Copy is now written automatically, and counts written as words no longer fail the copy checks (D37, amended). Shipped as `7cd31b8` (CI run 44); the API and worker run `7cd31b8`. Then copy waits for a product's review and is written when the batch's last review item is decided, and each product shows why its copy is needed (D37, amended). Shipped as `34054e6` (CI run 45).
- **Checked:** type check, lint and production build; the landing and guide pages in the browser at desktop and phone widths, light and dark. Signed-in pages weren't checked before shipping: sign-in fails in the desktop app's browser pane (Google returns, but the session exchange answers 400 "Verification not found", most likely a dropped cookie), so they're reviewed on the live site.

---

## M9 Human review

**Status:** proposal approved by the owner on 2026-11-15 (D40), all eight decisions as recommended. In progress on branch `m9-review`.

**Scope**
- Full review UI: editing, approving, bulk actions.
- **Editing a product's values** (owner, 2026-11-15): set or clear any value, not only through a review item, recorded in the audit trail. Found in testing: a missing brand or two products sharing a barcode raise errors with nothing in the app to fix them, because neither opens a review item. Each check gets a "fix" link to the value it's about, and the edit flows through (check cleared, copy rewritten).
- **Duplicates:** mark a flagged pair as the same product, so one is left out of approval and export.
- Audit trail.

**Acceptance criteria**
- Every change is recorded with its before and after values.
- Only approved products can be exported.

**Tests**
- Concurrent edits.
- Approval rules.
- Audit integrity.

**Implementation notes (built and checked locally; not yet shipped)**
- Migration `0012`: `products.revision`, approval and rejection columns and `duplicate_of`. Expand-only. Applied to `test` on 2026-11-15 with the owner's OK.
- `app/approval`:
  - `rules.py`: blockers, the approval fingerprint and the approval state, worked out on read.
  - `repository.py`: value and copy edits, approve, reject, restore, duplicates, bulk actions, and `approved_products` for M10.
  - `history.py`: a product's changes in words.
- **Writes:** each one locks the product, refuses a stale revision, records one event with its before and after, and increments the revision. Review decisions increment the revision too.
- **Value edits:** read with M6's unit rules and stored as a person's decision, which normalization already never overwrites. Editing a value resolves any open conflict on it. The product's own code (SKU) can't be edited.
- **Edited copy:** a version with status `edited`. Automatic writing skips it. A new `edited_copy` check warns when hand-written text says something the values don't.
- **Checks:** each issue names the attribute to edit (`attribute`), and rejected products drop out of duplicate checks.
- **API:**
  - `PUT /products/{id}/values/{code}` and `PUT /products/{id}/copy`;
  - `POST /products/{id}/approve`, `/reject`, `/restore` and `/duplicate`;
  - `GET /products/{id}/history`;
  - `GET /batches/{id}/approval`, plus `POST /batches/{id}/approve` and `/reject`;
  - `GET /review`;
  - `GET /products/{id}` adds `approval`.
- **Frontend:**
  - product page: approval bar, Edit or Add on every value, edit links on checks, duplicate buttons, a copy editor and a History timeline;
  - batch page: an Approval panel with bulk actions, and a final approval step in the next-step card;
  - a new `/review` page, with a count in the navigation.

**Tests:** 22 fast tests in `test_approval.py`, plus updates to the validation tests (18 rules now). 6 database tests in `test_approval_worker.py` pass on Neon `test` (19 minutes). They cover fixing the brand and barcode errors, stale saves, refused values, the whole approval cycle, bulk actions, both duplicate outcomes, the audit trail refusing updates, and cross-user, signed-out and waitlisted access on all 10 new endpoints.

---

## M10 Export

**Scope**
- CSV, Excel and Shopify-compatible CSV generated by the worker.
- Presigned download.

**Acceptance criteria**
- Exports contain approved records only.
- The Shopify file imports cleanly.

**Tests**
- Formula injection: cells starting with `= + - @` are sanitized.
- Variants and image URLs.
- Format conformance.

---

## Requirements for every module

- Backend tests run against real Postgres with pgvector, with AWS mocked at the boundary.
- Any endpoint added includes a cross-user access test.
- Any new usage-consuming feature enforces its limit, and reports what was skipped.
- Any new term goes in [GLOSSARY.md](GLOSSARY.md).
- Update [PROJECT_CONTEXT.md](PROJECT_CONTEXT.md) and this status table when the module completes.
