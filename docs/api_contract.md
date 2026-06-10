# API Contract

## Overview

This document defines the initial HTTP API contract for the Marawa web app.

The API is JSON-first, except for CSV upload and CSV download. It is designed for:
- a shared multi-user application;
- one active dataset at a time;
- optimistic concurrency on mutable resources;
- durable background jobs for dataset import and full rebuilds.

Base path:

```text
/api
```

No authentication is used in the initial product.

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

## Shared Schemas

### DatasetSummary

```json
{
  "id": "dataset_20260608_001",
  "version": 12,
  "status": "active",
  "source_filename": "20260417_mmp.csv",
  "story_count": 1284,
  "trope_count": 530,
  "keyword_count": 744,
  "last_successful_rebuild_at": "2026-06-08T18:30:00Z",
  "latest_job": {
    "id": "job_01",
    "job_type": "full_rebuild",
    "status": "succeeded"
  },
  "search_status": "ready"
}
```

### Term

```json
{
  "id": "trope_001",
  "text": "younger sibling outwits elder",
  "story_count": 18,
  "version": 3
}
```

### Story

```json
{
  "id": "story_001",
  "dataset_id": "dataset_20260608_001",
  "version": 4,
  "record_origin": "csv_import",
  "source_row_number": 17,
  "label": "The pandanus woman [story_001]",
  "created_at": "2026-06-08T18:30:00Z",
  "updated_at": "2026-06-08T18:42:00Z",
  "fields": {
    "Entered by": "Clothilde Volk",
    "Source first or second hand": "",
    "Source": "Bensa & Rivierre 1983",
    "pages": "16-31",
    "Other source": "",
    "URL ?": "",
    "territory": "New Caledonia",
    "lg group": "Cèmuhî",
    "original language": "Cèmuhî",
    "lg of publication": "French",
    "bilingual?": "yes",
    "storyteller": "Maurice Kodèm",
    "date of recording": "1971",
    "place of recording": "Kokingone",
    "space coord": "-20.859062, 165.258667",
    "editor": "",
    "translator": "",
    "Story title (Eng)": "The pandanus woman",
    "Story title (French)": "",
    "Story title (other)": "",
    "1-sentence summary": "",
    "Abstract (Eng)": "",
    "Abstract (Fr)": "",
    "Keywords (Eng)": "pandanus ; woman",
    "Motifs (Eng)": "§§ woman becomes tree",
    "proposition de nouveaux motifs": "",
    "species": "",
    "non-human": "",
    "placenames": "",
    "named characters": "",
    "external link": "",
    "description of link": "",
    "Connection to other stories": "",
    "Megamotifs": "",
    "Thème": "",
    "Conte type": "",
    "Autres infos données dans le texte, pour la fiche conte": "",
    "ATU conte-type(AI ?)": "",
    "ATU motifs (AI?)": ""
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

### Job

```json
{
  "id": "job_01",
  "dataset_id": "dataset_20260608_001",
  "job_type": "full_rebuild",
  "status": "queued",
  "requested_at": "2026-06-08T18:45:00Z",
  "started_at": null,
  "finished_at": null,
  "error_code": null,
  "error_message": null,
  "payload": {},
  "result": {}
}
```

## Endpoints

## Health

### `GET /api/health`

Purpose:
- liveness and basic readiness check.

Response `200 OK`:

```json
{
  "status": "ok"
}
```

## Active Dataset

### `GET /api/dataset`

Purpose:
- fetch the currently active dataset summary.

Response `200 OK`:
- `DatasetSummary`

## Jobs

### `GET /api/jobs`

Purpose:
- list recent import and rebuild jobs.

Query parameters:
- `limit` optional, default `20`
- `status` optional
- `job_type` optional

Response `200 OK`:

```json
{
  "items": [
    {
      "id": "job_01",
      "dataset_id": "dataset_20260608_001",
      "job_type": "full_rebuild",
      "status": "running",
      "requested_at": "2026-06-08T18:45:00Z",
      "started_at": "2026-06-08T18:45:01Z",
      "finished_at": null,
      "error_code": null,
      "error_message": null,
      "payload": {},
      "result": {}
    }
  ]
}
```

### `GET /api/jobs/{job_id}`

Purpose:
- fetch one job with its latest status.

Response `200 OK`:
- `Job`

Response `404 Not Found`:
- unknown job ID.

## Dataset Import And Rebuild

### `POST /api/imports`

Purpose:
- upload a CSV and queue a staged dataset replacement job.

Request:
- `multipart/form-data`
- fields:
  - `file`: CSV file
  - `expected_dataset_version`: integer

Response `202 Accepted`:

```json
{
  "job": {
    "id": "job_import_01",
    "dataset_id": "dataset_staged_20260608_002",
    "job_type": "import_dataset",
    "status": "queued",
    "requested_at": "2026-06-08T19:00:00Z",
    "started_at": null,
    "finished_at": null,
    "error_code": null,
    "error_message": null,
    "payload": {
      "source_filename": "20260417_mmp.csv"
    },
    "result": {}
  }
}
```

Response `409 Conflict`:
- active dataset version does not match `expected_dataset_version`.

Response `422 Unprocessable Entity`:
- missing file;
- invalid import parameters.

### `POST /api/rebuilds`

Purpose:
- queue a full rebuild for the active dataset.

Request:

```json
{
  "expected_dataset_version": 12,
  "reason": "manual_rebuild"
}
```

Response `202 Accepted`:
- `Job`

Response `409 Conflict`:
- active dataset version mismatch.

## Stories

### `GET /api/stories`

Purpose:
- list stories in the active dataset.

Query parameters:
- `q` optional free-text filter over titles and summaries
- `trope` optional exact trope text or trope ID
- `keyword` optional exact keyword text or keyword ID
- `territory` optional
- `limit` optional, default `50`
- `offset` optional, default `0`

Response `200 OK`:

```json
{
  "items": [
    {
      "id": "story_001",
      "dataset_id": "dataset_20260608_001",
      "version": 4,
      "record_origin": "csv_import",
      "source_row_number": 17,
      "label": "The pandanus woman [story_001]",
      "created_at": "2026-06-08T18:30:00Z",
      "updated_at": "2026-06-08T18:42:00Z",
      "fields": {
        "Story title (Eng)": "The pandanus woman",
        "territory": "New Caledonia",
        "1-sentence summary": ""
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
        }
      ]
    }
  ],
  "total": 1,
  "limit": 50,
  "offset": 0
}
```

### `GET /api/stories/{story_id}`

Purpose:
- fetch the full story representation.

Response `200 OK`:
- `Story`

Response `404 Not Found`:
- unknown story ID in the active dataset.

### `POST /api/stories`

Purpose:
- create a new story in the active dataset.

Request:

```json
{
  "expected_dataset_version": 12,
  "fields": {
    "Entered by": "Researcher",
    "Source first or second hand": "",
    "Source": "",
    "pages": "",
    "Other source": "",
    "URL ?": "",
    "territory": "",
    "lg group": "",
    "original language": "",
    "lg of publication": "",
    "bilingual?": "",
    "storyteller": "",
    "date of recording": "",
    "place of recording": "",
    "space coord": "",
    "editor": "",
    "translator": "",
    "Story title (Eng)": "New story",
    "Story title (French)": "",
    "Story title (other)": "",
    "1-sentence summary": "",
    "Abstract (Eng)": "",
    "Abstract (Fr)": "",
    "Keywords (Eng)": "",
    "Motifs (Eng)": "",
    "proposition de nouveaux motifs": "",
    "species": "",
    "non-human": "",
    "placenames": "",
    "named characters": "",
    "external link": "",
    "description of link": "",
    "Connection to other stories": "",
    "Megamotifs": "",
    "Thème": "",
    "Conte type": "",
    "Autres infos données dans le texte, pour la fiche conte": "",
    "ATU conte-type(AI ?)": "",
    "ATU motifs (AI?)": ""
  },
  "tropes": [
    "woman becomes tree"
  ],
  "keywords": [
    "pandanus"
  ]
}
```

Response `201 Created`:

```json
{
  "story": {},
  "queued_job": {
    "id": "job_rebuild_01",
    "dataset_id": "dataset_20260608_001",
    "job_type": "full_rebuild",
    "status": "queued",
    "requested_at": "2026-06-08T19:10:00Z",
    "started_at": null,
    "finished_at": null,
    "error_code": null,
    "error_message": null,
    "payload": {},
    "result": {}
  }
}
```

Response `409 Conflict`:
- active dataset version mismatch.

### `PUT /api/stories/{story_id}`

Purpose:
- replace the editable content of an existing story.

Request:

```json
{
  "expected_version": 4,
  "fields": {},
  "tropes": [
    "woman becomes tree",
    "jealous sibling causes exile"
  ],
  "keywords": [
    "pandanus",
    "jealousy"
  ]
}
```

Response `200 OK`:

```json
{
  "story": {},
  "queued_job": {
    "id": "job_rebuild_02",
    "dataset_id": "dataset_20260608_001",
    "job_type": "full_rebuild",
    "status": "queued",
    "requested_at": "2026-06-08T19:15:00Z",
    "started_at": null,
    "finished_at": null,
    "error_code": null,
    "error_message": null,
    "payload": {},
    "result": {}
  }
}
```

Response `404 Not Found`:
- unknown story ID.

Response `409 Conflict`:
- story version mismatch.

Notes:
- This endpoint may add or remove trope assignments and keyword assignments.
- There is intentionally no story delete endpoint.

## Tropes And Keywords

### `POST /api/search/tropes`

Purpose:
- semantic and lexical search over indexed tropes only.

Request:

```json
{
  "query": "someone drifts at sea inside a fruit",
  "limit": 10
}
```

Response `200 OK`:

```json
{
  "items": [
    {
      "id": "trope_012",
      "text": "hero hides inside coconut and drifts at sea",
      "story_count": 6,
      "version": 1,
      "score": 0.83
    }
  ],
  "model_name": "sentence-transformers/paraphrase-multilingual-mpnet-base-v2",
  "search_status": "ready"
}
```

### `POST /api/search/keywords`

Purpose:
- semantic and lexical search over indexed keywords only.

Request:

```json
{
  "query": "canoe",
  "limit": 10
}
```

Response `200 OK`:
- same shape as trope search, with keyword terms.

### `GET /api/tropes`

Purpose:
- list canonical tropes in the active dataset.

Query parameters:
- `q` optional substring filter
- `limit` optional, default `50`
- `offset` optional, default `0`

Response `200 OK`:

```json
{
  "items": [
    {
      "id": "trope_001",
      "text": "woman becomes tree",
      "story_count": 18,
      "version": 3
    }
  ],
  "total": 1,
  "limit": 50,
  "offset": 0
}
```

### `POST /api/tropes/{trope_id}/remove`

Purpose:
- remove a canonical trope.

Request:

```json
{
  "expected_version": 3,
  "remove_assignments": false
}
```

Behavior:
- if `remove_assignments` is `false`, the server removes the trope only when it has no story assignments;
- if `remove_assignments` is `true`, the server removes all story assignments to that trope, deletes the canonical trope, increments affected story versions, and queues a full rebuild.

Response `200 OK`:

```json
{
  "removed_trope_id": "trope_001",
  "affected_story_count": 0,
  "queued_job": {
    "id": "job_rebuild_03",
    "dataset_id": "dataset_20260608_001",
    "job_type": "full_rebuild",
    "status": "queued",
    "requested_at": "2026-06-08T19:20:00Z",
    "started_at": null,
    "finished_at": null,
    "error_code": null,
    "error_message": null,
    "payload": {},
    "result": {}
  }
}
```

Response `404 Not Found`:
- unknown trope ID.

Response `409 Conflict`:
- trope version mismatch;
- trope still has assignments while `remove_assignments` is `false`.

## Exploration

### `POST /api/exploration/trope-candidates`

Purpose:
- find the closest indexed tropes for a free-text exploration prompt.

Request:

```json
{
  "query": "someone gets inside a coconut and drifts at sea",
  "limit": 12
}
```

Response `200 OK`:

```json
{
  "items": [
    {
      "id": "trope_012",
      "text": "hero hides inside coconut and drifts at sea",
      "story_count": 6,
      "version": 1,
      "score": 0.83
    }
  ],
  "model_name": "sentence-transformers/paraphrase-multilingual-mpnet-base-v2",
  "search_status": "ready"
}
```

### `POST /api/exploration/network`

Purpose:
- build the map-ready exploration network for one selected trope and a similarity threshold.

Request:

```json
{
  "selected_trope_id": "trope_012",
  "min_similarity": 0.62,
  "related_limit": 60
}
```

Response `200 OK`:

```json
{
  "selected_trope": {
    "id": "trope_012",
    "text": "hero hides inside coconut and drifts at sea",
    "story_count": 6,
    "version": 1
  },
  "related_tropes": [
    {
      "id": "trope_044",
      "text": "person crosses ocean in floating shell",
      "story_count": 4,
      "version": 1,
      "score": 0.74
    }
  ],
  "original_markers": [],
  "related_markers": [],
  "connections": [],
  "bounds": null,
  "missing_original_coords": 0,
  "missing_related_coords": 0
}
```

Notes:
- `original_markers` are stories tagged with the selected trope.
- `related_markers` are stories tagged with related tropes, excluding stories already in the original set.
- The response shape is intended to be directly consumable by the frontend map layer.

## CSV Export

### `GET /api/exports/csv`

Purpose:
- download the active dataset as a legacy-compatible CSV.

Response `200 OK`:
- body is `text/csv`
- response header should include a downloadable filename

Response `409 Conflict`:
- optionally used if the system decides export should be blocked during a staged dataset swap

## Status And Staleness Semantics

- Story reads come from SQLite and should reflect committed edits immediately.
- Trope search, keyword search, and exploration responses read from the latest successful artifact revision.
- If a rebuild is queued or running, search endpoints should still return the last successful artifact status plus a `search_status` value such as `stale` or `rebuilding`.

## Explicit Non-Endpoints

- No authentication endpoints.
- No story delete endpoint.
- No general semantic search endpoint over arbitrary story text.
