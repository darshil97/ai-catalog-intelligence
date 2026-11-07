# Deployment

How this project is deployed, what each service needs, and what to do when something goes wrong. This file holds setting names only. **Never put secret values in it.**

**Status:** the API and frontend are live. The worker, AWS infrastructure and `deploy.yml` arrive in M0B, and their sections are marked Planned.

## Services

| Service | Where | URL or location | Deploys from |
|---|---|---|---|
| API | Render, free, Singapore | https://YOUR-SERVICE.onrender.com | `main`, `backend/` |
| Frontend | Vercel | https://ai-catalog-intelligence.vercel.app | `main`, `frontend/` |
| Database and auth | Neon, Sydney | Project `ai-catalog-intelligence` (`<NEON_PROJECT_ID>`) | Alembic migrations |
| Monitoring | UptimeRobot | `GET /health` on the API, every 5 minutes | — |
| Worker | EC2, Sydney | **Planned (M0B)** | `main`, `backend/` |

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

- **Current state:** the baseline is applied to `dev` only. `production` hasn't been migrated, because nothing there needs it yet.
- **Production migrations run from GitHub Actions** once `deploy.yml` exists (M0B), in the order migrate → worker → API. Render's pre-deploy commands are documented for paid services, so they aren't used.
- **Expand/contract only:** a migration must stay compatible with the code currently running.
- **Migrations use the direct URL** (pooling off), and the app uses the pooled one.

To migrate `dev` locally, from `backend/`:

```bash
alembic upgrade head
```

`backend/.env` must point at `dev`. Never point a local `.env` at `production`.

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

| Secret | Where to change it | Then |
|---|---|---|
| Neon `production` password | Neon console | Update `DATABASE_URL` on Render |
| Neon `dev` password | Neon console | Update both URLs in `backend/.env` |
| Bedrock API key | AWS console | Update `backend/.env` |

## Worker and AWS (Planned: M0B)

Not built yet. The design is in [PROJECT_CONTEXT.md](PROJECT_CONTEXT.md#9-infrastructure). M0B will add here:

- AWS stack creation and its parameters.
- EC2, Parameter Store and GitHub OIDC setup.
- The `deploy.yml` worker deploy, including draining.
- How to pause processing, and how to confirm EC2 has stopped.
- `processing_enabled` and the global cost caps.
