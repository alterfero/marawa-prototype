import L from "leaflet";
import { Component, type ReactNode, FormEvent, useEffect, useId, useMemo, useRef, useState } from "react";

import { buildExplorationNetwork, getErrorMessage, getStories } from "../api/client";
import {
  ExplorationFilterSetTermPicker,
  type ExplorationSemanticTermKind,
} from "../components/ExplorationFilterSetTermPicker";
import {
  createEmptyStoryFieldFilter,
  filterStoriesBySelectedSemanticTerms,
  normalizeStoryFieldFilters,
  serializeStoryFieldFilters,
  storyFieldFiltersAreComplete,
  storyMatchesFieldFilters,
  StoryFieldFilterBuilder,
  type StoryFieldFilter,
} from "../components/StoryFieldFilters";
import { roleAtLeast, useAuth } from "../auth";
import { TropeCard } from "../components/TropeCard";
import type {
  ExplorationAppliedFilter,
  ExplorationAppliedTermFilter,
  ExplorationAppliedTropeFilter,
  ExplorationCandidate,
  ExplorationConnection,
  ExplorationFilterSetResult,
  ExplorationMatchedTrope,
  ExplorationMarker,
  ExplorationNetworkResponse,
  ExplorationStoryTrope,
  StorySummary,
} from "../api/types";
import { getStoryFieldLabel } from "../constants/csv";
import {
  createAntimeridianAwareViewport,
  toNearestWorldConnectionCoordinates,
  toNearestWorldCoordinates,
  toViewportCoordinates,
  type MapViewport,
} from "../map/antimeridian";
import { routeHref, useHashSearch } from "../router";

const DEFAULT_CENTER: [number, number] = [-15, 180];
const DEFAULT_ZOOM = 3;
const SINGLE_POINT_ZOOM = 6;
const FILTER_SET_PALETTE = ["#1d4ed8", "#d97706", "#15803d", "#b91c1c", "#7c3aed", "#0f766e"];
const ORIGINAL_DENSITY_COLOR = "#d7263d";
const RELATED_DENSITY_COLOR = "#2c7bb6";
const DENSITY_RADIUS = 72;
const DENSITY_RADIUS_MIN = 44;
const DENSITY_RADIUS_MAX = 92;
const DEFAULT_MARKER_RADIUS = 9;
const MIN_MARKER_RADIUS = 4;
const MAX_MARKER_RADIUS = 18;
const RELATED_MARKER_RADIUS_OFFSET = 2;
const BUNDLE_RADIUS_GROWTH = 0.7;
const BUNDLE_RADIUS_MAX = 42;

type CoordinatePair = [number, number];
type MapRenderMode = "markers" | "density";
type ExplorationFilterState = {
  id: number;
  draftFilters: StoryFieldFilter[];
  appliedFilters: StoryFieldFilter[];
  themeQuery: string;
  tropeQuery: string;
  keywordQuery: string;
  draftSelectedThemes: ExplorationAppliedTermFilter[];
  draftSelectedTropes: ExplorationAppliedTropeFilter[];
  draftSelectedKeywords: ExplorationAppliedTermFilter[];
  appliedSelectedThemes: ExplorationAppliedTermFilter[];
  appliedSelectedTropes: ExplorationAppliedTropeFilter[];
  appliedSelectedKeywords: ExplorationAppliedTermFilter[];
};
type ExplorationFilterSetState = {
  id: number;
  label: string;
  color: string;
  filters: ExplorationFilterState[];
};
type FilterSetLegend = {
  id: string;
  label: string;
  color: string;
};
type VisibleExplorationMarker = ExplorationMarker & {
  coordinates: CoordinatePair;
  has_location: true;
};
type DensityPoint = {
  coordinates: CoordinatePair;
  weight: number;
};
type DensityGroup = {
  id: string;
  label: string;
  color: string;
  points: DensityPoint[];
};
type RenderableConnection = ExplorationConnection & {
  source_coordinates: CoordinatePair;
  target_coordinates: CoordinatePair;
};
type MapLegendItem = {
  color?: string;
  label: string;
  symbol: "dot" | "line" | "none";
};
type MapExportTile = {
  height: number;
  href: string;
  width: number;
  x: number;
  y: number;
};
type MapExportDensityImage = {
  height: number;
  href: string;
  width: number;
  x: number;
  y: number;
};
type MapExportView = {
  centerLongitude: number;
  pixelOrigin: L.Point;
  size: L.Point;
  zoom: number;
};
type MapLegendLayoutItem = MapLegendItem & {
  x: number;
  y: number;
};
type ProjectedExplorationMarker = {
  marker: VisibleExplorationMarker;
  point: L.Point;
  radius: number;
};
type MarkerCluster = {
  markers: ProjectedExplorationMarker[];
  point: L.Point;
  radius: number;
};
type DisplayedExplorationMarker =
  | {
      kind: "marker";
      marker: VisibleExplorationMarker;
      point: L.Point;
      radius: number;
    }
  | {
      color: string;
      count: number;
      fontSize: number;
      kind: "bundle";
      markers: VisibleExplorationMarker[];
      point: L.Point;
      radius: number;
      textColor: string;
    };

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.min(maximum, Math.max(minimum, value));
}

function isFiniteCoordinatePair(value: unknown): value is CoordinatePair {
  return (
    Array.isArray(value) &&
    value.length === 2 &&
    typeof value[0] === "number" &&
    Number.isFinite(value[0]) &&
    typeof value[1] === "number" &&
    Number.isFinite(value[1]) &&
    value[0] >= -90 &&
    value[0] <= 90 &&
    value[1] >= -180 &&
    value[1] <= 180
  );
}

function sameCoordinatePair(left: CoordinatePair, right: CoordinatePair): boolean {
  return left[0] === right[0] && left[1] === right[1];
}

function markerHasRenderableLocation(
  marker: ExplorationMarker,
): marker is ExplorationMarker & { coordinates: CoordinatePair; has_location: true } {
  return marker.has_location && isFiniteCoordinatePair(marker.coordinates);
}

function connectionHasRenderableCoordinates(connection: ExplorationConnection): connection is RenderableConnection {
  return (
    isFiniteCoordinatePair(connection.source_coordinates) &&
    isFiniteCoordinatePair(connection.target_coordinates)
  );
}

function colorToRgb(color: string): [number, number, number] {
  const normalized = color.trim();
  const hexMatch = normalized.match(/^#([0-9a-f]{3}|[0-9a-f]{6})$/i);
  if (hexMatch) {
    const hex = hexMatch[1];
    if (hex.length === 3) {
      return [
        Number.parseInt(`${hex[0]}${hex[0]}`, 16),
        Number.parseInt(`${hex[1]}${hex[1]}`, 16),
        Number.parseInt(`${hex[2]}${hex[2]}`, 16),
      ];
    }
    return [
      Number.parseInt(hex.slice(0, 2), 16),
      Number.parseInt(hex.slice(2, 4), 16),
      Number.parseInt(hex.slice(4, 6), 16),
    ];
  }

  const rgbMatch = normalized.match(/^rgba?\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})/i);
  if (rgbMatch) {
    return [
      clamp(Number.parseInt(rgbMatch[1], 10), 0, 255),
      clamp(Number.parseInt(rgbMatch[2], 10), 0, 255),
      clamp(Number.parseInt(rgbMatch[3], 10), 0, 255),
    ];
  }

  return [31, 113, 119];
}

function colorWithAlpha(color: string, alpha: number): string {
  const [red, green, blue] = colorToRgb(color);
  return `rgba(${red}, ${green}, ${blue}, ${alpha})`;
}

function formatHexColor(color: string): string {
  return color.toUpperCase();
}

class ExplorationResultBoundary extends Component<{ children: ReactNode }, { hasError: boolean }> {
  state = { hasError: false };

  static getDerivedStateFromError(): { hasError: boolean } {
    return { hasError: true };
  }

  render() {
    if (this.state.hasError) {
      return (
        <section className="notice notice-error">
          The selected trope could not be rendered. Try another trope or reload the page.
        </section>
      );
    }

    return this.props.children;
  }
}

function formatCoordinateLabel(marker: ExplorationMarker): string {
  if (!marker.has_location || !marker.coordinates) {
    return "No precise location";
  }
  return `${marker.coordinates[0].toFixed(4)}, ${marker.coordinates[1].toFixed(4)}`;
}

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function markerPopupHtml(marker: ExplorationMarker): string {
  const matchedTropes = marker.matched_tropes.length
    ? marker.matched_tropes
        .map(
          (trope) => `<span class="pill">${escapeHtml(trope.text)}</span>`,
        )
        .join("")
    : `<p class="muted">No matched tropes in this network response.</p>`;
  const filterSetLabel = marker.filter_set_label
    ? `<p class="muted">Filter set: ${escapeHtml(marker.filter_set_label)}</p>`
    : "";

  return `
    <div class="map-popup-content popup-stack">
      <div>
        <strong>${escapeHtml(marker.title)}</strong>
      </div>
      ${filterSetLabel}
      <p class="muted">${escapeHtml(formatCoordinateLabel(marker))}</p>
      <p>${escapeHtml(marker.abstract || "No abstract available.")}</p>
      <div class="stack">
        <strong>Matched tropes</strong>
        <div class="tag-list">${matchedTropes}</div>
      </div>
    </div>
  `;
}

function markerBundlePopupHtml(markers: VisibleExplorationMarker[]): string {
  const visibleMarkers = markers.slice(0, 20);
  const remainingLabel = markers.length > visibleMarkers.length ? `<p class="muted">+${markers.length - visibleMarkers.length} more stories</p>` : "";
  const storyItems = visibleMarkers
    .map(
      (marker) =>
        `<li><strong>${escapeHtml(marker.title)}</strong><br /><span class="muted">${escapeHtml(formatCoordinateLabel(marker))}</span></li>`,
    )
    .join("");

  return `
    <div class="map-popup-content popup-stack">
      <div>
        <strong>${markers.length} overlapping stories</strong>
      </div>
      <p class="muted">These markers are grouped at the current map scale. Zoom in to separate them.</p>
      <ul class="map-bundle-popup-list">${storyItems}</ul>
      ${remainingLabel}
    </div>
  `;
}

function storyTropesForMarker(marker: ExplorationMarker): ExplorationStoryTrope[] {
  return Array.isArray(marker.story_tropes) ? marker.story_tropes : [];
}

function normalizeExplorationMarker(marker: ExplorationMarker): ExplorationMarker {
  return {
    ...marker,
    matched_tropes: Array.isArray(marker.matched_tropes) ? marker.matched_tropes : [],
    story_tropes: storyTropesForMarker(marker),
  };
}

function normalizeExplorationFilterSetResult(result: ExplorationFilterSetResult): ExplorationFilterSetResult {
  return {
    ...result,
    filters: Array.isArray(result.filters) ? result.filters : [],
    selected_tropes: Array.isArray(result.selected_tropes) ? result.selected_tropes : [],
    related_tropes: Array.isArray(result.related_tropes) ? result.related_tropes : [],
    original_markers: Array.isArray(result.original_markers)
      ? result.original_markers.map(normalizeExplorationMarker)
      : [],
    related_markers: Array.isArray(result.related_markers)
      ? result.related_markers.map(normalizeExplorationMarker)
      : [],
    connections: Array.isArray(result.connections) ? result.connections : [],
  };
}

