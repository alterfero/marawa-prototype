import assert from "node:assert/strict";
import { test } from "node:test";
import { ancestorPath, buildTmiModel, clusterMarkers, layoutTmiTree, ROOT_RADIUS, zoomAt, type Marker } from "../src/tmi/layout.js";
import type { TmiCatalogue, TmiNode } from "../src/tmi/types.js";

const node = (id: string, parent_id: string | null): TmiNode => ({ id, code: id, text: id,
  kind: parent_id ? "trope" : "chapter", parent_id, chapter: id[0], source_rows: [], source_texts: [], alternate_texts: [], bibliographies: [], edition: "revised", first_edition_code: null, link_basis: "code" });
const catalogue = { roots: ["A", "D"], nodes: [node("A", null), node("A1", "A"), node("A1.1", "A1"),
  node("A1.2", "A1"), node("D", null), node("D373", "D")] } as TmiCatalogue;

test("every entry has a position and the chapter roots form a circle with outward descendants", () => {
  const model = buildTmiModel(catalogue);
  const points = layoutTmiTree(model);
  assert.equal(points.length, catalogue.nodes.length);
  points.forEach((point) => assert.equal(Math.round(Math.hypot(point.x, point.y)), ROOT_RADIUS + 480 * point.depth));
  assert.equal(model.tropeCounts.get("A"), 3);
});

test("focus includes the complete subtree through every leaf and retains the ancestor path", () => {
  const model = buildTmiModel(catalogue);
  assert.deepEqual(layoutTmiTree(model, "A1").map((point) => point.node.id), ["A1", "A1.1", "A1.2"]);
  assert.deepEqual(ancestorPath(model, "A1.2").map((entry) => entry.id), ["A", "A1", "A1.2"]);
  assert.equal(layoutTmiTree(model, "A1.2").length, 1);
});

test("overlap stickers preserve every member, consolidate collisions, and separate with zoom", () => {
  const point = layoutTmiTree(buildTmiModel(catalogue))[0];
  const input: Marker[] = Array.from({ length: 1000 }, (_, i) => ({ x: i % 50, y: Math.floor(i / 50), radius: 25, members: [point] }));
  const clustered = clusterMarkers(input);
  assert.equal(clustered.reduce((sum, marker) => sum + marker.members.length, 0), 1000);
  assert.ok(clustered.length < 10);
  assert.ok(input.every((marker) => marker.members.length === 1));
  clustered.forEach((a, i) => clustered.slice(i + 1).forEach((b) => {
    assert.ok(Math.hypot(a.x - b.x, a.y - b.y) >= a.radius + b.radius + 3);
  }));
  assert.equal(clusterMarkers(input.map((marker) => ({ ...marker, x: marker.x * 80, y: marker.y * 80 }))).length, 1000);
});

test("zoom keeps the world point under the cursor fixed", () => {
  const camera = { x: 10, y: -20, scale: 0.5 };
  const next = zoomAt(camera, 2, 400, 300);
  assert.equal((400 - next.x) / next.scale, (400 - camera.x) / camera.scale);
  assert.equal((300 - next.y) / next.scale, (300 - camera.y) / camera.scale);
});
