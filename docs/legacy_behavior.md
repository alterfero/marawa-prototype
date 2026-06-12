# Legacy Behavior Reference

This note summarizes the current behavior of the legacy prototype in [`legacy/oral_mytho`](../legacy/oral_mytho/).
It is derived from `app.py`, `mytho_app/*.py`, and the legacy tests.

Vocabulary note:
- This document uses `trope` as the product term.
- The legacy code and UI use `pattern` and `motif` interchangeably.
- Exact legacy names are preserved where they are part of a file, field, or CSV contract, especially `Motifs (Eng)` and `Keywords (Eng)`.

## 1. Legacy User Workflows

### 1.1 Global workspace model

- The app is a local Streamlit application with three pages: `Data processing`, `Data management`, and `Exploration`.
- The sidebar lets the user choose a processed-data directory. The default is `data/processed`.
- All generated artifacts are stored in that directory and reused across pages.
- The sidebar also shows artifact readiness and exposes CSV export buttons on processing and management pages.

### 1.2 Data processing workflow

- The user uploads a source CSV with `st.file_uploader`.
- The app validates the uploaded bytes as UTF-8 CSV, checks that the legacy required columns exist, rejects malformed rows, and skips blank rows.
- If validation succeeds, the app:
  1. Replaces the working JSONL dataset.
  2. Keeps a timestamped backup of the previous JSONL when one exists.
  3. Deletes stale derived artifacts.
  4. Rebuilds the lexical trope index, lexical keyword index, trope FAISS index, keyword FAISS index, and manifest.
- If semantic dependencies fail during rebuild, the JSONL may already have been replaced. The failure is surfaced after the write step.
- The page also provides:
  - an export button that rebuilds a CSV from the current JSONL;
  - a `Clean session` action that deletes the JSONL, manifest, indices, FAISS files, and backup directory from the current processed-data directory.

### 1.3 Data management workflow

- The management page is locked unless all of these are present and in sync:
  - `entries.jsonl`
  - `patterns_index.json`
  - `keywords_index.json`
  - `patterns.faiss`
  - `keywords.faiss`
  - `manifest.json`
- If JSONL exists but derived artifacts are missing or stale, the page first tries to rebuild them automatically from JSONL.
- The page has three tabs in the legacy UI:
  1. `Add entry`
  2. `Edit entry`
  3. `Delete entry`
- `Add entry` behavior:
  - starts from a blank entry with a generated manual ID like `manual-1a2b3c4d`;
  - separates trope selection from field editing;
  - offers semantic suggestions for tropes and keywords while the user types;
  - lets the user accept a suggestion or keep the typed term;
  - writes the new entry to JSONL, then rebuilds all derived artifacts.
- `Edit entry` behavior:
  - loads an existing entry by label;
  - allows the same trope/keyword picking workflow as add;
  - rewrites the full JSONL and rebuilds all derived artifacts after save.
- `Delete entry` behavior:
  - permanently removes the selected story entry from JSONL;
  - rebuilds all derived artifacts after deletion.
- Semantic suggestion behavior in management:
  - first tries FAISS-based semantic search;
  - falls back to lexical matching if embedding dependencies are unavailable;
  - also keeps a session-local in-memory index of newly typed tropes/keywords that are not yet saved, so later draft edits in the same Streamlit session can see them as suggestions.

### 1.4 Exploration workflow

- The exploration page is also locked until JSONL and all derived artifacts are ready, with the same auto-refresh attempt as management.
- The user types a free-text description of a trope.
- The app searches the saved trope index and offers the closest existing indexed tropes.
- The user chooses one indexed trope as the selected trope.
- The app then finds semantically related tropes for that selected trope, applies a similarity threshold slider, and builds a map network from the surviving related tropes.
- The page shows:
  - counts for original stories, related stories, related tropes, and connections;
  - a collapsible list of tropes currently shown;
  - an interactive Folium map;
  - a story detail panel populated from map clicks.
- Clicking a marker shows story details, matched tropes, abstract text, and all non-empty legacy CSV fields.

## 2. Legacy CSV Contract

### 2.1 Import contract

- The uploaded file must decode as UTF-8. BOM is accepted because the loader uses `utf-8-sig`.
- A readable header row is required.
- Header names are cleaned with whitespace trimming and BOM removal before validation.
- The import requires every legacy canonical column to be present.
- `motifs inhabituels à une version` is accepted as an import alias for `proposition de nouveaux motifs`.
- Column order is not validated on import.
- Extra columns are tolerated on import, including current template-only columns such as `Abstracts : AI or Human ?`, `Motifs validés`, and `motifs Pacifique  ?`.
- Blank data rows are skipped.
- A row that has more values than the header defines is rejected as malformed.
- A file with a header but no non-empty data rows is rejected.

