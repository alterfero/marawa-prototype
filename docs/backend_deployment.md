# Backend Deployment Guide

This guide is created to prepare the Marawa backend on a university-managed server. It consolidates the backend deployment details that are otherwise spread across the top-level README, the architecture notes, and the runtime configuration in code.

## What Must Be Preserved

The deployed backend is not a stateless FastAPI API. The current code assumes all of the following:

- FastAPI is the HTTP application.
- PostgreSQL is the production system of record.
- One writable persistent directory is mounted and exposed through `DATA_DIR`.
- Alembic migrations run automatically during application startup.
- A bootstrap-admin check runs during startup when bootstrap credentials are configured.
- An in-process background job runner starts inside the same application process.
- The application runs as a single deployed instance and a single Unicorn worker.

The single-worker rule matters just as much as the single-instance rule. Each process starts its own job runner during FastAPI lifespan startup. Do not add `--workers`, do not run multiple app replicas, and do not put this behind a process manager that forks multiple identical app processes unless the job runner is extracted into a separate service first.

## Current Backend Shape

As of the current codebase:

- relational data, sessions, jobs, embeddings, and similarity cache live in PostgreSQL;
- the sentence-transformers download cache lives under `DATA_DIR/model-cache`;
- the backend can serve the built frontend from `frontend/dist`, but it can also run as an API-only service if the frontend is hosted separately;
- stale jobs marked `RUNNING` are re-queued on the next successful startup.

One subtle but important operational detail: `/api/health` is only a liveness endpoint. It returns `{"status":"ok"}` when the process is up. Database migrations and bootstrap checks happen earlier during startup, so a broken database connection usually prevents the app from starting at all, but `/api/health` itself does not run a live database query.

## Recommended Deployment Shape

Use this topology on the server:

- one FastAPI application process;
- one PostgreSQL database;
- one mounted persistent volume or durable directory for `DATA_DIR`;
- one reverse proxy or ingress that terminates HTTPS and forwards standard proxy headers.

Recommended example layout:

```text
/srv/marawa/
  repo/                  cloned application repository
  data/                  mounted persistent runtime directory
  env/backend.env        backend environment file
```

If the frontend is served by FastAPI, the built frontend stays in:

```text
/srv/marawa/repo/frontend/dist
```

## Frontend Integration Options

### Option A: Same-Origin Deployment

This is the simplest and safest option for the current authentication model.

- Build the frontend from this repository.
- Let FastAPI serve `frontend/dist`.
- Keep the backend and frontend on the same public origin.
- Leave `SESSION_COOKIE_SAME_SITE=lax`.
- No CORS configuration is needed in the normal case.

This avoids most cookie and browser credential edge cases.

### Option B: Separate Frontend Host

- Build the frontend with `VITE_API_BASE_URL=https://your-backend-host.example.edu/api`.
- Set backend `CORS_ALLOWED_ORIGINS` to the exact frontend origin, for example `https://your-frontend-host.example.edu`.
- Keep `SESSION_COOKIE_SECURE=true`.
- If the frontend and backend are truly cross-site, not just different subdomains under the same site, set `SESSION_COOKIE_SAME_SITE=none`.

The frontend already sends requests with `credentials: "include"`, so the backend must be deployed in a way that allows browser cookies to flow correctly.

If there is any doubt about cookie policy or browser behavior, prefer the same-origin deployment.

## Required Runtime Inputs

Use PostgreSQL in university deployment. Do not rely on the SQLite fallback.

| Variable | Required | Notes |
| --- | --- | --- |
| `MARAWA_DATABASE_URL` | Yes | PostgreSQL connection URL. `postgres://...` and `postgresql://...` are normalized automatically. |
| `DATA_DIR` | Yes | Mounted persistent runtime directory. Must be writable by the app user. |
| `SESSION_SECRET` | Yes | Must be a strong non-default secret. Never use `dev-session-secret` in deployment. |
| `SESSION_COOKIE_SECURE` | Yes | Set to `true` for HTTPS deployment. |
| `BOOTSTRAP_ADMIN_EMAIL` | Recommended on first deploy | Creates the first admin if missing. |
| `BOOTSTRAP_ADMIN_PASSWORD` | Recommended on first deploy | Used when the bootstrap admin is first created. |
| `CORS_ALLOWED_ORIGINS` | Only if frontend is on a separate origin | Comma-separated list of exact allowed origins. |
| `FRONTEND_DIST_DIR` | Only if FastAPI serves built frontend from a non-default path | Default is `frontend/dist` inside the repo. |

Useful optional settings:

- `BOOTSTRAP_ADMIN_DISPLAY_NAME`
- `MODEL_NAME`
- `MAX_UPLOAD_BYTES`
- `SESSION_COOKIE_SAME_SITE`
- `PORT`
- `SESSION_TTL_HOURS`
- `INVITE_RESET_TTL_HOURS`

