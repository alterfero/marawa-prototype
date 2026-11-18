import type { OntologyLink, OntologyNode, TropeOntology } from "../api/types";

export const specificityLevels = [1, 2, 3, 4];

export function linkKey(link: Pick<OntologyLink, "child_id" | "parent_id">) {
  return `${link.child_id}:${link.parent_id}`;
}

export function markerRadius(trope: OntologyNode) {
  return 32 - 5 * trope.specificity_level;
}

export function droppedLink(source: OntologyNode, target: OntologyNode) {
  const sourceIsChild = source.specificity_level >= target.specificity_level;
  return {
    child_id: sourceIsChild ? source.id : target.id,
    parent_id: sourceIsChild ? target.id : source.id,
    status: "approved" as const,
  };
}

export const relations = {
  broader: { active: "child_id", other: "parent_id", title: "Less specific tropes", position: "above" },
  narrower: { active: "parent_id", other: "child_id", title: "More specific tropes", position: "below" },
} as const;
export type Direction = keyof typeof relations;

export const ratingLabels: Record<string, string> = {
  researcher: "Confirmed by researcher",
  curator: "Confirmed by curator",
  provisional: "Provisional",
  disputed: "Provisional (disputed)",
};

export function specificityLabel(trope: OntologyNode) {
  return `Level ${trope.specificity_level} · ${ratingLabels[trope.specificity_source] ?? trope.specificity_source}`;
}

export function relationChoices(graph: TropeOntology, node: OntologyNode, direction: Direction) {
  const fields = relations[direction];
  const levels = specificityLevels.filter(level =>
    direction === "broader" ? level <= node.specificity_level : level >= node.specificity_level);
  const linked: OntologyLink[] = [];
  const reverseEdges = new Map<string, string[]>();
  for (const link of graph.links) {
    const active = link[fields.active];
    const other = link[fields.other];
    if (active === node.id) {
      linked.push(link);
    }
    if (link.status !== "pending") {
      const neighbors = reverseEdges.get(other);
      if (neighbors) neighbors.push(active);
      else reverseEdges.set(other, [active]);
    }
  }
  // Following active edges the other way identifies links that would close a cycle.
  const excluded = new Set([node.id]);
  const todo = [node.id];
  while (todo.length) {
    for (const next of reverseEdges.get(todo.pop()!) ?? []) {
      if (!excluded.has(next)) {
        excluded.add(next);
        todo.push(next);
      }
    }
  }
  for (const link of linked) excluded.add(link[fields.other]);
  const candidates = graph.tropes.filter(t => levels.includes(t.specificity_level) && !excluded.has(t.id));
  return { linked, levels, candidates };
}

function words(text: string) {
  return text.normalize("NFC").toLowerCase().replace(/[^\p{L}\p{M}\p{N}]+/gu, " ").trim();
}

export function exactWordMatches(candidates: OntologyNode[], query: string) {
  const phrase = words(query);
  if (!phrase) return [];
  const needle = ` ${phrase} `;
  return candidates.filter(t => ` ${words(t.text)} `.includes(needle));
}
