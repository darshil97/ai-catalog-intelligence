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
| Worker | EC2, Sydney | **Not created yet (M0B stage B2)** | `main`, `backend/` |

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
| `DATABASE_URL` | Neon `production` branch, connection string with pooling on. Nothing uses it until M0B or M1. | **Yes** |

Render sets `PORT` and `RENDER_GIT_COMMIT` itself. `/health` reports the second as `code_sha`.

Do **not** add `AWS_BEARER_TOKEN_BEDROCK` to Render. The API never calls an LLM.

### Vercel (frontend)

Set under **Settings → Environment Variables**. A value change needs a new deployment, because it is built into the page.

| Variable | Value | Secret |
|---|---|---|
| `NEXT_PUBLIC_API_URL` | `https://YOUR-SERVICE.onrender.com`, no trailing slash | No. `NEXT_PUBLIC_` values reach the browser, so never put secrets in them. |

### Local (`backend/.env`, `frontend/.env.local`)

Both are gitignored. Start from the `.env.example` file beside each one.

| Variable | Value | Secret |
|---|---|---|
| `APP_ENV` | `local` | No |
| `LOG_LEVEL` | `INFO` | No |
| `CORS_ORIGINS` | `http://localhost:3000` | No |
| `DATABASE_URL` | Neon `dev` branch, pooling on | **Yes** |
| `DATABASE_URL_DIRECT` | Neon `dev` branch, pooling off. Used by migrations. | **Yes** |
| `ADMIN_TOKEN` | Temporary (M0B). Long random value. Protects the test-run endpoints, which are removed in M1. | **Yes** |
| `TEST_DATABASE_URL` | Neon `test` branch, pooling on. Tests erase its queue tables. | **Yes** |
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

## Database migrations

- **Current state:** migrations through `0002` are applied to `production`, `dev` and `test`. The first `production` migration was applied manually on 2026-11-08, with the owner's approval.
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

**Status:** files are written, and the stack hasn't been created yet.

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
| `ADMIN_TOKEN` | Temporary, removed in M1. From `openssl rand -hex 32`. | **Yes** |

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

### Planned

Worker deploys arrive in stage B3. GitHub OIDC can't be used, because the account's organization policy denies creating the provider (D26).
