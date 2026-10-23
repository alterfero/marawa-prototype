# CSV Contract

## Purpose

This document defines the CSV interface that Marawa must preserve for import and export compatibility.

Product terminology uses `trope` and `theme`, but CSV compatibility retains the exact legacy field names `Motifs (Eng)`, `Keywords (Eng)`, and `Thème`.

## Canonical Encoding And Delimiter

- CSV delimiter: comma
- Text encoding on import: UTF-8, with or without BOM
- Text encoding on export: UTF-8 with BOM
- Line terminator on export: `\n`

## Canonical Header Contract

The legacy CSV export must preserve the exact legacy column names and exact column order below.

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

## Import Rules

### Required Columns

- All canonical columns above are required on import.
- Header matching is exact after trimming surrounding whitespace and removing any BOM character.
- `motifs inhabituels à une version` is accepted as an import alias for the canonical legacy field `proposition de nouveaux motifs`.

### Extra Columns

- Extra non-canonical columns may be accepted on import for legacy compatibility.
- Known template-only columns such as `Abstracts : AI or Human ?`, `Motifs validés`, and `motifs Pacifique  ?` are accepted on import and ignored on export.
- The full Marawa export uses the appended `Marawa story metadata` and `Marawa term catalog` columns as a supported round-trip extension. These two columns must appear together when present.
- Other extra columns are not part of the supported round-trip contract.
- The legacy export writes only the canonical columns listed above.

### Blank Rows

- Fully blank data rows are ignored.

### Malformed Rows

- If a data row has more values than the header defines, the import must fail as malformed CSV.
- If the file has a header row but no non-empty story rows, the import must fail.

### Header Order

- Import validates the presence of canonical columns, not their order.
- Export always restores the canonical order.

## Export Rules

- Export always includes every canonical column, even when a value is empty.
- Export always uses the canonical header order.
- Export always serializes from the active dataset in PostgreSQL, not from the original uploaded file bytes.
- Internal metadata such as story IDs, versions, job IDs, timestamps, and dataset IDs is never exported.

### Full Marawa Export

`GET /api/dataset/export.csv?format=full` is the lossless CSV export. It appends two JSON columns after the legacy columns, so the legacy prefix remains unchanged:

- `Marawa story metadata` preserves story completeness, original source-row information, and each trope assignment's origin and validation status.
- `Marawa term catalog` appears on the first story row and preserves every trope, theme, and keyword, including unused terms. It records each term's text and `canonical` or `unconfirmed` confirmation status; tropes and keywords also retain their administrative review status.

The standard `GET /api/dataset/export.csv` response remains the strict legacy export for external tools that require the exact legacy header. A full export can be uploaded again to restore this semantic data.

## Field Value Normalization

### Shared Cleanup

On import, text cleanup must:
- convert `None` to an empty string;
- remove BOM characters;
- trim leading and trailing whitespace.

The normalized comparison form must:
- collapse internal whitespace runs to a single space;
- lowercase the value.

This normalized form is used for de-duplication and matching, not for general display.

## Tropes, Keywords, And Themes

### Terminology

- In product language, the values in `Motifs (Eng)` are `tropes`.
- `motif` and `pattern` remain legacy aliases only.

### `Keywords (Eng)` Parsing

- Split on semicolons and newlines.
- Trim each piece.
- Discard empty pieces.
- De-duplicate using normalized comparison while preserving first-seen order.

### `Keywords (Eng)` Export Serialization

- Join canonical keyword values with ` ; `.

Example:

```text
wolf ; moon ; river
```

### `Motifs (Eng)` Parsing

- Normalize `\r\n` to `\n`.
- If the cell contains `§§`, split on `§§`.
- Otherwise split on semicolons and newlines.
- Strip surrounding spaces, newlines, and semicolons from each piece.
- Discard empty pieces.
- De-duplicate using normalized comparison while preserving first-seen order.

### `Motifs (Eng)` Export Serialization

- Serialize one trope per line.
- Prefix each serialized trope with `§§ `.

Example:

```text
§§ trickster steals fire
§§ younger sibling outwits elder
```

### `Thème` Parsing And Export Serialization

- The CSV header remains exactly `Thème`; the product UI displays it as `Theme`.
- Themes use the same parsing, de-duplication, and `§§ `-prefixed multiline serialization rules as `Motifs (Eng)`.
- Themes are stored as structured story assignments so theme management can rename or remove them consistently across stories.

## Round-Trip Guarantees

### Guaranteed

- Exact exported legacy header names.
- Exact exported legacy column order.
- Preservation of all canonical field slots.
- Stable canonical serialization of trope, keyword, and theme values.
- A full Marawa export round-trips story completeness, trope assignment provenance and validation state, all term confirmation statuses, term review state where applicable, and unused terms.

### Not Guaranteed

- Exact preservation of non-canonical extra columns.
- Exact preservation of user formatting inside trope, keyword, and theme cells when it differs from canonical serialization.
- Exact preservation of quoting style, whitespace style, or line-ending style from the uploaded file.

## Internal Representation Expectations

- Stories should store the full canonical field map internally.
- Parsed tropes should also exist as a structured ordered list separate from the raw `Motifs (Eng)` string.
- Parsed keywords should also exist as a structured ordered list separate from the raw `Keywords (Eng)` string.
- Parsed themes should also exist as a structured ordered list separate from the raw `Thème` string.
- Export should reconstruct `Motifs (Eng)`, `Keywords (Eng)`, and `Thème` from the structured lists so the output stays canonical.

