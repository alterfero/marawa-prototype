import L from "leaflet";
import { KeyboardEvent, useEffect, useRef } from "react";

import { LONG_TEXT_FIELDS } from "../constants/csv";

export interface LocationDraft {
  place: string;
  coordinates: CoordinatePair | null;
}

type CoordinatePair = [number, number];

const DATE_OF_RECORDING_FIELD = "date of recording";
const PLACE_OF_RECORDING_FIELD = "place of recording";
const SPACE_COORD_FIELD = "space coord";
const LOCATION_PICKER_DEFAULT_CENTER: CoordinatePair = [0, 0];
const LOCATION_PICKER_DEFAULT_ZOOM = 2;
const ISO_DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
const DATE_NAVIGATION_KEYS = new Set([
  "Tab",
  "Enter",
  "Escape",
  "ArrowLeft",
  "ArrowRight",
  "ArrowUp",
  "ArrowDown",
  "Home",
  "End",
  "PageUp",
  "PageDown",
]);

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

function applyCoordinateDirection(value: number, direction: string): number {
  if (direction === "S" || direction === "W") {
    return -Math.abs(value);
  }
  if (direction === "N" || direction === "E") {
    return Math.abs(value);
  }
  return value;
}

function parseCoordinatePair(value: string): CoordinatePair | null {
  const text = value.trim().replace(/−/g, "-");
  if (!text) {
    return null;
  }

  const cleaned = text
    .replace(/≈/g, "")
    .replace(/~/g, "")
    .replace(/\(/g, " ")
    .replace(/\)/g, " ")
    .replace(/\[/g, " ")
    .replace(/\]/g, " ")
    .replace(/(?<=\d),(?=\d)/g, ".");
  const matches = Array.from(cleaned.matchAll(/([+-]?\d+(?:\.\d+)?)\s*°?\s*([NSEW])?/gi));
  if (matches.length < 2) {
    return null;
  }

  const latitude = applyCoordinateDirection(Number(matches[0][1]), (matches[0][2] ?? "").toUpperCase());
  const longitude = applyCoordinateDirection(Number(matches[1][1]), (matches[1][2] ?? "").toUpperCase());
  if (!isFiniteCoordinatePair([latitude, longitude])) {
    return null;
  }
  if (latitude === 0 && longitude === 0) {
    return null;
  }
  return [latitude, longitude];
}

function formatCoordinatePair(value: CoordinatePair): string {
  return `${value[0].toFixed(6)}, ${value[1].toFixed(6)}`;
}

function fieldInputId(prefix: string, field: string): string {
  const normalizedField = field
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return `${prefix}-${normalizedField}`;
}

function preventFreeTextDateEntry(event: KeyboardEvent<HTMLInputElement>) {
  if (event.ctrlKey || event.metaKey || event.altKey) {
    return;
  }
  if (DATE_NAVIGATION_KEYS.has(event.key)) {
    return;
  }
  if (event.key.length === 1 || event.key === "Backspace" || event.key === "Delete") {
    event.preventDefault();
  }
}

function openNativeDatePicker(input: HTMLInputElement | null) {
  if (!input) {
    return;
  }
  const pickerInput = input as HTMLInputElement & { showPicker?: () => void };
  pickerInput.showPicker?.();
}

function isIsoCalendarDate(value: string): boolean {
  return ISO_DATE_RE.test(value);
}

