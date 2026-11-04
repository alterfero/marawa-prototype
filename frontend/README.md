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

## Tests

```bash
cd frontend
npm test
```

The map layout tests cover shared-location polygons, zoom-dependent clustering,
stable story positions, marker sizes, and the antimeridian. Stories sharing a
location separate by zoom level 12; thin guides connect their display positions
to the recorded location. The live map and SVG/print exports use the same layout.

## TMI atlas

Admins can open **TMI atlas** (`#/tmi`) to explore the complete, separate reference
catalogue from Katja Mellmann’s CSV (2020), bundled as `legacy_tropes/tmi.csv`.
The chapter ring grows outward into divisions, sections and dotted codes.
Overlapping circles become count stickers; clicking a sticker zooms in,
and clicking an entry opens its complete descendant tree. Breadcrumbs and the
branch directory lead back to its ancestors. Search accepts codes or words.

Each trope’s inspector displays its bibliography exactly as supplied in the CSV,
or an explicit message when no sources are listed. Original entry text and row
numbers are available in CSV-record details. Historical first-edition entries
remain separately identifiable; the earlier `tmi.txt` is not used.

The canvas supports drag/pinch/wheel navigation, zoom and marker-size sliders,
fit/reset buttons, and fullscreen presentation. Keyboard users can use search
and the branch directory, or focus the canvas and use arrows, +/−, and 0.
The layout tests cover full node coverage, subtree traversal, clustering, and
cursor-anchored zoom. Source details are documented in `legacy_tropes/README.md`.
