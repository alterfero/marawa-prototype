# Product Specification

## Purpose

Marawa is a multi-user web application for researchers working with oral mythology stories. It replaces the legacy local Streamlit prototype with a hosted product that preserves CSV compatibility while adding authenticated access, durable background processing, safer concurrent editing, and a PostgreSQL-backed source of truth.

Stories enter the system through a legacy-compatible CSV import, are edited and explored in the product, and can be exported again as a legacy-compatible CSV with the exact legacy column names and order.

## Product Decisions

- The product is a shared multi-user web app.
- It is hosted on Railway.
- The backend is FastAPI.
- The frontend is React + TypeScript + Vite.
- PostgreSQL is the transactional source of truth.
- CSV remains an admin-only import/export interface for the active dataset.
- The system exposes one working dataset at a time.
- Built-in email/password authentication is required.
- Accounts are admin-created or invite-only. There is no self-signup.
- Password reset is admin-triggered.
- Anonymous public visitors may access the exploration experience only.
- Authenticated users have one of three roles: `guest`, `contributor`, or `admin`.
- Full rebuilds are acceptable and must run as durable background jobs.
- Concurrent browser users must be handled with optimistic concurrency.
- The deployment uses one Railway web service, one Railway-managed PostgreSQL service, and one persistent volume for local artifacts.
- The web service should run as a single replica because the app uses an in-process job runner and local artifact storage.
- The system must keep a durable audit trail for important auth, admin, review, dataset, and write actions.

## Terminology

- `trope` is the product term used in the UI, API, and code.
- `motif` and `pattern` are legacy aliases of `trope`.
- The legacy CSV field name remains exactly `Motifs (Eng)`.
- The legacy CSV keyword field name remains exactly `Keywords (Eng)`.
- Tropes are flat English strings.
- Keywords are flat English strings.
- Similarity search applies only to tropes and keywords.

## Access Model

### Anonymous

- May access the public exploration page and the minimum supporting API surface needed to render it.
- May not access dataset browsing, story browsing, curation, jobs, or authenticated search endpoints.

### Guest

- Authenticated read-only user.
- May read dataset status, stories, tropes, keywords, jobs, search results, and exploration results.
- May not perform any mutation.

### Contributor

- Has all `guest` permissions.
- May create stories.
- May edit stories.
- May add, replace, and remove trope and keyword assignments on stories.
- May create new canonical tropes and canonical keywords.
- Contributor-created content saves immediately but also generates admin review work.

### Admin

- Has all `contributor` permissions.
- May upload and export CSV.
- May manage users and roles.
- May activate, deactivate, and reset user access.
- May run dataset-level actions.
- May merge and delete canonical tropes.
- May curate canonical keywords.
- May resolve review items.

## Review Model

- Contributor story creates and edits save immediately and create review flags for admins.
- Contributor-created canonical tropes save immediately in a `pending_review` state.
- Contributor-created canonical keywords save immediately in a `pending_review` state.
- Pending contributor content is visible immediately in the normal UI with no review marker.
- Review state is administrative metadata and must be surfaced only through admin workflows unless a future requirement says otherwise.

## Product Scope

### In Scope

- Authenticate users with built-in email/password login.
- Create and manage users through admin workflows.
- Support anonymous public exploration access.
- Import a legacy-compatible CSV into the active working dataset.
- Validate CSV structure and parsing behavior against the legacy contract.
- Stage dataset replacement and promote only after successful rebuild.
- Create and edit stories inside the active dataset.
- Preserve all legacy CSV fields in the product, even when the UI emphasizes only a subset.
- Manage trope and keyword assignments on stories.
- Create canonical tropes and keywords from contributor workflows.
- Review contributor-created content through an admin queue.
- Search semantically over tropes and keywords using `sentence-transformers/paraphrase-multilingual-mpnet-base-v2`.
- Explore stories on a map by selecting an indexed trope and looking at related stories.
- Export the active dataset as a legacy-compatible CSV with exact legacy column names and order.
- Run durable background rebuild jobs for full artifact refreshes.
- Detect edit conflicts between concurrent users with optimistic concurrency checks.
- Record a durable audit trail for important actions.

### Out Of Scope For The Initial Production Rollout

- Self-service user registration.
- Multiple concurrently active datasets.
- Story deletion through the product UI or API.
- Hierarchical trope taxonomies, ontology management, or nested trope structures.
- Similarity search over full stories, abstracts, or arbitrary fields outside tropes and keywords.
- A separate worker service or distributed queue.
- Multi-replica web execution.

## Core User Workflows

### 1. Anonymous Exploration

1. A public visitor opens the exploration page.
2. The system allows access without authentication.
3. The visitor searches for or selects a trope through the public exploration workflow.
4. The product renders the exploration map and related stories.

### 2. Login

1. An invited or admin-created user opens the login screen.
2. The user signs in with email and password.
3. The system establishes a secure session.
4. The UI unlocks the routes and controls allowed by the user's role.

### 3. Replace The Working Dataset From CSV

1. An admin uploads a CSV file.
2. The system validates the file against the legacy CSV contract.
3. The system creates a durable background import/rebuild job.
4. The job stages the new dataset in PostgreSQL, performs a full rebuild, and promotes it to active only after success.
5. All users see the new dataset once promotion completes.

Product intent:
- Dataset replacement is a dataset-level action, not a series of per-story deletes.
- Previous dataset revisions may be retained internally for safety and traceability, but only one dataset is active in the UI.

### 4. Browse And Inspect Stories

1. A guest, contributor, or admin opens the shared dataset.
2. The user browses stories and opens a story detail view.
3. The product shows all legacy fields, plus parsed tropes and keywords.