function normalizeExplorationNetworkResponse(response: ExplorationNetworkResponse): ExplorationNetworkResponse {
  return {
    ...response,
    selected_trope_candidates: Array.isArray(response.selected_trope_candidates)
      ? response.selected_trope_candidates
      : [],
    related_tropes: Array.isArray(response.related_tropes) ? response.related_tropes : [],
    original_markers: Array.isArray(response.original_markers)
      ? response.original_markers.map(normalizeExplorationMarker)
      : [],
    related_markers: Array.isArray(response.related_markers)
      ? response.related_markers.map(normalizeExplorationMarker)
      : [],
    connections: Array.isArray(response.connections) ? response.connections : [],
    filter_set_results: Array.isArray(response.filter_set_results)
      ? response.filter_set_results.map(normalizeExplorationFilterSetResult)
      : [],
  };
}

function buildExplorationMapDataSignature(
  markers: VisibleExplorationMarker[],
  connections: RenderableConnection[],
): string {
  return JSON.stringify({
    markers: markers.map((marker) => ({
      story_id: marker.story_id,
      coordinates: marker.coordinates,
      color: marker.color,
      kind: marker.kind,
      similarity: marker.similarity,
      title: marker.title,
      source_row_number: marker.source_row_number,
      abstract: marker.abstract,
      matched_tropes: marker.matched_tropes,
      story_tropes: marker.story_tropes,
    })),
    connections: connections.map((connection) => ({
      source_story_id: connection.source_story_id,
      target_story_id: connection.target_story_id,
      source_coordinates: connection.source_coordinates,
      target_coordinates: connection.target_coordinates,
      color: connection.color,
      similarity: connection.similarity,
    })),
  });
}

function buildDensityGroups(
  markers: VisibleExplorationMarker[],
  viewport: MapViewport | null,
  filterSetLegends?: FilterSetLegend[],
): DensityGroup[] {
  if (!markers.length || !viewport) {
    return [];
  }

  if (filterSetLegends && filterSetLegends.length > 0) {
    const groups = new Map<string, DensityGroup>(
      filterSetLegends.map((legend) => [
        legend.id,
        {
          id: legend.id,
          label: legend.label,
          color: legend.color,
          points: [],
        },
      ]),
    );

    markers.forEach((marker) => {
      const groupId = marker.filter_set_id ?? marker.color;
      const group = groups.get(groupId) ?? {
        id: groupId,
        label: marker.filter_set_label ?? "Stories",
        color: marker.color,
        points: [],
      };
      group.points.push({
        coordinates: toViewportCoordinates(marker.coordinates, viewport),
        weight: marker.kind === "original" ? 1 : 0.72,
      });
      groups.set(groupId, group);
    });

    return Array.from(groups.values()).filter((group) => group.points.length > 0);
  }

  const originalStories: DensityGroup = {
    id: "original",
    label: "Original stories",
    color: ORIGINAL_DENSITY_COLOR,
    points: [],
  };
  const relatedStories: DensityGroup = {
    id: "related",
    label: "Related stories",
    color: RELATED_DENSITY_COLOR,
    points: [],
  };

  markers.forEach((marker) => {
    const targetGroup = marker.kind === "original" ? originalStories : relatedStories;
    targetGroup.points.push({
      coordinates: toViewportCoordinates(marker.coordinates, viewport),
      weight: marker.kind === "original" ? 1 : 0.78,
    });
  });

  return [originalStories, relatedStories].filter((group) => group.points.length > 0);
}

function buildDensityDataSignature(groups: DensityGroup[]): string {
  return JSON.stringify(
    groups.map((group) => ({
      id: group.id,
      color: group.color,
      label: group.label,
      points: group.points.map((point) => ({
        coordinates: point.coordinates,
        weight: point.weight,
      })),
    })),
  );
}

function getMapLegendItems(
  renderMode: MapRenderMode,
  filterSetLegends?: FilterSetLegend[],
): MapLegendItem[] {
  if (renderMode === "density") {
    if (filterSetLegends && filterSetLegends.length > 0) {
      return [
        ...filterSetLegends.map((legend) => ({ color: legend.color, label: legend.label, symbol: "dot" as const })),
        {
          label: "Darker zones mean more stories. Switch back to exact locations to inspect individual stories.",
          symbol: "none" as const,
        },
      ];
    }

    return [
      { color: ORIGINAL_DENSITY_COLOR, label: "Original story density", symbol: "dot" },
      { color: RELATED_DENSITY_COLOR, label: "Related story density", symbol: "dot" },
      {
        label: "Darker zones mean more stories. Switch back to exact locations for markers and connections.",
        symbol: "none",
      },
    ];
  }

  if (filterSetLegends && filterSetLegends.length > 0) {
    return filterSetLegends.map((legend) => ({ color: legend.color, label: legend.label, symbol: "dot" }));
  }

  return [
    { color: ORIGINAL_DENSITY_COLOR, label: "Original markers", symbol: "dot" },
    { color: RELATED_DENSITY_COLOR, label: "Related markers", symbol: "dot" },
    { color: "#5b6d72", label: "Closest connection", symbol: "line" },
  ];
}

function getRelatedMarkerRadius(markerRadius: number): number {
  return Math.max(MIN_MARKER_RADIUS - 1, markerRadius - RELATED_MARKER_RADIUS_OFFSET);
}

function getBundleRadius(storyCount: number, markerRadius: number): number {
  return clamp(
    markerRadius + Math.ceil(markerRadius * BUNDLE_RADIUS_GROWTH * Math.sqrt(storyCount)),
    markerRadius + 5,
    BUNDLE_RADIUS_MAX,
  );
}

function getBundleFontSize(storyCount: number, radius: number): number {
  const diameter = radius * 2;
  const digitCount = String(storyCount).length;
  const characterWidth = digitCount * 0.62 + 0.22;
  return Math.max(10, Math.floor(Math.min(diameter * 0.78, (diameter * 0.82) / characterWidth)));
}

function getTextColorForBackground(color: string): string {
  const [red, green, blue] = colorToRgb(color);
  const luminance = (red * 0.2126 + green * 0.7152 + blue * 0.0722) / 255;
  return luminance > 0.58 ? "#111111" : "#ffffff";
}

function chooseBundleColor(markers: ProjectedExplorationMarker[]): string {
  const colors = new Map<string, { color: string; originalCount: number; totalCount: number }>();
  markers.forEach(({ marker }) => {
    const current = colors.get(marker.color) ?? { color: marker.color, originalCount: 0, totalCount: 0 };
    current.totalCount += 1;
    if (marker.kind === "original") {
      current.originalCount += 1;
    }
    colors.set(marker.color, current);
  });

  return Array.from(colors.values()).sort(
    (left, right) =>
      right.totalCount - left.totalCount ||
      right.originalCount - left.originalCount ||
      left.color.localeCompare(right.color),
  )[0]?.color ?? "#1f7177";
}

function makeMarkerCluster(markers: ProjectedExplorationMarker[], markerRadius: number): MarkerCluster {
  const point = markers.reduce((sum, marker) => sum.add(marker.point), new L.Point(0, 0)).divideBy(markers.length);
  return {
    markers,
    point,
    radius: markers.length === 1 ? markers[0].radius : getBundleRadius(markers.length, markerRadius),
  };
}

function clustersOverlap(left: MarkerCluster, right: MarkerCluster): boolean {
  return left.point.distanceTo(right.point) <= left.radius + right.radius;
}

function buildDisplayedExplorationMarkers(
  markers: VisibleExplorationMarker[],
  markerRadius: number,
  project: (marker: VisibleExplorationMarker) => L.Point,
): DisplayedExplorationMarker[] {
  const clusters = markers.map((marker) =>
    makeMarkerCluster(
      [
        {
          marker,
          point: project(marker),
          radius: marker.kind === "original" ? markerRadius : getRelatedMarkerRadius(markerRadius),
        },
      ],
      markerRadius,
    ),
  );

  let merged = true;
  while (merged) {
    merged = false;
    for (let leftIndex = 0; leftIndex < clusters.length; leftIndex += 1) {
      for (let rightIndex = leftIndex + 1; rightIndex < clusters.length; rightIndex += 1) {
        if (!clustersOverlap(clusters[leftIndex], clusters[rightIndex])) {
          continue;
        }
        clusters[leftIndex] = makeMarkerCluster(
          [...clusters[leftIndex].markers, ...clusters[rightIndex].markers],
          markerRadius,
        );
        clusters.splice(rightIndex, 1);
        merged = true;
        break;
      }
      if (merged) {
        break;
      }
    }
  }

  return clusters.map((cluster) => {
    if (cluster.markers.length === 1) {
      const [{ marker, point, radius }] = cluster.markers;
      return { kind: "marker", marker, point, radius };
    }

    const color = chooseBundleColor(cluster.markers);
    return {
      color,
      count: cluster.markers.length,
      fontSize: getBundleFontSize(cluster.markers.length, cluster.radius),
      kind: "bundle",
      markers: cluster.markers.map(({ marker }) => marker),
      point: cluster.point,
      radius: cluster.radius,
      textColor: getTextColorForBackground(color),
    };
  });
}

function getVisibleMapTiles(map: L.Map): MapExportTile[] {
  const mapBounds = map.getContainer().getBoundingClientRect();
  const mapSize = map.getSize();
  if (!mapBounds.width || !mapBounds.height) {
    return [];
  }

  const scaleX = mapSize.x / mapBounds.width;
  const scaleY = mapSize.y / mapBounds.height;

  return Array.from(map.getContainer().querySelectorAll<HTMLImageElement>(".leaflet-tile-pane img"))
    .map((tile) => {
      const bounds = tile.getBoundingClientRect();
      return {
        height: bounds.height * scaleY,
        href: tile.currentSrc || tile.src,
        width: bounds.width * scaleX,
        x: (bounds.left - mapBounds.left) * scaleX,
        y: (bounds.top - mapBounds.top) * scaleY,
      };
    })
    .filter(
      (tile) =>
        Boolean(tile.href) &&
        tile.width > 0 &&
        tile.height > 0 &&
        tile.x + tile.width > 0 &&
        tile.y + tile.height > 0 &&
        tile.x < mapSize.x &&
        tile.y < mapSize.y,
    );
}

function getDensityExportImage(map: L.Map): MapExportDensityImage | null {
  const canvas = map.getContainer().querySelector<HTMLCanvasElement>(".exploration-density-layer");
  if (!canvas) {
    return null;
  }

  const mapBounds = map.getContainer().getBoundingClientRect();
  const canvasBounds = canvas.getBoundingClientRect();
  const mapSize = map.getSize();
  if (!mapBounds.width || !mapBounds.height || !canvasBounds.width || !canvasBounds.height) {
    return null;
  }

  try {
    return {
      height: (canvasBounds.height * mapSize.y) / mapBounds.height,
      href: canvas.toDataURL("image/png"),
      width: (canvasBounds.width * mapSize.x) / mapBounds.width,
      x: ((canvasBounds.left - mapBounds.left) * mapSize.x) / mapBounds.width,
      y: ((canvasBounds.top - mapBounds.top) * mapSize.y) / mapBounds.height,
    };
  } catch {
    return null;
  }
}

