import type { TmiCatalogue, TmiNode } from "./types.js";

export const ROOT_RADIUS = 1500;
export const MIN_ZOOM = 0.018;
export const MAX_ZOOM = 2048;
export const CHAPTER_COLORS = [
  "#e6bb7a", "#88cda5", "#ed9b93", "#92b9f3", "#b6a0dc", "#81c9cc",
  "#d9a876", "#b6cb83", "#e4c78c", "#e59fba", "#a3c9ea", "#b2b1e9",
  "#7bc6af", "#d5be8d", "#d6a2ca", "#92c4dd", "#e99d80", "#daa9b3",
  "#c8ce9b", "#adbcf0", "#87c9ba", "#eac58d", "#b2bdc5",
];

export interface TmiModel {
  byId: Map<string, TmiNode>;
  children: Map<string, string[]>;
  leaves: Map<string, number>;
  tropeCounts: Map<string, number>;
  colors: Map<string, string>;
  roots: string[];
}
export interface PositionedNode {
  node: TmiNode;
  x: number;
  y: number;
  angle: number;
  depth: number;
}
export interface Camera { x: number; y: number; scale: number }
export interface Bounds { minX: number; minY: number; maxX: number; maxY: number }
export interface Marker {
  x: number;
  y: number;
  radius: number;
  members: PositionedNode[];
}

export function buildTmiModel(catalogue: TmiCatalogue): TmiModel {
  const byId = new Map(catalogue.nodes.map((node) => [node.id, node]));
  const children = new Map<string, string[]>();
  catalogue.nodes.forEach((node) => {
    if (node.parent_id) {
      const list = children.get(node.parent_id) ?? [];
      list.push(node.id);
      children.set(node.parent_id, list);
    }
  });
  const collator = new Intl.Collator("en", { numeric: true });
  children.forEach((list) => list.sort((a, b) => collator.compare(byId.get(a)!.code, byId.get(b)!.code)));
  const leaves = new Map<string, number>();
  const tropeCounts = new Map<string, number>();
  function count(id: string): void {
    let leafCount = 0;
    let tropeCount = byId.get(id)?.kind === "trope" ? 1 : 0;
    for (const child of children.get(id) ?? []) {
      count(child);
      leafCount += leaves.get(child)!;
      tropeCount += tropeCounts.get(child)!;
    }
    leaves.set(id, Math.max(1, leafCount));
    tropeCounts.set(id, tropeCount);
  }
  catalogue.roots.forEach(count);
  return { byId, children, leaves, tropeCounts, roots: catalogue.roots,
    colors: new Map(catalogue.roots.map((id, i) => [id, CHAPTER_COLORS[i % CHAPTER_COLORS.length]])) };
}

export function ancestorPath(model: TmiModel, id: string): TmiNode[] {
  const path: TmiNode[] = [];
  let node = model.byId.get(id);
  while (node) {
    path.unshift(node);
    node = node.parent_id ? model.byId.get(node.parent_id) : undefined;
  }
  return path;
}

/** All descendants are laid out; clustering never removes catalogue entries. */
export function layoutTmiTree(model: TmiModel, focusId: string | null = null): PositionedNode[] {
  const result: PositionedNode[] = [];
  function visit(id: string, start: number, end: number, depth: number, focused: boolean) {
    const node = model.byId.get(id)!;
    const angle = (start + end) / 2;
    const radius = focused ? depth * 480 : ROOT_RADIUS + depth * 480;
    result.push({ node, x: Math.cos(angle) * radius, y: Math.sin(angle) * radius, angle, depth });
    const children = model.children.get(id) ?? [];
    const weight = children.reduce((sum, child) => sum + model.leaves.get(child)!, 0);
    let cursor = start;
    for (const child of children) {
      const width = ((end - start) * model.leaves.get(child)!) / weight;
      visit(child, cursor, cursor + width, depth + 1, focused);
      cursor += width;
    }
  }
  if (focusId && model.byId.has(focusId)) {
    visit(focusId, -Math.PI * 1.35, Math.PI * 0.35, 0, true);
  } else {
    const sector = (2 * Math.PI) / model.roots.length;
    model.roots.forEach((id, i) => {
      const center = -Math.PI / 2 + i * sector;
      visit(id, center - sector * 0.46, center + sector * 0.46, 0, false);
    });
  }
  return result;
}

