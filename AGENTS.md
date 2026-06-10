# AGENTS.md

## Project purpose

This repository is the product-grade rewrite of the legacy oral-mytho Streamlit prototype.

The application helps researchers structure, validate, compare, and explore oral mythology stories. Stories enter and leave the system as CSV files. Internally the system may use SQLite and computed artifacts.

## Hard requirements

- Do not use Streamlit.
- Use FastAPI for the backend.
- Use React + TypeScript + Vite for the frontend unless explicitly instructed otherwise.
- Preserve CSV-in / CSV-out compatibility.
- The exported CSV must preserve the exact legacy column names and column order.
- Use "trope" everywhere in code, API, and UI.
- Treat legacy terms "motif" and "pattern" as aliases of "trope".
- The CSV column remains `Motifs (Eng)` for compatibility.
- The CSV keyword column remains `Keywords (Eng)`.
- Stories are never deleted through the app.
- Trope assignments may be hard-deleted.
- Canonical tropes may be hard-deleted only when safe or when an explicit curation action removes their assignments.
- No authentication.
- The app must tolerate concurrent browser users through optimistic concurrency/version checks.
- Background jobs should be durable through SQLite job records.
- Keep domain logic independent from the UI.
- Keep storage logic independent from API route handlers.
- Keep embedding/vector search behind service interfaces.

## Legacy reference

The legacy prototype is in `legacy/oral-mytho`.

Use it as a behavioral reference, especially:
- `mytho_app/constants.py`
- `mytho_app/parsing.py`
- `mytho_app/pipeline.py`
- `mytho_app/exploration.py`
- `mytho_app/embeddings.py`
- `tests/`

Do not copy Streamlit UI structure from `app.py`.

## Example of data

An example of CSV data file is provided in example_data.

## Testing expectations

For every behavior ported from the legacy prototype:
- add or port tests first when practical;
- preserve parsing behavior unless the product spec says otherwise;
- run backend tests after backend changes;
- run frontend lint/build after frontend changes;
- include a short summary of what changed and what was verified.

## Architecture boundaries

Backend modules should follow this separation:

- `core`: pure parsing, normalization, CSV schema, coordinate parsing.
- `db`: SQLAlchemy models, sessions, migrations.
- `services`: business operations and transactions.
- `compute`: embeddings, similarity, rebuild jobs.
- `api`: FastAPI routes and Pydantic schemas only.
- `frontend`: React UI only.

API routes must not directly perform complex business logic.
