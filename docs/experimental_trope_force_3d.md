# Experimental Trope Force 3D

## What this visualization means

This experimental view treats each story as a geographically anchored vertical chain of trope occurrences.

- A story anchor node marks the story's parsed location.
- Each trope occurrence node belongs to one story-trope assignment.
- The vertical stack shows assignment order within that story.
- Semantic links connect trope occurrences from different stories when their canonical tropes are similar enough.

The layout is meant to make two forces legible at once:

- geography, which keeps each story tied to its location;
- semantics, which lets related trope occurrences drift slightly toward one another.

## What this visualization does not mean

- It is not a map of exact travel, diffusion, or chronology.
- It does not prove historical influence or statistical causality.
- A short semantic distance is only a similarity cue from the current embedding model.
- A vertical sequence is not necessarily narrative order.

## Geography versus semantic displacement

Geographic anchoring and semantic displacement are intentionally separate.

- Geography is fixed by story anchor nodes and their projected lon/lat positions.
- Trope occurrences start directly above their story anchor.
- The simulation then applies weak semantic attraction across stories.
- If a trope occurrence drifts sideways, that drift should be read as exploratory semantic tension, not relocation of the story itself.

## Node and link schema

### Nodes

- `story_anchor`
  - fixed at `fx`, `fy`, `fz`
  - includes story metadata and parsed coordinates
- `trope_occurrence`
  - includes `trope_id`, `trope_text`, `sequence_index`, `status`, `origin`
  - includes `anchor_x`, `anchor_y`, and `target_z` for constrained simulation

### Links

- `anchor`
  - connects each trope occurrence back to its story anchor
  - represents geographic tension
- `sequence`
  - connects consecutive trope occurrences in the same story
  - preserves readable vertical chains
- `semantic`
  - connects trope occurrences across different stories
  - includes `similarity` and a weak spring `strength`

## Known limitations

- Assignment order is not narrative order unless a future explicit sequence field exists.
- Full-corpus rendering is intentionally avoided; the graph is capped by `max_stories`.
- The layout is exploratory and should not be treated as statistical proof.
- Stories with missing or malformed coordinates are excluded rather than fabricated.
- The current projection is a simple deterministic equirectangular prototype, isolated so it can later be replaced.