Defaults are defined in `backend/app/core/config.py`.

## Bootstrap Admin Behavior

Bootstrap admin behavior is startup-driven:

- if `BOOTSTRAP_ADMIN_EMAIL` and `BOOTSTRAP_ADMIN_PASSWORD` are both present and the user does not exist, the app creates an active admin account;
- if that user already exists, startup ensures the account stays active and retains the `admin` role;
- startup does not rotate an existing password unless the stored account has no password hash.

Operationally, that means:

- set bootstrap credentials before the first production boot;
- confirm login works;
- after the first admin is established, you may remove bootstrap credentials from the service environment if you do not want them stored long-term.

## PostgreSQL Preparation

Before the first application start:

1. Create a PostgreSQL role for the application.
2. Create a dedicated PostgreSQL database for Marawa.
3. Grant the application role full rights on that database.
4. Put the resulting connection URL into `MARAWA_DATABASE_URL`.

Marawa applies Alembic migrations automatically on startup. There is no separate migration command required for the current single-process deployment model.

Because the app startup runs migrations before serving traffic, the service should fail fast if the database is unreachable or if the configured database does not exist.

## Persistent Storage Preparation

Mount a durable writable directory and point `DATA_DIR` at it.

Minimum expectations:

- the app user can create directories and files under `DATA_DIR`;
- the directory persists across restarts and deployments;
- the directory has enough space for downloaded sentence-transformers model files and normal runtime growth.

In the current code, `DATA_DIR/model-cache` is the critical live subdirectory because the embedding backend stores its downloaded model artifacts there. The rest of `DATA_DIR` should still remain durable because the repo documentation treats it as the home for local runtime artifacts and temporary CSV handling.

If the university server cannot download model files directly from the internet, pre-populate `DATA_DIR/model-cache` out of band before the first rebuild job.

## Install And Build

Example server-side install flow from the repository root:

```bash
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements
```

If the backend host will also serve the frontend, build the frontend on that host:

```bash
cd frontend
npm ci
npm run build
```

If the frontend is hosted separately, Node.js is only needed where that frontend build happens, not necessarily on the backend host.

## Launch Command

Run Uvicorn without `--reload` and without `--workers`.

Example direct launch:

```bash
cd /srv/marawa/repo/backend
../.venv/bin/python -m uvicorn app.main:app \
  --host 127.0.0.1 \
  --port 8000 \
  --proxy-headers \
  --forwarded-allow-ips="*"
```

`--proxy-headers` and `--forwarded-allow-ips="*"` are important when the app sits behind an HTTPS reverse proxy, because secure-cookie behavior and request IP logging depend on forwarded headers being trusted.

## Example systemd Service

One reasonable systemd unit looks like this:

```ini
[Unit]
Description=Marawa backend
After=network.target

[Service]
User=marawa
Group=marawa
WorkingDirectory=/srv/marawa/repo/backend
EnvironmentFile=/srv/marawa/env/backend.env
Environment=PYTHONUNBUFFERED=1
ExecStart=/srv/marawa/repo/.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --proxy-headers --forwarded-allow-ips=*
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Use a service user that owns or can write to `DATA_DIR`.

## Reverse Proxy Expectations

The reverse proxy should:

- terminate HTTPS;
- forward `Host`;
- forward `X-Forwarded-For`;
- forward `X-Forwarded-Proto`;
- allow request bodies at least as large as `MAX_UPLOAD_BYTES`.

For Nginx, the important ideas are equivalent to:

```nginx
client_max_body_size 20m;

location / {
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

If `client_max_body_size` is lower than the backend upload limit, CSV uploads will fail at the proxy before they ever reach FastAPI.

## Operational Checks After Deployment

Use this checklist after the first boot or any major redeploy:

1. Confirm the service starts cleanly and stays up.
2. Confirm `GET /api/health` returns `{"status":"ok"}`.
3. Log in with the bootstrap admin account.
4. Upload a representative CSV file.
5. Wait for the queued full rebuild job to complete.
6. Verify dataset counts, trope search, exploration views, and CSV export.
7. Confirm the frontend can maintain an authenticated session through the browser.

The first rebuild can be slower than later rebuilds because it may download the embedding model into `DATA_DIR/model-cache`.

## Operational Caveats

Keep these current limitations in mind:

- one active dataset is visible to everyone at a time;
- stories are not deleted through the application;
- the job runner is in-process, so horizontal scaling is not supported yet;
- multi-worker Uvicorn or multi-replica deployment is not supported yet;
- the health endpoint is liveness-only, not a full readiness probe.

## Related Documentation

- `README.md`: overall project and Railway deployment notes
- `docs/architecture.md`: system architecture and operational constraints
- `docs/api_contract.md`: API surface
- `docs/csv_contract.md`: CSV compatibility rules
- `backend/README.md`: backend entrypoint notes