## Validation Error Categories

The backend should distinguish at least these import failures:

- unreadable CSV header
- missing required columns
- malformed row shape
- invalid encoding
- empty dataset

These categories should surface clearly through the API and import job status.

## Injecting into an existing dataset

The Dataset page offers **Inject CSV into this dataset** separately from replacement upload.

1. Select a legacy or full Marawa CSV and run **Check CSV**. This saves a durable review without changing stories or terms.
2. Review messages, each with a severity, code, data row, physical line range, column where applicable, offending value excerpt, and correction guidance. Download the text issue report if useful. Correct the CSV externally, select the corrected file, and recheck it. All errors block application; warnings require explicit acknowledgement.
3. For each duplicate entry, choose **Keep both**, **Keep original**, or **Keep injected**. The comparison shows all populated fields and full-export metadata. With multiple candidates, select which original to retain or update. Other matching stories stay intact.
4. Apply the reviewed injection. The active dataset is updated atomically, with an audit record and a durable full-rebuild job for search and computed artifacts. No story is deleted. Replacing an original preserves its ID and source-row identity, increments its version, and replaces its fields and ordered term assignments.

Every added or replaced story starts as **Incomplete**, regardless of the completeness in a full export, and enters the admin **Review queue** using the contributor review workflow. Populated fields and new trope/keyword assignments receive pending review items; replacements also track removed assignments and previous field values so rejection can restore them. Even an identical replacement requires review. If multiple CSV rows resolve to the same story, review items describe its final injected content. Keep original leaves that story's completeness and review state unchanged. Review records commit with the injection and are not duplicated by retries. Admin approval of individual items does not automatically mark the story Complete.

An entry is a duplicate only when both a non-empty title and a non-empty abstract match the same database story or earlier CSV entry after normalizing capitalization and whitespace. Any of the three title language columns and either abstract language column can supply the matching values. Abstracts have no minimum length; empty abstracts never establish a duplicate. Other fields, source-row numbers, and term statuses do not determine duplicate identity. Fuzzy spelling and semantic similarity are not used.

When there is only a title collision, injection automatically appends the first available positive integer to each colliding title, for example `The legend of Titua 1`, with no warning or duplicate choice. Existing titles stay as they are. Generated titles avoid titles in the database, earlier generated titles, and explicit titles anywhere in the incoming CSV. Matching titles repeated in multiple language columns of one entry receive the same numbered form. The effective title appears in the preview and is used in the database and export. Repeated entries within the file are checked using both their original and effective titles, so numbering does not hide a true title-and-abstract duplicate; subsequent copies use the same effective title for review. Choosing Keep both for a true duplicate explicitly permits retaining both copies with that title.

Decisions apply in CSV order. An earlier row resolves to its newly added story, updated original, or retained original; later rows targeting it resolve against that same story. A later **Keep injected** can therefore replace an earlier choice's result. The UI explains this before application.

Injection is deliberately stricter about cell counts than replacement import: every nonblank row must have exactly as many cells as the header, including trailing empty cells. Invalid UTF-8, CSV syntax, missing/duplicate headers, invalid metadata, and missing catalog terms are errors. A CSV syntax error stops parsing at its physical line; subsequent entries require another check after correction. Blank rows are ignored. Unsupported columns, unrecognized coordinates/URLs, and missing titles produce warnings. Same-width cell shifts cannot always be detected; field comparisons and warnings support manual review.

For full exports, new terms retain confirmation status and unused catalog terms are included. Newly introduced tropes and keywords require administrative review, like contributor-created terms, regardless of their exported review status. Shared tropes, themes, and keywords silently retain the database's current confirmation and review statuses. Differences in incoming statuses do not produce warnings or require acknowledgement, including in previously saved reviews. New story assignments preserve full-export provenance and validation state; legacy assignments use the existing CSV import defaults. Incoming source-row numbers are ignored, including missing or malformed values. Newly added stories receive consecutive unused numbers after the dataset’s highest source-row number, allocated only when an entry is actually added. This produces no warnings and does not affect duplicate detection. Story source-row provenance remains unchanged when updating an original.

Recording dates during injection accept only a plain four-digit year or a properly formatted `[year1, year2]` interval within the supported 1800–2050 bounds, with year1 ≤ year2. A plain year becomes `[year, year]`. Other formats (including free-form dates, ISO calendar dates, unbracketed ranges, and reversed intervals) are silently left blank, with no date errors or warnings.

The server stores reviews and applied results in `csv_injections`. Pending reviews created under earlier validation rules must be checked again; completed results remain available. The current browser tab remembers the review ID and duplicate choices across refreshes. A changed dataset version or selected story version rejects application and requires a fresh review. Repeating an already completed application returns the saved result. An all-skipped legacy injection leaves the dataset version and search artifacts unchanged.

API:

- `POST /api/dataset/injections/preview` — multipart `file`; returns issues and duplicate candidates, including when validation fails.
- `GET /api/dataset/injections/{id}` — retrieve the saved review and any completed result.
- `POST /api/dataset/injections/{id}/apply` — JSON `decisions` (`row_number`, `action`, `target_key`) and `acknowledge_warnings`. Target keys are server-provided `story:<id>` or `row:<number>` values. Actions are `keep_both`, `keep_original`, and `keep_injected`; none is preselected.

These endpoints follow the existing dataset administration access controls and maintenance restrictions. A completed request can be retried during its queued rebuild.
