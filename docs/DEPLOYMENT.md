# Deployment

How this project is deployed, what each service needs, and what to do when something goes wrong. This file holds setting names only. **Never put secret values in it.**

**Status:** the API and frontend are live. The worker, AWS infrastructure and deployment jobs arrive in M0B, and their sections are marked Planned.

## Services

| Service | Where | URL or location | Deploys from |
|---|---|---|---|
| API | Render, free, Singapore | https://YOUR-SERVICE.onrender.com | `main`, `backend/` |
| Frontend | Vercel | https://ai-catalog-intelligence.vercel.app | `main`, `frontend/` |
| Database and auth | Neon, Sydney | Project `ai-catalog-intelligence` (`<NEON_PROJECT_ID>`). Branches: `production`, `dev`, `test`. | Alembic migrations |
| Monitoring | UptimeRobot | `GET /health` on the API, every 5 minutes | — |
| Worker | EC2, Sydney | `<WORKER_INSTANCE_ID>`, started on demand | `main`, `backend/`. Updated manually until B3. |

## Normal deployment

1. Work on a branch and open a pull request.
2. CI runs lint, types, tests and the frontend build. Wait for it to pass.
3. Merge to `main`.
4. CI runs again on `main`.
5. **API:** Render deploys automatically once checks pass, if `backend/` changed.
6. **Frontend:** Vercel builds automatically if `frontend/` changed.
7. Run the checks under [Verifying a deployment](#verifying-a-deployment).

Nothing deploys automatically if CI fails. Render also won't deploy a commit with no checks.

## Environment variables

### Render (API)

Set under the service's **Environment** tab. Saving redeploys the service.

| Variable | Value | Secret |
|---|---|---|
| `PYTHON_VERSION` | `3.12.7` | No |
| `APP_ENV` | `production` | No |
| `API_HOST` | `0.0.0.0` | No |
| `CORS_ORIGINS` | `https://ai-catalog-intelligence.vercel.app`. Comma-separated, no trailing slash. | No |
| `DATABASE_URL` | Neon `production` branch, connection string with pooling on. | **Yes** |
| `NEON_AUTH_BASE_URL` | Neon Auth URL of the `production` branch, such as `https://ep-xxx.neonauth.ap-southeast-2.aws.neon.tech/neondb/auth`. The API derives the JWKS URL, issuer and audience from it. | No |
| `ACCESS_ALLOWLIST` | Optional. Comma-separated emails always let in. Defaults to the owner's address in `config.yaml`. | No |
| `SIGNUPS_ENABLED` | Optional, default `false`. Only the owner turns it on, M7 at the earliest. | No |
| `S3_PREFIX` | `prod`. The default, `dev`, is refused by `catalog-api`'s permissions (D29). | No |
| `S3_BUCKET` | Optional. Defaults to `ai-catalog-intelligence`. | No |

The `catalog-api` access key (already set as `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY`) also signs uploads to the bucket.

Render sets `PORT` and `RENDER_GIT_COMMIT` itself. `/health` reports the second as `code_sha`.

Do **not** add `AWS_BEARER_TOKEN_BEDROCK` to Render. The API never calls an LLM.

### Vercel (frontend)

Set under **Settings → Environment Variables**. A value change needs a new deployment, because it is built into the page.

| Variable | Value | Secret |
|---|---|---|
| `NEXT_PUBLIC_API_URL` | `https://YOUR-SERVICE.onrender.com`, no trailing slash | No. `NEXT_PUBLIC_` values reach the browser, so never put secrets in them. |
| `NEON_AUTH_BASE_URL` | Neon Auth URL of the `production` branch, the same value as on Render. Server-only. | No |
| `NEON_AUTH_COOKIE_SECRET` | From `openssl rand -base64 32`. Signs the session cookie cache. Server-only, at least 32 characters. | **Yes** |

Sign-in requests go through the app's own `/api/auth` route, so session cookies are first-party. The Vercel URL is a trusted domain in Neon Auth on the `production` branch (added 2026-11-08). Preview URLs aren't, so sign-in works only on the production URL. Email-and-password sign-in is off on `production` and `dev` (D27).

### Local (`backend/.env`, `frontend/.env.local`)

Both are gitignored. Start from the `.env.example` file beside each one.

| Variable | Value | Secret |
|---|---|---|
| `APP_ENV` | `local` | No |
| `LOG_LEVEL` | `INFO` | No |
| `CORS_ORIGINS` | `http://localhost:3000` | No |
| `DATABASE_URL` | Neon `dev` branch, pooling on | **Yes** |
| `DATABASE_URL_DIRECT` | Neon `dev` branch, pooling off. Used by migrations. | **Yes** |
| `NEON_AUTH_BASE_URL` | Neon Auth URL of the `dev` branch. In both `backend/.env` and `frontend/.env.local`. | No |
| `NEON_AUTH_COOKIE_SECRET` (frontend) | From `openssl rand -base64 32` | **Yes** |
| `TEST_DATABASE_URL` | Neon `test` branch, pooling on. Tests erase its queue, profile, supplier and batch tables. | **Yes** |
| `AWS_BEARER_TOKEN_BEDROCK` | Bedrock API key, local development only | **Yes** |
| `AWS_REGION` | `ap-southeast-2` | No |
| `NEXT_PUBLIC_API_URL` (frontend) | `http://localhost:8000` | No |

## First-time setup

### Render

**New → Web Service**, connected to this repo. The service is created in the dashboard, not from a `render.yaml` (D23).

| Setting | Value |
|---|---|
| Region | Singapore |
| Branch | `main` |
| Root Directory | `backend` |
| Runtime | Python |
| Build Command | `pip install uv && uv sync --frozen --no-dev --extra api` |
| Start Command | `.venv/bin/python server.py` |
| Instance Type | Free |
| Health Check Path | `/health` |
| Auto-Deploy | After CI Checks Pass |

### Vercel

**Add New → Project**, importing this repo, with **Root Directory** `frontend`. `frontend/vercel.json` skips builds when nothing in `frontend/` changed.

### UptimeRobot

An **HTTP(s)** monitor on `/health` of the API, at a 5-minute interval. This also keeps Render awake, using about 744 of its 750 free monthly hours, so don't add a second free Render service.

### Local backend

Run from `backend/`. `--inexact` matters: without it, `uv` removes conda's own packages and breaks the environment.

```bash
conda env create -f environment.yml
```

```bash
conda activate ai-catalog
```

```bash
UV_PROJECT_ENVIRONMENT="$CONDA_PREFIX" uv sync --frozen --inexact --extra api --extra migrate
```

## File storage (M2)

One bucket, `ai-catalog-intelligence` in Sydney, created by the `catalog-worker` stack. Everything sits under `prod/`, `dev/` or `test/` (D29). The bucket is private and encrypted, accepts browser form uploads from the Vercel URL and `http://localhost:3000`, and deletes every object after 31 days.

- **Render and EC2:** `S3_PREFIX=prod`. The worker gets it from `render_env.py`.
- **Local:** create an access key for the `catalog-dev` IAM user in the console and put it in `backend/.env` as `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` (no AWS CLI needed). That user can reach only `dev/` and `test/`.
- **Live storage check:** `LIVE_STORAGE_TEST=1 uv run pytest tests/test_storage_live.py` proves S3 refuses an upload whose size or checksum differs from its link.

## LLM calls (M3)

Only the worker calls an LLM, through Amazon Bedrock in Sydney (D24).

- **EC2:** no key. The `catalog-worker` role has `bedrock:InvokeModel` on gpt-oss-120b (policy `worker-llm` in the stack). Adding a model means adding it to that policy and to `llm.tasks` in `config.yaml`.
- **Local:** the Bedrock API key in `backend/.env` (`AWS_BEARER_TOKEN_BEDROCK`).
- **Render and Vercel:** nothing. Never add the Bedrock key to either.
- **Tests:** `LLM_BACKEND=fake` is set by the test setup, so no test calls a model.
- **Live model check:** `LIVE_LLM_TEST=1 uv run pytest tests/test_llm_live.py` sends one made-up price list to the real model, for a fraction of a cent.
- **Spend cap:** `llm.monthly_spend_cap_usd` (default $10) is checked against this month's recorded cost in `model_runs`, for all users together. Once reached, LLM steps are skipped and the page says so.

## Image embeddings (M5A)

- **Worker:** Titan Multimodal Embeddings G1 on Bedrock (D34), through its role (`worker-llm` allows `amazon.titan-embed-image-v1`). No model is stored on the worker.
- **Local:** the Bedrock API key in `backend/.env`. Tests set `EMBEDDINGS_BACKEND=fake`.
- **Live check:** `LIVE_EMBEDDINGS_TEST=1 uv run pytest tests/test_embeddings_live.py`, four calls for $0.00032.
- **Benchmark:** `python tools/benchmark_images.py --photos ../samples --embed`.
- **Thumbnails** live under `derived/`; the API signs 15-minute links to them, so the API user can read `prod/derived/*`.

## OCR (M4)

- **Worker:** RapidOCR, with its models shipped inside the Python package. Nothing is downloaded at runtime.
- **Tesseract** is for the engine comparison only. It's installed in the local `ai-catalog` conda environment (`conda install -c conda-forge tesseract`), never on the worker.
- **Comparison:** from `backend/`, run `python tools/compare_ocr.py --photos ../samples/photos --out ocr-comparison.md`.

## Database migrations

- **Current state:** migrations through `0008` are applied to `production` and `test`, and through `0005` to `dev`. The first `production` migration was applied manually on 2026-11-08, with the owner's approval.
- **Production migrations run from GitHub Actions** once the deployment jobs exist in `ci.yml` (M0B), in the order migrate → worker → API. Render's pre-deploy commands are documented for paid services, so they aren't used.
- **Expand/contract only:** a migration must stay compatible with the code currently running.
- **Migrations use the direct URL** (pooling off), and the app uses the pooled one.

To migrate `dev` locally, from `backend/`:

```bash
alembic upgrade head
```

`backend/.env` must point at `dev`. Never point a local `.env` at `production`.

## Running the tests

**Postgres never runs locally.** Database tests use the Neon `test` branch, set as `TEST_DATABASE_URL` in `backend/.env`. They erase its queue tables, so the test setup refuses `production` and the `DATABASE_URL` in `.env`. Without `TEST_DATABASE_URL`, database tests are skipped.

From a laptop, the suite is slow because each query takes about 286 ms to reach Sydney. GitHub CI uses its own Postgres container, which isn't local, and finishes much faster.

```bash
pytest
```

## Running the worker locally

Run from `backend/`, in the `ai-catalog` environment. A local worker keeps polling when idle, never powers anything off, and refuses to start against the production database. Stop it with Ctrl+C.

```bash
python worker.py
```

## Verifying a deployment

1. **API:** `GET /health` returns `"status":"ok"`, and `code_sha` matches the commit on `main`.
2. **Frontend:** the site says "API connected", with the first 7 characters of that commit.
3. **CORS:** if the site says the API isn't available but `/health` works, check `CORS_ORIGINS` first.
4. **Monitoring:** UptimeRobot shows the monitor as up.

## Rolling back

| Service | How |
|---|---|
| API | Render → **Deploys** → choose the last good deploy → **Rollback**. |
| Frontend | Vercel → **Deployments** → last good deployment → **Promote** (Instant Rollback). |
| Either, permanently | Revert the commit on a branch, and merge through a pull request. A dashboard rollback alone leaves the bad commit on `main`. |
| Database | Don't undo migrations on `production` under pressure. Expand/contract means the previous code still works, so roll back the code first. |

## Troubleshooting

| Symptom | Likely cause | What to do |
|---|---|---|
| Render didn't deploy after a merge | CI failed, or `backend/` didn't change | Check the commit's checks on GitHub |
| Build fails on `uv sync --frozen` | `uv.lock` is out of date | Run `uv lock` in `backend/`, then commit it |
| API unavailable at Render's port | `API_HOST` isn't `0.0.0.0` | Fix the variable |
| First request is slow | Render or Neon was asleep | Render takes about a minute to wake, Neon less |
| Site can't reach the API | `CORS_ORIGINS` or `NEXT_PUBLIC_API_URL` is wrong, often a trailing slash | Fix it, and redeploy Vercel after changing its variable |
| Neon connection refused for everyone | Monthly CU or egress quota used up | Check usage in the Neon console |

## Rotating secrets

A new Neon branch starts with **the same role passwords as its parent**, so a password from `dev` or `test` may also work on `production` until it's reset.

| Secret | Where to change it | Then |
|---|---|---|
| Neon `production` password | Neon console | Update `DATABASE_URL` on Render |
| Neon `dev` or `test` password | Neon console | Update the URLs in `backend/.env` |
| Bedrock API key | AWS console | Update `backend/.env` |

## Worker and AWS

**Status:** stack `catalog-worker` created on 2026-11-08. Instance `<WORKER_INSTANCE_ID>`, normally stopped. API user `catalog-api` has one access key, used by Render.

The template is [worker-stack.yaml](../backend/infra/aws/worker-stack.yaml). Region is always `ap-southeast-2`, because the account's policy denies other regions.

### Before creating the stack

The instance reads these on first boot, so they must exist first. Create both in **Systems Manager → Parameter Store** as **SecureString**. The merged code must also be on `main`, because the instance clones it.

| Parameter | Value |
|---|---|
| `/catalog/prod/worker/database_url` | Neon `production` branch, pooling on |
| `/catalog/prod/worker/deploy_key` | The private half of a read-only GitHub deploy key |

Create the deploy key locally, then add `catalog_deploy.pub` under the repo's **Settings → Deploy keys**, leaving write access off:

```bash
ssh-keygen -t ed25519 -N "" -C catalog-worker -f catalog_deploy
```

Delete both key files once it's stored.

### After creating the stack

Set these on Render, as well as those listed under [Environment variables](#environment-variables):

| Variable | Value | Secret |
|---|---|---|
| `WORKER_CONTROLLER` | `ec2` | No |
| `WORKER_INSTANCE_ID` | The stack's `InstanceId` output | No |
| `AWS_REGION` | `ap-southeast-2` | No |
| `AWS_ACCESS_KEY_ID` | Access key of IAM user `catalog-api`, created in the console | **Yes** |
| `AWS_SECRET_ACCESS_KEY` | As above | **Yes** |

### How the worker stops

| Layer | What it does |
|---|---|
| Idle shutdown | After 5 idle minutes, the worker asks systemd to power off through a file. It has no privileges and needs no EC2 permissions. |
| Job time limit | A job running over 30 minutes ends the process, because a hung job would otherwise keep the machine on. |
| Watchdog timer | Every 5 minutes, powers off if the heartbeat has been stale for 15 minutes, or after 8 hours of uptime. |
| Monthly cap | The API won't start EC2 after 30 hours in a calendar month. |
| Bootstrap | First boot powers off on any failure, and after 45 minutes regardless. |
| Budget | The owner's $20 AWS budget. |

### Working on the instance

Connect with **Systems Manager → Session Manager**. There is no SSH. To stop the watchdog powering off during up to an hour of maintenance:

```bash
sudo touch /run/catalog-maintenance
```

Useful logs:

```bash
journalctl -u catalog-worker -n 100
```

```bash
sudo cat /var/log/catalog-bootstrap.log
```

### Deleting the stack

Turn off termination protection on the instance first, or the delete fails. This stops the EBS charge.

### Automatic worker deploys (B3)

On every push to `main` that changes `backend/`, `ci.yml` runs `migrate`, then `deploy-worker` if worker or shared code changed. Changes are counted since the last commit whose CI run fully succeeded on `main`, so a push after a failed run also deploys what that run didn't (on 2026-11-10 a fix pushed after a failed M3 run skipped the worker, which was then deployed by hand). If no such commit is found, everything is deployed. They run in the GitHub environment `production`, one deploy at a time. Render waits for these checks before deploying the API.

**`deploy-worker`, step by step:**
1. Pauses the worker in the database, for at most 30 minutes.
2. Starts EC2 if it's stopped.
3. Waits until no job is running, for up to 20 minutes.
4. Sends the `catalog-worker-deploy` command with the commit SHA.
5. On the instance, `deploy.sh` checks the SHA is on `main`, updates, self-checks, restarts and confirms the new version is running. If anything fails, it rolls back.
6. Unpauses the worker, which then stops EC2 itself after 5 idle minutes.

GitHub uses IAM user `catalog-github-deploy`, whose key can only:
- Start the worker instance.
- Run that one document on it.
- Read `/catalog/prod/ci/*`.

**Known limit:** if several merges land quickly, GitHub may cancel a queued deploy in favour of the newest one. The latest commit still deploys.

#### GitHub setup

| Where | Name | Value |
|---|---|---|
| Environment `production`, secret | `AWS_ACCESS_KEY_ID` | Access key of `catalog-github-deploy` |
| Environment `production`, secret | `AWS_SECRET_ACCESS_KEY` | Its secret |
| Environment `production`, variable | `AWS_REGION` | `ap-southeast-2` |
| Environment `production`, variable | `WORKER_INSTANCE_ID` | `<WORKER_INSTANCE_ID>` |

Limit the environment's deployment branches to `main`, and rotate the key every 90 days.

**Parameter:** `/catalog/prod/ci/database_url_direct` holds the `production` URL with pooling off. The deploy key can read it, so a leaked key would expose the production database. Add a migration-only database login before real users arrive.

### Updating the worker by hand

Only if the automatic deploy is broken. Start the instance, then run this as root through Session Manager:

```bash
cd /opt/catalog/app && GIT_SSH_COMMAND='ssh -i /root/.ssh/catalog_deploy -o IdentitiesOnly=yes' git fetch -q origin main && git checkout -q --detach origin/main && bash backend/infra/worker/install.sh
```
