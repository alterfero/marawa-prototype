# API Contract

## Overview

This document defines the target HTTP API contract for the authenticated Marawa web app.

The API is JSON-first, except for CSV upload and CSV download. It is designed for:
- a shared multi-user application;
- one active dataset at a time;
- anonymous public exploration access;
- role-based authenticated access for guests, contributors, and admins;
- optimistic concurrency on mutable resources;
- durable background jobs for dataset import and full rebuilds;
- durable review and audit workflows.

Base path:

```text
/api
```

## Authentication Model

- Authentication uses built-in email/password accounts.
- Accounts are admin-created or invite-only.
- There is no self-signup endpoint.
- Authenticated sessions are cookie-based.
- Mutating authenticated requests must include CSRF protection.
- Anonymous users may access only the public exploration experience and its minimum supporting endpoints.

## Access Matrix

| Surface | Anonymous | Guest | Contributor | Admin |
| --- | --- | --- | --- | --- |
| Health | Yes | Yes | Yes | Yes |
| Public exploration endpoints | Yes | Yes | Yes | Yes |
| Dataset read | No | Yes | Yes | Yes |
| Stories read | No | Yes | Yes | Yes |
| Search read | No | Yes | Yes | Yes |
| Jobs read | No | Yes | Yes | Yes |
| Story create and edit | No | No | Yes | Yes |
| Story trope and keyword assignment changes | No | No | Yes | Yes |
| Canonical trope and keyword create | No | No | Yes | Yes |
| CSV import and export | No | No | No | Yes |
| Canonical trope merge and delete | No | No | No | Yes |
| Canonical keyword curation | No | No | No | Yes |
| User management | No | No | No | Yes |
| Review queue and review resolution | No | No | No | Yes |

## Conventions

### Content Types

- JSON requests and responses use `application/json`.
- CSV import uses `multipart/form-data`.
- CSV export uses `text/csv`.

### Timestamps

- All timestamps use UTC ISO 8601 strings.

### Identifiers

- Resource identifiers are opaque strings.
- The frontend must not infer meaning from IDs.

### Optimistic Concurrency

- Mutable resources expose an integer `version`.
- Mutating requests must include the relevant optimistic concurrency token such as `expected_version` or `expected_dataset_version`.
- On mismatch, the API returns `409 Conflict`.

### Review Visibility

- Contributor-created and contributor-edited content may be reviewed later by admins.
- Normal read models do not need to expose review-state markers to non-admin users.
- Admin review endpoints may expose review state and resolution metadata.

### Error Shape

```json
{
  "error": {
    "code": "version_conflict",
    "message": "Story version does not match the current server version.",
    "details": {},
    "current_version": 7,
    "current_resource": {}
  }
}
```

Common auth and access errors:
- `401 Unauthorized` for missing or invalid authenticated sessions.
- `403 Forbidden` for authenticated users who lack the required role.
- `409 Conflict` for optimistic concurrency failures or curation conflicts.

## Shared Schemas

### CurrentUser

```json
{
  "id": "user_001",
  "email": "admin@example.org",
  "display_name": "Admin User",
  "role": "admin",
  "status": "active"
}
```

### DatasetSummary

```json
{
  "id": "dataset_20260617_001",
  "version": 12,
  "status": "active",
  "source_filename": "20260417_mmp.csv",
  "story_count": 1284,
  "trope_count": 530,
  "keyword_count": 744,
  "last_successful_rebuild_at": "2026-06-17T18:30:00Z",
  "latest_job": {
    "id": "job_01",
    "job_type": "full_rebuild",
    "status": "succeeded"
  },
  "search_status": "ready"
}
```

### Story

```json
{
  "id": "story_001",
  "dataset_id": "dataset_20260617_001",
  "version": 4,
  "record_origin": "csv_import",
  "source_row_number": 17,
  "label": "The pandanus woman [story_001]",
  "created_at": "2026-06-17T18:30:00Z",
  "updated_at": "2026-06-17T18:42:00Z",
  "fields": {
    "Story title (Eng)": "The pandanus woman",
    "Abstract (Eng)": "",
    "Keywords (Eng)": "pandanus ; woman",
    "Motifs (Eng)": "woman becomes tree"
  },
  "tropes": [
    {
      "id": "trope_001",
      "text": "woman becomes tree"
    }
  ],
  "keywords": [
    {
      "id": "keyword_001",
      "text": "pandanus"
    },
    {
      "id": "keyword_002",
      "text": "woman"
    }
  ]
}
```

### CanonicalTerm

