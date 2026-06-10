import L from "leaflet";
import { Component, type ReactNode, FormEvent, useEffect, useRef, useState } from "react";

import { buildExplorationNetwork, getErrorMessage } from "../api/client";
import { TropeCard } from "../components/TropeCard";
import type {
  ExplorationCandidate,
  ExplorationConnection,
  ExplorationMatchedTrope,
  ExplorationMarker,
  ExplorationNetworkResponse,
  ExplorationStoryTrope,
} from "../api/types";
import { routeHref, useHashSearch } from "../router";

const DEFAULT_CENTER: [number, number] = [0, 0];
const DEFAULT_ZOOM = 2;
const SINGLE_POINT_ZOOM = 6;

type CoordinatePair = [number, number];
type RenderableConnection = ExplorationConnection & {
  source_coordinates: CoordinatePair;
  target_coordinates: CoordinatePair;
};

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
          (trope) =>
            `<span class="pill">${escapeHtml(trope.text)} · ${trope.score.toFixed(2)}</span>`,
        )
        .join("")
    : `<p class="muted">No matched tropes in this network response.</p>`;

  return `
    <div class="map-popup-content popup-stack">
      <div>
        <strong>${escapeHtml(marker.title)}</strong>
        <p class="muted">${marker.kind === "original" ? "Original story" : "Related story"}</p>
      </div>
      <p class="muted">Row ${marker.source_row_number ?? "unknown"} · ${escapeHtml(formatCoordinateLabel(marker))}</p>
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
  };
}

function buildExplorationMapDataSignature(
  markers: Array<ExplorationMarker & { coordinates: CoordinatePair; has_location: true }>,
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
          <div className="card-row">
            <div>
              <h3>{marker.title}</h3>
              <p className="muted">
                {marker.kind === "original" ? "Original story" : "Related story"} · row{" "}
                {marker.source_row_number ?? "unknown"}
              </p>
            </div>
            <span className="pill">{marker.kind}</span>
          </div>
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
        <TropeCard
          compact
          key={candidate.id}
          subtitle={`score ${candidate.score.toFixed(2)}`}
          trope={candidate}
        />
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
    return (
      <p className="muted">
        No candidate tropes matched this phrase yet. If you just uploaded a dataset, wait for the rebuild job to finish
        or try a more exact trope phrase.
      </p>
    );
  }

  return (
    <div className="stack">
      {candidates.map((candidate) => (
        <TropeCard
          key={candidate.id}
          subtitle={`score ${candidate.score.toFixed(2)}`}
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
        <TropeCard compact key={trope.id} subtitle={`score ${trope.score.toFixed(2)}`} trope={trope} />
      ))}
    </div>
  );
}

function ExplorationMap({
  markers,
  connections,
  bounds,
}: {
  markers: Array<ExplorationMarker & { coordinates: CoordinatePair; has_location: true }>;
  connections: RenderableConnection[];
  bounds: [CoordinatePair, CoordinatePair] | null;
}) {
  if (!markers.length && !connections.length) {
    return (
      <div className="card subdued">
        <p className="muted">
          No stories in this network have a usable precise location, so the map cannot be drawn for this trope.
        </p>
      </div>
    );
  }

  const mapElementRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<L.Map | null>(null);
  const overlayLayerRef = useRef<L.LayerGroup | null>(null);
  const dataSignature = buildExplorationMapDataSignature(markers, connections, bounds);

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
    mapRef.current = map;
    overlayLayerRef.current = overlayLayer;

    return () => {
      overlayLayer.clearLayers();
      overlayLayerRef.current = null;
      map.remove();
      mapRef.current = null;
    };
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    const overlayLayer = overlayLayerRef.current;
    if (!map || !overlayLayer) {
      return;
    }

    overlayLayer.clearLayers();

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
  }, [dataSignature]);

  return (
    <div className="map-shell">
      <div className="map-canvas" ref={mapElementRef} />
      <div className="legend-row">
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
      </div>
    </div>
  );
}

export function ExplorationPage() {
  const hashSearch = useHashSearch();
  const [query, setQuery] = useState("");
  const [selectedTropeId, setSelectedTropeId] = useState<string | null>(null);
  const [network, setNetwork] = useState<ExplorationNetworkResponse | null>(null);
  const [threshold, setThreshold] = useState(0.62);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const selectedTropeParam = new URLSearchParams(hashSearch).get("selected_trope_id");

  async function requestNetwork(payload: {
    selected_trope_id?: string | null;
    query?: string | null;
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
    if (!selectedTropeParam) {
      setSelectedTropeId(null);
      return;
    }
    setSelectedTropeId((current) => (current === selectedTropeParam ? current : selectedTropeParam));
  }, [selectedTropeParam]);

  useEffect(() => {
    if (!selectedTropeId) {
      return;
    }

    const timeoutId = window.setTimeout(() => {
      void requestNetwork({
        selected_trope_id: selectedTropeId,
        min_similarity: threshold,
      });
    }, 180);

    return () => {
      window.clearTimeout(timeoutId);
    };
  }, [selectedTropeId, threshold]);

  async function handleSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    window.location.hash = routeHref("/exploration");
    setSelectedTropeId(null);
    await requestNetwork({
      query,
      min_similarity: threshold,
    });
  }

  function handleSelectCandidate(candidate: ExplorationCandidate) {
    window.location.hash = routeHref("/exploration", { selected_trope_id: candidate.id });
  }

  const originalWithoutLocation = network?.original_markers.filter((marker) => !markerHasRenderableLocation(marker)) ?? [];
  const relatedWithoutLocation = network?.related_markers.filter((marker) => !markerHasRenderableLocation(marker)) ?? [];
  const visibleMarkers = [
    ...(network?.original_markers.filter(markerHasRenderableLocation) ?? []),
    ...(network?.related_markers.filter(markerHasRenderableLocation) ?? []),
  ];
  const visibleConnections = network?.connections.filter(connectionHasRenderableCoordinates) ?? [];
  const mapBounds = sanitizeBounds(network?.bounds ?? null) ?? computeBoundsFromMarkers(visibleMarkers);

  return (
    <div className="page-stack">
      <section className="panel">
        <div className="panel-header">
          <div>
            <p className="eyebrow">Exploration</p>
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
                setNetwork(null);
                setError(null);
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
          <p className="muted">
            Higher values keep only more similar related tropes and stories. When a trope is selected, moving the slider
            refreshes the network automatically.
          </p>
        </div>
      </section>

      {error && <section className="notice notice-error">{error}</section>}
      {busy ? (
        <section className="panel">
          <p className="muted">
            {selectedTropeId ? "Loading exploration network..." : "Searching for candidate tropes..."}
          </p>
        </section>
      ) : null}
      {!busy && !error && !network ? (
        <section className="panel">
          <p className="muted">
            Start with a phrase query to find candidate tropes, then select one to render the exploration map.
          </p>
        </section>
      ) : null}

      {network && !network.selected_trope ? (
        <section className="panel">
          <div className="panel-header">
            <div>
              <h2>Candidate similar tropes</h2>
              <p className="muted">Choose a canonical trope to build the map network.</p>
            </div>
          </div>
          {renderCandidateCards(network.selected_trope_candidates, busy, handleSelectCandidate)}
        </section>
      ) : null}

      {network?.selected_trope ? (
        <ExplorationResultBoundary key={network.selected_trope.id}>
          <section className="panel">
            <div className="panel-header">
              <div>
                <h2>{network.selected_trope.text}</h2>
                <p className="muted">
                  {network.selected_trope.story_count} stories · {network.related_tropes.length} related tropes above the
                  threshold
                </p>
              </div>
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
              <div>
                <h2>Map</h2>
                <p className="muted">
                  Only stories with valid coordinates are placed on the map. Stories with missing or malformed coordinates
                  stay in the review list below.
                </p>
              </div>
            </div>
            <ExplorationMap bounds={mapBounds} connections={visibleConnections} markers={visibleMarkers} />
          </section>

          <section className="two-column-layout">
            <div className="panel">
              <h2>Original story markers</h2>
              <div className="stack">
                {network.original_markers.map((marker) => (
                  <article className="card" key={marker.story_id}>
                    <div className="card-row">
                      <div>
                        <h3>{marker.title}</h3>
                        <p className="muted">
                          {formatCoordinateLabel(marker)} · score {marker.similarity.toFixed(2)}
                        </p>
                      </div>
                      <span className="pill">original</span>
                    </div>
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
                      <div className="card-row">
                        <div>
                          <h3>{marker.title}</h3>
                          <p className="muted">
                            {formatCoordinateLabel(marker)} · score {marker.similarity.toFixed(2)}
                          </p>
                        </div>
                        <span className="pill">related</span>
                      </div>
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
    </div>
  );
}
