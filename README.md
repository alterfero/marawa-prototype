# Marawa

Marawa is the product-grade rewrite of the legacy oral mythology prototype. The backend is FastAPI, the frontend is React + TypeScript + Vite, and production serves the built frontend directly from FastAPI as a single web service.

## Local Development

- Backend:

```bash
cd backend
../.venv/bin/python -m uvicorn app.main:app --reload --port 8000
```

- Frontend:

```bash
cd frontend
npm install
npm run dev
```

By default, the backend stores SQLite data under [data](/Users/sebastienchristian/d/CODE%20LAB/marawa/data). If you do not set `DATA_DIR`, the local default is the repository `data/` directory.

## Runtime Environment

The deployed app reads these environment variables:

- `DATA_DIR`: base directory for runtime data. Default locally: repo `data/`.
- `DATABASE_PATH`: optional explicit SQLite file path. If set, it overrides the default `DATA_DIR/app.db`.
- `MODEL_NAME`: embedding model name. Default: `sentence-transformers/paraphrase-multilingual-mpnet-base-v2`.
- `PORT`: HTTP port for the FastAPI process. Railway sets this automatically.

The backend stores:

- SQLite database at `DATABASE_PATH` or `DATA_DIR/app.db`
- sentence-transformers cache under `DATA_DIR/model-cache`

## Railway Deployment

This repository is configured for a single Railway service via [railway.toml](/Users/sebastienchristian/d/CODE%20LAB/marawa/railway.toml). The build step installs backend dependencies, builds the Vite frontend, and the FastAPI app serves the resulting `frontend/dist` files in production.

Because the frontend and backend manifests live under `frontend/` and `backend/` instead of the repo root, Railway also needs the root [railpack.json](/Users/sebastienchristian/d/CODE%20LAB/marawa/railpack.json) file so Railpack installs both Node.js and Python before running the build command.

Railway and Railpack already execute the configured commands in a shell, so the commands in `railway.toml` should not be wrapped in `sh -lc`.

### Recommended Railway setup

1. Create one Railway service from this repository.
2. Add a persistent volume to that service.
3. Mount the volume at `/storage`.
4. Set `DATA_DIR=/storage`.
5. Leave `PORT` unset unless you have a special reason; Railway injects it.
6. Set `MODEL_NAME` only if you want to override the default embedding model.

If you prefer an explicit SQLite file path, set `DATABASE_PATH=/storage/app.db`. In the normal setup, `DATA_DIR=/storage` is enough.

### Public domain

After the service deploys successfully, open the Railway service networking settings and generate a public domain for the service. The generated domain will serve both the FastAPI API under `/api/*` and the built frontend at `/`.

### Persistent volume note

Railway volumes are the right place for:

- the SQLite database
- downloaded sentence-transformers model artifacts

Known limitation: Railway services using a mounted volume should run as a single instance. Do not scale this service to multiple replicas while it depends on a single attached volume.

## Known Limitations

- No authentication: the application is currently intended for trusted environments only.
- Single Railway instance: the deployment is designed for one Railway service instance when using a mounted volume.
- SQLite one-writer behavior: concurrent readers are fine, but write-heavy workloads are still constrained by SQLite's single-writer model.
- No story deletion: stories can be replaced through dataset upload, but individual stories are not deleted through the app.
- No audit trail: the app does not yet keep a durable history of who changed trope assignments or when those review decisions were made.