### 2.2 Export contract

- Export always writes UTF-8 with BOM.
- Export always uses `\n` as the line terminator.
- Export always writes exactly the canonical legacy columns, in the canonical order below.
- Extra columns that were tolerated on import are not written back out.
- The exported trope and keyword cells are regenerated from internal normalized arrays, so formatting is canonicalized on export even if the import formatting differed.

### 2.3 Canonical legacy column order

1. `Entered by`
2. `Source first or second hand`
3. `Source`
4. `pages`
5. `Other source`
6. `URL ?`
7. `territory`
8. `lg group`
9. `original language`
10. `lg of publication`
11. `bilingual?`
12. `storyteller`
13. `date of recording`
14. `place of recording`
15. `space coord`
16. `editor`
17. `translator`
18. `Story title (Eng)`
19. `Story title (French)`
20. `Story title (other)`
21. `1-sentence summary`
22. `Abstract (Eng)`
23. `Abstract (Fr)`
24. `Keywords (Eng)`
25. `Motifs (Eng)`
26. `proposition de nouveaux motifs`
27. `species`
28. `non-human`
29. `placenames`
30. `named characters`
31. `external link`
32. `description of link`
33. `Connection to other stories`
34. `Megamotifs`
35. `Thème`
36. `Conte type`
37. `Autres infos données dans le texte, pour la fiche conte`
38. `ATU conte-type(AI ?)`
39. `ATU motifs (AI?)`

### 2.4 Internal entry records created from CSV

- Imported rows become entries with IDs like `csv-00001`.
- `source_row_number` stores the original CSV data-row number as read by `csv.DictReader`.
- `record_origin` is `csv_import` for imported rows and `manual` for manually created rows.
- Timestamps are stored as UTC ISO 8601 strings without microseconds.
- Internal entries also include derived fields such as `patterns`, `keywords`, `search_text`, and `label`.

## 3. Parsing Behavior For Tropes And Keywords

### 3.1 Shared text cleanup and normalization

- `clean_text`:
  - turns `None` into an empty string;
  - removes BOM characters;
  - trims leading and trailing whitespace.
- `normalize_text`:
  - runs `clean_text`;
  - collapses any internal whitespace run to a single space;
  - lowercases the result.
- Deduplication is case-insensitive and whitespace-insensitive, but preserves the first encountered surface form.

### 3.2 Keyword parsing

- The legacy keyword column is exactly `Keywords (Eng)`.
- Keywords are split on semicolons and newlines with the regex `[;\n]+`.
- Each piece is trimmed with `clean_text`.
- Empty pieces are discarded.
- Duplicates are removed while preserving first-seen order.
- Keywords serialize back to CSV as a single line joined with ` ; `.

Example:

```text
wolf ; moon
wolf;  river
```

becomes:

```text
["wolf", "moon", "river"]
```

and reserializes as:

```text
wolf ; moon ; river
```

### 3.3 Trope parsing

- The legacy trope column is exactly `Motifs (Eng)`.
- The legacy code treats this field as a list of `patterns`; the rewrite should treat those as tropes.
- Parsing first normalizes Windows newlines to `\n`.
- If the cell contains `§§`, the parser splits only on `§§`.
- If the cell does not contain `§§`, the parser falls back to splitting on semicolons and newlines.
- Each piece is stripped of surrounding spaces, newlines, and semicolons.
- Empty pieces are discarded.
- Duplicates are removed while preserving first-seen order.
- Tropes serialize back to CSV one per line, each prefixed with `§§ `.

Example:

```text
§§ first trope
§§ second trope
§§ first trope
```

becomes:

```text
["first trope", "second trope"]
```

and reserializes as:

```text
§§ first trope
§§ second trope
```

### 3.4 Entry synchronization behavior

- `sync_entry_fields` is the canonical normalizer for an entry.
- It ensures every canonical CSV column exists in `fields`.
- It preserves extra non-canonical fields inside the JSONL entry object.
- It derives trope and keyword arrays either from explicit `patterns`/`keywords` lists or from the legacy CSV field strings.
- It overwrites `fields["Motifs (Eng)"]` and `fields["Keywords (Eng)"]` with canonical serialized values.
- It rebuilds `search_text` from title fields, abstract fields, tropes, and keywords.
- It rebuilds `label` from the first non-empty title, otherwise a summary snippet, otherwise `entry_id`.
- It always refreshes `updated_at`.