function blobToDataUrl(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(reader.error ?? new Error("Unable to read the map tile."));
    reader.onload = () => resolve(String(reader.result));
    reader.readAsDataURL(blob);
  });
}

async function embedMapTiles(tiles: MapExportTile[]): Promise<{ externalTiles: boolean; tiles: MapExportTile[] }> {
  let externalTiles = false;
  const embeddedTiles = await Promise.all(
    tiles.map(async (tile) => {
      if (tile.href.startsWith("data:")) {
        return tile;
      }

      try {
        const response = await fetch(tile.href, { mode: "cors" });
        if (!response.ok) {
          throw new Error(`Tile request failed with ${response.status}.`);
        }
        return { ...tile, href: await blobToDataUrl(await response.blob()) };
      } catch {
        externalTiles = true;
        return tile;
      }
    }),
  );

  return { externalTiles, tiles: embeddedTiles };
}

function layoutMapLegend(items: MapLegendItem[], mapWidth: number, mapHeight: number): {
  height: number;
  items: MapLegendLayoutItem[];
} {
  const outerPadding = 16;
  const itemGap = 20;
  const rowHeight = 24;
  const usableWidth = Math.max(1, mapWidth - outerPadding * 2);
  let x = outerPadding;
  let y = mapHeight + 22;
  const positionedItems: MapLegendLayoutItem[] = [];

  items.forEach((item) => {
    const estimatedWidth = item.label.length * 7.1 + (item.symbol === "none" ? 0 : 26);
    if (x > outerPadding && x + estimatedWidth > outerPadding + usableWidth) {
      x = outerPadding;
      y += rowHeight;
    }
    positionedItems.push({ ...item, x, y });
    x += estimatedWidth + itemGap;
  });

  return {
    height: Math.max(48, y - mapHeight + 18),
    items: positionedItems,
  };
}

function createMapExportView(map: L.Map): MapExportView {
  return {
    centerLongitude: map.getCenter().lng,
    pixelOrigin: map.getPixelOrigin(),
    size: map.getSize(),
    zoom: map.getZoom(),
  };
}

function mapPointToSvg(map: L.Map, coordinates: CoordinatePair, view: MapExportView): L.Point {
  return map.project(toNearestWorldCoordinates(coordinates, view.centerLongitude), view.zoom).subtract(view.pixelOrigin);
}

function createExplorationMapSvg({
  connections,
  densityImage,
  filterSetLegends,
  includeBasemap,
  map,
  markerRadius,
  markers,
  renderMode,
  tiles,
  view,
}: {
  connections: RenderableConnection[];
  densityImage: MapExportDensityImage | null;
  filterSetLegends?: FilterSetLegend[];
  includeBasemap: boolean;
  map: L.Map;
  markerRadius: number;
  markers: VisibleExplorationMarker[];
  renderMode: MapRenderMode;
  tiles: MapExportTile[];
  view: MapExportView;
}): string {
  const mapWidth = Math.max(1, Math.round(view.size.x));
  const mapHeight = Math.max(1, Math.round(view.size.y));
  const legend = layoutMapLegend(getMapLegendItems(renderMode, filterSetLegends), mapWidth, mapHeight);
  const exportHeight = mapHeight + legend.height;
  const displayedMarkers =
    renderMode === "markers"
      ? buildDisplayedExplorationMarkers(markers, markerRadius, (marker) => mapPointToSvg(map, marker.coordinates, view))
      : [];
  const markerElements =
    renderMode === "markers"
      ? displayedMarkers
          .map((displayedMarker) => {
            if (displayedMarker.kind === "bundle") {
              return `<g><title>${displayedMarker.count} overlapping stories</title><circle cx="${displayedMarker.point.x}" cy="${displayedMarker.point.y}" r="${displayedMarker.radius}" fill="${escapeHtml(displayedMarker.color)}" stroke="${escapeHtml(displayedMarker.color)}" stroke-width="2.5" /><text fill="${displayedMarker.textColor}" font-family="Arial, Helvetica, sans-serif" font-size="${displayedMarker.fontSize}" font-weight="700" text-anchor="middle" x="${displayedMarker.point.x}" y="${displayedMarker.point.y}" dy="0.35em">${displayedMarker.count}</text></g>`;
            }

            const { marker, point, radius } = displayedMarker;
            const strokeWidth = marker.kind === "original" ? 2.5 : 1.5;
            const fillOpacity = marker.kind === "original" ? 0.88 : 0.62;
            return `<circle cx="${point.x}" cy="${point.y}" r="${radius}" fill="${escapeHtml(marker.color)}" fill-opacity="${fillOpacity}" stroke="${escapeHtml(marker.color)}" stroke-width="${strokeWidth}" />`;
          })
          .join("")
      : "";
  const connectionElements =
    renderMode === "markers"
      ? connections
          .map((connection) => {
            const points = toNearestWorldConnectionCoordinates(
              connection.source_coordinates,
              connection.target_coordinates,
              view.centerLongitude,
            )
              .map((coordinates) => mapPointToSvg(map, coordinates as CoordinatePair, view))
              .map((point) => `${point.x},${point.y}`)
              .join(" ");
            return `<polyline fill="none" opacity="0.62" points="${points}" stroke="${escapeHtml(connection.color)}" stroke-width="2" />`;
          })
          .join("")
      : "";
  const tileElements = includeBasemap
    ? tiles
        .map(
          (tile) =>
            `<image height="${tile.height}" href="${escapeHtml(tile.href)}" preserveAspectRatio="none" width="${tile.width}" x="${tile.x}" y="${tile.y}" />`,
        )
        .join("")
    : "";
  const densityElement =
    renderMode === "density" && densityImage
      ? `<image height="${densityImage.height}" href="${escapeHtml(densityImage.href)}" preserveAspectRatio="none" width="${densityImage.width}" x="${densityImage.x}" y="${densityImage.y}" />`
      : "";
  const legendElements = legend.items
    .map((item) => {
      const symbol =
        item.symbol === "dot"
          ? `<circle cx="${item.x + 5}" cy="${item.y - 5}" fill="${escapeHtml(item.color ?? "#48656c")}" r="5" />`
          : item.symbol === "line"
            ? `<line stroke="${escapeHtml(item.color ?? "#5b6d72")}" stroke-width="2" x1="${item.x}" x2="${item.x + 18}" y1="${item.y - 5}" y2="${item.y - 5}" />`
            : "";
      const textX = item.x + (item.symbol === "none" ? 0 : 26);
      return `${symbol}<text fill="#48656c" font-family="Arial, Helvetica, sans-serif" font-size="13" x="${textX}" y="${item.y}">${escapeHtml(item.label)}</text>`;
    })
    .join("");

  return `<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" height="${exportHeight}" role="img" viewBox="0 0 ${mapWidth} ${exportHeight}" width="${mapWidth}">
  <title>Marawa exploration map</title>
  <defs><clipPath id="map-clip"><rect height="${mapHeight}" rx="16" width="${mapWidth}" x="0" y="0" /></clipPath></defs>
  <rect fill="${includeBasemap ? "#dce7e8" : "#ffffff"}" height="${mapHeight}" rx="16" width="${mapWidth}" x="0" y="0" />
  <g clip-path="url(#map-clip)">${tileElements}${densityElement}${connectionElements}${markerElements}</g>
  <rect fill="none" height="${mapHeight}" rx="16" stroke="#d4dee0" width="${mapWidth}" x="0.5" y="0.5" />
  ${includeBasemap ? `<text fill="#48656c" font-family="Arial, Helvetica, sans-serif" font-size="10" text-anchor="end" x="${mapWidth - 10}" y="${mapHeight - 10}">© OpenStreetMap contributors</text>` : ""}
  <rect fill="#fffdf9" height="${legend.height}" width="${mapWidth}" x="0" y="${mapHeight}" />
  ${legendElements}
</svg>`;
}

