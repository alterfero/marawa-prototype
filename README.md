# Marawa

Marawa is the product-grade rewrite of the legacy oral mythology prototype. The backend is FastAPI, the frontend is React + TypeScript + Vite, and production serves the built frontend directly from FastAPI as a single Railway web service.

The target production architecture is:
- PostgreSQL as the transactional source of truth;
- a Railway-managed PostgreSQL service;
- a mounted Railway volume for model cache, uploads, exports, and search artifacts;
- built-in email/password authentication with role-based access control;
- anonymous public exploration access only for the exploration experience.

For backend deployment outside Railway, including a university-managed PostgreSQL plus mounted-storage setup, use [docs/backend_deployment.md](docs/backend_deployment.md).

## Local Development

- Backend:

```bash
cd backend
MARAWA_DATABASE_URL="$(../scripts/local_postgres.sh url dev)" 
../.venv/bin/python -m uvicorn app.main:app --reload --port 8000

```

- Frontend:

```bash
cd frontend
npm install
npm run dev
```

By default, runtime artifact data lives under [data](/Users/sebastienchristian/d/CODE%20LAB/marawa/data). PostgreSQL should be provided through `MARAWA_DATABASE_URL`.

### Local PostgreSQL

The repository now includes [scripts/local_postgres.sh](/Users/sebastienchristian/d/CODE%20LAB/marawa/scripts/local_postgres.sh) to help with local PostgreSQL development and smoke verification.

Typical flow:

```bash
brew services start postgresql@14
scripts/local_postgres.sh create-dev-db
export MARAWA_DATABASE_URL="$(scripts/local_postgres.sh url dev)"
cd backend
../.venv/bin/python -m uvicorn app.main:app --reload --port 8000
```

By default that local helper creates and targets `marawa_dev`, not `marawa`.

To run the real PostgreSQL smoke test:

```bash
scripts/local_postgres.sh test
```

That command recreates a dedicated `marawa_test` database by default and runs [backend/tests/test_postgres_smoke.py](/Users/sebastienchristian/d/CODE%20LAB/marawa/backend/tests/test_postgres_smoke.py).

## Runtime Environment

The deployed app is expected to read these environment variables:

- `MARAWA_DATABASE_URL`: PostgreSQL connection URL.
- `DATA_DIR`: base directory for runtime artifact data. Default locally: repo `data/`.
- `MODEL_NAME`: embedding model name. Default: `sentence-transformers/paraphrase-multilingual-mpnet-base-v2`.
- `SESSION_SECRET`: secret used to sign or protect session state.
- `SESSION_COOKIE_SECURE`: should be `true` in Railway production. The Railway start script defaults it to `true`.
- `SESSION_COOKIE_SAME_SITE`: defaults to `lax`. Leave that value unless you deliberately need a different cookie policy.
- `PORT`: HTTP port for the FastAPI process. Railway sets this automatically.
- `BOOTSTRAP_ADMIN_EMAIL`: bootstrap admin email used on startup.
- `BOOTSTRAP_ADMIN_PASSWORD`: bootstrap admin password used on startup.
- `BOOTSTRAP_ADMIN_DISPLAY_NAME`: optional bootstrap admin display name.

The backend stores:

- relational state in PostgreSQL;
- sentence-transformers cache under `DATA_DIR/model-cache`;
- a persistent `DATA_DIR` for runtime artifacts and temporary CSV handling.

## Railway Deployment

This repository is configured for a single Railway web service via [railway.toml](/Users/sebastienchristian/d/CODE%20LAB/marawa/railway.toml). The build step installs backend dependencies, builds the Vite frontend, and the FastAPI app serves the resulting `frontend/dist` files in production.

Because the frontend and backend manifests live under `frontend/` and `backend/` instead of the repo root, Railway also needs the root [railpack.json](/Users/sebastienchristian/d/CODE%20LAB/marawa/railpack.json) file so Railpack installs both Node.js and Python before running the build command.

Railway and Railpack already execute the configured commands in a shell, so the commands in `railway.toml` should not be wrapped in `sh -lc`.

