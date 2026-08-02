import L from "leaflet";
import { useEffect, useRef, useState } from "react";

import { DATE_OF_RECORDING_FIELD, getStoryFieldLabel, LONG_TEXT_FIELDS } from "../constants/csv";
import { normalizeLongitude } from "../map/antimeridian";

export interface LocationDraft {
  place: string;
  coordinates: CoordinatePair | null;
}

type CoordinatePair = [number, number];

const PLACE_OF_RECORDING_FIELD = "place of recording";
const SPACE_COORD_FIELD = "space coord";
const LOCATION_PICKER_DEFAULT_CENTER: CoordinatePair = [0, 0];
const LOCATION_PICKER_DEFAULT_ZOOM = 2;
const ISO_DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
const MANUAL_COORDINATE_PAIR_RE = /^\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*,\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*$/;

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
  if (!Number.isFinite(latitude) || !Number.isFinite(longitude) || latitude < -90 || latitude > 90) {
    return null;
  }
  const normalizedLongitude = normalizeLongitude(longitude);
  if (latitude === 0 && normalizedLongitude === 0) {
    return null;
  }
  return [latitude, normalizedLongitude];
}

function validateManualCoordinatePair(value: string): string | null {
  if (!value.trim()) {
    return null;
  }

  const matches = MANUAL_COORDINATE_PAIR_RE.exec(value);
  if (!matches) {
    return "Use decimal degrees in the format latitude, longitude (for example, -17.650000, -149.426000).";
  }

  const latitude = Number(matches[1]);
  const longitude = Number(matches[2]);
  if (!Number.isFinite(latitude) || !Number.isFinite(longitude)) {
    return "Coordinates must be finite decimal numbers.";
  }
  if (latitude < -90 || latitude > 90) {
    return "Latitude must be between -90 and +90.";
  }
  if (longitude < -180 || longitude > 180) {
    return "Longitude must be between -180 and +180.";
  }
  return null;
}