function LocationPickerMap({
  selectedCoordinates,
  onSelect,
}: {
  selectedCoordinates: CoordinatePair | null;
  onSelect: (coordinates: CoordinatePair) => void;
}) {
  const mapElementRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<L.Map | null>(null);
  const overlayLayerRef = useRef<L.LayerGroup | null>(null);
  const onSelectRef = useRef(onSelect);

  useEffect(() => {
    onSelectRef.current = onSelect;
  }, [onSelect]);

  useEffect(() => {
    if (!mapElementRef.current || mapRef.current) {
      return;
    }

    const map = L.map(mapElementRef.current, {
      center: selectedCoordinates ?? LOCATION_PICKER_DEFAULT_CENTER,
      zoom: selectedCoordinates ? 6 : LOCATION_PICKER_DEFAULT_ZOOM,
      scrollWheelZoom: true,
    });

    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    }).addTo(map);

    const overlayLayer = L.layerGroup().addTo(map);
    map.on("click", (event) => {
      onSelectRef.current([event.latlng.lat, event.latlng.lng]);
    });

    mapRef.current = map;
    overlayLayerRef.current = overlayLayer;

    return () => {
      overlayLayer.clearLayers();
      overlayLayerRef.current = null;
      map.remove();
      mapRef.current = null;
    };
  }, [selectedCoordinates]);

  useEffect(() => {
    const map = mapRef.current;
    const overlayLayer = overlayLayerRef.current;
    if (!map || !overlayLayer) {
      return;
    }

    overlayLayer.clearLayers();
    if (selectedCoordinates) {
      L.circleMarker(selectedCoordinates, {
        color: "#11545b",
        fillColor: "#2a7278",
        fillOpacity: 0.92,
        radius: 8,
        weight: 3,
      }).addTo(overlayLayer);
    }

    window.requestAnimationFrame(() => {
      map.invalidateSize();
    });
  }, [selectedCoordinates]);

  return (
    <div className="location-picker-map">
      <div className="map-canvas location-picker-canvas" ref={mapElementRef} />
      <p className="muted">Click anywhere on the map to place the recording point.</p>
    </div>
  );
}

export function buildLocationDraft(fields: Record<string, string>): LocationDraft {
  return {
    place: fields[PLACE_OF_RECORDING_FIELD] || "",
    coordinates: parseCoordinatePair(fields[SPACE_COORD_FIELD] || ""),
  };
}

export function applyLocationDraftToFields(fields: Record<string, string>, locationDraft: LocationDraft): Record<string, string> {
  return {
    ...fields,
    [PLACE_OF_RECORDING_FIELD]: locationDraft.place,
    [SPACE_COORD_FIELD]: locationDraft.coordinates ? formatCoordinatePair(locationDraft.coordinates) : "",
  };
}

export function StoryFieldInput({
  field,
  value,
  disabled,
  inputIdPrefix,
  onChange,
  onOpenLocationPicker,
}: {
  field: string;
  value: string;
  disabled: boolean;
  inputIdPrefix: string;
  onChange: (value: string) => void;
  onOpenLocationPicker: () => void;
}) {
  const inputId = fieldInputId(inputIdPrefix, field);

  if (field === DATE_OF_RECORDING_FIELD) {
    if (value && !isIsoCalendarDate(value)) {
      return (
        <div className="field">
          <label htmlFor={inputId}>{field}</label>
          <div className="input-with-action">
            <input className="input" disabled={disabled} id={inputId} onChange={(event) => onChange(event.target.value)} value={value} />
            <button className="button button-ghost" disabled={disabled} onClick={() => onChange("")} type="button">
              Use picker
            </button>
          </div>
          <p className="muted">This imported value is not an ISO date yet. Keep the raw text or switch to the date picker.</p>
        </div>
      );
    }

    return (
      <div className="field">
        <label htmlFor={inputId}>{field}</label>
        <div className="input-with-action">
          <input
            id={inputId}
            className="input"
            disabled={disabled}
            onChange={(event) => onChange(event.target.value)}
            onClick={(event) => openNativeDatePicker(event.currentTarget)}
            onKeyDown={preventFreeTextDateEntry}
            onPaste={(event) => event.preventDefault()}
            onDrop={(event) => event.preventDefault()}
            type="date"
            value={value}
          />
          {value ? (
            <button className="button button-ghost" disabled={disabled} onClick={() => onChange("")} type="button">
              Clear date
            </button>
          ) : null}
        </div>
      </div>
    );
  }

  if (field === PLACE_OF_RECORDING_FIELD) {
    return (
      <div className="field">
        <label htmlFor={inputId}>{field}</label>
        <div className="input-with-action">
          <input className="input" disabled={disabled} id={inputId} onChange={(event) => onChange(event.target.value)} value={value} />
          <button className="button button-ghost" disabled={disabled} onClick={onOpenLocationPicker} type="button">
            Pick on map
          </button>
        </div>
        <p className="muted">The map helper can update this place label and its exact coordinates together.</p>
      </div>
    );
  }

  if (field === SPACE_COORD_FIELD) {
    return (
      <div className="field">
        <label htmlFor={inputId}>{field}</label>
        <div className="input-with-action">
          <input
            id={inputId}
            className="input input-readonly-button"
            disabled={disabled}
            onClick={onOpenLocationPicker}
            onKeyDown={(event) => {
              if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                onOpenLocationPicker();
              }
            }}
            placeholder="Open the map to choose coordinates"
            readOnly
            value={value}
          />
          {value ? (
            <button className="button button-ghost" disabled={disabled} onClick={() => onChange("")} type="button">
              Clear coord
            </button>
          ) : null}
        </div>
      </div>
    );
  }

  const isLongText = LONG_TEXT_FIELDS.has(field);
  return (
    <label className={`field ${isLongText ? "field-span-full" : ""}`}>
      <span>{field}</span>
      {isLongText ? (
        <textarea className="input input-textarea" disabled={disabled} onChange={(event) => onChange(event.target.value)} rows={field === "1-sentence summary" ? 3 : 5} value={value} />
      ) : (
        <input className="input" disabled={disabled} onChange={(event) => onChange(event.target.value)} value={value} />
      )}
    </label>
  );
}