```json
{
  "id": "trope_001",
  "dataset_id": "dataset_20260617_001",
  "text": "woman becomes tree",
  "story_count": 18,
  "version": 3
}
```

### Job

```json
{
  "id": "job_01",
  "dataset_id": "dataset_20260617_001",
  "job_type": "full_rebuild",
  "status": "queued",
  "requested_at": "2026-06-17T18:45:00Z",
  "started_at": null,
  "finished_at": null,
  "error_code": null,
  "error_message": null,
  "payload": {},
  "result": {}
}
```

### ReviewItem

```json
{
  "id": "review_001",
  "dataset_id": "dataset_20260617_001",
  "review_type": "trope_pending",
  "subject_table": "tropes",
  "subject_id": "trope_001",
  "status": "pending",
  "created_by_user_id": "user_002",
  "resolved_by_user_id": null,
  "created_at": "2026-06-17T18:45:00Z",
  "resolved_at": null,
  "metadata": {}
}
```

## Endpoints

## Health

### `GET /api/health`

Access:
- public

Purpose:
- liveness and basic readiness check.

## Authentication

### `POST /api/auth/login`

Access:
- public

Purpose:
- authenticate a user with email and password and create a session.

Notes:
- no self-signup companion endpoint exists.

### `POST /api/auth/redeem-token`

Access:
- public

Purpose:
- redeem an admin-issued invite token or admin-issued reset token, set a new password, and create a session.

### `POST /api/auth/logout`

Access:
- authenticated users

Purpose:
- revoke the current session.

### `GET /api/auth/me`

Access:
- authenticated users

Purpose:
- fetch the currently authenticated user and role.

## User Management

### `GET /api/admin/users`

Access:
- admin

Purpose:
- list users and their statuses.

### `POST /api/admin/users`

Access:
- admin

Purpose:
- create a new user account or invite.

Notes:
- the response may include invite or reset metadata needed for first login.

### `PATCH /api/admin/users/{user_id}`

Access:
- admin

Purpose:
- update display name, role, or account metadata.

### `POST /api/admin/users/{user_id}/deactivate`

Access:
- admin

Purpose:
- deactivate the user and revoke active sessions.

### `POST /api/admin/users/{user_id}/activate`

Access:
- admin

Purpose:
- reactivate a previously deactivated user.

### `POST /api/admin/users/{user_id}/reset-password`

Access:
- admin

Purpose:
- create an admin-triggered password reset flow.

## Dataset

### `GET /api/dataset/status`

Access:
- guest, contributor, admin

Purpose:
- fetch the currently active dataset summary and rebuild status.

### `POST /api/dataset/upload`

Access:
- admin

Purpose:
- upload a CSV and queue a staged dataset replacement job.

Contract notes:
- import is whole-dataset replacement only;
- the active dataset must remain unchanged until the staged import succeeds.

### `GET /api/dataset/export.csv`

Access:
- admin

Purpose:
- export the active dataset as a legacy-compatible CSV.

### `DELETE /api/dataset`

Access:
- admin only

Purpose:
- destructive maintenance or debug action.

Contract notes:
- should be disabled or tightly controlled in production.

## Jobs

### `GET /api/jobs`

Access:
- guest, contributor, admin

Purpose:
- list recent import and rebuild jobs.

### `GET /api/jobs/{job_id}`

Access:
- guest, contributor, admin

Purpose:
- fetch one job with its latest status.

## Stories

### `GET /api/stories`

Access:
- guest, contributor, admin

Purpose:
- list stories in the active dataset.

### `GET /api/stories/{story_id}`

Access:
- guest, contributor, admin

Purpose:
- fetch one story detail record.

### `POST /api/stories`

Access:
- contributor, admin

Purpose:
- create a story in the active dataset.

Contract notes:
- contributor-created stories save immediately;
- contributor-created stories also create review work for admins.

### `PATCH /api/stories/{story_id}`

Access:
- contributor, admin

Purpose:
- partially update a story's editable CSV-backed fields in an optimistic-concurrency-protected workflow.

Contract notes:
- contributor edits save immediately;
- contributor edits also create or update review work for admins.
- trope and keyword assignment changes use the dedicated assignment endpoints below.

### `GET /api/stories/{story_id}/tropes`

Access:
- guest, contributor, admin

Purpose:
- list trope assignments for a story.

### `POST /api/stories/{story_id}/tropes`

Access:
- contributor, admin

Purpose:
- add a trope assignment to a story.

### `PUT /api/stories/{story_id}/tropes/{trope_id}`

Access:
- contributor, admin

Purpose:
- replace a trope assignment on a story.

