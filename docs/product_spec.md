# Product Specification

## Purpose

Marawa is a multi-user web application for researchers working with oral mythology stories. It replaces the legacy local Streamlit prototype with a hosted product that preserves CSV compatibility while adding clearer workflows, durable background processing, and safer concurrent editing.

The system ingests a legacy-compatible CSV, lets users review and edit stories, supports similarity search over tropes and keywords, and exports a legacy-compatible CSV.

## Product Decisions

- The product is a shared multi-user web app.
- It is hosted on Railway.
- There is no authentication.
- The system exposes one working dataset at a time.
- The backend is FastAPI.
- The frontend is React + TypeScript + Vite.
- Internal storage is SQLite.
- Full rebuilds are acceptable and must run as background jobs.
- Concurrent browser users must be handled with optimistic concurrency.
- The deployment uses a single Railway service with a persistent volume.

## Terminology

- `trope` is the product term used in the UI, API, and code.
- `motif` and `pattern` are legacy aliases of `trope`.
- The legacy CSV field name remains exactly `Motifs (Eng)`.
- The legacy CSV keyword field name remains exactly `Keywords (Eng)`.
- Tropes are flat English strings.
- Similarity search applies only to tropes and keywords.

## Target Users

- Researchers importing and curating a shared story dataset.
- Researchers comparing stories through trope and keyword search.
- Researchers exploring geographic relationships between stories linked by semantically similar tropes.

## Product Scope

### In Scope

- Import a legacy-compatible CSV into the active working dataset.
- Validate CSV structure and parsing behavior against the legacy contract.
- Create and edit stories inside the active dataset.
- Preserve all legacy CSV fields in the product, even when the UI emphasizes only a subset.
- Manage trope and keyword assignments on stories.
- Search semantically over tropes and keywords using `sentence-transformers/paraphrase-multilingual-mpnet-base-v2`.
- Explore stories on a map by selecting an indexed trope and looking at related stories.
- Export the active dataset as a legacy-compatible CSV with exact legacy column names and order.
- Run durable background rebuild jobs for full artifact refreshes.
- Detect edit conflicts between concurrent users with optimistic concurrency checks.

### Out of Scope For The Initial Product

- Authentication, user accounts, or permissions.
- Multiple concurrently active datasets.
- Story deletion through the product UI or API.
- Hierarchical trope taxonomies, ontology management, or nested trope structures.
- Similarity search over full stories, abstracts, or arbitrary fields outside tropes and keywords.
- Distributed workers or a separate job queue service.

## Core User Workflows

### 1. Replace The Working Dataset From CSV

1. A user uploads a CSV file.
2. The system validates the file against the legacy CSV contract.
3. The system creates a durable background import/rebuild job.
4. The job stages the new dataset, performs a full rebuild, and promotes it to the active dataset only after success.
5. All users see the new dataset once promotion completes.

Product intent:
- Dataset replacement is a dataset-level action, not a series of per-story deletes.
- Previous dataset revisions may be retained internally for safety, but only one dataset is active in the product at a time.

### 2. Browse And Inspect Stories

1. A user opens the shared dataset.
2. The user browses stories and opens a story detail view.
3. The product shows all legacy fields, plus parsed tropes and keywords.

### 3. Create A New Story

1. A user creates a new story record inside the active dataset.
2. The user fills the legacy-compatible fields.
3. The user assigns trope and keyword strings.
4. The system saves the story and queues a full rebuild job.

### 4. Edit An Existing Story

1. A user opens a story and edits fields, tropes, or keywords.
2. The save request includes the story's current version.
3. If the version matches, the system commits the edit and queues a full rebuild job.
4. If the version is stale, the system rejects the save and returns the latest server version so the user can reconcile.

### 5. Get Trope And Keyword Suggestions

1. A user types a trope or keyword.
2. The system returns the closest indexed terms for that category only.
3. The user may reuse an existing term or keep a new typed one.

### 6. Explore Stories Geographically

1. A user describes a trope in free text.
2. The system returns the closest indexed tropes.
3. The user selects one indexed trope.
4. The system retrieves related tropes by semantic similarity.
5. The user adjusts a similarity threshold.
6. The product renders original stories and related stories on a map, with connections to nearby exact-match stories.

### 7. Export CSV

1. A user requests CSV export.
2. The system exports the active dataset from the current relational source of truth.
3. The output preserves the exact legacy column names and order.

## Functional Requirements

### Dataset Management

- The system must expose one active working dataset at a time.
- CSV import must be a whole-dataset replacement workflow.
- Import must not partially replace the active dataset after a failed rebuild.
- The UI must show the active dataset status and the latest rebuild status.

### Story Management

- Stories must be creatable and editable.
- Stories must not be deletable through the app.
- Stories must retain all legacy CSV fields needed for round-trip export.
- Story edits must update trope and keyword assignments in the same transaction.

### Tropes And Keywords

- Tropes must be stored and surfaced as flat English strings.
- The product must treat `motif` and `pattern` as legacy aliases of `trope`.
- Similarity search must be scoped to tropes and keywords only.
- Trope assignments may be hard-deleted.
- Canonical tropes may be hard-deleted only when safe or when an explicit curation action removes their assignments.

### Background Rebuilds

- Full rebuilds are required after dataset import.
- Full rebuilds are also acceptable after story edits and trope curation actions.
- Rebuild execution must be durable through SQLite job records.
- The UI must surface job state such as queued, running, succeeded, and failed.

### Concurrent Editing

- Every mutable resource must carry a version number.
- Mutations must require an expected version.
- On version mismatch, the API must reject the write and return enough information for the frontend to show a conflict state.

## Non-Functional Requirements

### Hosting And Operations

- The product runs as a single Railway service.
- The service uses a persistent volume for SQLite, uploads, exports, and search artifacts.
- The service should be configured as a single running instance because SQLite and volume-backed artifacts are shared local resources.

### Reliability

- Background jobs must survive process restarts because job intent is recorded in SQLite.
- The active dataset must remain usable while a rebuild is pending for routine edits.
- Dataset replacement imports must promote only fully validated, fully rebuilt data.

### Performance

- Full rebuild latency is acceptable for the initial product.
- The initial product optimizes for correctness and simplicity over incremental indexing.

## User Experience Rules

- The UI must use `trope` consistently.
- The UI may mention `motif` or `pattern` only as legacy aliases when needed for migration clarity.
- The UI must communicate when similarity views are rebuilding or temporarily stale.
- The UI must communicate optimistic concurrency conflicts clearly and non-destructively.

## Success Criteria For The Initial Release

- Researchers can upload a legacy-compatible CSV and make it the active dataset.
- Researchers can create and edit stories without breaking CSV round-trip compatibility.
- Researchers can search for similar tropes and keywords using the configured embedding model.
- Researchers can explore related stories geographically from a selected trope.
- Researchers can export a CSV with the exact legacy column names and order.
- Concurrent edits do not silently overwrite one another.
