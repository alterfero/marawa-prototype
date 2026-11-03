type ScreenPoint = { x: number; y: number };
type LocatedStory = {
  story_id: string;
  coordinates: [number, number];
  filter_set_id?: string | null;
  kind?: string;
};

export type ProjectedStoryMarker<T extends LocatedStory> = {
  marker: T;
  point: ScreenPoint;
  radius: number;
  origin?: ScreenPoint;
};

type StoryMarkerCluster<T extends LocatedStory> = {
  markers: ProjectedStoryMarker<T>[];
  point: ScreenPoint;
  radius: number;
};

// At this zoom shared locations have enough space for individually clickable
// markers. Above it, keep the polygon small instead of doubling its screen size.
const COINCIDENT_MARKER_ZOOM = 12;
const MARKER_GAP = 10;

function spreadCoincidentMarkers<T extends LocatedStory>(
  markers: ProjectedStoryMarker<T>[],
  zoom: number,
): ProjectedStoryMarker<T>[] {
  const groups = new Map<string, ProjectedStoryMarker<T>[]>();
  const positioned = markers.map((entry) => ({ ...entry }));
  positioned.forEach((entry) => {
    const [latitude, longitude] = entry.marker.coordinates;
    // Both longitude spellings refer to the same point on the wrapped map.
    const key = `${latitude},${longitude === 180 ? -180 : longitude}`;
    const group = groups.get(key) ?? [];
    group.push(entry);
    groups.set(key, group);
  });

  const scale = Math.min(1, 2 ** (zoom - COINCIDENT_MARKER_ZOOM));
  groups.forEach((group) => {
    if (group.length < 2) {
      return;
    }
    group.sort((left, right) =>
      left.marker.story_id.localeCompare(right.marker.story_id) ||
      (left.marker.filter_set_id ?? "").localeCompare(right.marker.filter_set_id ?? "") ||
      (left.marker.kind ?? "").localeCompare(right.marker.kind ?? ""),
    );
    const largestRadius = group.reduce((radius, entry) => Math.max(radius, entry.radius), 0);
    // The chord between adjacent vertices must clear both markers and strokes.
    const polygonRadius = Math.max(28, (2 * largestRadius + MARKER_GAP) / (2 * Math.sin(Math.PI / group.length)));
    group.forEach((entry, index) => {
      const angle = -Math.PI / 2 + (index * 2 * Math.PI) / group.length;
      entry.origin = entry.point;
      entry.point = {
        x: entry.origin.x + Math.cos(angle) * polygonRadius * scale,
        y: entry.origin.y + Math.sin(angle) * polygonRadius * scale,
      };
    });
  });
  return positioned;
}

/** Presentation-only geometry shared by the live map and SVG/print exports. */
export function buildStoryMarkerClusters<T extends LocatedStory>(
  markers: ProjectedStoryMarker<T>[],
  zoom: number,
  bundleRadius: (count: number) => number,
  mergeOverlaps = true,
): StoryMarkerCluster<T>[] {
  function cluster(entries: ProjectedStoryMarker<T>[]): StoryMarkerCluster<T> {
    const sum = entries.reduce((total, entry) => ({
      x: total.x + entry.point.x,
      y: total.y + entry.point.y,
    }), { x: 0, y: 0 });
    return {
      markers: entries,
      point: { x: sum.x / entries.length, y: sum.y / entries.length },
      radius: entries.length === 1 ? entries[0].radius : bundleRadius(entries.length),
    };
  }

  const clusters = spreadCoincidentMarkers(markers, zoom).map((entry) => cluster([entry]));
  let merged = mergeOverlaps;
  while (merged) {
    merged = false;
    for (let leftIndex = 0; leftIndex < clusters.length; leftIndex += 1) {
      for (let rightIndex = leftIndex + 1; rightIndex < clusters.length; rightIndex += 1) {
        const left = clusters[leftIndex];
        const right = clusters[rightIndex];
        if (Math.hypot(left.point.x - right.point.x, left.point.y - right.point.y) > left.radius + right.radius) {
          continue;
        }
        clusters[leftIndex] = cluster([...left.markers, ...right.markers]);
        clusters.splice(rightIndex, 1);
        merged = true;
        break;
      }
      if (merged) {
        break;
      }
    }
  }
  return clusters;
}
