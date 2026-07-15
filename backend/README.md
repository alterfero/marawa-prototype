# Marawa Backend

This directory contains the FastAPI backend for Marawa.

## Important Runtime Facts

- Production deployment should use PostgreSQL, not SQLite.
- Alembic migrations run automatically during backend startup.
- The app starts an in-process background job runner during startup.
- The app must run as a single deployed instance and a single Uvicorn worker.
- If `BOOTSTRAP_ADMIN_EMAIL` and `BOOTSTRAP_ADMIN_PASSWORD` are configured, startup ensures an admin account exists.
- If `frontend/dist` exists, FastAPI can serve the built frontend directly.

## Main Entry Point

The backend entry point is:

```text
backend/app/main.py
```

Typical local run command:

```bash
cd backend
MARAWA_DATABASE_URL="$(../scripts/local_postgres.sh url dev)" ../.venv/bin/python -m uvicorn app.main:app --reload --port 8000
```

## Deployment Guide

For the backend handoff and server-preparation checklist, see:

- [../docs/backend_deployment.md](../docs/backend_deployment.md)

For broader system context, see:

- [../README.md](../README.md)
- [../docs/architecture.md](../docs/architecture.md)
