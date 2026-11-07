# Implementation Plan

**Nothing below is implemented yet.** Status values: Not Started, In Progress, Completed.

Each module gets its own proposal and approval before work starts, and review after it ends. See the process in [PROJECT_CONTEXT.md](PROJECT_CONTEXT.md#13-development-process).

## Status

| Module | Name | Depends on | Status |
|---|---|---|---|
| M0A | Foundation | — | Completed |
| M0B | Processing platform | M0A | Not Started |
| M1 | Accounts, suppliers and batches | M0A | Not Started |
| M2 | File ingestion and storage | M0B, M1 | Not Started |
| M3 | Spreadsheet extraction | M2 | Not Started |
| M4 | Document and image text extraction | M3 | Not Started |
| M5A | Image processing | M2, M0B | Not Started |
| M5B | Image association | M3, M4, M5A | Not Started |
| M6 | Product normalization | M3, M4 | Not Started |
| M7 | Catalog enrichment | M6 | Not Started |
| M8 | Catalog validation | M6, M7 | Not Started |
| M9 | Human review | M5B, M8 | Not Started |
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

**Scope**
- Job queue tables, claiming, leases, heartbeat, retries and reaper.
- Pipeline runs and stages.
- Worker loop, graceful stop, idle shutdown, `WorkerController` and `PowerController` with `noop` implementations.
- CloudFormation stack, IAM identities, EC2 setup, systemd units, watchdog, AWS Budgets.
- `deploy.yml`, including migrations and the safe worker deploy.
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

---

## M2 File ingestion and storage

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

---

## M3 Spreadsheet extraction

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

---

## M4 Document and image text extraction

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

---

## M5A Image processing

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

---

## M5B Image association

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

---

## M6 Product normalization

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

---

## M7 Catalog enrichment

**Scope**
- Titles, descriptions and bullet points from verified attributes.
- LLM calls through LangChain, using gpt-oss-120b for text, with a per-task config entry. If LLM calls first arrive in M4, the setup moves there.
- Prompts in template files.
- Global LLM spend cap and `blocked_quota`.

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

---

## M8 Catalog validation

**Scope**
- Rule registry and a small configurable marketplace ruleset.
- Duplicate detection across products in a batch.
- Validation issues.

**Acceptance criteria**
- Missing, conflicting and duplicate data is flagged.
- Rules come from config.

**Tests**
- Registry contract, each rule, and severity handling.

---

## M9 Human review

**Scope**
- Full review UI: editing, approving, bulk actions.
- Audit trail.

**Acceptance criteria**
- Every change is recorded with its before and after values.
- Only approved products can be exported.

**Tests**
- Concurrent edits.
- Approval rules.
- Audit integrity.

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