export function StoryLocationPickerModal({
  busy,
  locationDraft,
  onChange,
  onCancel,
  onApply,
}: {
  busy: boolean;
  locationDraft: LocationDraft;
  onChange: (locationDraft: LocationDraft) => void;
  onCancel: () => void;
  onApply: () => void;
}) {
  return (
    <div className="modal-backdrop" onClick={onCancel} role="presentation">
      <section
        aria-labelledby="location-picker-title"
        aria-modal="true"
        className="modal-shell modal-shell-wide"
        onClick={(event) => event.stopPropagation()}
        role="dialog"
      >
        <div className="panel-header">
          <h2 id="location-picker-title">Pick recording location</h2>
          <button className="button button-ghost" disabled={busy} onClick={onCancel} type="button">
            Close
          </button>
        </div>

        <div className="location-picker-layout">
          <div className="stack">
            <label className="field">
              <span>{PLACE_OF_RECORDING_FIELD}</span>
              <input
                className="input"
                disabled={busy}
                onChange={(event) =>
                  onChange({
                    ...locationDraft,
                    place: event.target.value,
                  })
                }
                value={locationDraft.place}
              />
            </label>

            <label className="field">
              <span>{SPACE_COORD_FIELD}</span>
              <input className="input" placeholder="Click the map to choose coordinates" readOnly value={locationDraft.coordinates ? formatCoordinatePair(locationDraft.coordinates) : ""} />
            </label>

            <div className="card subdued">
              <p className="muted">
                Click the map to place the story location. The place label stays editable so you can keep the wording used in the
                source while still saving precise coordinates.
              </p>
            </div>

            <div className="button-row wrap-row">
              <button
                className="button button-ghost"
                disabled={busy || (!locationDraft.place && !locationDraft.coordinates)}
                onClick={() =>
                  onChange({
                    place: "",
                    coordinates: null,
                  })
                }
                type="button"
              >
                Clear location
              </button>
              <button className="button button-ghost" disabled={busy} onClick={onCancel} type="button">
                Cancel
              </button>
              <button className="button" disabled={busy} onClick={onApply} type="button">
                Use location
              </button>
            </div>
          </div>

          <LocationPickerMap
            onSelect={(coordinates) =>
              onChange({
                ...locationDraft,
                coordinates,
              })
            }
            selectedCoordinates={locationDraft.coordinates}
          />
        </div>
      </section>
    </div>
  );
}