function downloadSvg(svg: string): void {
  const url = URL.createObjectURL(new Blob([svg], { type: "image/svg+xml;charset=utf-8" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = `marawa-exploration-map-${new Date().toISOString().slice(0, 10)}.svg`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
}

class ExplorationDensityLayer extends L.Layer {
  private activeMap: L.Map | null = null;
  private canvasElement: HTMLCanvasElement | null = null;
  private groups: DensityGroup[] = [];
  private isVisible = false;

  onAdd(map: L.Map): this {
    this.activeMap = map;
    this.canvasElement = L.DomUtil.create("canvas", "exploration-density-layer") as HTMLCanvasElement;
    this.canvasElement.style.pointerEvents = "none";
    this.canvasElement.setAttribute("aria-hidden", "true");
    map.getPanes().overlayPane.appendChild(this.canvasElement);
    map.on("moveend zoomend resize viewreset", this.resetCanvas, this);
    this.resetCanvas();
    this.updateVisibility();
    return this;
  }

  onRemove(map: L.Map): this {
    map.off("moveend zoomend resize viewreset", this.resetCanvas, this);
    if (this.canvasElement) {
      L.DomUtil.remove(this.canvasElement);
      this.canvasElement = null;
    }
    this.activeMap = null;
    return this;
  }

  setGroups(groups: DensityGroup[]): this {
    this.groups = groups;
    this.redraw();
    return this;
  }

  setVisible(isVisible: boolean): this {
    this.isVisible = isVisible;
    this.updateVisibility();
    this.redraw();
    return this;
  }

  private updateVisibility(): void {
    if (!this.canvasElement) {
      return;
    }
    this.canvasElement.style.display = this.isVisible ? "block" : "none";
  }

  private resetCanvas = (): void => {
    if (!this.activeMap || !this.canvasElement) {
      return;
    }

    const size = this.activeMap.getSize();
    const pixelRatio = window.devicePixelRatio || 1;
    const topLeft = this.activeMap.containerPointToLayerPoint([0, 0]);

    L.DomUtil.setPosition(this.canvasElement, topLeft);
    this.canvasElement.width = Math.max(1, Math.round(size.x * pixelRatio));
    this.canvasElement.height = Math.max(1, Math.round(size.y * pixelRatio));
    this.canvasElement.style.width = `${size.x}px`;
    this.canvasElement.style.height = `${size.y}px`;
    this.redraw();
  };

  private redraw(): void {
    if (!this.activeMap || !this.canvasElement) {
      return;
    }

    const context = this.canvasElement.getContext("2d");
    if (!context) {
      return;
    }

    const map = this.activeMap;
    if (!map) {
      return;
    }

    const pixelRatio = window.devicePixelRatio || 1;

    context.setTransform(1, 0, 0, 1, 0, 0);
    context.clearRect(0, 0, this.canvasElement.width, this.canvasElement.height);

    if (!this.isVisible || this.groups.length === 0) {
      return;
    }

    context.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0);
    context.globalCompositeOperation = "source-over";

    const radius = clamp(
      DENSITY_RADIUS + (DEFAULT_ZOOM - map.getZoom()) * 4,
      DENSITY_RADIUS_MIN,
      DENSITY_RADIUS_MAX,
    );

    this.groups.forEach((group) => {
      group.points.forEach((point) => {
        const pixelPoint = map.latLngToContainerPoint(
          toNearestWorldCoordinates(point.coordinates, map.getCenter().lng),
        );

        const strength = clamp(0.16 * point.weight, 0.09, 0.24);
        const gradient = context.createRadialGradient(pixelPoint.x, pixelPoint.y, 0, pixelPoint.x, pixelPoint.y, radius);
        gradient.addColorStop(0, colorWithAlpha(group.color, strength));
        gradient.addColorStop(0.34, colorWithAlpha(group.color, strength * 0.82));
        gradient.addColorStop(0.7, colorWithAlpha(group.color, strength * 0.36));
        gradient.addColorStop(1, colorWithAlpha(group.color, 0));

        context.fillStyle = gradient;
        context.beginPath();
        context.arc(pixelPoint.x, pixelPoint.y, radius, 0, Math.PI * 2);
        context.fill();
      });
    });
  }
}

function MissingLocationList({
  markers,
  emptyLabel,
}: {
  markers: ExplorationMarker[];
  emptyLabel: string;
}) {
  if (!markers.length) {
    return <p className="muted">{emptyLabel}</p>;
  }

  return (
    <div className="stack">
      {markers.map((marker) => (
        <article className="card subdued" key={marker.story_id}>
          <h3>{marker.title}</h3>
          <p>{marker.abstract || "No abstract available."}</p>
          <div className="stack">
            <strong>Matched tropes</strong>
            {renderMatchedTropeCards(marker.matched_tropes, "No matched tropes in this network response.")}
          </div>
        </article>
      ))}
    </div>
  );
}

function renderRelatedTropes(candidates: ExplorationCandidate[]) {
  if (!candidates.length) {
    return <p className="muted">No related tropes met the current threshold.</p>;
  }

  return (
    <div className="trope-card-grid">
      {candidates.map((candidate) => (
        <TropeCard compact key={candidate.id} trope={candidate} />
      ))}
    </div>
  );
}

function renderCandidateCards(
  candidates: ExplorationCandidate[],
  busy: boolean,
  onSelect: (candidate: ExplorationCandidate) => void,
) {
  if (!candidates.length) {
    return <p className="muted">No candidate tropes matched this phrase.</p>;
  }

  return (
    <div className="stack">
      {candidates.map((candidate) => (
        <TropeCard
          key={candidate.id}
          trope={candidate}
          actions={
            <button className="button" disabled={busy} onClick={() => onSelect(candidate)} type="button">
              Select trope
            </button>
          }
        />
      ))}
    </div>
  );
}

function renderStoryTropeCards(tropes: ExplorationStoryTrope[], emptyLabel: string) {
  if (!tropes.length) {
    return <p className="muted">{emptyLabel}</p>;
  }

  return (
    <div className="trope-card-grid">
      {tropes.map((trope) => (
        <TropeCard compact key={trope.id} trope={trope} />
      ))}
    </div>
  );
}

function renderMatchedTropeCards(tropes: ExplorationMatchedTrope[], emptyLabel: string) {
  if (!tropes.length) {
    return <p className="muted">{emptyLabel}</p>;
  }

  return (
    <div className="trope-card-grid">
      {tropes.map((trope) => (
        <TropeCard compact key={trope.id} trope={trope} />
      ))}
    </div>
  );
}

function storyFiltersPayload(filters: StoryFieldFilter[]) {
  return filters.map((filter) => ({
    field: filter.field,
    selected_values: filter.selectedValues,
  }));
}

function serializeSelectedTermFilters(terms: ExplorationAppliedTermFilter[]): string {
  return JSON.stringify(
    terms
      .map((term) => term.id)
      .sort((left, right) => left.localeCompare(right)),
  );
}

function toggleSelectedTerms(
  selectedTerms: ExplorationAppliedTermFilter[],
  term: ExplorationAppliedTermFilter,
): ExplorationAppliedTermFilter[] {
  return selectedTerms.some((item) => item.id === term.id)
    ? selectedTerms.filter((item) => item.id !== term.id)
    : [...selectedTerms, term];
}

function createExplorationFilter(nextId: number): ExplorationFilterState {
  return {
    id: nextId,
    draftFilters: [],
    appliedFilters: [],
    themeQuery: "",
    tropeQuery: "",
    keywordQuery: "",
    draftSelectedThemes: [],
    draftSelectedTropes: [],
    draftSelectedKeywords: [],
    appliedSelectedThemes: [],
    appliedSelectedTropes: [],
    appliedSelectedKeywords: [],
  };
}

function createExplorationFilterSet(nextId: number, firstFilterId: number): ExplorationFilterSetState {
  return {
    id: nextId,
    label: `Set ${nextId}`,
    color: FILTER_SET_PALETTE[(nextId - 1) % FILTER_SET_PALETTE.length],
    filters: [createExplorationFilter(firstFilterId)],
  };
}

function serializeExplorationFilterSets(filterSets: ExplorationFilterSetState[]): string {
  return JSON.stringify(
    filterSets.map((filterSet) => ({
      id: filterSet.id,
      label: filterSet.label,
      color: filterSet.color,
      filters: filterSet.filters.map((filter) => ({
        id: filter.id,
        draftFilters: JSON.parse(serializeStoryFieldFilters(filter.draftFilters)),
        appliedFilters: JSON.parse(serializeStoryFieldFilters(filter.appliedFilters)),
        draftSelectedThemes: JSON.parse(serializeSelectedTermFilters(filter.draftSelectedThemes)),
        draftSelectedTropes: JSON.parse(serializeSelectedTermFilters(filter.draftSelectedTropes)),
        draftSelectedKeywords: JSON.parse(serializeSelectedTermFilters(filter.draftSelectedKeywords)),
        appliedSelectedThemes: JSON.parse(serializeSelectedTermFilters(filter.appliedSelectedThemes)),
        appliedSelectedTropes: JSON.parse(serializeSelectedTermFilters(filter.appliedSelectedTropes)),
        appliedSelectedKeywords: JSON.parse(serializeSelectedTermFilters(filter.appliedSelectedKeywords)),
      })),
    })),
  );
}

function filterHasPendingChanges(filter: ExplorationFilterState): boolean {
  return (
    serializeStoryFieldFilters(filter.draftFilters) !== serializeStoryFieldFilters(filter.appliedFilters) ||
    serializeSelectedTermFilters(filter.draftSelectedThemes) !== serializeSelectedTermFilters(filter.appliedSelectedThemes) ||
    serializeSelectedTermFilters(filter.draftSelectedTropes) !== serializeSelectedTermFilters(filter.appliedSelectedTropes) ||
    serializeSelectedTermFilters(filter.draftSelectedKeywords) !== serializeSelectedTermFilters(filter.appliedSelectedKeywords)
  );
}

function filterHasAppliedCriteria(filter: ExplorationFilterState): boolean {
  return (
    filter.appliedFilters.length > 0 ||
    filter.appliedSelectedThemes.length > 0 ||
    filter.appliedSelectedTropes.length > 0 ||
    filter.appliedSelectedKeywords.length > 0
  );
}

function filterStoriesForExplorationFilter(stories: StorySummary[], filter: ExplorationFilterState): StorySummary[] {
  return filterStoriesBySelectedSemanticTerms(stories, {
    themes: filter.draftSelectedThemes,
    tropes: filter.draftSelectedTropes,
    keywords: filter.draftSelectedKeywords,
  }).filter((story) => storyMatchesFieldFilters(story, filter.draftFilters));
}

function filterSetHasPendingChanges(filterSet: ExplorationFilterSetState): boolean {
  return filterSet.filters.some(filterHasPendingChanges);
}

function filterSetHasAppliedCriteria(filterSet: ExplorationFilterSetState): boolean {
  return filterSet.filters.some(filterHasAppliedCriteria);
}

function normalizedFilterSetLabel(filterSet: Pick<ExplorationFilterSetState, "id" | "label">): string {
  return filterSet.label.trim() || `Set ${filterSet.id}`;
}

function buildStoryFilterSetsPayload(filterSets: ExplorationFilterSetState[]) {
  return filterSets
    .filter(filterSetHasAppliedCriteria)
    .map((filterSet) => ({
      id: `filter-set-${filterSet.id}`,
      label: normalizedFilterSetLabel(filterSet),
      color: filterSet.color,
      filter_groups: filterSet.filters.filter(filterHasAppliedCriteria).map((filter) => ({
        filters: storyFiltersPayload(filter.appliedFilters),
        selected_themes: filter.appliedSelectedThemes.map((theme) => ({ id: theme.id, text: theme.text })),
        selected_tropes: filter.appliedSelectedTropes.map((trope) => ({ id: trope.id, text: trope.text })),
        selected_keywords: filter.appliedSelectedKeywords.map((keyword) => ({ id: keyword.id, text: keyword.text })),
      })),
    }));
}

function summarizeMarkerTitles(markers: ExplorationMarker[]): string {
  if (!markers.length) {
    return "No stories";
  }
  const titles = markers.slice(0, 3).map((marker) => marker.title);
  if (markers.length <= 3) {
    return titles.join(" · ");
  }
  return `${titles.join(" · ")} · +${markers.length - 3} more`;
}

function renderMarkerTitleList(markers: ExplorationMarker[]) {
  if (!markers.length) {
    return <p className="muted">No stories.</p>;
  }

  return (
    <ul className="exploration-story-title-list">
      {markers.map((marker) => (
        <li key={marker.story_id}>{marker.title}</li>
      ))}
    </ul>
  );
}

function summarizeAppliedFilter(filter: ExplorationAppliedFilter): string {
  const fieldLabel = getStoryFieldLabel(filter.field);
  const valuesLabel = filter.selected_values.join(" or ");
  return `${fieldLabel}: ${valuesLabel}`;
}

function ExplorationMap({
  markers,
  connections,
  filterSetLegends,
}: {
  markers: VisibleExplorationMarker[];
  connections: RenderableConnection[];
  filterSetLegends?: FilterSetLegend[];
}) {
  const mapViewId = useId();
  const markerSizeId = useId();
  const mapElementRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<L.Map | null>(null);
  const overlayLayerRef = useRef<L.LayerGroup | null>(null);
  const densityLayerRef = useRef<ExplorationDensityLayer | null>(null);
  const [renderMode, setRenderMode] = useState<MapRenderMode>("markers");
  const [markerRadius, setMarkerRadius] = useState(DEFAULT_MARKER_RADIUS);
  const [exportStatus, setExportStatus] = useState<"idle" | "preparing">("idle");
  const [exportNotice, setExportNotice] = useState<string | null>(null);
  const dataSignature = buildExplorationMapDataSignature(markers, connections);
  const viewport = useMemo(
    () =>
      createAntimeridianAwareViewport([
        ...markers.map((marker) => marker.coordinates),
        ...connections.flatMap((connection) => [connection.source_coordinates, connection.target_coordinates]),
      ]),
    [dataSignature],
  );
  const densityGroups = buildDensityGroups(markers, viewport, filterSetLegends);
  const densitySignature = buildDensityDataSignature(densityGroups);

  useEffect(() => {
    if (!mapElementRef.current || mapRef.current) {
      return;
    }

    const map = L.map(mapElementRef.current, {
      center: DEFAULT_CENTER,
      zoom: DEFAULT_ZOOM,
      scrollWheelZoom: true,
      worldCopyJump: true,
    });

    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    }).addTo(map);

    const overlayLayer = L.layerGroup().addTo(map);
    const densityLayer = new ExplorationDensityLayer().addTo(map);
    mapRef.current = map;
    overlayLayerRef.current = overlayLayer;
    densityLayerRef.current = densityLayer;

    return () => {
      overlayLayer.clearLayers();
      overlayLayerRef.current = null;
      densityLayerRef.current = null;
      map.remove();
      mapRef.current = null;
    };
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    const overlayLayer = overlayLayerRef.current;
    const densityLayer = densityLayerRef.current;
    if (!map || !overlayLayer || !densityLayer) {
      return;
    }
    const activeMap = map;
    const activeOverlayLayer = overlayLayer;

    function renderMarkerOverlay() {
      activeOverlayLayer.clearLayers();

      if (renderMode !== "markers") {
        return;
      }

      const mapCenterLongitude = activeMap.getCenter().lng;
      connections.forEach((connection) => {
        L.polyline(
          toNearestWorldConnectionCoordinates(
            connection.source_coordinates,
            connection.target_coordinates,
            mapCenterLongitude,
          ),
          {
            color: connection.color,
            opacity: 0.62,
            weight: 2,
          },
        ).addTo(activeOverlayLayer);
      });

      const displayedMarkers = buildDisplayedExplorationMarkers(markers, markerRadius, (marker) =>
        activeMap.latLngToContainerPoint(toNearestWorldCoordinates(marker.coordinates, mapCenterLongitude)),
      );
      displayedMarkers.forEach((displayedMarker) => {
        if (displayedMarker.kind === "bundle") {
          const diameter = displayedMarker.radius * 2;
          const bundleIcon = L.divIcon({
            className: "map-marker-bundle-icon",
            html: `<span class="map-marker-bundle" style="--bundle-color: ${escapeHtml(displayedMarker.color)}; --bundle-font-size: ${displayedMarker.fontSize}px; --bundle-size: ${diameter}px; --bundle-text-color: ${displayedMarker.textColor};">${displayedMarker.count}</span>`,
            iconAnchor: [displayedMarker.radius, displayedMarker.radius],
            iconSize: [diameter, diameter],
          });
          L.marker(activeMap.containerPointToLatLng(displayedMarker.point), { icon: bundleIcon })
            .bindTooltip(`${displayedMarker.count} overlapping stories`, {
              direction: "top",
              opacity: 0.92,
              sticky: true,
            })
            .bindPopup(markerBundlePopupHtml(displayedMarker.markers), {
              maxWidth: 360,
            })
            .addTo(activeOverlayLayer);
          return;
        }

        const { marker, radius } = displayedMarker;
        L.circleMarker(toNearestWorldCoordinates(marker.coordinates, mapCenterLongitude), {
          color: marker.color,
          fillColor: marker.color,
          fillOpacity: marker.kind === "original" ? 0.88 : 0.62,
          weight: marker.kind === "original" ? 2.5 : 1.5,
          radius,
        })
          .bindTooltip(escapeHtml(marker.title), {
            direction: "top",
            opacity: 0.92,
            sticky: true,
          })
          .bindPopup(markerPopupHtml(marker), {
            maxWidth: 320,
          })
          .addTo(activeOverlayLayer);
      });
    }

    renderMarkerOverlay();
    map.on("moveend zoomend", renderMarkerOverlay);

    densityLayer.setGroups(densityGroups);
    densityLayer.setVisible(renderMode === "density");

    window.requestAnimationFrame(() => {
      map.invalidateSize();
    });

    return () => {
      map.off("moveend zoomend", renderMarkerOverlay);
    };
  }, [dataSignature, densitySignature, markerRadius, renderMode]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) {
      return;
    }

    if (viewport) {
      const [southWest, northEast] = viewport.bounds;
      if (sameCoordinatePair(southWest, northEast)) {
        map.setView(southWest, SINGLE_POINT_ZOOM);
      } else {
        map.fitBounds(viewport.bounds, {
          padding: [32, 32],
        });
      }
    } else {
      map.setView(DEFAULT_CENTER, DEFAULT_ZOOM);
    }

  }, [dataSignature, viewport]);

  async function prepareMapExport(includeBasemap: boolean): Promise<{ externalTiles: boolean; svg: string }> {
    const map = mapRef.current;
    if (!map) {
      throw new Error("The map is still loading. Please try again in a moment.");
    }

    const view = createMapExportView(map);
    const densityImage = renderMode === "density" ? getDensityExportImage(map) : null;
    const tileSnapshot = includeBasemap ? getVisibleMapTiles(map) : [];
    const { externalTiles, tiles } = includeBasemap
      ? await embedMapTiles(tileSnapshot)
      : { externalTiles: false, tiles: [] };

    return {
      externalTiles,
      svg: createExplorationMapSvg({
        connections,
        densityImage,
        filterSetLegends,
        includeBasemap,
        map,
        markerRadius,
        markers,
        renderMode,
        tiles,
        view,
      }),
    };
  }

  async function handleSvgExport(includeBasemap: boolean): Promise<void> {
    try {
      setExportStatus("preparing");
      setExportNotice(null);
      const { externalTiles, svg } = await prepareMapExport(includeBasemap);
      downloadSvg(svg);
      setExportNotice(
        externalTiles
          ? "SVG saved. Some basemap tiles could not be embedded, so the file references their original URLs."
          : renderMode === "density"
            ? "SVG saved. The legend remains editable; density zones are embedded at their displayed resolution."
            : "SVG saved. Markers, connections, and the legend remain editable vectors.",
      );
    } catch (caughtError) {
      setExportNotice(caughtError instanceof Error ? caughtError.message : "The map could not be exported.");
    } finally {
      setExportStatus("idle");
    }
  }

  async function handlePrintExport(): Promise<void> {
    const printWindow = window.open("", "_blank");
    if (!printWindow) {
      setExportNotice("Your browser blocked the print window. Allow pop-ups for this site, then try again.");
      return;
    }

    printWindow.document.write("<!doctype html><title>Preparing map export</title><p>Preparing map for print…</p>");
    printWindow.document.close();

    try {
      setExportStatus("preparing");
      setExportNotice(null);
      const { externalTiles, svg } = await prepareMapExport(true);
      const svgUrl = URL.createObjectURL(new Blob([svg], { type: "image/svg+xml;charset=utf-8" }));
      printWindow.document.open();
      printWindow.document.write(`<!doctype html>
<html><head><title>Marawa exploration map</title><style>@page { size: landscape; margin: 12mm; } body { margin: 0; } img { display: block; height: auto; max-height: 100vh; max-width: 100%; width: 100%; }</style></head>
<body><img alt="Exploration map" onload="window.focus(); window.print();" src="${svgUrl}" /></body></html>`);
      printWindow.document.close();
      printWindow.addEventListener("afterprint", () => URL.revokeObjectURL(svgUrl), { once: true });
      printWindow.addEventListener("beforeunload", () => URL.revokeObjectURL(svgUrl), { once: true });
      setExportNotice(
        externalTiles
          ? "The print view is ready. Some basemap tiles remain linked to their original URLs."
          : "The print view is ready. Choose “Save as PDF” in your browser’s print dialog for a paper-ready file.",
      );
    } catch (caughtError) {
      printWindow.close();
      setExportNotice(caughtError instanceof Error ? caughtError.message : "The print export could not be prepared.");
    } finally {
      setExportStatus("idle");
    }
  }

  if (!markers.length && !connections.length) {
    return (
      <div className="card subdued">
        <p className="muted">
          No stories in this network have a usable precise location, so the map cannot be drawn for this trope.
        </p>
      </div>
    );
  }

  return (
    <div className="map-shell">
      <div className="map-toolbar">
        <label className="field map-marker-size-control" htmlFor={markerSizeId}>
          <span className="map-view-label">Marker size</span>
          <div className="map-marker-size-row">
            <input
              aria-label="Marker size"
              className="range-input"
              id={markerSizeId}
              max={MAX_MARKER_RADIUS}
              min={MIN_MARKER_RADIUS}
              onChange={(event) => setMarkerRadius(Number(event.target.value))}
              step="1"
              type="range"
              value={markerRadius}
            />
            <output className="pill" htmlFor={markerSizeId}>{markerRadius}px radius</output>
          </div>
          {renderMode === "density" ? <span className="map-control-hint">Applied when showing exact locations.</span> : null}
        </label>
        <label className="field map-view-control" htmlFor={mapViewId}>
          <span className="map-view-label">Map view</span>
          <select
            className="input map-view-select"
            id={mapViewId}
            onChange={(event) => setRenderMode(event.target.value as MapRenderMode)}
            value={renderMode}
          >
            <option value="markers">Exact locations</option>
            <option value="density">Density zones</option>
          </select>
        </label>
        <details className="map-export-menu">
          <summary className="button button-ghost">Export map</summary>
          <div className="map-export-options">
            <button
              className="button button-ghost"
              disabled={exportStatus === "preparing"}
              onClick={() => void handleSvgExport(true)}
              type="button"
            >
              {exportStatus === "preparing"
                ? "Preparing export…"
                : renderMode === "density"
                  ? "SVG map + density zones + legend"
                  : "SVG map + markers + legend"}
            </button>
            <button
              className="button button-ghost"
              disabled={exportStatus === "preparing"}
              onClick={() => void handleSvgExport(false)}
              type="button"
            >
              {exportStatus === "preparing"
                ? "Preparing export…"
                : renderMode === "density"
                  ? "SVG density zones + legend"
                  : "SVG vector overlay + legend"}
            </button>
            <button
              className="button button-ghost"
              disabled={exportStatus === "preparing"}
              onClick={() => void handlePrintExport()}
              type="button"
            >
              Print / Save as PDF
            </button>
            <p className="map-export-help">
              {renderMode === "density"
                ? "All exports use the current extent. Density zones are embedded at their displayed resolution; the legend remains vector."
                : "All exports use the current extent and marker size. The map SVG includes the current basemap, dots or clusters, connections, and legend; the vector-overlay option is for fully vector figures."}
            </p>
          </div>
        </details>
      </div>
      <div className="map-canvas" ref={mapElementRef} />
      <div className="legend-row">
        {getMapLegendItems(renderMode, filterSetLegends).map((item) => (
          <span className="legend-item" key={`${item.symbol}-${item.color ?? ""}-${item.label}`}>
            {item.symbol === "dot" ? <span className="legend-dot" style={{ background: item.color }} /> : null}
            {item.symbol === "line" ? <span className="legend-line" style={{ borderColor: item.color }} /> : null}
            {item.label}
          </span>
        ))}
      </div>
      {exportNotice ? <p className="map-export-notice" role="status">{exportNotice}</p> : null}
    </div>
  );
}

