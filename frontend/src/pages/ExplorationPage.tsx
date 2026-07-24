import L from "leaflet";
import { Component, type ReactNode, FormEvent, useEffect, useId, useRef, useState } from "react";

import { buildExplorationNetwork, getErrorMessage, getStories } from "../api/client";
import { ExplorationFilterSetTropePicker } from "../components/ExplorationFilterSetTropePicker";
import {
  createEmptyStoryFieldFilter,
  filterStoriesBySelectedTropes,
  normalizeStoryFieldFilters,
  serializeStoryFieldFilters,
  storyFieldFiltersAreComplete,
  StoryFieldFilterBuilder,
  type StoryFieldFilter,
} from "../components/StoryFieldFilters";
import { roleAtLeast, useAuth } from "../auth";
import { TropeCard } from "../components/TropeCard";
import type {
  ExplorationAppliedFilter,
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
import { routeHref, useHashSearch } from "../router";

const DEFAULT_CENTER: [number, number] = [0, 0];
const DEFAULT_ZOOM = 2;
const SINGLE_POINT_ZOOM = 6;
const FILTER_SET_PALETTE = ["#1d4ed8", "#d97706", "#15803d", "#b91c1c", "#7c3aed", "#0f766e"];
const ORIGINAL_DENSITY_COLOR = "#d7263d";
const RELATED_DENSITY_COLOR = "#2c7bb6";
const DENSITY_RADIUS = 72;
const DENSITY_RADIUS_MIN = 44;
const DENSITY_RADIUS_MAX = 92;

type CoordinatePair = [number, number];
type MapRenderMode = "markers" | "density";
type ExplorationFilterSetState = {
  id: number;
  color: string;
  draftFilters: StoryFieldFilter[];
  appliedFilters: StoryFieldFilter[];
  tropeQuery: string;
  draftSelectedTropes: ExplorationAppliedTropeFilter[];
  appliedSelectedTropes: ExplorationAppliedTropeFilter[];
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

function sanitizeBounds(bounds: number[][] | null): [CoordinatePair, CoordinatePair] | null {
  if (!bounds || bounds.length !== 2) {
    return null;
  }

  const southWest = bounds[0];
  const northEast = bounds[1];
  if (!isFiniteCoordinatePair(southWest) || !isFiniteCoordinatePair(northEast)) {
    return null;
  }

  return [southWest, northEast];
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

function computeBoundsFromMarkers(
  markers: Array<ExplorationMarker & { coordinates: CoordinatePair }>,
): [CoordinatePair, CoordinatePair] | null {
  if (!markers.length) {
    return null;
  }

  const latitudes = markers.map((marker) => marker.coordinates[0]);
  const longitudes = markers.map((marker) => marker.coordinates[1]);
  return [
    [Math.min(...latitudes), Math.min(...longitudes)],
    [Math.max(...latitudes), Math.max(...longitudes)],
  ];
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
  bounds: [CoordinatePair, CoordinatePair] | null,
): string {
  return JSON.stringify({
    bounds,
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
  filterSetLegends?: FilterSetLegend[],
): DensityGroup[] {
  if (!markers.length) {
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
        coordinates: marker.coordinates,
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
      coordinates: marker.coordinates,
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
        const pixelPoint = map.latLngToContainerPoint(point.coordinates);

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

function serializeSelectedTropeFilters(tropes: ExplorationAppliedTropeFilter[]): string {
  return JSON.stringify(
    tropes
      .map((trope) => trope.id)
      .sort((left, right) => left.localeCompare(right)),
  );
}

function createExplorationFilterSet(nextId: number): ExplorationFilterSetState {
  return {
    id: nextId,
    color: FILTER_SET_PALETTE[(nextId - 1) % FILTER_SET_PALETTE.length],
    draftFilters: [],
    appliedFilters: [],
    tropeQuery: "",
    draftSelectedTropes: [],
    appliedSelectedTropes: [],
  };
}

function serializeExplorationFilterSets(filterSets: ExplorationFilterSetState[]): string {
  return JSON.stringify(
    filterSets.map((filterSet) => ({
      id: filterSet.id,
      color: filterSet.color,
      draftFilters: JSON.parse(serializeStoryFieldFilters(filterSet.draftFilters)),
      appliedFilters: JSON.parse(serializeStoryFieldFilters(filterSet.appliedFilters)),
      draftSelectedTropes: JSON.parse(serializeSelectedTropeFilters(filterSet.draftSelectedTropes)),
      appliedSelectedTropes: JSON.parse(serializeSelectedTropeFilters(filterSet.appliedSelectedTropes)),
    })),
  );
}

function filterSetHasPendingChanges(filterSet: ExplorationFilterSetState): boolean {
  return (
    serializeStoryFieldFilters(filterSet.draftFilters) !== serializeStoryFieldFilters(filterSet.appliedFilters) ||
    serializeSelectedTropeFilters(filterSet.draftSelectedTropes) !==
      serializeSelectedTropeFilters(filterSet.appliedSelectedTropes)
  );
}

function filterSetHasAppliedCriteria(filterSet: ExplorationFilterSetState): boolean {
  return filterSet.appliedFilters.length > 0 || filterSet.appliedSelectedTropes.length > 0;
}

function buildFilterSetLabel(index: number): string {
  return `Set ${index + 1}`;
}

function buildStoryFilterSetsPayload(filterSets: ExplorationFilterSetState[]) {
  return filterSets
    .filter(filterSetHasAppliedCriteria)
    .map((filterSet, index) => ({
      id: `filter-set-${filterSet.id}`,
      label: buildFilterSetLabel(index),
      color: filterSet.color,
      filters: storyFiltersPayload(filterSet.appliedFilters),
      selected_tropes: filterSet.appliedSelectedTropes.map((trope) => ({
        id: trope.id,
        text: trope.text,
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
  bounds,
  filterSetLegends,
}: {
  markers: VisibleExplorationMarker[];
  connections: RenderableConnection[];
  bounds: [CoordinatePair, CoordinatePair] | null;
  filterSetLegends?: FilterSetLegend[];
}) {
  const mapViewId = useId();
  const mapElementRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<L.Map | null>(null);
  const overlayLayerRef = useRef<L.LayerGroup | null>(null);
  const densityLayerRef = useRef<ExplorationDensityLayer | null>(null);
  const [renderMode, setRenderMode] = useState<MapRenderMode>("markers");
  const dataSignature = buildExplorationMapDataSignature(markers, connections, bounds);
  const densityGroups = buildDensityGroups(markers, filterSetLegends);
  const densitySignature = buildDensityDataSignature(densityGroups);

  useEffect(() => {
    if (!mapElementRef.current || mapRef.current) {
      return;
    }

    const map = L.map(mapElementRef.current, {
      center: DEFAULT_CENTER,
      zoom: DEFAULT_ZOOM,
      scrollWheelZoom: true,
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

    overlayLayer.clearLayers();

    if (renderMode === "markers") {
      connections.forEach((connection) => {
        L.polyline([connection.source_coordinates, connection.target_coordinates], {
          color: connection.color,
          opacity: 0.62,
          weight: 2,
        }).addTo(overlayLayer);
      });

      markers.forEach((marker) => {
        L.circleMarker(marker.coordinates, {
          color: marker.color,
          fillColor: marker.color,
          fillOpacity: marker.kind === "original" ? 0.88 : 0.62,
          weight: marker.kind === "original" ? 2.5 : 1.5,
          radius: marker.kind === "original" ? 9 : 7,
        })
          .bindTooltip(escapeHtml(marker.title), {
            direction: "top",
            opacity: 0.92,
            sticky: true,
          })
          .bindPopup(markerPopupHtml(marker), {
            maxWidth: 320,
          })
          .addTo(overlayLayer);
      });
    }

    densityLayer.setGroups(densityGroups);
    densityLayer.setVisible(renderMode === "density");

    if (bounds) {
      const [southWest, northEast] = bounds;
      if (sameCoordinatePair(southWest, northEast)) {
        map.setView(southWest, SINGLE_POINT_ZOOM);
      } else {
        map.fitBounds(bounds, {
          padding: [32, 32],
        });
      }
    } else {
      map.setView(DEFAULT_CENTER, DEFAULT_ZOOM);
    }

    window.requestAnimationFrame(() => {
      map.invalidateSize();
    });
  }, [dataSignature, densitySignature, renderMode]);

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
      </div>
      <div className="map-canvas" ref={mapElementRef} />
      <div className="legend-row">
        {renderMode === "density" ? (
          filterSetLegends && filterSetLegends.length > 0 ? (
            <>
              {filterSetLegends.map((legend) => (
                <span className="legend-item" key={legend.id}>
                  <span className="legend-dot" style={{ background: legend.color }} />
                  {legend.label}
                </span>
              ))}
              <span className="legend-item">
                Darker zones mean more stories. Switch back to exact locations to inspect individual stories.
              </span>
            </>
          ) : (
            <>
              <span className="legend-item">
                <span className="legend-dot legend-dot-original" />
                Original story density
              </span>
              <span className="legend-item">
                <span className="legend-dot legend-dot-related" />
                Related story density
              </span>
              <span className="legend-item">
                Darker zones mean more stories. Switch back to exact locations for markers and connections.
              </span>
            </>
          )
        ) : filterSetLegends && filterSetLegends.length > 0 ? (
          <>
            {filterSetLegends.map((legend) => (
              <span className="legend-item" key={legend.id}>
                <span className="legend-dot" style={{ background: legend.color }} />
                {legend.label}
              </span>
            ))}
            <span className="legend-item">Solid markers are selected stories. Lighter markers and lines are related stories.</span>
          </>
        ) : (
          <>
            <span className="legend-item">
              <span className="legend-dot legend-dot-original" />
              Original markers
            </span>
            <span className="legend-item">
              <span className="legend-dot legend-dot-related" />
              Related markers
            </span>
            <span className="legend-item">
              <span className="legend-line" />
              Closest connection
            </span>
          </>
        )}
      </div>
    </div>
  );
}

export function ExplorationPage() {
  const { user } = useAuth();
  const hashSearch = useHashSearch();
  const nextFilterIdRef = useRef(1);
  const nextSetIdRef = useRef(2);
  const [query, setQuery] = useState("");
  const [selectedTropeId, setSelectedTropeId] = useState<string | null>(null);
  const [selectedTropePreview, setSelectedTropePreview] = useState<string | null>(null);
  const [stories, setStories] = useState<StorySummary[]>([]);
  const [storiesLoading, setStoriesLoading] = useState(false);
  const [filterSets, setFilterSets] = useState<ExplorationFilterSetState[]>([createExplorationFilterSet(1)]);
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
      filters: Array<{ field: string; selected_values: string[] }>;
      selected_tropes?: Array<{ id: string; text: string }>;
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

    const normalizedFilterSets = filterSets.map((filterSet) => ({
      ...filterSet,
      draftFilters: normalizeStoryFieldFilters(
        filterSet.draftFilters,
        filterStoriesBySelectedTropes(stories, filterSet.draftSelectedTropes),
      ),
    }));
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
    setFilterSets((current) => [...current, createExplorationFilterSet(nextSetId)]);
  }

  function removeFilterSet(filterSetId: number) {
    setFilterSets((current) => current.filter((filterSet) => filterSet.id !== filterSetId));
  }

  function updateFilterSetTropeQuery(filterSetId: number, tropeQuery: string) {
    setFilterSets((current) =>
      current.map((filterSet) =>
        filterSet.id === filterSetId
          ? {
              ...filterSet,
              tropeQuery,
            }
          : filterSet,
      ),
    );
  }

  function toggleFilterSetSelectedTrope(filterSetId: number, trope: ExplorationAppliedTropeFilter) {
    setFilterSets((current) =>
      current.map((filterSet) => {
        if (filterSet.id !== filterSetId) {
          return filterSet;
        }
        const alreadySelected = filterSet.draftSelectedTropes.some((item) => item.id === trope.id);
        return {
          ...filterSet,
          draftSelectedTropes: alreadySelected
            ? filterSet.draftSelectedTropes.filter((item) => item.id !== trope.id)
            : [...filterSet.draftSelectedTropes, trope],
        };
      }),
    );
  }

  function addDraftFilter(filterSetId: number) {
    const nextId = nextFilterIdRef.current;
    nextFilterIdRef.current += 1;
    setFilterSets((current) =>
      current.map((filterSet) =>
        filterSet.id === filterSetId
          ? {
              ...filterSet,
              draftFilters: [...filterSet.draftFilters, createEmptyStoryFieldFilter(nextId)],
            }
          : filterSet,
      ),
    );
  }

  function updateDraftFilterField(filterSetId: number, filterId: number, field: string) {
    setFilterSets((current) =>
      current.map((filterSet) =>
        filterSet.id === filterSetId
          ? {
              ...filterSet,
              draftFilters: filterSet.draftFilters.map((filter) =>
                filter.id === filterId
                  ? {
                      ...filter,
                      field,
                      selectedValues: [],
                    }
                  : filter,
              ),
            }
          : filterSet,
      ),
    );
  }

  function updateDraftFilterValues(filterSetId: number, filterId: number, selectedValues: string[]) {
    setFilterSets((current) =>
      current.map((filterSet) =>
        filterSet.id === filterSetId
          ? {
              ...filterSet,
              draftFilters: filterSet.draftFilters.map((filter) =>
                filter.id === filterId
                  ? {
                      ...filter,
                      selectedValues,
                    }
                  : filter,
              ),
            }
          : filterSet,
      ),
    );
  }

  function removeDraftFilter(filterSetId: number, filterId: number) {
    setFilterSets((current) =>
      current.map((filterSet) =>
        filterSet.id === filterSetId
          ? {
              ...filterSet,
              draftFilters: filterSet.draftFilters.filter((filter) => filter.id !== filterId),
            }
          : filterSet,
      ),
    );
  }

  function applyDraftFilters(filterSetId: number) {
    setFilterSets((current) =>
      current.map((filterSet) => {
        if (filterSet.id !== filterSetId || !storyFieldFiltersAreComplete(filterSet.draftFilters)) {
          return filterSet;
        }
        return {
          ...filterSet,
          appliedFilters: filterSet.draftFilters.map((filter) => ({
            ...filter,
            selectedValues: [...filter.selectedValues],
          })),
          appliedSelectedTropes: filterSet.draftSelectedTropes.map((trope) => ({
            ...trope,
          })),
        };
      }),
    );
  }

  function clearFilterSet(filterSetId: number) {
    setFilterSets((current) =>
      current.map((filterSet) =>
        filterSet.id === filterSetId
          ? {
              ...filterSet,
              draftFilters: [],
              appliedFilters: [],
              tropeQuery: "",
              draftSelectedTropes: [],
              appliedSelectedTropes: [],
            }
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
  const mapBounds = sanitizeBounds(network?.bounds ?? null) ?? computeBoundsFromMarkers(visibleMarkers);
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
              <p className="muted">Build trope-aware story sets and compare them on the map.</p>
            </div>
          </div>
          <div className="stack">
            <strong>Filter sets</strong>
            <div className="stack">
              {filterSets.map((filterSet, index) => {
                const storiesMatchingSelectedTropes = filterStoriesBySelectedTropes(stories, filterSet.draftSelectedTropes);
                return (
                  <article className="panel exploration-filter-set-panel" key={filterSet.id}>
                    <div className="card-row">
                      <div className="exploration-filter-set-heading">
                        <span className="exploration-filter-set-swatch" style={{ backgroundColor: filterSet.color }} />
                        <strong>{buildFilterSetLabel(index)}</strong>
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
                    <StoryFieldFilterBuilder
                      activeCount={filterSet.appliedFilters.length + filterSet.appliedSelectedTropes.length}
                      appliedFilters={filterSet.appliedFilters}
                      clearDisabled={
                        filterSet.draftFilters.length === 0 &&
                        filterSet.appliedFilters.length === 0 &&
                        filterSet.draftSelectedTropes.length === 0 &&
                        filterSet.appliedSelectedTropes.length === 0 &&
                        !filterSet.tropeQuery.trim()
                      }
                      draftFilters={filterSet.draftFilters}
                      hasPendingChanges={filterSetHasPendingChanges(filterSet)}
                      loading={storiesLoading || busy}
                      onAddFilter={() => addDraftFilter(filterSet.id)}
                      onApplyFilters={() => applyDraftFilters(filterSet.id)}
                      onClearFilters={() => clearFilterSet(filterSet.id)}
                      onRemoveFilter={(filterId) => removeDraftFilter(filterSet.id, filterId)}
                      onUpdateFilterField={(filterId, field) => updateDraftFilterField(filterSet.id, filterId, field)}
                      onUpdateFilterValues={(filterId, selectedValues) =>
                        updateDraftFilterValues(filterSet.id, filterId, selectedValues)
                      }
                      stories={storiesMatchingSelectedTropes}
                    >
                      {canUseFilterSets ? (
                        <div className="stack">
                          <ExplorationFilterSetTropePicker
                            loading={storiesLoading || busy}
                            onQueryChange={(value) => updateFilterSetTropeQuery(filterSet.id, value)}
                            onToggleTrope={(trope) => toggleFilterSetSelectedTrope(filterSet.id, trope)}
                            query={filterSet.tropeQuery}
                            selectedTropes={filterSet.draftSelectedTropes}
                          />
                          {filterSet.draftSelectedTropes.length > 0 && storiesMatchingSelectedTropes.length === 0 ? (
                            <p className="muted">
                              No stories match the selected tropes yet, so no hard filters are available for this set.
                            </p>
                          ) : null}
                        </div>
                      ) : null}
                      </StoryFieldFilterBuilder>
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
                bounds={mapBounds}
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
                  {result.selected_tropes.length > 0 ? (
                    <div className="stack">
                      <strong>Selected tropes</strong>
                      <div className="tag-list">
                        {result.selected_tropes.map((trope) => (
                          <span className="pill exploration-filter-summary-pill" key={`${result.filter_set_id}-${trope.id}`}>
                            {trope.text}
                          </span>
                        ))}
                      </div>
                    </div>
                  ) : null}
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
              <ExplorationMap bounds={mapBounds} connections={visibleConnections} markers={visibleMarkers} />
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
              <ExplorationMap bounds={mapBounds} connections={visibleConnections} markers={visibleMarkers} />
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