### `DELETE /api/stories/{story_id}/tropes/{trope_id}`

Access:
- contributor, admin

Purpose:
- hard-delete a trope assignment from a story.

### `GET /api/stories/{story_id}/keywords`

Access:
- guest, contributor, admin

Purpose:
- list keyword assignments for a story.

### `POST /api/stories/{story_id}/keywords`

Access:
- contributor, admin

Purpose:
- add a keyword assignment to a story.

### `PUT /api/stories/{story_id}/keywords/{keyword_id}`

Access:
- contributor, admin

Purpose:
- replace a keyword assignment on a story.

### `DELETE /api/stories/{story_id}/keywords/{keyword_id}`

Access:
- contributor, admin

Purpose:
- remove a keyword assignment from a story.

## Canonical Tropes

### `GET /api/tropes`

Access:
- guest, contributor, admin

Purpose:
- list canonical tropes in the active dataset.

### `GET /api/tropes/{trope_id}`

Access:
- guest, contributor, admin

Purpose:
- fetch one canonical trope detail record.

### `POST /api/tropes`

Access:
- contributor, admin

Purpose:
- create a canonical trope in the active dataset.

Contract notes:
- contributor-created tropes become visible immediately and enter `pending_review`.

### `DELETE /api/tropes/{trope_id}`

Access:
- admin

Purpose:
- delete a canonical trope when safe or through an explicit curation action that removes its assignments.

## Canonical Keywords

### `GET /api/keywords`

Access:
- guest, contributor, admin

Purpose:
- list canonical keywords in the active dataset.

### `GET /api/keywords/{keyword_id}`

Access:
- guest, contributor, admin

Purpose:
- fetch one canonical keyword detail record.

### `POST /api/keywords`

Access:
- contributor, admin

Purpose:
- create a canonical keyword in the active dataset.

Contract notes:
- contributor-created keywords become visible immediately and enter `pending_review`.

## Search

### `POST /api/search/tropes`

Access:
- guest, contributor, admin

Purpose:
- return the closest indexed trope terms in the active dataset.

### `POST /api/search/keywords`

Access:
- guest, contributor, admin

Purpose:
- return the closest indexed keyword terms in the active dataset.

## Exploration

### `POST /api/exploration/network`

Access:
- public

Purpose:
- build the public exploration response for a trope phrase or selected trope.

Contract notes:
- this endpoint is part of the minimum public API surface available to anonymous users.

## Visualizations

### `POST /api/visualizations/trope-sequence-graph`

Access:
- guest, contributor, admin

Purpose:
- build the authenticated trope-sequence graph response for internal exploratory analysis.

## Curation

### `GET /api/curation/near-duplicate-tropes`

Access:
- admin

Purpose:
- list near-duplicate trope candidates for curation.

### `POST /api/curation/merge-tropes`

Access:
- admin

Purpose:
- merge one canonical trope into another.

### `POST /api/curation/validate-merges`

Access:
- admin

Purpose:
- validate and apply multiple trope merge decisions.

## Review

### `GET /api/review/items`

Access:
- admin

Purpose:
- list pending and recently resolved review items.

Contract notes:
- defaults to pending items when no status filter is provided.
- includes current subject preview data for stories and canonical terms when the subject still exists.

### `GET /api/review/items/{review_id}`

Access:
- admin

Purpose:
- fetch one review item with resolution context.

### `POST /api/review/items/{review_id}/approve`

Access:
- admin

Purpose:
- approve a pending review item.

Contract notes:
- story review items resolve administrative review work only and do not roll back the already-saved story content.
- approving a pending canonical trope or keyword promotes its review status to `approved`.

### `POST /api/review/items/{review_id}/reject`

Access:
- admin

Purpose:
- reject or otherwise resolve a pending review item.

Contract notes:
- story review items resolve administrative review work only and do not roll back the already-saved story content.
- rejecting a pending canonical term must define the resolution path, such as merge-and-resolve or remove-assignments-and-delete.
- pending canonical keywords follow the same merge-or-delete review resolution path as pending canonical tropes.

## Implementation Notes Locked By This Contract

- PostgreSQL is the transactional system of record.
- CSV import and export remain legacy compatibility surfaces, not the live working store.
- Tropes and keywords are dataset-scoped.
- Anonymous access is limited to exploration.
- Guests are read-only.
- Contributors write stories and new terms.
- Admins own dataset actions, user management, curation, and review.
- Contributor-created content is visible immediately and unmarked in the normal UI.
- Important auth, admin, review, dataset, and write actions must be auditable.
