# AI Catalog Intelligence — Project Context

**This is the primary source of truth.** Read it with `GUIDELINES.md` at the start of every development session.

| | |
|---|---|
| **Current status** | **M0A Foundation in progress.** Everything below describes the planned system unless listed as implemented here. |
| **Implemented so far** | Project documentation. On branch `m0a-foundation`: backend skeleton (config, logging, request IDs, `/health`, temporary latency endpoint, Alembic baseline, placeholder worker), frontend shell, CI, `render.yaml`. Not yet deployed. |
| **Neon** | Project `ai-catalog-intelligence` (`<NEON_PROJECT_ID>`), Sydney, Postgres 18, compute capped at 1 CU, Better Auth on. Branches: `production` (default) and `dev`. `pgvector` 0.8.6 is available; the baseline migration hasn't been applied yet. |
| **Next steps** | Finish M0A: deploy to Render and Vercel, measure latency, remove the latency endpoint, and complete the model review. Then M0B. |
| **Last updated** | 2026-11-07 |

Related documents: [Implementation plan](IMPLEMENTATION_PLAN.md) · [Database schema](DATABASE_SCHEMA.md) · [Decisions](DECISIONS.md) · [Glossary](GLOSSARY.md)

---

## 1. Product vision

AI Catalog Intelligence is an end-to-end supplier catalog onboarding system. It accepts messy supplier inputs (Excel/CSV, PDF specifications, product images, ZIP folders of images) and turns them into structured, normalized, enriched and validated product records. It associates images with SKUs, supports human review, and exports approved catalogs.

**Workflow**

1. Create a supplier and batch.
2. Upload supplier files.
3. Parse structured data and extract information from PDFs and images.
4. Identify images and associate them with SKUs or products.
5. Normalize attributes and categories.
6. Generate titles, descriptions and bullet points from verified information.
7. Validate records and flag missing, conflicting or duplicate information.
8. Human review, editing and approval.
9. Export CSV, Excel and Shopify-compatible CSV.

**Audience:** a cost-conscious POC with open Google sign-up and strict limits on how much each user can process.

---

## 2. Design principles

- Never fabricate product specifications.
- Preserve original supplier-provided values.
- Maintain attribute-level provenance.
- Never silently overwrite conflicting values.
- Separate extraction, normalization, enrichment and validation.
- Use deterministic code for unit conversions, schema validation and business rules.
- Use LLMs and vision models only where they provide genuine value.
- Send uncertain decisions to human review.
- Image similarity alone must not establish a definitive SKU match.
- Keep processing asynchronous, retryable and traceable.
- Record processing errors, model versions, processing times and reviewer changes.
- Report anything discarded by a limit. Never drop items silently.
- Keep the architecture category-independent. Home & Kitchen is only the demo category.

---

## 3. MVP scope

**Included**

1. Supplier and batch management
2. CSV, XLSX, PDF, image and ZIP ingestion
3. Spreadsheet extraction
4. Document and image extraction
5. Image intelligence
6. Product normalization
7. AI-assisted enrichment
8. Catalog validation
9. Human review and approval
10. CSV, Excel and Shopify-compatible CSV exports
11. Google sign-up with per-user limits and global cost caps

**Constraints**

- One canonical product schema initially.
- JPEG, PNG and WebP images.
- Common spreadsheet formats.
- Text-based PDFs first. Scanned PDFs can follow later.
- Uncertain image matches require human review.
- Potential variants are flagged, never automatically merged.
- A small configurable marketplace ruleset.

**Excluded**

- Direct Amazon or Flipkart publishing, and direct Shopify API integration
- Supplier portal and automatic email ingestion
- Teams, organizations or complex enterprise permissions
- Advanced analytics
- Autonomous agents and custom model fine-tuning
- Real-time image processing

Do not expand scope without explaining the trade-offs and getting approval.

---

## 4. Confirmed technology stack

