# TMI reference catalogue

`tmi.csv` is the sole source for the TMI atlas: descriptions, bibliography text,
chapter titles, divisions and sections all come from this file. The earlier
team-provided `tmi.txt` is retained as an unused legacy file; the atlas never reads
it. TMI remains a read-only reference collection, separate from Marawa datasets,
canonical tropes, assignments and story exports.

## Attribution and snapshot

- Katja Mellmann, *Thompson's Motif-Index as CSV File*. Open Science Framework, 2020.
- Publication: <https://doi.org/10.17605/OSF.IO/XEB67>
- Repository: <https://github.com/KatjaMellmann/TMI_as_CSV>
- Download: <https://raw.githubusercontent.com/KatjaMellmann/TMI_as_CSV/main/tmi.csv>
- License: [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)
- Retrieved: 2026-11-04
- Original CSV SHA-256: `4fea6e7bff2830b51403379c5b19c16cab6240c16ff6d8df4324a6403dd5c0ec`

The CSV refers to Thompson's revised and enlarged 1955–1958 edition and also
contains explicitly labelled first-edition entries. The local CSV is an
unmodified copy. It ships with the backend; there are no runtime downloads or
LLM calls. The response includes its SHA-256, attribution and license. Replacing
the CSV invalidates the in-process cache when its modification time or size changes.

## Interpretation

All 46,299 records are included: 46,239 have a revised-edition code and 60 have
only a first-edition code. The latter receive separate internal IDs and remain in
their CSV section rather than being merged into revised-edition entries. One
additional record with a populated code is explicitly labelled first-edition in
its text, so 61 entries carry that label in the interface.

The parser follows the chapter, division and section columns for undotted codes,
and immediate decimal prefixes for dotted codes. A section that is also a trope
is one node, using the full entry description and bibliography. Range divisions
remain separate from similarly numbered entries (D300-D399 is not D300). Absent
decimal prefixes become dashed placeholders with no invented description.
Bracketed decimal additions retain their published code; for example,
M202.1[.1] attaches to M202.1. Historical entries attach to their stated section.
This editorial navigation depth is not a measured semantic abstraction level.

Display adaptations: heading dashes are normalized and “motifs” becomes “tropes”
in heading labels. A leading code is separated from the entry description for
readability. The full original entry text is retained in CSV-record details,
including source corrections and differing code spellings. CSV row numbers count
records with the header as row 1, not physical lines within quoted multiline cells.

The Sources section preserves each entry's `bibliographies` field exactly as
text, including abbreviations, line breaks and editorial symbols. References
are not split on punctuation, expanded, interpreted as Markdown, or inherited
from other entries. Empty fields are explicitly identified. Parent references
remain available by navigating the ancestor path.

`/api/tmi/graph` requires an admin session, works without an active dataset or
embeddings, and does not expose the CSV as a public frontend asset.