## 4. Current Artifact Pipeline

### 4.1 Files produced by the legacy pipeline

- `entries.jsonl`
  - one JSON object per line;
  - stores normalized entries;
  - may be backed up to `backups/entries-<mtime>.jsonl` before overwrite.
- `patterns_index.json`
  - the lexical trope index;
  - each item contains `normalized`, `text`, `entry_ids`, and `entry_count`.
- `keywords_index.json`
  - the lexical keyword index with the same shape.
- `patterns.faiss`
  - the semantic vector index for the trope texts from `patterns_index.json`.
- `keywords.faiss`
  - the semantic vector index for the keyword texts from `keywords_index.json`.
- `manifest.json`
  - records counts, model name, artifact paths, vector index stats, source CSV name, and the JSONL mtime used to determine sync state.

### 4.2 Rebuild process

- Upload processing and JSONL edits both normalize entries through `sync_entry_fields`.
- `build_term_index` groups tropes or keywords by normalized text.
- Each index item collects unique `entry_ids`.
- Index items are sorted by descending `entry_count`, then alphabetically by the preserved display text.
- FAISS indices are built from the display text values, not from normalized keys.
- The embedding model is `sentence-transformers/paraphrase-multilingual-mpnet-base-v2`.
- FAISS uses an `IndexFlatIP` inner-product index on normalized embeddings.

### 4.3 Staleness and auto-refresh

- The manifest stores `jsonl_mtime_ns`.
- Artifact readiness requires:
  - all six files to exist;
  - the manifest mtime snapshot to match the current JSONL mtime.
- If JSONL exists but the derived artifacts are missing or out of sync, management and exploration attempt an automatic rebuild from JSONL before rendering.

### 4.4 Failure and edge behavior

- Missing `faiss` or `sentence_transformers` raises `EmbeddingDependencyError`.
- Model download problems are retried with `local_files_only=True`; if the model is not already cached locally, semantic rebuild/search remains unavailable.
- The management suggestion UI falls back to lexical search when embeddings are unavailable.
- Upload processing does not catch every non-embedding rebuild failure. In particular, `build_faiss_index` raises `ValueError` for an empty text list, so a dataset with zero unique tropes or zero unique keywords can fail the rebuild after JSONL has already been written.

## 5. Exploration And Map Computation Behavior

### 5.1 Trope retrieval flow

- The exploration query is free text, not a strict existing trope name.
- The app searches the saved trope index and proposes the closest existing indexed tropes.
- Suggestions are limited to saved indexed tropes; unsaved session-local draft tropes are excluded here.
- After the user chooses a selected trope, the app queries the same trope index again to retrieve related tropes, up to 60 candidates.
- The selected trope itself is removed from the related-trope list by normalized comparison.
- A similarity threshold slider filters related tropes before building the network.
- The default threshold in the legacy UI is `0.62`.

### 5.2 Coordinate parsing

- `space coord` is parsed with tolerant logic:
  - accepts plain signed decimals such as `-20.859062, 165.258667`;
  - accepts directional coordinates such as `≈ 16.0° S, 168.4° E`;
  - accepts semicolon-separated values such as `22.2994° ; 166.7483°`;
  - accepts decimal commas such as `-4,198,\n152,163`;
  - ignores approximate markers like `≈` and `~`;
  - strips surrounding parentheses and brackets;
  - converts Unicode minus `−` to ASCII `-`.
- Parsing fails if fewer than two coordinate tokens are found or if the resulting latitude/longitude are out of range.

### 5.3 Network construction

- Original stories are entries whose trope list contains the selected trope after normalized matching.
- Related stories are entries whose trope list contains any filtered related trope, excluding stories already present in the original set.
- If one related story matches several related tropes:
  - it appears once;
  - it keeps all matched tropes;
  - its displayed similarity is the maximum related-trope score.
- Original markers are sorted by title and entry ID.
- Related markers are sorted by descending similarity, then title, then entry ID.

### 5.4 Missing-coordinate fallback behavior

- Original stories with missing or malformed coordinates are placed at `(0.0, 170.0)`.
- Related stories with missing or malformed coordinates are placed around `(0.0, 170.0)` on a fixed fallback ring so they do not all overlap perfectly.
- These markers are labeled `no location` in the UI.

### 5.5 Connections, bounds, and marker display

- Each related story connects to the geographically nearest original story using great-circle distance.
- Each connection stores source coordinates, target coordinates, similarity, and a similarity-based color.
- Related marker colors are interpolated from blue toward red based on similarity and the current threshold.
- Exact selected-trope stories always render in red.
- Map bounds are the min/max latitude and longitude across all visible markers.

