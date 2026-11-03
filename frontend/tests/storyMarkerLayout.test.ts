import assert from "node:assert/strict";
import { test } from "node:test";

import { buildStoryMarkerClusters, type ProjectedStoryMarker } from "../src/map/storyMarkerLayout.js";

function stories(count: number, radius = 9) {
  return Array.from({ length: count }, (_, index) => ({
    marker: { story_id: `story-${index}`, coordinates: [-17.5, -149.5] as [number, number] },
    point: { x: 400, y: 300 },
    radius,
  }));
}

const bundleRadius = (count: number) => Math.min(42, 9 + Math.ceil(6.3 * Math.sqrt(count)));
const distance = (a: { x: number; y: number }, b: { x: number; y: number }) => Math.hypot(a.x - b.x, a.y - b.y);

test("coincident stories remain bundled at overview zoom", () => {
  const clusters = buildStoryMarkerClusters(stories(8), 3, bundleRadius);
  assert.equal(clusters.length, 1);
  assert.equal(clusters[0].markers.length, 8);
  assert.ok(distance(clusters[0].point, { x: 400, y: 300 }) < 0.001);
});

test("zooming in makes every story individually selectable, including large groups and marker sizes", () => {
  for (const count of [2, 3, 6, 24, 100]) {
    for (const radius of [0.5, 9, 18]) {
      const clusters = buildStoryMarkerClusters(stories(count, radius), 12, bundleRadius);
      assert.equal(clusters.length, count);
      assert.ok(clusters.every((cluster) => cluster.markers.length === 1));
      const polygonRadius = distance(clusters[0].point, { x: 400, y: 300 });
      for (let i = 0; i < count; i += 1) {
        assert.ok(Math.abs(distance(clusters[i].point, { x: 400, y: 300 }) - polygonRadius) < 0.001);
        for (let j = i + 1; j < count; j += 1) {
          assert.ok(distance(clusters[i].point, clusters[j].point) > radius * 2 + 6);
        }
      }
    }
  }
});

test("layouts preserve recorded coordinates and the source projection", () => {
  const input = stories(4);
  const before = structuredClone(input);
  const clusters = buildStoryMarkerClusters(input, 12, bundleRadius);
  assert.deepEqual(input, before);
  for (const cluster of clusters) {
    assert.deepEqual(cluster.markers[0].origin, { x: 400, y: 300 });
    assert.deepEqual(cluster.markers[0].marker.coordinates, [-17.5, -149.5]);
  }
});

test("story and filter-set identity keep polygon positions stable after input reordering", () => {
  const input = stories(4).map((entry, index) => ({
    ...entry,
    marker: { ...entry.marker, story_id: "same-story", filter_set_id: `set-${index}` },
  }));
  const positions = (entries: typeof input) => Object.fromEntries(
    buildStoryMarkerClusters(entries, 12, bundleRadius).flatMap((cluster) =>
      cluster.markers.map((entry) => [entry.marker.filter_set_id, entry.point]),
    ),
  );
  assert.deepEqual(positions(input), positions([...input].reverse()));
});

test("single locations and nearby but distinct locations retain their positions", () => {
  const input = stories(2);
  input[1].marker.coordinates[0] += 0.001;
  input[1].point.x += 100;
  const clusters = buildStoryMarkerClusters(input, 12, bundleRadius);
  assert.equal(clusters.length, 2);
  clusters.forEach((cluster, index) => {
    assert.deepEqual(cluster.point, input[index].point);
    assert.equal(cluster.markers[0].origin, undefined);
  });
});

test("opposite longitude representations of the antimeridian share one polygon", () => {
  const input = stories(2);
  input[0].marker.coordinates[1] = 180;
  input[1].marker.coordinates[1] = -180;
  const clusters = buildStoryMarkerClusters(input, 12, bundleRadius);
  assert.equal(clusters.length, 2);
  assert.ok(distance(clusters[0].point, clusters[1].point) > 24);
});

test("panning translates the polygon and further zooming keeps it compact", () => {
  const input = stories(6);
  const initial = buildStoryMarkerClusters(input, 12, bundleRadius);
  assert.deepEqual(buildStoryMarkerClusters(input, 18, bundleRadius), initial);
  const translated = buildStoryMarkerClusters(input.map((entry) => ({
    ...entry,
    point: { x: entry.point.x + 800, y: entry.point.y - 150 },
  })), 12, bundleRadius);
  translated.forEach((cluster, index) => {
    assert.ok(Math.abs(cluster.point.x - initial[index].point.x - 800) < 0.001);
    assert.ok(Math.abs(cluster.point.y - initial[index].point.y + 150) < 0.001);
  });
});

test("unrelated overlapping locations still cluster; point mode may bypass clustering", () => {
  const input: ProjectedStoryMarker<{ story_id: string; coordinates: [number, number] }>[] = stories(2);
  input[1].marker.coordinates[0] += 0.001;
  input[1].point.x += 3;
  assert.equal(buildStoryMarkerClusters(input, 12, bundleRadius).length, 1);
  assert.equal(buildStoryMarkerClusters(input, 12, bundleRadius, false).length, 2);
});