### 5. Create A New Story

1. A contributor or admin creates a new story record inside the active dataset.
2. The user fills the legacy-compatible fields.
3. The user assigns trope and keyword strings.
4. The system saves the story, queues a rebuild job, and creates an admin review item.

### 6. Edit An Existing Story

1. A contributor or admin opens a story and edits fields, tropes, or keywords.
2. The save request includes the story's current version.
3. If the version matches, the system commits the edit, queues a rebuild job, and creates or updates an admin review item.
4. If the version is stale, the system rejects the save and returns the latest server version so the user can reconcile.

### 7. Create A Canonical Trope Or Keyword

1. A contributor or admin enters a new trope or keyword that does not already exist in the active dataset.
2. The system creates the canonical term inside the active dataset.
3. If the creator is a contributor, the term is marked `pending_review`.
4. The term is usable immediately in story workflows.

### 8. Review Contributor Changes

1. An admin opens the review queue.
2. The system lists pending story review items and pending canonical term review items.
3. The admin approves, rejects, merges, or otherwise resolves the items.
4. The system records the decision in the audit trail.

### 9. Export CSV

1. An admin requests CSV export.
2. The system exports the active dataset from PostgreSQL.
3. The output preserves the exact legacy column names and order.

## Functional Requirements

### Authentication And Access Control

- The system must require authentication for all routes except the public exploration experience and basic health/static asset access.
- The system must support built-in email/password authentication.
- The system must not support self-signup.
- Users must be admin-created or invite-only.
- Password reset must be admin-triggered.
- User deactivation must be supported.
- User hard deletion must not be required for the initial rollout.
- Role checks must be enforced on the server, not only in the frontend.

### Dataset Management

- The system must expose one active working dataset at a time.
- CSV import must be an admin-only whole-dataset replacement workflow.
- Import must not partially replace the active dataset after a failed rebuild.
- The active dataset must remain available while a staged replacement is building.
- CSV export must be admin-only.
- The UI must show the active dataset status and the latest rebuild status to authenticated users with read access.

### Story Management

- Stories must be creatable and editable by contributors and admins.
- Stories must not be deletable through the app.
- Stories must retain all legacy CSV fields needed for round-trip export.
- Story edits must update trope and keyword assignments in the same transaction.
- Story create and edit actions from contributors must create review work for admins.

### Tropes And Keywords

- Tropes and keywords must be dataset-scoped.
- Tropes and keywords must be stored and surfaced as flat strings.
- The product must treat `motif` and `pattern` as legacy aliases of `trope`.
- Similarity search must be scoped to tropes and keywords only.
- Contributor-created canonical tropes and keywords must enter a `pending_review` state.
- Trope assignments may be hard-deleted.
- Canonical tropes may be hard-deleted only when safe or when an explicit curation action removes their assignments.

### Review And Audit

- The system must keep durable admin review records for contributor-created or contributor-edited content.
- Pending contributor content must remain visible in the main UI without a review marker.
- The system must keep a durable audit trail for:
  - login and logout events;
  - user creation, role changes, deactivation, and password reset actions;
  - dataset import and export actions;
  - story, trope, and keyword mutations;
  - review decisions;
  - job requests and important job state transitions.

### Background Rebuilds

- Full rebuilds are required after dataset import.
- Full rebuilds are also acceptable after story edits and trope or keyword curation actions.
- Rebuild execution must be durable through PostgreSQL job records.
- The UI must surface job state such as queued, running, succeeded, and failed to authenticated users with read access.

### Concurrent Editing

- Every mutable resource must carry a version number.
- Mutations must require an expected version.
- On version mismatch, the API must reject the write and return enough information for the frontend to show a conflict state.

## Non-Functional Requirements

### Hosting And Operations

- The product runs on Railway.
- The deployment uses:
  - one Railway web service;
  - one Railway-managed PostgreSQL service;
  - one mounted persistent volume for model cache, uploads, exports, and search artifacts.
- The web service should run as a single instance because the product uses an in-process job runner and local artifact storage.

### Reliability

- Background jobs must survive process restarts because job intent is recorded in PostgreSQL.
- The active dataset must remain usable while a rebuild is pending for routine edits.
- Dataset replacement imports must promote only fully validated, fully rebuilt data.

### Security

- Passwords must be stored as secure password hashes, never as plaintext.
- Sessions must be revocable.
- Deactivating a user must prevent future access and invalidate active sessions.
- The app must support secure deployment behind Railway-managed HTTPS.

### Performance

- Full rebuild latency is acceptable for the initial product.
- The initial product optimizes for correctness and simplicity over incremental indexing.

## User Experience Rules

- The UI must use `trope` consistently.
- The UI may mention `motif` or `pattern` only as legacy aliases when needed for migration clarity.
- The UI must communicate when similarity views are rebuilding or temporarily stale.
- The UI must communicate optimistic concurrency conflicts clearly and non-destructively.
- The normal user-facing UI must not mark pending contributor content as pending review.

## Success Criteria For The Initial Production Release

- Anonymous users can access the exploration experience and nothing else.
- Authenticated guests can browse the dataset read-only.
- Contributors can create and edit stories and create new canonical terms without breaking CSV round-trip compatibility.
- Admins can manage users, import and export CSV, curate canonical terms, and resolve review items.
- Researchers can search for similar tropes and keywords using the configured embedding model.
- Researchers can explore related stories geographically from a selected trope.
- The system exports CSV with the exact legacy column names and order.
- Concurrent edits do not silently overwrite one another.
- Important auth, admin, review, and write actions are durably auditable.
