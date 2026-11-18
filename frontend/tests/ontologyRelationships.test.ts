import assert from "node:assert/strict";
import { test } from "node:test";
import type { OntologyNode, TropeOntology } from "../src/api/types.js";
import { droppedLink, exactWordMatches, markerRadius, relationChoices, specificityLabel } from "../src/ontology/relationships.js";

function trope(id: string, level = 2): OntologyNode {
  return { id, text: id, ontology_id: id, specificity_level: level, specificity_source: "provisional", confirmation_status: "canonical" };
}

test("graph marker sizes decrease at every specificity level", () => {
  const sizes = [1, 2, 3, 4].map(level => markerRadius(trope("t", level)));
  assert.ok(sizes.every((r, i) => r > 0 && (i === 0 || r < sizes[i - 1])));
});

test("dropping across levels uses the broader parent in either direction, with the target as parent for peers", () => {
  const a = trope("a", 1), b = trope("b", 3), c = trope("c", 3);
  assert.deepEqual(droppedLink(a, b), { child_id: "b", parent_id: "a", status: "approved" });
  assert.deepEqual(droppedLink(b, a), droppedLink(a, b));
  assert.deepEqual(droppedLink(b, c), { child_id: "b", parent_id: "c", status: "approved" });
});

test("word and phrase matches respect word boundaries, accents, and punctuation", () => {
  const terms = [trope("A Māui legend"), trope("A MĀUI–legend retold"), trope("Māuian legends")];
  assert.deepEqual(exactWordMatches(terms, "Ma\u0304ui"), terms.slice(0, 2));
  assert.deepEqual(exactWordMatches(terms, "māui  legend"), terms.slice(0, 2));
  assert.deepEqual(exactWordMatches(terms, "."), []);
});

test("both directions allow peers and exclude existing links and cycles", () => {
  const active = trope("active");
  const graph: TropeOntology = {
    dataset_id: "dataset", version: 1, root_children: [], root: { id: "root", text: "Root", specificity_level: 0 },
    tropes: [active, trope("ancestor"), trope("parent"), trope("child"), trope("descendant"), trope("peer"),
      trope("pending"), trope("broad", 1), trope("narrow", 4)],
    links: [
      { child_id: "parent", parent_id: "ancestor", status: "approved" },
      { child_id: "active", parent_id: "parent", status: "inferred" },
      { child_id: "active", parent_id: "pending", status: "pending" },
      { child_id: "child", parent_id: "active", status: "approved" },
      { child_id: "descendant", parent_id: "child", status: "inferred" },
    ],
  };
  const above = relationChoices(graph, active, "broader");
  assert.deepEqual(above.levels, [1, 2]);
  assert.deepEqual(above.linked.map(l => l.parent_id), ["parent", "pending"]);
  assert.deepEqual(above.candidates.map(t => t.id), ["ancestor", "peer", "broad"]);
  const below = relationChoices(graph, active, "narrower");
  assert.deepEqual(below.levels, [2, 3, 4]);
  assert.deepEqual(below.linked.map(l => l.child_id), ["child"]);
  assert.deepEqual(below.candidates.map(t => t.id), ["descendant", "peer", "pending", "narrow"]);
});

test("specificity labels distinguish human ratings from provisional and disputed ratings", () => {
  const node = trope("active", 3);
  assert.equal(specificityLabel(node), "Level 3 · Provisional");
  assert.equal(specificityLabel({ ...node, specificity_source: "curator" }), "Level 3 · Confirmed by curator");
  assert.equal(specificityLabel({ ...node, specificity_source: "researcher" }), "Level 3 · Confirmed by researcher");
  assert.equal(specificityLabel({ ...node, specificity_source: "disputed" }), "Level 3 · Provisional (disputed)");
});