### 5.6 Story detail behavior

- Marker popups include a hidden `data-entry-id` so the detail panel can identify the clicked story.
- If popup HTML is unavailable, the app falls back to matching clicked coordinates against marker coordinates.
- The detail panel prefers abstract text in this order:
  1. `Abstract (Eng)`
  2. `Abstract (Fr)`
  3. `1-sentence summary`
- The detail panel then shows matched tropes and every non-empty CSV field.

## 6. Legacy Tests That Should Be Ported

### 6.1 Parsing and CSV contract tests

Port the behavior covered by these legacy tests, even if the new test names change to use `trope` vocabulary:

- `tests/test_pipeline.py::ParsingTests::test_split_patterns_handles_section_markers`
- `tests/test_pipeline.py::ParsingTests::test_split_keywords_deduplicates_values`
- `tests/test_pipeline.py::ParsingTests::test_sync_entry_fields_serializes_terms`
- `tests/test_pipeline.py::PipelineTests::test_entries_to_csv_bytes_exports_normalized_rows`
- `tests/test_pipeline.py::PipelineTests::test_validate_uploaded_csv_bytes_accepts_expected_schema`
- `tests/test_pipeline.py::PipelineTests::test_validate_uploaded_csv_bytes_rejects_missing_columns`

These are the minimum legacy guarantees for trope/keyword parsing, canonical serialization, and CSV schema validation.

### 6.2 Artifact and storage tests

Port the artifact behavior behind the new backend/service layers:

- `tests/test_pipeline.py::PipelineTests::test_term_index_collects_entry_ids`
- `tests/test_pipeline.py::PipelineTests::test_write_entries_jsonl_roundtrip`
- `tests/test_pipeline.py::PipelineTests::test_clear_artifacts_removes_generated_files_and_backups`

The implementation will change, but the observable storage and indexing behavior should stay covered.

### 6.3 Exploration tests

Port the exploration and coordinate behavior:

- `tests/test_exploration.py::ExplorationParsingTests::test_parse_space_coord_accepts_decimal_coordinates`
- `tests/test_exploration.py::ExplorationParsingTests::test_parse_space_coord_accepts_directional_coordinates`
- `tests/test_exploration.py::ExplorationParsingTests::test_parse_space_coord_accepts_semicolon_coordinates`
- `tests/test_exploration.py::ExplorationParsingTests::test_parse_space_coord_accepts_decimal_commas`
- `tests/test_exploration.py::ExplorationParsingTests::test_primary_abstract_prefers_abstract_over_summary`
- `tests/test_exploration.py::ExplorationParsingTests::test_english_story_title_prefers_english_title`
- `tests/test_exploration.py::ExplorationParsingTests::test_entry_id_from_map_click_prefers_hidden_popup_id`
- `tests/test_exploration.py::ExplorationParsingTests::test_entry_id_from_map_click_can_fall_back_to_coordinates`
- `tests/test_exploration.py::ExplorationNetworkTests::test_build_exploration_network_aggregates_related_entries`

### 6.4 Tests that should not be ported verbatim

- `tests/test_pipeline.py::UIStateTests::test_mark_and_apply_widget_reset` is Streamlit widget plumbing.
- Its underlying intent may disappear entirely in the FastAPI + React rewrite, so it should not be ported 1:1.

## 7. Streamlit-Specific Design That Must Not Be Copied

- The monolithic `app.py` structure mixes UI rendering, file I/O, validation, artifact rebuilding, semantic search, and mutation logic in one place.
- `st.session_state` is used as the main state container for selected entries, widget reset flags, transient trope suggestions, form versioning, and the active output directory.
- `st.rerun()` is part of the control flow after mutations and widget resets.
- `@st.cache_resource` is used as a process-local cache for models and FAISS assets.
- The management and exploration pages directly perform storage mutations instead of calling a separate API/service layer.
- Folium is embedded through `streamlit-folium`, and map selection depends on popup HTML conventions.
- The sidebar/page/tab layout, progress boxes, status widgets, and expanders are Streamlit UI patterns, not product architecture.
- The ability to type an arbitrary local processed-data directory into the sidebar is a local-app convenience, not a product requirement.
- The `Clean session` flow deletes local generated artifacts and backups from disk.
- The legacy UI allows whole-story deletion, which conflicts with the rewrite requirement that stories are never deleted through the app.
- Legacy UI and helper text talk about `pattern` and `motif`; the rewrite should use `trope` consistently while keeping CSV compatibility fields exact.
