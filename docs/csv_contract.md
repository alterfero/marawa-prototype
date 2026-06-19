# CSV Contract

## Purpose

This document defines the CSV interface that Marawa must preserve for import and export compatibility.

Product terminology uses `trope`, but CSV compatibility retains the exact legacy field names `Motifs (Eng)` and `Keywords (Eng)`.

## Canonical Encoding And Delimiter

- CSV delimiter: comma
- Text encoding on import: UTF-8, with or without BOM
- Text encoding on export: UTF-8 with BOM
- Line terminator on export: `\n`

## Canonical Header Contract

The exported CSV must preserve the exact legacy column names and exact column order below.

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
- Extra columns are not part of the supported round-trip contract.
- Export writes only the canonical columns listed above.

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

## Tropes And Keywords

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

## Round-Trip Guarantees

### Guaranteed

- Exact exported legacy header names.
- Exact exported legacy column order.
- Preservation of all canonical field slots.
- Stable canonical serialization of trope and keyword values.

### Not Guaranteed

- Exact preservation of non-canonical extra columns.
- Exact preservation of user formatting inside trope and keyword cells when it differs from canonical serialization.
- Exact preservation of quoting style, whitespace style, or line-ending style from the uploaded file.

## Internal Representation Expectations

- Stories should store the full canonical field map internally.
- Parsed tropes should also exist as a structured ordered list separate from the raw `Motifs (Eng)` string.
- Parsed keywords should also exist as a structured ordered list separate from the raw `Keywords (Eng)` string.
- Export should reconstruct `Motifs (Eng)` and `Keywords (Eng)` from the structured lists so the output stays canonical.

## Validation Error Categories

The backend should distinguish at least these import failures:

- unreadable CSV header
- missing required columns
- malformed row shape
- invalid encoding
- empty dataset

These categories should surface clearly through the API and import job status.