function parseManualCoordinatePair(value: string): CoordinatePair | null {
  if (validateManualCoordinatePair(value)) {
    return null;
  }

  const matches = MANUAL_COORDINATE_PAIR_RE.exec(value);
  if (!matches) {
    return null;
  }
  return [Number(matches[1]), Number(matches[2])];
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

function openNativeDatePicker(input: HTMLInputElement | null) {
  if (!input) {
    return;
  }
  const pickerInput = input as HTMLInputElement & { showPicker?: () => void };
  if (pickerInput.showPicker) {
    pickerInput.showPicker();
    return;
  }
  input.focus();
  input.click();
}

export function isValidRecordingDate(value: string): boolean {
  if (!ISO_DATE_RE.test(value)) {
    return false;
  }

  const [year, month, day] = value.split("-").map(Number);
  if (year < 1) {
    return false;
  }
  const parsed = new Date(`${value}T00:00:00Z`);
  return (
    !Number.isNaN(parsed.getTime()) &&
    parsed.getUTCFullYear() === year &&
    parsed.getUTCMonth() === month - 1 &&
    parsed.getUTCDate() === day
  );
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
      onSelectRef.current([event.latlng.lat, normalizeLongitude(event.latlng.lng)]);
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
  const fieldLabel = getStoryFieldLabel(field);
  const datePickerRef = useRef<HTMLInputElement | null>(null);

  if (field === DATE_OF_RECORDING_FIELD) {
    const dateError = value && !isValidRecordingDate(value) ? "Enter a valid date in YYYY-MM-DD format (for example, 2026-08-01)." : null;

    return (
      <div className="field">
        <label htmlFor={inputId}>{fieldLabel}</label>
        <div className="date-actions">
          <input
            aria-hidden="true"
            aria-label="Choose a recording date from the calendar"
            className="date-picker-proxy"
            disabled={disabled}
            onChange={(event) => onChange(event.target.value)}
            ref={datePickerRef}
            tabIndex={-1}
            type="date"
            value={isValidRecordingDate(value) ? value : ""}
          />
          <button className="button button-ghost" disabled={disabled} onClick={() => openNativeDatePicker(datePickerRef.current)} type="button">
            Choose date
          </button>
          {value ? (
            <button className="button button-ghost" disabled={disabled} onClick={() => onChange("")} type="button">
              Clear date
            </button>
          ) : null}
        </div>
        <input
          id={inputId}
          aria-describedby={dateError ? `${inputId}-help ${inputId}-error` : `${inputId}-help`}
          aria-invalid={Boolean(dateError)}
          className="input date-manual-input"
          disabled={disabled}
          onChange={(event) => onChange(event.target.value)}
          onBlur={(event) => onChange(event.target.value.trim())}
          pattern={"\\d{4}-\\d{2}-\\d{2}"}
          placeholder="YYYY-MM-DD"
          type="text"
          value={value}
        />
        <p className="muted" id={`${inputId}-help`}>Enter a date as YYYY-MM-DD, or choose it from the calendar.</p>
        {dateError ? <p className="form-error" id={`${inputId}-error`} role="alert">{dateError}</p> : null}
      </div>
    );
  }

  if (field === PLACE_OF_RECORDING_FIELD) {
    return (
      <div className="field">
        <label htmlFor={inputId}>{fieldLabel}</label>
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
        <label htmlFor={inputId}>{fieldLabel}</label>
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
      <span>{fieldLabel}</span>
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
  onApply: (locationDraft: LocationDraft) => void;
}) {
  const [coordinateInput, setCoordinateInput] = useState(() =>
    locationDraft.coordinates ? formatCoordinatePair(locationDraft.coordinates) : "",
  );
  const [coordinateError, setCoordinateError] = useState<string | null>(null);

  useEffect(() => {
    setCoordinateInput(locationDraft.coordinates ? formatCoordinatePair(locationDraft.coordinates) : "");
    setCoordinateError(null);
  }, [locationDraft.coordinates]);

  function updateCoordinateInput(value: string) {
    setCoordinateInput(value);
    setCoordinateError(validateManualCoordinatePair(value));
  }

  function applyLocation() {
    const validationError = validateManualCoordinatePair(coordinateInput);
    if (validationError) {
      setCoordinateError(validationError);
      return;
    }

    onApply({
      ...locationDraft,
      coordinates: parseManualCoordinatePair(coordinateInput),
    });
  }

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
              <span>{getStoryFieldLabel(PLACE_OF_RECORDING_FIELD)}</span>
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

            <div className="field">
              <label htmlFor="location-coordinate-input">{getStoryFieldLabel(SPACE_COORD_FIELD)}</label>
              <input
                aria-describedby="location-coordinate-help"
                aria-invalid={Boolean(coordinateError)}
                className="input"
                disabled={busy}
                id="location-coordinate-input"
                inputMode="decimal"
                onBlur={() => {
                  const coordinates = parseManualCoordinatePair(coordinateInput);
                  if (coordinates) {
                    setCoordinateInput(formatCoordinatePair(coordinates));
                  }
                }}
                onChange={(event) => updateCoordinateInput(event.target.value)}
                placeholder="-17.650000, -149.426000"
                value={coordinateInput}
              />
              <p className="muted" id="location-coordinate-help">
                Enter decimal degrees as latitude, longitude. Latitude must be −90 to +90; longitude must be −180 to +180.
              </p>
              {coordinateError ? <p className="form-error">{coordinateError}</p> : null}
            </div>

            <div className="card subdued">
              <p className="muted">
                Click the map or enter coordinates manually. The place label stays editable so you can keep the wording used in the
                source while still saving precise coordinates.
              </p>
            </div>

            <div className="button-row wrap-row">
              <button
                className="button button-ghost"
                disabled={busy || (!locationDraft.place && !coordinateInput.trim())}
                onClick={() => {
                  setCoordinateInput("");
                  setCoordinateError(null);
                  onChange({
                    place: "",
                    coordinates: null,
                  });
                }}
                type="button"
              >
                Clear location
              </button>
              <button className="button button-ghost" disabled={busy} onClick={onCancel} type="button">
                Cancel
              </button>
              <button className="button" disabled={busy || Boolean(coordinateError)} onClick={applyLocation} type="button">
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