export function boundsOf(nodes: { x: number; y: number }[]): Bounds {
  const bounds = { minX: Infinity, minY: Infinity, maxX: -Infinity, maxY: -Infinity };
  for (const node of nodes) {
    bounds.minX = Math.min(bounds.minX, node.x);
    bounds.minY = Math.min(bounds.minY, node.y);
    bounds.maxX = Math.max(bounds.maxX, node.x);
    bounds.maxY = Math.max(bounds.maxY, node.y);
  }
  return nodes.length ? bounds : { minX: 0, minY: 0, maxX: 0, maxY: 0 };
}

export function fitCamera(bounds: Bounds, width: number, height: number, padding = 130): Camera {
  const scale = Math.min(1.2, (Math.max(80, width - padding * 2)) / Math.max(1, bounds.maxX - bounds.minX),
    (Math.max(80, height - padding * 2)) / Math.max(1, bounds.maxY - bounds.minY));
  return { x: width / 2 - (bounds.minX + bounds.maxX) / 2 * scale,
    y: height / 2 - (bounds.minY + bounds.maxY) / 2 * scale,
    scale: Math.max(MIN_ZOOM, scale) };
}

export function zoomAt(camera: Camera, factor: number, x: number, y: number): Camera {
  const scale = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, camera.scale * factor));
  const ratio = scale / camera.scale;
  return { x: x - (x - camera.x) * ratio, y: y - (y - camera.y) * ratio, scale };
}

/** Spatial hashing, followed by overlap consolidation. No quadratic all-pairs scan. */
export function clusterMarkers(input: Marker[]): Marker[] {
  let markers = input;
  let merged = true;
  while (merged) {
    merged = false;
    const grid = new Map<string, Set<Marker>>();
    const output: Marker[] = [];
    const cellSize = markers.reduce((size, marker) => Math.max(size, marker.radius * 2 + 4), 1);
    const keyFor = (x: number, y: number) => `${Math.floor(x / cellSize)},${Math.floor(y / cellSize)}`;
    for (const marker of markers) {
      const cellX = Math.floor(marker.x / cellSize);
      const cellY = Math.floor(marker.y / cellSize);
      let target: Marker | undefined;
      let closest = Infinity;
      for (let dx = -1; dx <= 1; dx++) for (let dy = -1; dy <= 1; dy++) {
        for (const candidate of grid.get(`${cellX + dx},${cellY + dy}`) ?? []) {
          const distance = Math.hypot(candidate.x - marker.x, candidate.y - marker.y);
          if (distance < candidate.radius + marker.radius + 3 && distance < closest) {
            target = candidate;
            closest = distance;
          }
        }
      }
      if (target) {
        grid.get(keyFor(target.x, target.y))!.delete(target);
        const count = target.members.length + marker.members.length;
        target.x = (target.x * target.members.length + marker.x * marker.members.length) / count;
        target.y = (target.y * target.members.length + marker.y * marker.members.length) / count;
        target.radius = Math.max(target.radius, marker.radius);
        for (const member of marker.members) target.members.push(member);
        merged = true;
      } else {
        target = { ...marker, members: [...marker.members] };
        output.push(target);
      }
      const key = keyFor(target.x, target.y);
      if (!grid.has(key)) grid.set(key, new Set());
      grid.get(key)!.add(target);
    }
    markers = output;
  }
  return markers;
}

export function visibleMarkers(nodes: PositionedNode[], camera: Camera, width: number, height: number, radius: number): Marker[] {
  const markers: Marker[] = [];
  for (const point of nodes) {
    const x = point.x * camera.scale + camera.x;
    const y = point.y * camera.scale + camera.y;
    const r = point.node.kind === "chapter" ? radius + 5 : radius;
    if (x < -r * 2 || y < -r * 2 || x > width + r * 2 || y > height + r * 2) continue;
    markers.push({ x, y, radius: r, members: [point] });
  }
  return clusterMarkers(markers);
}