The Railway start command now runs [scripts/railway_start.sh](/Users/sebastienchristian/d/CODE%20LAB/marawa/scripts/railway_start.sh), which:

- requires `MARAWA_DATABASE_URL` and a non-default `SESSION_SECRET`;
- defaults `DATA_DIR` to `/storage` when a Railway volume is mounted there;
- defaults `SESSION_COOKIE_SECURE=true` and `SESSION_COOKIE_SAME_SITE=lax`;
- starts Uvicorn with `--proxy-headers` and `--forwarded-allow-ips="*"` so Railway's forwarded scheme and client IP headers are honored correctly.

Database migrations are applied automatically during FastAPI startup through Alembic before the app begins serving requests. With the current single-replica Railway deployment model, no separate migration job is required.

Railpack's Python runtime also expects a root [requirements.txt](/Users/sebastienchristian/d/CODE%20LAB/marawa/requirements.txt) file. In this repository that file mirrors `backend/pyproject.toml`'s main dependency list so Railpack can install FastAPI, Uvicorn, and the related backend packages directly into the deploy image.

### Recommended Railway setup

1. Create one Railway web service from this repository.
2. Attach one Railway-managed PostgreSQL service.
3. Add a persistent volume to the web service.
4. Mount the volume at `/storage`.
5. Set `DATA_DIR=/storage`.
6. Set `MARAWA_DATABASE_URL` from the Railway PostgreSQL service.
7. Set `SESSION_SECRET`.
8. Set `SESSION_COOKIE_SECURE=true` if you want the value to be explicit in Railway rather than relying on the start-script default.
9. Set `BOOTSTRAP_ADMIN_EMAIL` and `BOOTSTRAP_ADMIN_PASSWORD` for the first deploy.
10. Leave `PORT` unset unless you have a special reason; Railway injects it.
11. Set `MODEL_NAME` only if you want to override the default embedding model.
12. Keep the web service at a single replica.

### Public domain

After the service deploys successfully, open the Railway service networking settings and generate a public domain for the service. The generated domain will serve both the FastAPI API under `/api/*` and the built frontend at `/`.

### Persistent volume note

Railway volumes are the right place for:

- downloaded sentence-transformers model artifacts;
- the persistent runtime directory expected by the backend;
- short-lived CSV uploads and CSV exports if you choose to retain them locally.

PostgreSQL data should live in Railway-managed PostgreSQL, not on the mounted volume.

### Cutover checklist

Use the production maintenance window to:

1. deploy the app with the Railway Postgres service attached and the volume mounted;
2. confirm the service boots cleanly and `/api/health` passes;
3. sign in as the bootstrap admin;
4. upload the fresh production CSV;
5. wait for the rebuild job to finish;
6. verify dataset counts, search, exploration, CSV export, and the admin review/users screens;
7. open production traffic only after those checks pass.

## Product Access Model

- Anonymous users may access only the exploration page and its minimum supporting API surface.
- Authenticated `guest` users can browse the dataset read-only.
- Authenticated `contributor` users can create and edit stories and create new canonical terms.
- Authenticated `admin` users can manage users, import and export CSV, run dataset actions, curate terms, and resolve review work.

## Exploration UX

- Non-admin users use the public single-trope exploration workflow: search for one trope, select it, and inspect original plus related stories on the map.
- Admin users use exploration filter sets instead of the public single-trope card.
- Each admin filter set can combine:
  - a sentence-based vectorized trope search;
  - one or more selected tropes, including a bulk `Select all tropes` action on the current suggestion list;
  - optional hard story-field filters.
- When a filter set already has selected tropes, the hard-filter controls only offer fields and values that keep the set non-empty.

## Known Product Constraints

- Single active dataset: only one dataset is active in the product at a time.
- Single Railway web replica: the current architecture relies on an in-process job runner and local artifact storage.
- No story deletion: stories can be replaced through dataset upload, but individual stories are not deleted through the app.
- No self-signup: accounts are admin-created or invite-only.
- Pending contributor content is visible immediately and is not marked in the normal UI.
- CSV remains a compatibility surface, not the live working store.
