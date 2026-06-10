# Frontend

This directory contains the React + TypeScript + Vite frontend for Marawa.

## Requirements

- Node.js 20+ is recommended.
- The FastAPI backend should be running locally.

## Install

```bash
cd frontend
npm install
```

## Run in Development

Start the FastAPI backend first. The Vite dev server proxies `/api/*` requests to `http://127.0.0.1:8000` by default.

```bash
cd backend
../.venv/bin/python -m uvicorn app.main:app --reload --port 8000
```

In a second terminal:

```bash
cd frontend
npm run dev
```

Open the local Vite URL, usually [http://127.0.0.1:5173](http://127.0.0.1:5173).

If the Dataset page shows `GET /api/dataset/status could not reach the backend`, the Vite dev server is up but the FastAPI backend is not reachable on the proxy target.

## Custom Backend Origin

If your backend is listening on a different origin, set `VITE_BACKEND_ORIGIN` before starting Vite:

```bash
cd frontend
VITE_BACKEND_ORIGIN=http://127.0.0.1:8010 npm run dev
```

## Build

```bash
cd frontend
npm run build
```

## Lint

```bash
cd frontend
npm run lint
```
