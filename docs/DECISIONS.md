# Decisions

Important technical decisions, with status **Proposed**, **Approved** or **Superseded**. Approved means agreed by the owner, not necessarily implemented. Nothing has been implemented yet.

To change an approved decision, explain the trade-offs and get approval, then add a new entry and mark the old one Superseded.

Provider details were checked on 2026-11-06 and 2026-11-07, and they can change. Estimates are labelled as such.

| # | Decision | Status |
|---|---|---|
| [D1](#d1-regions) | Regions | Approved |
| [D2](#d2-llm-models) | gpt-oss-120b for text, Claude Haiku 4.5 for images | Approved |
| [D3](#d3-global-inference) | Global inference allowed | Approved |
| [D4](#d4-cpu-credit-mode) | CPU credits: Unlimited | Approved |
| [D5](#d5-api-deployment) | Render connected repo, After CI Checks Pass | Approved |
| [D6](#d6-authentication-and-open-sign-up) | Neon Auth with Google, open sign-up, POC limits | Approved |
| [D7](#d7-ocr-library) | RapidOCR | Approved |
| [D8](#d8-infrastructure-as-code) | CloudFormation | Approved |
| [D9](#d9-git-access-from-ec2) | Read-only deploy key | Approved |
| [D10](#d10-module-sequencing) | Module sequencing | Approved |
| [D11](#d11-api-package-layout) | `app/api` split into packages | Approved |
| [D12](#d12-uptime-monitoring) | UptimeRobot on `/health` | Approved |
| [D13](#d13-handling-items-over-the-limit) | Discard extra items, and report them | Approved |
| [D14](#d14-separate-api-and-worker) | Separate API and worker | Approved |
| [D15](#d15-postgresql-as-the-job-queue) | PostgreSQL as the job queue | Approved |
| [D16](#d16-on-demand-ec2-worker) | On-demand EC2 worker | Approved |
| [D17](#d17-worker-deployment) | Worker deploy via GitHub OIDC and SSM | Approved, OIDC part blocked (D26) |
| [D18](#d18-repository-layout) | `backend/`, `frontend/`, `docs/` | Approved |
| [D19](#d19-attribute-provenance-model) | Append-only attribute provenance | Approved |
| [D20](#d20-image-similarity-and-duplicates) | pHash + OpenCLIP, exact search | Approved |
| [D21](#d21-guidelines-as-reference) | `GUIDELINES.md` as a reference | Approved |
| [D22](#d22-local-python-environment) | Conda env `ai-catalog` for local development | Approved |
| [D23](#d23-render-setup) | Create the Render service manually | Approved |
| [D24](#d24-langchain-for-llm-calls) | LangChain for all LLM calls | Approved |
| [D25](#d25-langgraph-only-if-required) | LangGraph only if required | Approved |
| [D26](#d26-github-oidc-is-blocked) | GitHub OIDC is blocked by organization policy | Proposed: replacement undecided |

---

## D1: Regions

**Status:** Approved

**Decision:** EC2, S3, Bedrock and Neon in Sydney (`ap-southeast-2`). Render API in Singapore. Vercel for the frontend. Users in India.

**Context:** The owner's AWS account is restricted to Sydney. Neon offers Sydney but not Mumbai. Render's closest region is Singapore.

**Alternatives considered:**
- *Mumbai for AWS:* blocked by the account restriction, and Neon has no Mumbai region.
- *Koyeb:* its free instance runs only in Frankfurt or Washington, D.C., and its pricing page lists no free compute for new accounts. Rejected after the owner reviewed this.

**Rationale:** Worker and database share a region, so the chatty processing path is fast. Singapore is close to both Sydney and India.

**Consequences:**
- API↔Neon crosses regions: about 95 ms per query and about 580 ms per new connection, measured in M0A. Endpoints must stay low on queries and reuse connections.
- India↔Sydney adds latency to uploads and downloads. Small thumbnails and POC limits reduce the impact.
- Sydney's EC2 price is higher than Mumbai's.

---

## D2: LLM models

**Status:** Approved

**Decision:**
- **Text-only tasks:** gpt-oss-120b (`openai.gpt-oss-120b-1:0`, in Sydney). Tested and working.
- **Tasks needing images:** Claude Haiku 4.5 (`au.anthropic.claude-haiku-4-5-20251001-v1:0`, Australia profile). Tested and working. The owner chose Haiku 5.5 first, but AWS refuses it for this account (below).

Moving to Haiku 5.5 later is a config change, if AWS grants access. Haiku 4.5's "EOL no sooner than" date has passed, so watch for a retirement notice.

**Test results, 2026-11-07,** through LangChain from `ap-southeast-2`:

| Model | Route | Result |
|---|---|---|
| gpt-oss-120b | In Sydney | Text: works |
| Claude Haiku 5.5 | Australia, Global | **Refused:** "not available for this account" |
| Claude Sonnet 5.5 | Australia | **Refused:** same message |
| Claude Haiku 4.5 | Australia, Global | Image: works |
| Amazon Nova Lite | In Sydney | Image: works |
| Amazon Nova Pro | In Sydney | Image: works |
| Mistral Ministral 3 14B | In Sydney | Image: works |
| Google Gemma 3 27B | In Sydney | Image: works |

Each image test was one small solid-red image, so these show that a model is accessible, not how good it is.

**Model review, 2026-11-07,** using AWS's Sydney price list. Prices are per million tokens at the standard tier:

| Model | Input types | Access from Sydney | Input | Output |
|---|---|---|---|---|
| Amazon Nova Micro | Text | In-region | $0.037 | $0.148 |
| Amazon Nova Lite | Text, image | In-region | $0.063 | $0.252 |
| gpt-oss-20b | Text | In-region | $0.072 | $0.309 |
| Claude Haiku 5.5 | Text, image | Australia profile | $0.11 | $0.55 |
| gpt-oss-120b | Text | In-region | $0.155 | $0.618 |
| Claude Haiku 4.5 | Text, image | Australia profile | $1.10 | $5.50 |
| Claude Sonnet 5.5 | Text, image | Australia profile | $2.20 | $11.00 |

Being listed in Sydney doesn't mean a model can be used, as the Haiku 5.5 result shows.

**Context:** The owner mentioned gpt-oss-120b, gpt-oss-20b and Claude Haiku 4.5 as possibilities, then chose to base the decision on what the account can actually access.

**Alternatives considered:** Fixing a model now. Rejected, because availability and pricing are better checked directly.

**Rationale:** Real availability and pricing lead to a better decision than assumptions.

**Consequences:**
- The owner pastes a Bedrock API key into `backend/.env`, after `.gitignore` is committed. It's for this review and local development only, and it never goes to EC2 or GitHub.
- The key gives access but not pricing, so prices come from AWS's public sources.
- The architecture can't depend on image input or provider structured outputs. Responses are validated against schemas, and each task has its own model entry in config.
- Noted for the review: Haiku 4.5's "EOL no sooner than" date (Oct 16, 2026) has passed, although it's still listed Active.

---

## D3: Global inference

**Status:** Approved

**Decision:** Supplier data may go through Bedrock Global cross-region inference where a chosen model requires it.

**Context:** Some models are available only through geographic or Global inference profiles.

**Alternatives considered:** Restricting to in-region or Australia-only routing.

**Rationale:** The owner accepts this for a POC.

**Consequences:** Prefer in-region or Australia routing when it's available. Record the routing used by the chosen model.

---

## D4: CPU credit mode

**Status:** Approved

**Decision:** Run `t3a.medium` in Unlimited mode.

**Context:** `t3a.medium` earns 24 CPU credits per hour while running, and spends 120 at full load. It has no launch credits and earns nothing while stopped, so a usually-off machine runs out quickly under CLIP and OCR.

**Alternatives considered:**
- *Standard:* throttles to 20% speed with no surcharge. One hour of full CPU work takes about 5 hours and costs about $0.24.
- *Non-burstable instance:* steady performance, but a higher hourly price.

**Rationale:** Unlimited does the same work in about an hour for about $0.13, by estimate. It finishes sooner and shuts down sooner.

**Consequences:** Up to about $0.08/hour extra under sustained load. The estimate is unverified, to be checked during M0B. Offset by AWS Budgets alerts and the global EC2 hour cap.

---

## D5: API deployment

**Status:** Approved

**Decision:** Render connected to the GitHub repo, with auto-deploy set to After CI Checks Pass, and root directory `backend/`.

**Context:** On-commit auto-deploy would race database migrations. Render documents pre-deploy commands for paid services.

**Alternatives considered:**
- *On-commit auto-deploy:* races migrations.
- *Deploy hook from GitHub Actions:* works, but disables the connected flow and adds a secret.

**Rationale:** It keeps the connected-repo design while enforcing migrate → worker → API.

**Consequences:** A failed worker deploy holds back the API, which keeps them in step. A commit with no checks detected doesn't deploy.

---

## D6: Authentication and open sign-up

**Status:** Approved

**Decision:** Neon Auth (managed Better Auth) with Google sign-in. People can sign up, with per-user POC limits and global caps. `signups_enabled` stays false until the owner turns it on, M7 at the earliest.

**Context:** This replaces the original assumption of 2–3 known users. Open sign-up exposes EC2 hours, LLM spend and Neon's 1 GB to anyone with a Google account. The owner confirmed Better Auth is available in Sydney.

**Alternatives considered:**
- *Custom email and password:* more to build and secure.
- *Invite-only:* less exposure, but doesn't meet the goal of open sign-up.
- *Per-user limits alone:* many accounts could still exhaust the budget, so global caps are also needed.

**Rationale:** Google has verified the email, and there's no password flow to build. The API verifies JWTs locally using JWKS with no database query, which suits the cross-region path. Example limits, such as 2 lifetime submissions, 1 per day and 5 products per submission, keep usage tiny.

**Consequences:**
- Ownership and cross-user tests on every endpoint.
- `usage_ledger`, `user_limits` and a global-cap framework in M1.
- Neon describes the service as in active development, so token checks sit behind a small interface.
- The owner must create a Google OAuth client before opening sign-ups.

---

## D7: OCR library

**Status:** Approved

**Decision:** RapidOCR behind a registry. Compare with Tesseract on sample images in M4.

**Context:** Product photos contain text on packaging, not clean scanned pages.

**Alternatives considered:** Tesseract, which is lighter but expected to be weaker on photographs. EasyOCR and PaddleOCR, which are heavier.

**Rationale:** It should give better results on photos on CPU. The M4 comparison checks this assumption.

**Consequences:** A registry lets the engine change with no pipeline changes.

---

## D8: Infrastructure as code

**Status:** Approved

**Decision:** One CloudFormation stack, in `backend/infra/aws/`.

**Context:** AWS resources should be reviewable, reproducible and deletable as a group.

**Alternatives considered:**
- *Terraform or OpenTofu:* widely known, but needs somewhere to keep state.
- *AWS CDK:* uses Python, with more moving parts for one small stack.
- *Console clicks:* hard to reproduce, and easy to leave things running.

**Rationale:** Free, nothing to install or host, and AWS tracks what was created.

**Consequences:** AWS-only, and more verbose.

---

## D9: Git access from EC2

**Status:** Approved

**Decision:** A read-only deploy key stored in Parameter Store.

**Context:** The repo is private, and the worker deploy pulls code on EC2.

**Alternatives considered:** Building artifacts into S3 or Docker/ECR, which adds infrastructure for a single instance.

**Rationale:** Simple, narrowly scoped, and matches Git-based deployment.

**Consequences:** The key needs rotation procedures.

---

## D10: Module sequencing

**Status:** Approved

**Decision:** Split M0 into M0A and M0B, split M5 into M5A and M5B, and add a review backbone at M5B.

**Context:** The original list had one setup module and placed review at M9, while uncertain matches need review from M5.

**Alternatives considered:** The original M0–M10 order.

**Rationale:** Proves the riskiest platform work first, and respects real dependencies.

**Consequences:** See [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md).

---

## D11: API package layout

**Status:** Approved

**Decision:** `app/api/routes/`, `services/` and `schemas/` as packages, split by concept.

**Context:** `GUIDELINES.md` shows single files, which would grow too large across more than 10 modules.

**Alternatives considered:** Single files.

**Rationale:** Same layering, easier navigation.

**Consequences:** A small difference from the guidelines layout.

---

## D12: Uptime monitoring

**Status:** Approved

**Decision:** UptimeRobot calls `GET /health` every 5 minutes. `/health` never touches the database.

**Context:** A database-touching health check would stop Neon scaling to zero and burn its CU-hours. Render sleeps after 15 idle minutes, and takes about a minute to wake.

**Alternatives considered:** A longer interval, which accepts cold starts.

**Rationale:** It gives monitoring and avoids cold starts.

**Consequences:** Uses about 744 of Render's 750 free monthly hours, leaving no room for a second free service. UptimeRobot can't monitor the worker, which is off by design.

---

## D13: Handling items over the limit

**Status:** Approved

**Decision:** Keep the first N products in file order, by distinct SKU or product key. Drop the rest before OCR, embeddings and LLM calls, tell the user, and store the counts. Files and images follow the same rule.

**Context:** The owner wants extra items discarded during the POC. The product principles require preserving supplier values and not hiding what happened.

**Alternatives considered:** Rejecting the whole submission. Letting the user pick products, which is more work for a POC.

**Rationale:** Predictable and free for dropped items. The original file stays in S3 until retention ends, so nothing is altered.

**Consequences:** Discarded products get no database rows, only counts on `pipeline_runs`.

---

## D14: Separate API and worker

**Status:** Approved

**Decision:** Separate processes in the same repository, with separate entry points and shared modules. They communicate only through Neon.

**Context:** Render's free tier offers 512 MB and 0.1 CPU, which can't run OCR or CLIP.

**Alternatives considered:** Running processing inside the API. Separate repositories.

**Rationale:** Isolates heavy work, while sharing schemas and queue code.

**Consequences:** Dependency extras, an import-boundary test, and coordinated deployment.

---

## D15: PostgreSQL as the job queue

**Status:** Approved

**Decision:** Use Neon PostgreSQL with `FOR UPDATE SKIP LOCKED`, leases, heartbeats and a lease token that blocks stale writers.

**Context:** A few batches per week on a single worker.

**Alternatives considered:** Redis, Celery, RabbitMQ and Kafka.

**Rationale:** No always-on service to pay for, and job state and results commit atomically.

**Consequences:**
- Throughput is modest but ample here.
- Avoid session-level features, because of the transaction pooler.
- Claim commits immediately, so ownership rests on the lease.

---

## D16: On-demand EC2 worker

**Status:** Approved

**Decision:** One `t3a.medium` in Sydney, Ubuntu 24.04, 20 GB gp3. The API starts it. The worker powers it off through the OS after 5 idle minutes. No public SSH.

**Context:** The target is about $3–5/month, excluding S3 and LLM.

**Alternatives considered:**
- *Always-on instance:* too expensive.
- *`ec2:StopInstances` on the instance role:* OS shutdown needs no EC2 permissions.
- *Spot instances:* can interrupt work, and add complexity.

**Rationale:** Pay only while processing. `RunInstances` isn't granted, so duplicate instances are impossible.

**Consequences:**
- First processing must wait for boot.
- A crashed worker can't stop the machine, so a separate watchdog timer, systemd limits and AWS Budgets are required.
- EBS keeps billing while stopped, and is the main fixed cost.

---

## D17: Worker deployment

**Status:** Approved, but its GitHub OIDC part is blocked (D26)

**Decision:** GitHub Actions with OIDC starts EC2 if needed, then uses SSM with one custom document, accepting only a validated commit SHA. A Python virtual environment and Git checkout, not Docker.

**Context:** Long-lived AWS keys should stay out of GitHub, and deploys must not interrupt jobs.

**Alternatives considered:** Docker with ECR. SSH. Arbitrary `AWS-RunShellScript` from GitHub.

**Rationale:**
- A database pause flag with expiry makes booting from stopped safe, and a crashed workflow can't strand the worker.
- Normal idle logic stops EC2, so there's only one stop path.

**Consequences:**
- Self-check and automatic SHA rollback are needed.
- `contract_version` and expand/contract migrations keep the API and worker compatible.
- The drain wait is capped at 20 minutes, after which the workflow fails safely.

---

## D18: Repository layout

**Status:** Approved

**Decision:** `backend/`, `frontend/` and `docs/`, each deployment with its own root, config and env. `GUIDELINES.md` and `README.md` stay at the root.

**Context:** Proposed by the owner.

**Alternatives considered:** Backend at the repository root.

**Rationale:** Clean deployment roots and dependency separation.

**Consequences:** CI paths are prefixed with `backend/`. Claude reads `docs/PROJECT_CONTEXT.md` at session start.

---

## D19: Attribute provenance model

**Status:** Approved

**Decision:** Three layers: immutable `attribute_observations`, versioned `normalized_values`, and `product_attributes` as a pointer with a state.

**Context:** The product must preserve originals, trace each value and never silently overwrite conflicts.

**Alternatives considered:** A JSONB document on each product, which is compact but makes provenance and conflicts hard to query.

**Rationale:** Directly enforces the product principles, and stays category-independent.

**Consequences:** More rows per product, so large payloads go to S3 and retention purges matter.

---

## D20: Image similarity and duplicates

**Status:** Approved

**Decision:** pHash for duplicates. OpenCLIP `ViT-B-32` with `laion2b_s34b_b79k` on CPU, stored in pgvector with its model recorded. Exact batch-scoped search, with no HNSW index initially. Visual similarity alone can't produce an accepted match.

**Context:** Single-model MVP, on CPU, with a limited storage budget.

**Alternatives considered:** DINOv2 or SigLIP, to be evaluated later. An HNSW index now, which uses storage with little benefit at batch scope.

**Rationale:** A well-known model at a reasonable CPU cost. Exact search is accurate at this size.

**Consequences:** Throughput is unknown until the M5A benchmark. A new embedding dimension requires a new table.

---

## D21: Guidelines as reference

**Status:** Approved

**Decision:** `GUIDELINES.md` is a reference for backend Python, not a strict rule. It stays unchanged, and doesn't apply to the Next.js frontend.

**Context:** Stated by the owner.

**Alternatives considered:** Treating it as binding.

**Rationale:** Keep its useful patterns, and adapt where the project needs to.

**Consequences:** Intentional differences get recorded here, such as D11.

---

## D22: Local Python environment

**Status:** Approved

**Decision:** Local development uses a conda environment named `ai-catalog`, not `backend/.venv`. `uv` installs locked versions into it. CI, Render and EC2 keep `uv` with `uv.lock`.

**Context:** The owner uses Anaconda.

**Alternatives considered:** A project `.venv`. Conda everywhere, which isn't available on Render's Python runtime and would replace the lockfile.

**Rationale:** It matches the owner's tools, and local, CI and production all install the same locked versions.

**Consequences:** Local installs need `UV_PROJECT_ENVIRONMENT="$CONDA_PREFIX"` and `--inexact`. Without `--inexact`, `uv sync` removes conda's own packages and breaks the environment.

---

## D23: Render setup

**Status:** Approved

**Decision:** Create the Render service manually in the dashboard, with no `render.yaml`. Settings are recorded in [DEPLOYMENT.md](DEPLOYMENT.md#render).

**Context:** Requested by the owner. There's one service, so a Blueprint adds little. D5's behavior is unchanged.

**Alternatives considered:** A `render.yaml` Blueprint, which keeps settings in Git.

**Rationale:** Simple for one service.

**Consequences:** Dashboard changes aren't tracked in Git, so the settings table must be kept up to date by hand.

---

## D24: LangChain for LLM calls

**Status:** Approved

**Decision:** All LLM calls go through LangChain, using `ChatBedrockConverse` from `langchain-aws`. Application code doesn't call Bedrock or a model provider's SDK directly.

**Context:** Requested by the owner. Tested on 2026-11-07 with `langchain-aws` 1.9.2 and `langchain-core` 1.7.6.

**Alternatives considered:** Direct Bedrock Converse calls through `boto3`, with fewer dependencies. Provider SDKs, which would differ per model.

**Rationale:** One interface across models and providers, so moving a task to another model stays a config change.

**Consequences:**
- LangChain belongs in the `worker` extra only, added by the first module that makes LLM calls. It never loads in the API, and the import-boundary test will enforce that.
- Replies differ by model: gpt-oss-120b returns `reasoning_content` blocks alongside its text, while Claude Haiku 4.5 returned a plain string. A small helper must normalize these, and reasoning must stay out of stored content.
- Schema validation and the `model_runs` record (D15) still apply to every call.

---

## D25: LangGraph only if required

**Status:** Approved

**Decision:** LangGraph is optional, and is added only if a module needs it. It is not part of the base design.

**Context:** Stated by the owner. The pipeline's stages are already orchestrated by the job queue (D15).

**Alternatives considered:** Adopting it from the start.

**Rationale:** The current pipeline is a fixed sequence that doesn't need it. It would only earn its place for a multi-step LLM flow with branching or retry loops inside a single job.

**Consequences:**
- A module proposing LangGraph must explain why plain code won't do, and get approval.
- If used, it runs inside a job. The PostgreSQL queue stays responsible for claiming, retries and recovery.
- The MVP exclusion of autonomous agents still stands.

---

## D26: GitHub OIDC is blocked

**Status:** Proposed: the replacement needs the owner's decision in stage B3

**Finding (2026-11-07):** the AWS organization's service control policy explicitly denies `iam:CreateOpenIDConnectProvider` and `iam:ListOpenIDConnectProviders`. It can't be changed from this account, and it won't be worked around. EC2, CloudFormation, S3, Systems Manager and IAM users and roles are all allowed in Sydney.

**Context:** D17 planned for GitHub Actions to reach AWS through OIDC, to keep long-lived AWS keys out of GitHub.

**Alternatives to decide between:**
- Ask the organization's administrator to allow it, or whether a provider already exists. This keeps the original design.
- The worker updates itself: GitHub records the wanted commit in the database, and the worker applies it at a safe moment. No AWS access from GitHub at all.
- A narrowly scoped IAM user with keys in GitHub secrets, which D17 set out to avoid.

**Consequences:** D17's deployment path changes once this is decided. Stage B2 isn't affected.