| Component | Technology | Location |
|---|---|---|
| Frontend | Next.js + TypeScript | Vercel free tier |
| Backend API | Python + FastAPI | Render free tier, Singapore |
| Processing worker | Python, separate entry point | On-demand EC2 `t3a.medium`, Sydney |
| Database and queue | PostgreSQL on Neon free tier | Sydney |
| Authentication | Neon Auth (managed Better Auth) with Google | Sydney |
| Vector search | pgvector on Neon | Sydney |
| File storage | AWS S3 | Sydney |
| LLM | Amazon Bedrock. **Model not chosen yet** (D2). | Sydney |
| Image embeddings | OpenCLIP `ViT-B-32`, `laion2b_s34b_b79k`, CPU | Worker |
| Duplicate detection | Perceptual hashing (pHash) | Worker |
| Image processing | OpenCV and Pillow | Worker |
| OCR | RapidOCR, behind a registry | Worker |
| Backend CI/CD | Connected GitHub repo, Render "After CI Checks Pass" | |
| Worker CI/CD | GitHub Actions + AWS Systems Manager | |
| Infrastructure as code | CloudFormation | |
| Monitoring | UptimeRobot | |

Users are in India. Why these regions: [D1](DECISIONS.md#d1-regions).

---

## 5. Repository structure

```
ai-catalog-intelligence/
├── README.md
├── GUIDELINES.md                 # reference for backend Python work
├── render.yaml
├── docs/
├── .github/workflows/            # ci.yml, deploy.yml
├── frontend/                     # Vercel root directory
│   ├── package.json  next.config.ts  .env.example  src/
└── backend/                      # Render root directory; also what EC2 runs
    ├── pyproject.toml  uv.lock  config.yaml  .env.example  alembic.ini
    ├── server.py                 # API entry point
    ├── worker.py                 # worker entry point (same on laptop and EC2)
    ├── migrations/
    ├── tests/
    ├── infra/
    │   ├── aws/                  # CloudFormation stack
    │   └── worker/               # systemd units, deploy.sh, render-env.sh, watchdog
    └── app/
        ├── config.py             # only reader of config.yaml and env vars
        ├── middleware/request_context.py
        ├── utils/logger/
        ├── db/                   # engine, tables, repositories  [shared]
        ├── jobs/                 # queue: claim/lease/retry, handler registry
        │   └── handlers/         # one module per job type  [worker only]
        ├── storage/              # S3 + presigning  [shared]
        ├── compute/              # WorkerController: ec2 | noop  [API]
        ├── auth/                 # Neon Auth token verification  [API]
        ├── usage/                # POC limits, usage ledger, global caps  [shared]
        ├── api/                  # main, routes/, services/, schemas/  [API only]
        ├── worker/               # loop, heartbeat, PowerController  [worker only]
        ├── catalog/              # suppliers, batches, products, attribute definitions
        ├── ingest/parsers/       # csv, xlsx, pdf, image, zip
        ├── vision/               # phash, embedders/, ocr/, clustering  [worker only]
        ├── matching/signals/     # image↔SKU evidence
        ├── normalization/        # units, categories, mapping (deterministic)
        ├── enrichment/llm/       # LLM provider registry
        ├── validation/rules/
        ├── review/
        ├── export/exporters/     # csv, xlsx, shopify
        └── templates/{prompts,sql}/
```

- Dependencies split into `api` and `worker` extras. Torch, OpenCV and OCR exist only in `worker`.
- `GUIDELINES.md` applies to backend Python only and is a reference, not a strict rule. Record intentional differences in `DECISIONS.md`.
- The backend publishes its OpenAPI schema, and frontend TypeScript types are generated from it.

---

## 6. Architecture overview

```
                  ┌─────────────────┐     ┌────────────┐
 Users (India) ─▶ │ Next.js (Vercel)│ ◀──▶│ Neon Auth  │ Google sign-in
                  └───┬─────────┬───┘     │ (Sydney)   │
       Bearer JWT     │         │         └─────┬──────┘
                      │         │ presigned     │ JWKS public keys
                      ▼         │ upload/       │ (cached by API)
   UptimeRobot  ┌────────────────────┐ download │
   GET /health ▶│ FastAPI (Render    │◀─────────┘
    (no DB)     │ free, Singapore)   ││
                │ 512 MB / 0.1 CPU   ││
                └─┬──────┬─────────┬─┘│
   StartInstances │  SQL │    sign │  ▼
   (fixed ID only)│      ▼         └▶ ┌─────────────────┐
                  │ ┌───────────────────┐ │ S3 (Sydney)     │
                  │ │ Neon (Sydney)     │ │ raw/derived/    │
                  │ │ data, job queue,  │ │ exports         │
                  │ │ pgvector, auth    │ └──────▲──────────┘
                  │ └─────────▲─────────┘        │ gateway endpoint
                  │           │ claim, heartbeat,│ (free)
                  ▼           │ results          │
        ┌─────────────────────┴──────────────────┴───┐
        │ EC2 t3a.medium (Sydney), on demand         │──▶ Bedrock (Sydney)
        │ systemd: catalog-worker + watchdog timer   │
        │ pandas, PDF, OpenCV, pHash, OpenCLIP, OCR  │
        └─────────────────────▲──────────────────────┘
                              │ SSM (no inbound ports)
                              └── GitHub Actions (OIDC)
```

### API and worker responsibilities

| Concern | API | Worker |
|---|---|---|
| Runs on | Render free | EC2, or a laptop locally |
| Entry point | `backend/server.py` | `backend/worker.py` |
| Does | Token checks, validation, CRUD, presigning, job creation, limit checks, starting EC2, status | All file parsing, CV, embeddings, OCR, LLM calls, validation runs, export generation |
| Never does | Open supplier files, run inference, proxy file bytes | Serve HTTP, decide auth |
| Talks to the other | **Only through Neon** | Same |

Rules that must hold:
- `/health` never touches the database, so UptimeRobot can't keep Neon awake.
- Keep endpoints low on queries, because API↔Neon crosses Singapore↔Sydney.
- All file transfer goes directly between the browser and S3.

### Pipeline

Each stage is a separate job type:

`ingest → extract_structured / extract_documents → image_intelligence → associate → normalize → enrich → validate → (human review) → export`

---

## 7. Main workflows

### Job queue

The queue lives in PostgreSQL. A `pipeline_run` has stages, and each stage has `jobs`, one per chunk. Details are in [DATABASE_SCHEMA.md](DATABASE_SCHEMA.md#job-queue).

```
             claim (SKIP LOCKED)              success
 pending ───────────────────────▶ running ───────────▶ succeeded
    ▲                             │  │  │
    │ retryable error + backoff   │  │  └─ user cancel ─▶ cancelled
    ├─────────────────────────────┘  │
    │ lease expired (reaper)         │ permanent error, or
    └────────────────────────────────┤ attempts >= max
                                     ▼
 blocked_quota ◀─ (global cap hit)  failed ──(manual retry)──▶ pending
```

| Requirement | Mechanism |
|---|---|
| Concurrency | One worker and one job at a time. Bedrock calls inside a job use a small bounded thread pool. |
| Lease and heartbeat | Renew every 30s against a 120s lease. If renewal hits 0 rows, the job aborts cooperatively. |
| Stale writers | Result commits start with `UPDATE jobs … WHERE id = :id AND lease_token = :token`. On 0 rows the transaction rolls back. |
| Idempotency | `UNIQUE(idempotency_key)` on jobs. Results upsert on natural keys. S3 keys are deterministic. |
| No double LLM billing | `model_runs` stores an input hash, so a retry reuses a stored response. |
| Retries | Only transient errors, with backoff and limits from config. Corrupt inputs are permanent. |
| One bad row | Written to `processing_errors`. The chunk continues and the record goes to review. |
| Fairness | One active pipeline run per user. |
| Graceful stop | On SIGTERM: stop claiming, finish within a grace period, otherwise release with no attempt penalty. |
| Unexpected EC2 loss | No state lives on the instance. On startup and every 60s, the reaper requeues expired leases. |

**Neon connection rules:** pooled endpoint at runtime, direct endpoint for migrations, no session-level features (`LISTEN`, advisory locks), pre-ping because suspends drop connections.

### Starting the worker

The API has `ec2:StartInstances` on one instance ID and no `RunInstances`, so duplicate instances are impossible.

1. Commit the job first, then attempt the start. A failed start never loses work.
2. Stop if the global monthly EC2 hour cap is reached. Jobs wait, and the UI says why.
3. If the worker heartbeat is under 60s old, stop here with no AWS call.
4. Atomically take a cooldown on `worker_control`, so concurrent submissions make one call.
5. `DescribeInstances`, then act:

| State | Action |
|---|---|
| `stopped` | `StartInstances` |
| `pending`, `running` | Wait for heartbeat |
| `stopping` | Retry until `stopped`, then start |
| `shutting-down`, `terminated`, not found | Mark worker unavailable, surface in UI, log error |
| Insufficient capacity or API error | Back off and retry. Jobs stay `pending`, and the UI shows "worker unavailable". |

Retries work without an always-on scheduler: an in-process retry task on the API, status polling from the UI (subject to the cooldown), and one check at API startup.

### Worker lifecycle

```
 boot ─▶ systemd starts worker ─▶ self-check ─▶ register, requeue expired leases
                                                       │
           ┌───────────────────────────────────────────┘
           ▼
     ┌──▶ POLL ──job──▶ PROCESS ──done──┐     poll 5s while recently busy,
     │     │  ▲                         │     15s when idle
     └─────┼──┴─────────────────────────┘
           │ no work for idle_timeout (300s)
           ▼
     SHUTDOWN CHECK (one transaction):
       lock worker_control; count pending + running + expiring retries
         ├─ work found ─▶ back to POLL
         └─ none, no deploy pause ─▶ state = 'shutting_down', commit
                                         ▼
                              OS poweroff (behavior = stop)
```

- **Shutdown race:** a job committed just after the check isn't lost. The API sees `shutting_down` or `stopping` and starts the instance once it reaches `stopped`.
- **Shutdown method:** narrow sudo rule for OS poweroff, so the instance role needs no EC2 permissions.
- **Backoff limit:** retry backoff stays below the idle timeout, so a retry can't strand itself on a stopped instance.
- **Cost protection:** systemd restart limits, `WatchdogSec` and `MemoryMax`; an independent watchdog timer powers off if the worker is down or stale for over 15 minutes; AWS Budgets alerts.

### Image association

Independent signals (SKU/model number, filename, OCR text, brand and attributes, supplier identifiers, visual similarity) each record their evidence. Auto-accept requires an exact identifier hit with no competing candidate. Everything else goes to human review. Visual similarity alone can never produce an accepted match.

### Authentication

1. The frontend signs users in with Google through Neon Auth. Users and sessions live in the `neon_auth` schema.
2. The frontend sends a 15-minute JWT as `Authorization: Bearer`.
3. The API verifies it locally using cached JWKS public keys (EdDSA signature, `iss`, `aud`, `exp`), with no database query.
4. On first use the API creates a `user_profiles` row keyed by `sub`.

Google-only at launch. Roles are `user` and `admin`, from the token's `role` claim, and the `banned` claim is also checked. Each Neon branch has its own auth environment.

### Data separation

- Every supplier has an `owner_user_id`. Repositories require the caller's user ID on every read and write.
- Another user's resource returns 404, including presign requests, downloads, status and exports.
- S3 keys are prefixed by owner, and access goes through ownership-checked presigned URLs.
- Duplicate detection and similarity search never cross users.
- Automated tests attempt cross-user access on every endpoint.

---

## 8. POC limits

**All numbers are examples set in `backend/config.yaml`.** Per-user overrides in `user_limits` let an admin account be exempt.

| Per-user limit | Example | Enforced by |
|---|---|---|
| Processing submissions, lifetime | 2 | API, at submit |
| Processing submissions per rolling 24 hours | 1 | API, at submit |
| Files per submission | 5 | API, at presign and submit |
| Size per file | 10 MB | Presigned upload policy, rechecked on completion |
| Total size per submission | 25 MB | API, at submit |
| PDF pages per document | 10 | Worker, at ingest |
| Images per submission, including inside ZIPs | 25 | Worker, at ingest |
| Products catalogued per submission | 5 | Worker, before expensive processing |
| Retention of a user's data | 30 days, then purged | Scheduled worker job |

| Global cap | Example | When reached |
|---|---|---|
| EC2 hours per month | 30 | New work waits, and the UI says capacity is reached |
| LLM spend per month | $10 | Enrichment jobs enter `blocked_quota` |
| Total users | 25 | New sign-ups see a waitlist message |
| `processing_enabled` | true | Emergency stop |
| `signups_enabled` | **false until the owner turns it on, M7 at the earliest** | Only allowed accounts can use the app |

- **A submission** is pressing "process" on a batch. Editing, reviewing and exporting stay unlimited.
- **Restored only on platform failure**, not when a user's file is unreadable.
- **Extra products:** keep the first N in file order by distinct SKU or product key, drop the rest before OCR, embeddings and LLM calls, tell the user ("Processed 5 of 42 products; 37 discarded by the POC limit"), and store the counts on the run. The original file stays in S3 until retention ends.
- **Accounting:** a submission's count is taken in the same transaction that creates the job, so simultaneous requests can't both pass.

---

## 9. Infrastructure

### Local and production

| Concern | Local | Production |
|---|---|---|
| API | `python backend/server.py` | Render |
| Worker | `python backend/worker.py` | systemd on EC2 |
| Frontend | `npm run dev` in `frontend/` | Vercel |
| `WorkerController` | `noop` | `ec2` |
| `PowerController` | `noop` | `os_shutdown` |
| Idle behavior | Keep polling | Shut down after timeout |
| Database and auth | Neon `dev` branch | Neon `production` branch |
| Storage | Dev S3 bucket | Prod S3 bucket |
| Config and secrets | `backend/.env`, `frontend/.env.local`, both gitignored | Render and Vercel env settings; on EC2, Parameter Store rendered to a root-owned `.env` before start |
| AWS auth | Named local profile | Render: scoped IAM user. EC2: instance role. |

A local-mode worker refuses to start if its database host is the production one.

### AWS resources in Sydney (`ap-southeast-2`)

| Resource | Configuration |
|---|---|
| EC2 | `t3a.medium`, Ubuntu 24.04, Unlimited credits, IMDSv2 required, termination protection, shutdown behavior `stop`, no key pair |
| EBS | 20 GB gp3, encrypted |
| Network | Default VPC with public IP, no inbound rules, no NAT, no Elastic IP |
| S3 | Dev and prod buckets, public access blocked, CORS for the frontend, lifecycle rules, free gateway endpoint |
| Parameter Store | SecureStrings, standard tier |
| CloudWatch Logs | Short retention, worker only |
| AWS Budgets | Alerts at chosen thresholds |

Install CPU-only torch so it fits on 20 GB, and keep model weights on the EBS volume. The worker runs as a non-root user with systemd hardening, because it parses files uploaded by the public.

### Identities

| Identity | Permissions |
|---|---|
| `catalog-api` IAM user | `ec2:StartInstances` on the one instance, `ec2:DescribeInstances`, S3 Get/Put on data prefixes for presigning. Stored in Render's environment, with documented rotation. |
| `catalog-worker` instance role | S3 read/write on bucket prefixes, `ssm:GetParameter` on `/catalog/prod/*`, `kms:Decrypt` for that key, `bedrock:InvokeModel*` on approved models, `AmazonSSMManagedInstanceCore`, CloudWatch log writes. No EC2 permissions. |
| `catalog-github-deploy` OIDC role (repo and `main` only) | Start and describe the one instance, `ssm:SendCommand` limited to that instance and one custom document, read the migration URL parameter. |

### CI/CD

```
 PR ─▶ ci.yml: lint, types, tests with pgvector, import-boundary + API memory test,
               frontend build

 push to main ─▶ deploy.yml
    1. detect changed paths
    2. tests
    3. migrate       (if backend/migrations changed; expand-only)
    4. deploy-worker (if worker or shared paths changed)
    5. all checks green ─▶ Render deploys the API automatically

 frontend/ changes ─▶ Vercel builds on its own (root directory + ignored build step)
```

| Paths changed (under `backend/`) | Migrate | Worker | API |
|---|---|---|---|
| `app/api`, `app/auth`, `app/compute`, `server.py` | | | ✓ |
| `app/worker`, `app/jobs/handlers`, `app/vision`, `app/ingest`, `app/matching`, `app/normalization`, `app/enrichment`, `app/validation`, `app/export`, `worker.py`, `infra/worker` | | ✓ | |
| `app/db`, `app/jobs` core, `app/storage`, `app/usage`, `app/catalog`, `app/review`, `app/config.py`, `config.yaml`, `app/utils`, `pyproject.toml`, `uv.lock`, `app/templates/sql` | | ✓ | ✓ |
| `migrations/` | ✓ | ✓ | ✓ |

**Version compatibility:**
1. Fixed order: migrate → worker → API. A failed step holds back the later ones.
2. Migrations stay backward compatible with running code (expand/contract).
3. Each job stores a `contract_version`. The worker supports a range and publishes it in `worker_control`.
4. An unsupported job waits in `pending`. The start check and idle-shutdown query ignore such jobs, so they can't cause boot loops.

**Worker deploy with no interrupted jobs:**
```
 1. Set worker_control.pause_reason='deploy' with a 30-minute expiry
 2. Start EC2 if stopped; wait for instance + SSM readiness
    (a booting worker sees the pause and claims nothing)
 3. Wait for the current chunk to finish, capped at 20 minutes
    └─ cap hit ▶ fail the workflow, no code changed, safe to re-run
 4. SSM ▶ deploy.sh <sha>:
      - fetch via read-only deploy key, verify SHA is on origin/main, checkout detached
      - uv sync --frozen --extra worker only if the lock changed
      - worker.py --self-check (imports, DB, S3, weights, schema at head)
      - restart service, confirm heartbeat reports the new SHA
      - on failure: restore previous SHA, restart, fail the workflow
 5. Clear pause. Worker resumes, then normal idle logic stops EC2.
```

### Cost estimate (EC2 worker only)

| Item | 12 hours, light CPU | 20 hours, sustained CPU |
|---|---|---|
| EC2 at about $0.0475/hour | $0.57 | $0.95 |
| Extra Unlimited CPU (about $0.08/hour) | ~$0 | $1.60 |
| EBS 20 GB gp3 at about $0.096/GB-month | $1.92 | $1.92 |
| Public IPv4 at $0.005/hour | $0.06 | $0.10 |
| **Total** | **~$2.55** | **~$4.57** |

Target: about $3–5/month. Worst case at the example 30-hour cap under full load: about $5.90. **These rates are unverified estimates** (third-party sources or memory), to be checked in the AWS calculator during M0B. LLM and S3 are excluded.

---

## 10. LLM approach

**The model is not chosen.** At the start of development, after `.gitignore` is committed, the owner pastes a Bedrock API key into `backend/.env`. Claude then lists the models available in Sydney, finds current prices from AWS's public pricing sources, and presents a table for a joint decision. The key is for this review and local development only. EC2 uses its instance role, and the key never goes to EC2 or GitHub.

Until then, the architecture assumes:
- Each task (extraction, enrichment) has its own model entry in config, so switching needs no code change.
- No dependence on a model reading images. If the chosen model is text-only, images go through OCR and CLIP.
- No dependence on provider structured outputs. Every response is validated against a Pydantic schema, malformed output is retried a limited number of times, then sent to review.
- Supplier text is treated as data, not instructions.

---

## 11. Known limitations

- **Neon free quotas are shared by all users.** Exhausting CU-hours or egress suspends the whole project until the next cycle. Exceeding 1 GB makes writes fail.
- **Worker start gap:** with no browser open and the API restarted mid-retry, the EC2 start waits for the next API startup or request. Jobs remain safely `pending`.
- **Render free:** can restart at any time. Keep-alive pings use about 744 of 750 monthly hours, so this is the only free service.
- **First processing is slow to start:** EC2 must boot.
- **Multiple accounts from one person** can only be bounded by global caps, not prevented.
- **Neon Auth** is described by Neon as in active development.
- **CLIP and OCR throughput on this CPU is unknown** until M5A.
- **India↔Sydney file transfer** has noticeable latency.

---

## 12. Owner actions

| When | Action | Status |
|---|---|---|
| Before M0A | Create the Neon project in Sydney with Better Auth enabled | Done |
| M0A | Confirm how Claude accesses Neon | Done: Neon MCP, with admin access |
| M0A | Connect the repo to Render and Vercel, and add the UptimeRobot monitor | Pending |
| M0A | Paste database URLs and the Bedrock key into `backend/.env` | Pending |
| Before opening sign-ups | Create a Google OAuth client and publish the consent screen | Pending |

---

## 13. Development process

Develop one module at a time. For every module:

1. Explain the objective.
2. Explain the proposed implementation.
3. Identify database, API, frontend and infrastructure changes.
4. Explain dependencies and edge cases.
5. Define tests and acceptance criteria.
6. Wait for approval.
7. Implement only the approved module.
8. Test the implementation.
9. Update the documentation, especially this file.
10. Wait for review before proceeding.

At the start of every session, read this file and `GUIDELINES.md`, then continue from the next pending module. If a better technical approach appears, explain the trade-offs and ask for approval before changing an agreed decision.

Ask before any schema change or destructive action made directly on a Neon database.
