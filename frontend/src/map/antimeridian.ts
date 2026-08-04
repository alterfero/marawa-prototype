export type CoordinatePair = [number, number];

export type MapViewport = {
  bounds: [CoordinatePair, CoordinatePair];
  longitudeStart: number;
};

export function normalizeLongitude(longitude: number): number {
  if (longitude >= -180 && longitude <= 180) {
    return longitude;
  }
  const normalized = ((longitude + 180) % 360 + 360) % 360 - 180;
  return normalized === -180 && longitude > 0 ? 180 : normalized;
}

export function createAntimeridianAwareViewport(points: CoordinatePair[]): MapViewport | null {
  if (points.length === 0) {
    return null;
  }

  const latitudes = points.map(([latitude]) => latitude);
  const longitudes = points.map(([, longitude]) => normalizeLongitude(longitude)).sort((left, right) => left - right);

  let widestGap = -1;
  let widestGapIndex = 0;
  longitudes.forEach((longitude, index) => {
    const nextLongitude = index === longitudes.length - 1 ? longitudes[0] + 360 : longitudes[index + 1];
    const gap = nextLongitude - longitude;
    if (gap > widestGap) {
      widestGap = gap;
      widestGapIndex = index;
    }
  });

  const longitudeStart = longitudes[(widestGapIndex + 1) % longitudes.length];
  const longitudeEnd = longitudeStart + 360 - widestGap;
  return {
    bounds: [
      [Math.min(...latitudes), longitudeStart],
      [Math.max(...latitudes), longitudeEnd],
    ],
    longitudeStart,
  };
}

export function toViewportCoordinates([latitude, longitude]: CoordinatePair, viewport: MapViewport): CoordinatePair {
  const normalizedLongitude = normalizeLongitude(longitude);
  return [
    latitude,
    normalizedLongitude < viewport.longitudeStart ? normalizedLongitude + 360 : normalizedLongitude,
  ];
}

/**
 * Places a coordinate in the copy of the wrapped map nearest a reference
 * longitude. Leaflet repeats map tiles horizontally, but vector overlays do
 * not repeat automatically.
 */
export function toNearestWorldCoordinates(
  [latitude, longitude]: CoordinatePair,
  referenceLongitude: number,
): CoordinatePair {
  const normalizedLongitude = normalizeLongitude(longitude);
  return [
    latitude,
    normalizedLongitude + 360 * Math.round((referenceLongitude - normalizedLongitude) / 360),
  ];
}

/**
 * Keeps a connection in the world copy nearest the map while preserving its
 * shortest path across the antimeridian.
 */
export function toNearestWorldConnectionCoordinates(
  source: CoordinatePair,
  target: CoordinatePair,
  referenceLongitude: number,
): [CoordinatePair, CoordinatePair] {
  const sourceCoordinates = toNearestWorldCoordinates(source, referenceLongitude);
  return [sourceCoordinates, toNearestWorldCoordinates(target, sourceCoordinates[1])];
}