export function ExplorationPage() {
  const { user } = useAuth();
  const hashSearch = useHashSearch();
  const nextFilterIdRef = useRef(1);
  const nextSemanticFilterIdRef = useRef(2);
  const nextSetIdRef = useRef(2);
  const [query, setQuery] = useState("");
  const [selectedTropeId, setSelectedTropeId] = useState<string | null>(null);
  const [selectedTropePreview, setSelectedTropePreview] = useState<string | null>(null);
  const [stories, setStories] = useState<StorySummary[]>([]);
  const [storiesLoading, setStoriesLoading] = useState(false);
  const [filterSets, setFilterSets] = useState<ExplorationFilterSetState[]>([createExplorationFilterSet(1, 1)]);
  const [network, setNetwork] = useState<ExplorationNetworkResponse | null>(null);
  const [threshold, setThreshold] = useState(0.62);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [resultMode, setResultMode] = useState<"idle" | "candidate_search" | "map">("idle");
  const selectedTropeParam = new URLSearchParams(hashSearch).get("selected_trope_id");
  const canUseFilterSets = roleAtLeast(user?.role, "admin");
  const canUseSingleTropeExploration = !canUseFilterSets;

  const hasPendingFilterChanges = filterSets.some(filterSetHasPendingChanges);
  const appliedFilterSetCount = filterSets.filter(filterSetHasAppliedCriteria).length;
  const canMapWithCurrentSelection = appliedFilterSetCount > 0;
  const showNoStoriesForSelectedFilters =
    resultMode === "map" &&
    appliedFilterSetCount > 0 &&
    network !== null &&
    network.original_markers.length === 0 &&
    network.related_markers.length === 0;

  async function requestNetwork(payload: {
    selected_trope_id?: string | null;
    query?: string | null;
    story_filters?: Array<{ field: string; selected_values: string[] }>;
    story_filter_sets?: Array<{
      id: string;
      label: string;
      color: string;
      filter_groups: Array<{
        filters: Array<{ field: string; selected_values: string[] }>;
        selected_themes?: Array<{ id: string; text: string }>;
        selected_tropes?: Array<{ id: string; text: string }>;
        selected_keywords?: Array<{ id: string; text: string }>;
      }>;
    }>;
    min_similarity?: number;
  }) {
    try {
      setBusy(true);
      setError(null);
      const result = await buildExplorationNetwork({
        ...payload,
        min_similarity: payload.min_similarity ?? threshold,
        related_limit: 20,
        candidate_limit: 12,
      });
      setNetwork(normalizeExplorationNetworkResponse(result));
    } catch (caughtError) {
      setError(getErrorMessage(caughtError));
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    if (canUseFilterSets) {
      setSelectedTropeId(null);
      setSelectedTropePreview(null);
      return;
    }
    if (!selectedTropeParam) {
      setSelectedTropeId(null);
      setSelectedTropePreview(null);
      return;
    }
    setSelectedTropeId((current) => (current === selectedTropeParam ? current : selectedTropeParam));
  }, [canUseFilterSets, selectedTropeParam]);

  useEffect(() => {
    if (!canUseFilterSets) {
      setStories([]);
      return;
    }

    let cancelled = false;
    void (async () => {
      try {
        setStoriesLoading(true);
        const result = await getStories();
        if (cancelled) {
          return;
        }
        setStories(result.items);
      } catch (caughtError) {
        if (cancelled) {
          return;
        }
        setError(getErrorMessage(caughtError));
      } finally {
        if (!cancelled) {
          setStoriesLoading(false);
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [canUseFilterSets]);

  useEffect(() => {
    if (!canUseFilterSets) {
      return;
    }

    const normalizedFilterSets = filterSets.map((filterSet) => {
      let storiesRemaining = stories;
      return {
        ...filterSet,
        filters: filterSet.filters.map((filter) => {
          const storiesMatchingSelectedTerms = filterStoriesBySelectedSemanticTerms(storiesRemaining, {
            themes: filter.draftSelectedThemes,
            tropes: filter.draftSelectedTropes,
            keywords: filter.draftSelectedKeywords,
          });
          const normalizedFilter = {
            ...filter,
            draftFilters: normalizeStoryFieldFilters(filter.draftFilters, storiesMatchingSelectedTerms),
          };
          storiesRemaining = filterStoriesForExplorationFilter(storiesRemaining, normalizedFilter);
          return normalizedFilter;
        }),
      };
    });
    if (serializeExplorationFilterSets(normalizedFilterSets) !== serializeExplorationFilterSets(filterSets)) {
      setFilterSets(normalizedFilterSets);
    }
  }, [canUseFilterSets, filterSets, stories]);

  useEffect(() => {
    if (network?.selected_trope) {
      setSelectedTropePreview(network.selected_trope.text);
    }
  }, [network]);

  useEffect(() => {
    if (!selectedTropeId) {
      return;
    }
    if (!canUseSingleTropeExploration) {
      return;
    }

    setResultMode("map");
    const timeoutId = window.setTimeout(() => {
      void requestNetwork({
        selected_trope_id: selectedTropeId,
        min_similarity: threshold,
      });
    }, 180);

    return () => {
      window.clearTimeout(timeoutId);
    };
  }, [canUseSingleTropeExploration, selectedTropeId, threshold]);

  async function handleSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    window.location.hash = routeHref("/exploration");
    setSelectedTropeId(null);
    setSelectedTropePreview(null);
    setResultMode("candidate_search");
    await requestNetwork({
      query,
      min_similarity: threshold,
    });
  }

  function handleSelectCandidate(candidate: ExplorationCandidate) {
    setSelectedTropePreview(candidate.text);
    window.location.hash = routeHref("/exploration", { selected_trope_id: candidate.id });
  }

  function addFilterSet() {
    const nextSetId = nextSetIdRef.current;
    nextSetIdRef.current += 1;
    const nextFilterId = nextSemanticFilterIdRef.current;
    nextSemanticFilterIdRef.current += 1;
    setFilterSets((current) => [...current, createExplorationFilterSet(nextSetId, nextFilterId)]);
  }

  function addFilter(filterSetId: number) {
    const nextFilterId = nextSemanticFilterIdRef.current;
    nextSemanticFilterIdRef.current += 1;
    setFilterSets((current) =>
      current.map((filterSet) =>
        filterSet.id === filterSetId
          ? { ...filterSet, filters: [...filterSet.filters, createExplorationFilter(nextFilterId)] }
          : filterSet,
      ),
    );
  }

  function removeFilter(filterSetId: number, semanticFilterId: number) {
    setFilterSets((current) =>
      current.map((filterSet) =>
        filterSet.id === filterSetId
          ? { ...filterSet, filters: filterSet.filters.filter((filter) => filter.id !== semanticFilterId) }
          : filterSet,
      ),
    );
  }

  function removeFilterSet(filterSetId: number) {
    setFilterSets((current) => current.filter((filterSet) => filterSet.id !== filterSetId));
  }

  function updateFilterSetColor(filterSetId: number, color: string) {
    setFilterSets((current) =>
      current.map((filterSet) => (filterSet.id === filterSetId ? { ...filterSet, color } : filterSet)),
    );
  }

  function updateFilterSetLabel(filterSetId: number, label: string) {
    setFilterSets((current) =>
      current.map((filterSet) => (filterSet.id === filterSetId ? { ...filterSet, label } : filterSet)),
    );
  }

  function updateFilterSetTermQuery(
    filterSetId: number,
    semanticFilterId: number,
    kind: ExplorationSemanticTermKind,
    query: string,
  ) {
    setFilterSets((current) =>
      current.map((filterSet) => {
        if (filterSet.id !== filterSetId) return filterSet;
        return {
          ...filterSet,
          filters: filterSet.filters.map((filter) =>
            filter.id !== semanticFilterId
              ? filter
              : kind === "theme"
                ? { ...filter, themeQuery: query }
                : kind === "trope"
                  ? { ...filter, tropeQuery: query }
                  : { ...filter, keywordQuery: query },
          ),
        };
      }),
    );
  }

  function toggleFilterSetSelectedTerm(
    filterSetId: number,
    semanticFilterId: number,
    kind: ExplorationSemanticTermKind,
    term: ExplorationAppliedTermFilter,
  ) {
    setFilterSets((current) =>
      current.map((filterSet) => {
        if (filterSet.id !== filterSetId) return filterSet;
        return {
          ...filterSet,
          filters: filterSet.filters.map((filter) => {
            if (filter.id !== semanticFilterId) return filter;
            if (kind === "theme") return { ...filter, draftSelectedThemes: toggleSelectedTerms(filter.draftSelectedThemes, term) };
            if (kind === "trope") return { ...filter, draftSelectedTropes: toggleSelectedTerms(filter.draftSelectedTropes, term) };
            return { ...filter, draftSelectedKeywords: toggleSelectedTerms(filter.draftSelectedKeywords, term) };
          }),
        };
      }),
    );
  }

  function addDraftFilter(filterSetId: number, semanticFilterId: number) {
    const nextId = nextFilterIdRef.current;
    nextFilterIdRef.current += 1;
    setFilterSets((current) =>
      current.map((filterSet) =>
        filterSet.id === filterSetId
          ? { ...filterSet, filters: filterSet.filters.map((filter) => filter.id === semanticFilterId ? { ...filter, draftFilters: [...filter.draftFilters, createEmptyStoryFieldFilter(nextId)] } : filter) }
          : filterSet,
      ),
    );
  }

  function updateDraftFilterField(filterSetId: number, semanticFilterId: number, filterId: number, field: string) {
    setFilterSets((current) =>
      current.map((filterSet) =>
        filterSet.id === filterSetId
          ? { ...filterSet, filters: filterSet.filters.map((semanticFilter) => semanticFilter.id !== semanticFilterId ? semanticFilter : {
              ...semanticFilter,
              draftFilters: semanticFilter.draftFilters.map((filter) =>
                filter.id === filterId
                  ? {
                      ...filter,
                      field,
                      selectedValues: [],
                    }
                  : filter,
              ),
            }) }
          : filterSet,
      ),
    );
  }

  function updateDraftFilterValues(filterSetId: number, semanticFilterId: number, filterId: number, selectedValues: string[]) {
    setFilterSets((current) =>
      current.map((filterSet) =>
        filterSet.id === filterSetId
          ? { ...filterSet, filters: filterSet.filters.map((semanticFilter) => semanticFilter.id !== semanticFilterId ? semanticFilter : {
              ...semanticFilter,
              draftFilters: semanticFilter.draftFilters.map((filter) =>
                filter.id === filterId
                  ? {
                      ...filter,
                      selectedValues,
                    }
                  : filter,
              ),
            }) }
          : filterSet,
      ),
    );
  }

  function removeDraftFilter(filterSetId: number, semanticFilterId: number, filterId: number) {
    setFilterSets((current) =>
      current.map((filterSet) =>
        filterSet.id === filterSetId
          ? { ...filterSet, filters: filterSet.filters.map((semanticFilter) => semanticFilter.id !== semanticFilterId ? semanticFilter : { ...semanticFilter, draftFilters: semanticFilter.draftFilters.filter((filter) => filter.id !== filterId) }) }
          : filterSet,
      ),
    );
  }

  function applyDraftFilters(filterSetId: number, semanticFilterId: number) {
    setFilterSets((current) =>
      current.map((filterSet) => {
        if (filterSet.id !== filterSetId) {
          return filterSet;
        }
        return {
          ...filterSet,
          filters: filterSet.filters.map((filter) =>
            filter.id !== semanticFilterId || !storyFieldFiltersAreComplete(filter.draftFilters)
              ? filter
              : {
                  ...filter,
                  appliedFilters: filter.draftFilters.map((fieldFilter) => ({ ...fieldFilter, selectedValues: [...fieldFilter.selectedValues] })),
                  appliedSelectedThemes: filter.draftSelectedThemes.map((theme) => ({ ...theme })),
                  appliedSelectedTropes: filter.draftSelectedTropes.map((trope) => ({ ...trope })),
                  appliedSelectedKeywords: filter.draftSelectedKeywords.map((keyword) => ({ ...keyword })),
                },
          ),
        };
      }),
    );
  }

  function clearFilterSet(filterSetId: number, semanticFilterId: number) {
    setFilterSets((current) =>
      current.map((filterSet) =>
        filterSet.id === filterSetId
          ? { ...filterSet, filters: filterSet.filters.map((filter) => filter.id !== semanticFilterId ? filter : {
              ...filter,
              draftFilters: [],
              appliedFilters: [],
              themeQuery: "",
              tropeQuery: "",
              keywordQuery: "",
              draftSelectedThemes: [],
              draftSelectedTropes: [],
              draftSelectedKeywords: [],
              appliedSelectedThemes: [],
              appliedSelectedTropes: [],
              appliedSelectedKeywords: [],
            }) }
          : filterSet,
      ),
    );
  }

  async function handleMap() {
    if (!canMapWithCurrentSelection || hasPendingFilterChanges) {
      return;
    }

    setResultMode("map");
    await requestNetwork({
      selected_trope_id: selectedTropeId,
      story_filter_sets: buildStoryFilterSetsPayload(filterSets),
      min_similarity: threshold,
    });
  }

  const filterSetResults = network?.filter_set_results ?? [];
  const isMultiSetNetwork = filterSetResults.length > 0;
  const originalWithoutLocation = isMultiSetNetwork
    ? filterSetResults.flatMap((result) => result.original_markers.filter((marker) => !markerHasRenderableLocation(marker)))
    : network?.original_markers.filter((marker) => !markerHasRenderableLocation(marker)) ?? [];
  const relatedWithoutLocation = isMultiSetNetwork
    ? filterSetResults.flatMap((result) => result.related_markers.filter((marker) => !markerHasRenderableLocation(marker)))
    : network?.related_markers.filter((marker) => !markerHasRenderableLocation(marker)) ?? [];
  const visibleMarkers: VisibleExplorationMarker[] = isMultiSetNetwork
    ? filterSetResults.flatMap((result) => [
        ...result.original_markers.filter(markerHasRenderableLocation),
        ...result.related_markers.filter(markerHasRenderableLocation),
      ])
    : [
        ...(network?.original_markers.filter(markerHasRenderableLocation) ?? []),
        ...(network?.related_markers.filter(markerHasRenderableLocation) ?? []),
      ];
  const visibleConnections = isMultiSetNetwork
    ? filterSetResults.flatMap((result) => result.connections.filter(connectionHasRenderableCoordinates))
    : network?.connections.filter(connectionHasRenderableCoordinates) ?? [];
  const filterSetLegends = filterSetResults.map((result) => ({
    id: result.filter_set_id,
    label: result.filter_set_label,
    color: result.filter_set_color,
  }));
  const shouldShowCandidateCards =
    resultMode === "candidate_search" &&
    network !== null &&
    network.selected_trope === null &&
    network.selected_trope_candidates.length > 0;
  const shouldShowMultiSetResults = resultMode === "map" && network !== null && isMultiSetNetwork;
  const shouldShowNoStoriesForFilterSets =
    shouldShowMultiSetResults &&
    filterSetResults.every((result) => result.original_markers.length === 0 && result.related_markers.length === 0);
  const shouldShowFilterOnlyResults =
    resultMode === "map" &&
    network !== null &&
    !isMultiSetNetwork &&
    network.selected_trope === null &&
    network.selected_trope_candidates.length === 0;

  return (
    <div className="page-stack">
      {canUseSingleTropeExploration ? (
        <section className="panel">
          <div className="panel-header">
            <div>
              <h1>Explore the story network around a trope</h1>
            </div>
          </div>
          <form className="inline-form wrap-row" onSubmit={(event) => void handleSearch(event)}>
            <input
              className="input"
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Type a phrase to find candidate tropes"
              value={query}
            />
            <button className="button" disabled={busy || !query.trim()} type="submit">
              {busy ? "Loading..." : "Find candidates"}
            </button>
            {(selectedTropeId || network) && (
              <button
                className="button button-ghost"
                onClick={() => {
                  window.location.hash = routeHref("/exploration");
                  setSelectedTropeId(null);
                  setSelectedTropePreview(null);
                  setNetwork(null);
                  setError(null);
                  setResultMode("idle");
                }}
                type="button"
              >
                Clear
              </button>
            )}
          </form>
          <div className="field">
            <div className="card-row">
              <label htmlFor="similarity-threshold">
                <strong>Similarity threshold</strong>
              </label>
              <span className="pill">{threshold.toFixed(2)}</span>
            </div>
            <input
              className="range-input"
              disabled={busy}
              id="similarity-threshold"
              max="0.95"
              min="0.5"
              onChange={(event) => setThreshold(Number(event.target.value))}
              step="0.01"
              type="range"
              value={threshold}
            />
          </div>
        </section>
      ) : null}

      {canUseFilterSets ? (
        <section className="panel">
          <div className="panel-header">
            <div>
              <h1>Filter Sets</h1>
              <p className="muted">Build theme-, trope-, and keyword-aware story sets and compare them on the map.</p>
            </div>
          </div>
          <div className="stack">
            <strong>Filter sets</strong>
            <div className="stack">
              {filterSets.map((filterSet) => {
                return (
                  <article className="panel exploration-filter-set-panel" key={filterSet.id}>
                    <div className="card-row">
                      <div className="exploration-filter-set-heading">
                        <span className="exploration-filter-set-swatch" style={{ backgroundColor: filterSet.color }} />
                        <label className="exploration-filter-set-name">
                          <span>Set name</span>
                          <input
                            aria-label={`Name for Set ${filterSet.id}`}
                            className="input"
                            disabled={busy || storiesLoading}
                            onBlur={(event) =>
                              updateFilterSetLabel(
                                filterSet.id,
                                event.target.value.trim() || `Set ${filterSet.id}`,
                              )
                            }
                            onChange={(event) => updateFilterSetLabel(filterSet.id, event.target.value)}
                            value={filterSet.label}
                          />
                        </label>
                        <label className="exploration-filter-set-color-picker">
                          <span>Color</span>
                          <input
                            aria-label={`Color for ${normalizedFilterSetLabel(filterSet)}`}
                            disabled={busy || storiesLoading}
                            onChange={(event) => updateFilterSetColor(filterSet.id, event.target.value)}
                            type="color"
                            value={filterSet.color}
                          />
                          <output>{formatHexColor(filterSet.color)}</output>
                        </label>
                      </div>
                      <button
                        className="button button-ghost"
                        disabled={busy || storiesLoading}
                        onClick={() => removeFilterSet(filterSet.id)}
                        type="button"
                      >
                        Remove set
                      </button>
                    </div>
                    <div className="stack">
                      {(() => {
                        let storiesRemaining = stories;
                        return filterSet.filters.map((filter, index) => {
                        const storiesBeforeFilter = storiesRemaining;
                        const storiesMatchingSelectedTerms = filterStoriesBySelectedSemanticTerms(storiesBeforeFilter, {
                          themes: filter.draftSelectedThemes,
                          tropes: filter.draftSelectedTropes,
                          keywords: filter.draftSelectedKeywords,
                        });
                        const storiesAfterFilter = filterStoriesForExplorationFilter(storiesBeforeFilter, filter);
                        storiesRemaining = storiesAfterFilter;
                        const hasDraftSemanticTerms = filter.draftSelectedThemes.length > 0 || filter.draftSelectedTropes.length > 0 || filter.draftSelectedKeywords.length > 0;
                        return (
                          <div className="stack" key={filter.id}>
                            {filterSet.filters.length > 1 ? (
                              <div className="card-row">
                                <strong>Filter {index + 1}</strong>
                                <button className="button button-ghost" disabled={busy || storiesLoading} onClick={() => removeFilter(filterSet.id, filter.id)} type="button">Remove filter</button>
                              </div>
                            ) : null}
                            <StoryFieldFilterBuilder
                              activeCount={filter.appliedFilters.length + filter.appliedSelectedThemes.length + filter.appliedSelectedTropes.length + filter.appliedSelectedKeywords.length}
                              addFilterLabel="Add field filter"
                              appliedFilters={filter.appliedFilters}
                              clearDisabled={filter.draftFilters.length === 0 && filter.appliedFilters.length === 0 && filter.draftSelectedThemes.length === 0 && filter.appliedSelectedThemes.length === 0 && filter.draftSelectedTropes.length === 0 && filter.appliedSelectedTropes.length === 0 && filter.draftSelectedKeywords.length === 0 && filter.appliedSelectedKeywords.length === 0 && !filter.themeQuery.trim() && !filter.tropeQuery.trim() && !filter.keywordQuery.trim()}
                              draftFilters={filter.draftFilters}
                              hasPendingChanges={filterHasPendingChanges(filter)}
                              loading={storiesLoading || busy}
                              onAddFilter={() => addDraftFilter(filterSet.id, filter.id)}
                              onApplyFilters={() => applyDraftFilters(filterSet.id, filter.id)}
                              onClearFilters={() => clearFilterSet(filterSet.id, filter.id)}
                              onRemoveFilter={(fieldFilterId) => removeDraftFilter(filterSet.id, filter.id, fieldFilterId)}
                              onUpdateFilterField={(fieldFilterId, field) => updateDraftFilterField(filterSet.id, filter.id, fieldFilterId, field)}
                              onUpdateFilterValues={(fieldFilterId, selectedValues) => updateDraftFilterValues(filterSet.id, filter.id, fieldFilterId, selectedValues)}
                              stories={storiesMatchingSelectedTerms}
                            >
                              <div className="stack">
                                <div className="exploration-semantic-filter-grid">
                            <ExplorationFilterSetTermPicker
                              allowMultipleQueries
                              kind="theme"
                              loading={storiesLoading || busy}
                              onQueryChange={(value) => updateFilterSetTermQuery(filterSet.id, filter.id, "theme", value)}
                              onToggleTerm={(theme) => toggleFilterSetSelectedTerm(filterSet.id, filter.id, "theme", theme)}
                              query={filter.themeQuery}
                              availableStories={storiesBeforeFilter}
                              selectedTermsScrollable={filter.appliedSelectedThemes.length > 0}
                              selectedTerms={filter.draftSelectedThemes}
                            />
                            <ExplorationFilterSetTermPicker
                              allowMultipleQueries
                              kind="trope"
                              loading={storiesLoading || busy}
                              onQueryChange={(value) => updateFilterSetTermQuery(filterSet.id, filter.id, "trope", value)}
                              onToggleTerm={(trope) => toggleFilterSetSelectedTerm(filterSet.id, filter.id, "trope", trope)}
                              query={filter.tropeQuery}
                              availableStories={storiesBeforeFilter}
                              selectedTermsScrollable={filter.appliedSelectedTropes.length > 0}
                              selectedTerms={filter.draftSelectedTropes}
                            />
                            <ExplorationFilterSetTermPicker
                              allowMultipleQueries
                              kind="keyword"
                              loading={storiesLoading || busy}
                              onQueryChange={(value) => updateFilterSetTermQuery(filterSet.id, filter.id, "keyword", value)}
                              onToggleTerm={(keyword) => toggleFilterSetSelectedTerm(filterSet.id, filter.id, "keyword", keyword)}
                              query={filter.keywordQuery}
                              availableStories={storiesBeforeFilter}
                              selectedTermsScrollable={filter.appliedSelectedKeywords.length > 0}
                              selectedTerms={filter.draftSelectedKeywords}
                            />
                          </div>
                                {hasDraftSemanticTerms && storiesMatchingSelectedTerms.length === 0 ? <p className="muted">No stories match the selected themes, tropes, and keywords yet, so no hard filters are available for this filter.</p> : null}
                              </div>
                            </StoryFieldFilterBuilder>
                          </div>
                        );
                        });
                      })()}
                      <div className="button-row"><button className="button button-ghost" disabled={busy || storiesLoading} onClick={() => addFilter(filterSet.id)} type="button">Add filter</button></div>
                    </div>
                    </article>
                  );
                })}
            </div>
            <div className="button-row">
              <button className="button button-ghost" disabled={busy || storiesLoading} onClick={addFilterSet} type="button">
                Add filter set
              </button>
            </div>
          </div>
          <div className="stack">
            {selectedTropeId ? (
              <p className="muted">
                Selected trope: {selectedTropePreview || "Ready to map"}
              </p>
            ) : null}
            {!selectedTropeId && appliedFilterSetCount === 0 ? (
              <p className="muted">Add and apply at least one filter set, or select a trope to map without filters.</p>
            ) : null}
            <button
              className="button"
              disabled={busy || storiesLoading || hasPendingFilterChanges || !canMapWithCurrentSelection}
              onClick={() => void handleMap()}
              type="button"
            >
              {busy && resultMode === "map" ? "Mapping..." : "Map it"}
            </button>
          </div>
        </section>
      ) : null}

      {error && <section className="notice notice-error">{error}</section>}
      {busy ? (
        <section className="panel">
          <p className="muted">
            {resultMode === "candidate_search" ? "Searching for candidate tropes..." : "Loading exploration network..."}
          </p>
        </section>
      ) : null}
      {shouldShowCandidateCards ? (
        <section className="panel">
          <div className="panel-header">
            <h2>Candidate similar tropes</h2>
          </div>
          {renderCandidateCards(network.selected_trope_candidates, busy, handleSelectCandidate)}
        </section>
      ) : null}

      {shouldShowMultiSetResults ? (
        <ExplorationResultBoundary key={network.selected_trope?.id || "multi-filter-sets"}>
          <section className="panel">
            <div className="panel-header">
              <h2>{network.selected_trope ? network.selected_trope.text : "Filter set comparison"}</h2>
            </div>
            <div className="stats-grid">
              <article className="stat-card">
                <span className="stat-label">Filter sets</span>
                <strong>{filterSetResults.length}</strong>
              </article>
              <article className="stat-card">
                <span className="stat-label">Mapped stories</span>
                <strong>
                  {filterSetResults.reduce(
                    (sum, result) => sum + result.original_markers.length + result.related_markers.length,
                    0,
                  )}
                </strong>
              </article>
              <article className="stat-card">
                <span className="stat-label">No precise location</span>
                <strong>{originalWithoutLocation.length + relatedWithoutLocation.length}</strong>
              </article>
            </div>
          </section>

          <section className="panel">
            <div className="panel-header">
              <h2>Map</h2>
            </div>
            {shouldShowNoStoriesForFilterSets ? (
              <p className="muted">No stories corresponding to the filters selected</p>
            ) : (
              <ExplorationMap
                connections={visibleConnections}
                filterSetLegends={filterSetLegends}
                markers={visibleMarkers}
              />
            )}
          </section>

          <section className="two-column-layout">
            {filterSetResults.map((result) => {
              const setIsEmpty = result.original_markers.length === 0 && result.related_markers.length === 0;
              return (
                <article className="panel exploration-filter-set-summary" key={result.filter_set_id}>
                  <div className="card-row">
                    <div className="exploration-filter-set-heading">
                      <span className="exploration-filter-set-swatch" style={{ backgroundColor: result.filter_set_color }} />
                      <h3>{result.filter_set_label}</h3>
                    </div>
                  </div>
                  <div className="stack">
                    <strong>Filters</strong>
                    {result.filters.length > 0 ? (
                      <div className="tag-list">
                        {result.filters.map((filter) => (
                          <span
                            className="pill exploration-filter-summary-pill"
                            key={`${result.filter_set_id}-${filter.field}`}
                          >
                            {summarizeAppliedFilter(filter)}
                          </span>
                        ))}
                      </div>
                    ) : (
                      <p className="muted">No hard field filters applied.</p>
                    )}
                  </div>
                  {[
                    { label: "Selected themes", terms: result.selected_themes },
                    { label: "Selected tropes", terms: result.selected_tropes },
                    { label: "Selected keywords", terms: result.selected_keywords },
                  ].map(
                    ({ label, terms }) =>
                      terms.length > 0 ? (
                        <div className="stack" key={label}>
                          <strong>{label}</strong>
                          <div className="exploration-selected-term-list">
                            {terms.map((term) => (
                              <span
                                className="pill exploration-filter-summary-pill"
                                key={`${result.filter_set_id}-${term.id}`}
                                title={term.text}
                              >
                                {term.text}
                              </span>
                            ))}
                          </div>
                        </div>
                      ) : null,
                  )}
                  <div className="stats-grid">
                    <article className="stat-card">
                      <span className="stat-label">Original</span>
                      <strong>{result.original_markers.length}</strong>
                    </article>
                    <article className="stat-card">
                      <span className="stat-label">Related</span>
                      <strong>{result.related_markers.length}</strong>
                    </article>
                    <article className="stat-card">
                      <span className="stat-label">No precise location</span>
                      <strong>{result.missing_original_coords + result.missing_related_coords}</strong>
                    </article>
                  </div>
                  {setIsEmpty ? (
                    <p className="muted">No stories corresponding to the filters selected</p>
                  ) : (
                    <div className="stack">
                      {network.selected_trope ? (
                        <p className="muted">Related stories: {summarizeMarkerTitles(result.related_markers)}</p>
                      ) : null}
                    </div>
                  )}
                  {network.selected_trope ? (
                    <div className="stack">
                      <strong>Related tropes</strong>
                      {renderRelatedTropes(result.related_tropes)}
                    </div>
                  ) : null}
                  {!setIsEmpty ? (
                    <div className="stack">
                      <strong>Original stories</strong>
                      {renderMarkerTitleList(result.original_markers)}
                    </div>
                  ) : null}
                </article>
              );
            })}
          </section>
        </ExplorationResultBoundary>
      ) : null}

      {network?.selected_trope && !shouldShowMultiSetResults ? (
        <ExplorationResultBoundary key={network.selected_trope.id}>
          <section className="panel">
            <div className="panel-header">
              <h2>{network.selected_trope.text}</h2>
            </div>
            <div className="stats-grid">
              <article className="stat-card">
                <span className="stat-label">Original markers</span>
                <strong>{network.original_markers.length}</strong>
              </article>
              <article className="stat-card">
                <span className="stat-label">Related markers</span>
                <strong>{network.related_markers.length}</strong>
              </article>
              <article className="stat-card">
                <span className="stat-label">Connections</span>
                <strong>{network.connections.length}</strong>
              </article>
              <article className="stat-card">
                <span className="stat-label">No precise location</span>
                <strong>{originalWithoutLocation.length + relatedWithoutLocation.length}</strong>
              </article>
            </div>
            <div className="stack">
              <strong>Related tropes</strong>
              {renderRelatedTropes(network.related_tropes)}
            </div>
          </section>

          <section className="panel">
            <div className="panel-header">
              <h2>Map</h2>
            </div>
            {showNoStoriesForSelectedFilters ? (
              <p className="muted">No stories corresponding to the filters selected</p>
            ) : (
              <ExplorationMap connections={visibleConnections} markers={visibleMarkers} />
            )}
          </section>

          <section className="two-column-layout">
            <div className="panel">
              <h2>Original story markers</h2>
              <div className="stack">
                {network.original_markers.map((marker) => (
                  <article className="card" key={marker.story_id}>
                    <h3>{marker.title}</h3>
                    <p className="muted">{formatCoordinateLabel(marker)}</p>
                    <p>{marker.abstract || "No abstract available."}</p>
                    <div className="stack">
                      <strong>Story tropes</strong>
                      {renderStoryTropeCards(storyTropesForMarker(marker), "No tropes on this story.")}
                    </div>
                  </article>
                ))}
              </div>
            </div>
            <div className="panel">
              <h2>Related story markers</h2>
              <div className="stack">
                {network.related_markers.length ? (
                  network.related_markers.map((marker) => (
                    <article className="card" key={marker.story_id}>
                      <h3>{marker.title}</h3>
                      <p className="muted">{formatCoordinateLabel(marker)}</p>
                      <p>{marker.abstract || "No abstract available."}</p>
                      <div className="stack">
                        <strong>Matched tropes</strong>
                        {renderMatchedTropeCards(marker.matched_tropes, "No matched tropes in this network response.")}
                      </div>
                    </article>
                  ))
                ) : (
                  <p className="muted">No related stories met the current threshold.</p>
                )}
              </div>
            </div>
          </section>

          <section className="two-column-layout">
            <div className="panel">
              <h2>Original stories without precise location</h2>
              <MissingLocationList
                emptyLabel="Every original story in this network has a valid map location."
                markers={originalWithoutLocation}
              />
            </div>
            <div className="panel">
              <h2>Related stories without precise location</h2>
              <MissingLocationList
                emptyLabel="Every related story in this network has a valid map location."
                markers={relatedWithoutLocation}
              />
            </div>
          </section>
        </ExplorationResultBoundary>
      ) : null}

      {shouldShowFilterOnlyResults && !shouldShowMultiSetResults ? (
        <>
          <section className="panel">
            <div className="panel-header">
              <h2>Filtered stories</h2>
            </div>
            <div className="stats-grid">
              <article className="stat-card">
                <span className="stat-label">Stories</span>
                <strong>{network.original_markers.length}</strong>
              </article>
              <article className="stat-card">
                <span className="stat-label">No precise location</span>
                <strong>{network.missing_original_coords}</strong>
              </article>
            </div>
          </section>

          <section className="panel">
            <div className="panel-header">
              <h2>Map</h2>
            </div>
            {showNoStoriesForSelectedFilters ? (
              <p className="muted">No stories corresponding to the filters selected</p>
            ) : (
              <ExplorationMap connections={visibleConnections} markers={visibleMarkers} />
            )}
          </section>

          <section className="two-column-layout">
            <div className="panel">
              <h2>Matching story markers</h2>
              <div className="stack">
                {network.original_markers.length ? (
                  network.original_markers.map((marker) => (
                    <article className="card" key={marker.story_id}>
                      <h3>{marker.title}</h3>
                      <p className="muted">{formatCoordinateLabel(marker)}</p>
                      <p>{marker.abstract || "No abstract available."}</p>
                      <div className="stack">
                        <strong>Story tropes</strong>
                        {renderStoryTropeCards(storyTropesForMarker(marker), "No tropes on this story.")}
                      </div>
                    </article>
                  ))
                ) : (
                  <p className="muted">No stories corresponding to the filters selected</p>
                )}
              </div>
            </div>
            <div className="panel">
              <h2>Stories without precise location</h2>
              <MissingLocationList
                emptyLabel="Every matching story has a valid map location."
                markers={originalWithoutLocation}
              />
            </div>
          </section>
        </>
      ) : null}
    </div>
  );
}
