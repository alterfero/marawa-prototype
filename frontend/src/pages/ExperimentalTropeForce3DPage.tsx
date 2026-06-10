import { forceCollide, forceX, forceY, forceZ } from "d3-force-3d";
import ForceGraph3D, { type ForceGraphMethods, type GraphData, type LinkObject, type NodeObject } from "react-force-graph-3d";
import { FormEvent, useEffect, useRef, useState } from "react";
import {
  AmbientLight,
  CanvasTexture,
  DirectionalLight,
  Group,
  Mesh,
  MeshBasicMaterial,
  MeshLambertMaterial,
  Object3D,
  PlaneGeometry,
  SphereGeometry,
  Sprite,
  SpriteMaterial,
} from "three";

import { buildTropeSequenceGraph, getErrorMessage } from "../api/client";
import type {
  TropeSequenceGraphLink,
  TropeSequenceGraphNode,
  TropeSequenceGraphResponse,
} from "../api/types";
import { useHashSearch } from "../router";

const DEFAULT_QUERY = "";
const DEFAULT_SIMILARITY_THRESHOLD = 0.65;
const DEFAULT_MAX_STORIES = 150;
const DEFAULT_MAX_LINKS_PER_NODE = 4;
const DEFAULT_VERTICAL_SPACING = 30;
const DEFAULT_GEOGRAPHIC_STRENGTH = 0.22;
const DEFAULT_SEMANTIC_STRENGTH = 0.55;
const DEFAULT_COLLISION_RADIUS = 14;
const GRAPH_BACKGROUND = "#f4efe6";
const MAP_PROJECTION_SCALE = 8;
const MAP_PLANE_PADDING = 32;
const MAP_PLANE_COLOR = "#eadfcf";
const MAP_WATER_COLOR = "#f6f0e5";
const MAP_GRATICULE_COLOR = "#c8baa5";
const MAP_COAST_COLOR = "#3a7a78";
const MAP_PLANE_NAME = "trope-force-3d-map-plane";
const MAP_PLANE_ELEVATION = -1.5;
const ANCHOR_LABEL_ELEVATION = 7;
const ANCHOR_DOT_RADIUS = 2.8;
const OCCURRENCE_BASE_HEIGHT = 18;

type GraphViewportSize = {
  width: number;
  height: number;
};

type ForceGraphNode = TropeSequenceGraphNode & {
  geo_x?: number;
  geo_z?: number;
  vertical_target_y?: number;
};
type ForceGraphLink = TropeSequenceGraphLink;
type GraphLibNode = NodeObject<ForceGraphNode>;
type GraphLibLink = LinkObject<ForceGraphNode, ForceGraphLink>;
type ForceGraphData = GraphData<GraphLibNode, GraphLibLink>;

type LinkForceApi = {
  distance?: (accessor: (link: ForceGraphLink) => number) => void;
  strength?: (accessor: (link: ForceGraphLink) => number) => void;
  iterations?: (count: number) => void;
};

type GraphBounds = {
  center: { x: number; y: number; z: number };
  maxSpan: number;
};

type GraphPlaneBounds = {
  minX: number;
  maxX: number;
  minZ: number;
  maxZ: number;
  width: number;
  depth: number;
  centerX: number;
  centerZ: number;
};

type MapProjectionContext = {
  centerLon: number;
  centerLat: number;
  scale: number;
};

type DebugSnapshot = {
  graphRefReady: boolean;
  forceGraphDataReady: boolean;
  renderMode: "simple" | "tuned";
  nodeCount: number;
  linkCount: number;
  graphBounds: GraphBounds;
  graphBbox:
    | {
        x?: [number, number];
        y?: [number, number];
        z?: [number, number];
      }
    | null;
  camera:
    | {
        x?: number;
        y?: number;
        z?: number;
        near?: number;
        far?: number;
      }
    | null;
  sceneChildCount: number | null;
  viewport: GraphViewportSize;
};

function useElementSize() {
  const elementRef = useRef<HTMLDivElement | null>(null);
  const [size, setSize] = useState<GraphViewportSize>({ width: 0, height: 0 });

  useEffect(() => {
    const element = elementRef.current;
    if (!element) {
      return;
    }

    const updateSize = () => {
      const nextWidth = Math.max(0, Math.floor(element.clientWidth));
      const nextHeight = Math.max(0, Math.floor(element.clientHeight));
      setSize((current) => {
        if (current.width === nextWidth && current.height === nextHeight) {
          return current;
        }
        return { width: nextWidth, height: nextHeight };
      });
    };

    updateSize();
    const resizeObserver = new ResizeObserver(updateSize);
    resizeObserver.observe(element);
    window.addEventListener("resize", updateSize);

    return () => {
      resizeObserver.disconnect();
      window.removeEventListener("resize", updateSize);
    };
  }, []);

  return { elementRef, size };
}

function parseNumericInput(value: string, fallback: number): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function rounded(value: number | null | undefined, digits = 2): string {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return "n/a";
  }
  return value.toFixed(digits);
}

function computeGraphBounds(nodes: ForceGraphNode[]): GraphBounds {
  if (!nodes.length) {
    return {
      center: { x: 0, y: 0, z: 0 },
      maxSpan: 180,
    };
  }

  const xs = nodes.map((node) => node.x ?? 0);
  const ys = nodes.map((node) => node.y ?? 0);
  const zs = nodes.map((node) => node.z ?? 0);
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const minY = Math.min(...ys);
  const maxY = Math.max(...ys);
  const minZ = Math.min(...zs);
  const maxZ = Math.max(...zs);

  return {
    center: {
      x: (minX + maxX) / 2,
      y: (minY + maxY) / 2,
      z: (minZ + maxZ) / 2,
    },
    maxSpan: Math.max(maxX - minX, maxY - minY, maxZ - minZ, 180),
  };
}

function computePlaneBounds(projection: MapProjectionContext): GraphPlaneBounds {
  const corners = [
    projectMapLonLatToPlane(-180, 90, projection),
    projectMapLonLatToPlane(180, 90, projection),
    projectMapLonLatToPlane(-180, -90, projection),
    projectMapLonLatToPlane(180, -90, projection),
  ];
  const xs = corners.map((corner) => corner.x);
  const zs = corners.map((corner) => corner.z);
  const minX = Math.min(...xs) - MAP_PLANE_PADDING;
  const maxX = Math.max(...xs) + MAP_PLANE_PADDING;
  const minZ = Math.min(...zs) - MAP_PLANE_PADDING;
  const maxZ = Math.max(...zs) + MAP_PLANE_PADDING;

  return {
    minX,
    maxX,
    minZ,
    maxZ,
    width: maxX - minX,
    depth: maxZ - minZ,
    centerX: (minX + maxX) / 2,
    centerZ: (minZ + maxZ) / 2,
  };
}

function computeMapProjectionContext(nodes: TropeSequenceGraphNode[]): MapProjectionContext {
  const anchorNodes = nodes.filter(
    (node) => node.kind === "story_anchor" && typeof node.lon === "number" && typeof node.lat === "number",
  );
  if (!anchorNodes.length) {
    return {
      centerLon: 0,
      centerLat: 0,
      scale: MAP_PROJECTION_SCALE,
    };
  }

  const centerLon =
    anchorNodes.reduce((sum, node) => sum + (node.lon ?? 0), 0) /
    Math.max(anchorNodes.length, 1);
  const centerLat =
    anchorNodes.reduce((sum, node) => sum + (node.lat ?? 0), 0) /
    Math.max(anchorNodes.length, 1);

  return {
    centerLon,
    centerLat,
    scale: MAP_PROJECTION_SCALE,
  };
}

function projectMapLonLatToPlane(
  lon: number,
  lat: number,
  projection: MapProjectionContext,
): { x: number; z: number } {
  return {
    x: (lon - projection.centerLon) * projection.scale,
    z: (projection.centerLat - lat) * projection.scale,
  };
}

type CoastPolyline = Array<[lon: number, lat: number]>;

const MAP_COASTLINES: CoastPolyline[] = [
  [
    [-168, 72],
    [-150, 70],
    [-136, 63],
    [-125, 55],
    [-122, 48],
    [-118, 34],
    [-110, 24],
    [-97, 18],
    [-90, 20],
    [-83, 25],
    [-81, 31],
    [-75, 40],
    [-65, 47],
    [-60, 53],
    [-54, 59],
    [-45, 66],
    [-28, 73],
    [-18, 66],
    [-10, 58],
    [-8, 50],
    [-5, 44],
    [2, 41],
    [12, 43],
    [22, 38],
    [31, 31],
    [35, 24],
    [43, 14],
    [51, 12],
    [59, 23],
    [70, 31],
    [78, 24],
    [89, 22],
    [98, 15],
    [108, 21],
    [120, 23],
    [132, 33],
    [141, 43],
    [150, 52],
    [160, 59],
    [171, 63],
  ],
  [
    [-82, 12],
    [-78, 5],
    [-76, -4],
    [-74, -15],
    [-70, -25],
    [-66, -35],
    [-60, -47],
    [-52, -54],
    [-45, -50],
    [-41, -40],
    [-38, -28],
    [-42, -12],
    [-49, -2],
    [-58, 6],
    [-70, 11],
    [-78, 13],
  ],
  [
    [-18, 36],
    [-6, 35],
    [8, 37],
    [18, 33],
    [28, 31],
    [38, 23],
    [45, 12],
    [50, 4],
    [49, -8],
    [43, -18],
    [34, -26],
    [23, -33],
    [12, -35],
    [2, -34],
    [-8, -28],
    [-15, -18],
    [-17, -4],
    [-10, 7],
    [-13, 16],
    [-17, 27],
    [-18, 36],
  ],
  [
    [112, -12],
    [116, -22],
    [124, -31],
    [134, -35],
    [145, -39],
    [153, -31],
    [153, -18],
    [146, -12],
    [136, -14],
    [127, -17],
    [118, -16],
    [112, -12],
  ],
  [
    [48, -13],
    [51, -17],
    [49, -23],
    [45, -24],
    [43, -18],
    [46, -14],
    [48, -13],
  ],
  [
    [166, -34],
    [173, -41],
    [175, -46],
    [170, -45],
    [167, -40],
    [166, -34],
  ],
];

function buildMapTexture(
  planeBounds: GraphPlaneBounds,
  projection: MapProjectionContext,
): CanvasTexture {
  const canvas = document.createElement("canvas");
  canvas.width = 2048;
  canvas.height = 1024;

  const context = canvas.getContext("2d");
  if (!context) {
    return new CanvasTexture(canvas);
  }

  context.fillStyle = MAP_WATER_COLOR;
  context.fillRect(0, 0, canvas.width, canvas.height);

  const xToCanvas = (value: number) => ((value - planeBounds.minX) / planeBounds.width) * canvas.width;
  const zToCanvas = (value: number) => ((planeBounds.maxZ - value) / planeBounds.depth) * canvas.height;

  context.fillStyle = MAP_PLANE_COLOR;
  context.fillRect(0, 0, canvas.width, canvas.height);
  context.fillStyle = MAP_WATER_COLOR;
  context.fillRect(20, 20, canvas.width - 40, canvas.height - 40);

  context.strokeStyle = MAP_GRATICULE_COLOR;
  context.lineWidth = 1;
  context.setLineDash([10, 12]);
  for (let lon = -150; lon <= 150; lon += 30) {
    const projected = projectMapLonLatToPlane(lon, projection.centerLat, projection);
    const x = xToCanvas(projected.x);
    context.beginPath();
    context.moveTo(x, 0);
    context.lineTo(x, canvas.height);
    context.stroke();
  }
  for (let lat = -60; lat <= 60; lat += 30) {
    const projected = projectMapLonLatToPlane(projection.centerLon, lat, projection);
    const z = zToCanvas(projected.z);
    context.beginPath();
    context.moveTo(0, z);
    context.lineTo(canvas.width, z);
    context.stroke();
  }
  context.setLineDash([]);

  context.strokeStyle = MAP_COAST_COLOR;
  context.lineWidth = 2.5;
  MAP_COASTLINES.forEach((polyline) => {
    context.beginPath();
    polyline.forEach(([lon, lat], index) => {
      const projected = projectMapLonLatToPlane(lon, lat, projection);
      const x = xToCanvas(projected.x);
      const z = zToCanvas(projected.z);
      if (index === 0) {
        context.moveTo(x, z);
      } else {
        context.lineTo(x, z);
      }
    });
    context.stroke();
  });

  context.strokeStyle = MAP_GRATICULE_COLOR;
  context.lineWidth = 8;
  context.strokeRect(8, 8, canvas.width - 16, canvas.height - 16);

  const texture = new CanvasTexture(canvas);
  texture.needsUpdate = true;
  return texture;
}

function wrapStoryTitle(text: string, maxCharsPerLine = 26, maxLines = 2): string[] {
  const words = text.trim().split(/\s+/).filter(Boolean);
  if (!words.length) {
    return ["Untitled story"];
  }

  const lines: string[] = [];
  let currentLine = "";

  for (const word of words) {
    const candidate = currentLine ? `${currentLine} ${word}` : word;
    if (candidate.length <= maxCharsPerLine) {
      currentLine = candidate;
      continue;
    }
    if (currentLine) {
      lines.push(currentLine);
    }
    currentLine = word;
    if (lines.length === maxLines - 1) {
      break;
    }
  }

  if (currentLine && lines.length < maxLines) {
    lines.push(currentLine);
  }

  const consumedWords = lines.join(" ").split(/\s+/).filter(Boolean).length;
  if (consumedWords < words.length && lines.length) {
    lines[lines.length - 1] = `${lines[lines.length - 1].replace(/[. ]+$/u, "")}…`;
  }

  return lines.slice(0, maxLines);
}

function createAnchorNodeObject(node: ForceGraphNode): Object3D {
  const group = new Group();

  const dot = new Mesh(
    new SphereGeometry(ANCHOR_DOT_RADIUS, 18, 18),
    new MeshLambertMaterial({ color: "#172c33" }),
  );
  dot.position.y = ANCHOR_DOT_RADIUS;
  group.add(dot);

  const lines = wrapStoryTitle(node.story_title);
  const canvas = document.createElement("canvas");
  canvas.width = 768;
  canvas.height = 192;
  const context = canvas.getContext("2d");
  if (context) {
    context.clearRect(0, 0, canvas.width, canvas.height);
    context.fillStyle = "rgba(244, 239, 230, 0.94)";
    context.strokeStyle = "rgba(23, 44, 51, 0.28)";
    context.lineWidth = 4;
    context.beginPath();
    context.roundRect(12, 18, canvas.width - 24, canvas.height - 36, 22);
    context.fill();
    context.stroke();
    context.fillStyle = "#172c33";
    context.font = "600 42px Avenir Next, sans-serif";
    context.textAlign = "center";
    context.textBaseline = "middle";
    const lineHeight = 50;
    const startY = canvas.height / 2 - ((lines.length - 1) * lineHeight) / 2;
    lines.forEach((line, index) => {
      context.fillText(line, canvas.width / 2, startY + index * lineHeight);
    });
  }

  const texture = new CanvasTexture(canvas);
  texture.needsUpdate = true;
  const sprite = new Sprite(
    new SpriteMaterial({
      map: texture,
      transparent: true,
      depthWrite: false,
    }),
  );
  sprite.scale.set(68, 17, 1);
  sprite.position.set(0, ANCHOR_LABEL_ELEVATION, 0);
  group.add(sprite);

  return group;
}

function createOccurrenceNodeObject(node: ForceGraphNode, selectedTropeId: string): Object3D {
  return new Mesh(
    new SphereGeometry(nodeSize(node, selectedTropeId), 18, 18),
    new MeshLambertMaterial({ color: nodeColor(node, selectedTropeId) }),
  );
}

function createNodeObject(node: ForceGraphNode, selectedTropeId: string): Object3D {
  if (node.kind === "story_anchor") {
    return createAnchorNodeObject(node);
  }
  return createOccurrenceNodeObject(node, selectedTropeId);
}

function toForceGraphData(response: TropeSequenceGraphResponse): ForceGraphData {
  return {
    nodes: response.nodes.map((node) => {
      const geoX = node.kind === "story_anchor" ? (node.x ?? 0) : (node.anchor_x ?? node.x ?? 0);
      const geoZ = node.kind === "story_anchor" ? (node.y ?? 0) : (node.anchor_y ?? node.y ?? 0);

      if (node.kind === "story_anchor") {
        return {
          ...node,
          geo_x: geoX,
          geo_z: geoZ,
          vertical_target_y: 0,
          x: geoX,
          y: 0,
          z: geoZ,
          fx: geoX,
          fy: 0,
          fz: geoZ,
        };
      }

      const verticalTargetY = OCCURRENCE_BASE_HEIGHT + (node.target_z ?? node.z ?? 0);
      return {
        ...node,
        geo_x: geoX,
        geo_z: geoZ,
        vertical_target_y: verticalTargetY,
        x: geoX,
        y: verticalTargetY,
        z: geoZ,
        fx: undefined,
        fy: undefined,
        fz: undefined,
      };
    }) as GraphLibNode[],
    links: response.links.map((link) => ({
      ...link,
      similarity: link.similarity ?? undefined,
    })) as GraphLibLink[],
  };
}

function formatNodeLabel(node: ForceGraphNode): string {
  if (node.kind === "story_anchor") {
    return `${node.story_title}\nAnchor\n${rounded(node.lat, 4)}, ${rounded(node.lon, 4)}`;
  }

  return `${node.trope_text}\n${node.story_title}\nSequence ${((node.sequence_index ?? 0) + 1).toString()}`;
}

function nodeColor(node: ForceGraphNode, selectedTropeId: string): string {
  if (node.kind === "story_anchor") {
    return "#172c33";
  }
  if (node.trope_id === selectedTropeId) {
    return "#d25d34";
  }
  if ((node.selected_similarity_score ?? 0) > 0) {
    return "#1f7177";
  }
  return "#95a7ab";
}

function nodeSize(node: ForceGraphNode, selectedTropeId: string): number {
  if (node.kind === "story_anchor") {
    return 2.6;
  }
  if (node.trope_id === selectedTropeId) {
    return 7.5;
  }
  if ((node.selected_similarity_score ?? 0) > 0) {
    return 5.4;
  }
  return 3.8;
}

function linkColor(link: ForceGraphLink): string {
  if (link.kind === "sequence") {
    return "rgba(27, 93, 99, 0.72)";
  }
  if (link.kind === "anchor") {
    return "rgba(23, 44, 51, 0.20)";
  }
  return "rgba(59, 104, 123, 0.24)";
}

function linkWidth(link: ForceGraphLink): number {
  if (link.kind === "sequence") {
    return 2.1;
  }
  if (link.kind === "anchor") {
    return 0.9;
  }
  return 1.1;
}

function detailValue(label: string, value: string) {
  return (
    <div className="detail-row" key={label}>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}

export function ExperimentalTropeForce3DPage() {
  const graphRef = useRef<ForceGraphMethods<GraphLibNode, GraphLibLink> | undefined>(undefined);
  const { elementRef, size } = useElementSize();
  const hashSearch = useHashSearch();
  const hashParams = new URLSearchParams(hashSearch);
  const routeQuery = hashParams.get("query")?.trim() ?? "";
  const autoBuildRequested = hashParams.get("auto_build") === "1";
  const debugRequested = hashParams.get("debug") === "1";
  const renderMode = hashParams.get("render_mode") === "simple" ? "simple" : "tuned";
  const hasAutoBuiltRef = useRef(false);
  const hasSeenEngineTickRef = useRef(false);

  const [query, setQuery] = useState(routeQuery || DEFAULT_QUERY);
  const [similarityThreshold, setSimilarityThreshold] = useState(DEFAULT_SIMILARITY_THRESHOLD.toString());
  const [maxStories, setMaxStories] = useState(DEFAULT_MAX_STORIES.toString());
  const [maxLinksPerNode, setMaxLinksPerNode] = useState(DEFAULT_MAX_LINKS_PER_NODE.toString());
  const [verticalSpacing, setVerticalSpacing] = useState(DEFAULT_VERTICAL_SPACING.toString());

  const [geographicStrength, setGeographicStrength] = useState(DEFAULT_GEOGRAPHIC_STRENGTH.toString());
  const [semanticStrength, setSemanticStrength] = useState(DEFAULT_SEMANTIC_STRENGTH.toString());
  const [collisionRadius, setCollisionRadius] = useState(DEFAULT_COLLISION_RADIUS.toString());

  const [graphResponse, setGraphResponse] = useState<TropeSequenceGraphResponse | null>(null);
  const [forceGraphData, setForceGraphData] = useState<ForceGraphData | null>(null);
  const [selectedNode, setSelectedNode] = useState<TropeSequenceGraphNode | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [webglAvailable, setWebglAvailable] = useState<boolean>(true);
  const [graphCanReheat, setGraphCanReheat] = useState(false);
  const [debugSnapshot, setDebugSnapshot] = useState<DebugSnapshot | null>(null);

  const selectedTropeId = graphResponse?.layout_basis.selected_trope.id ?? "";
  const occurrenceNodes = graphResponse?.nodes.filter((node) => node.kind === "trope_occurrence") ?? [];
  const anchorNodes = graphResponse?.nodes.filter((node) => node.kind === "story_anchor") ?? [];
  const semanticLinks = graphResponse?.links.filter((link) => link.kind === "semantic") ?? [];
  const sequenceLinks = graphResponse?.links.filter((link) => link.kind === "sequence") ?? [];
  const anchorLinks = graphResponse?.links.filter((link) => link.kind === "anchor") ?? [];
  const distinctStoryCount = graphResponse ? new Set(graphResponse.nodes.map((node) => node.story_id)).size : 0;

  const parsedGeographicStrength = parseNumericInput(geographicStrength, DEFAULT_GEOGRAPHIC_STRENGTH);
  const parsedSemanticStrength = parseNumericInput(semanticStrength, DEFAULT_SEMANTIC_STRENGTH);
  const parsedCollisionRadius = parseNumericInput(collisionRadius, DEFAULT_COLLISION_RADIUS);
  const parsedVerticalSpacing = parseNumericInput(verticalSpacing, DEFAULT_VERTICAL_SPACING);

  const warnings = [...(graphResponse?.warnings ?? [])];
  if (graphResponse?.layout_basis.sequence_axis_label === "assignment order") {
    warnings.unshift(
      "Vertical stacking reflects assignment order, which may differ from narrative order."
    );
  }
  const projectionContext = computeMapProjectionContext(graphResponse?.nodes ?? []);
  const graphBounds = computeGraphBounds(forceGraphData?.nodes ?? []);
  const planeBounds = computePlaneBounds(projectionContext);

  useEffect(() => {
    if (!query && routeQuery) {
      setQuery(routeQuery);
    }
  }, [query, routeQuery]);

  useEffect(() => {
    const canvas = document.createElement("canvas");
    const supportsWebgl = Boolean(
      canvas.getContext("webgl") ||
        canvas.getContext("webgl2") ||
        canvas.getContext("experimental-webgl"),
    );
    setWebglAvailable(supportsWebgl);
  }, []);

  useEffect(() => {
    if (!forceGraphData || renderMode !== "tuned") {
      return;
    }
    let cancelled = false;
    let timerId: number | undefined;
    let cameraTimerId: number | undefined;

    const applyGraphTuning = (attempt = 0) => {
      if (cancelled) {
        return;
      }

      const graph = graphRef.current;
      if (!graph) {
        if (attempt < 20) {
          timerId = window.setTimeout(() => applyGraphTuning(attempt + 1), 120);
        }
        return;
      }

      const renderer = graph.renderer();
      const camera = graph.camera() as
        | {
            near?: number;
            far?: number;
            updateProjectionMatrix?: () => void;
          }
        | undefined;
      const scene = graph.scene() as { children?: unknown[] } | undefined;
      const chargeForce = graph.d3Force("charge") as { strength: (value: number) => void } | undefined;
      const linkForce = graph.d3Force("link") as LinkForceApi | undefined;

      const ready = Boolean(renderer && camera && chargeForce && linkForce && scene);
      if (!ready) {
        if (attempt < 20) {
          timerId = window.setTimeout(() => applyGraphTuning(attempt + 1), 120);
        }
        return;
      }

      const readyLinkForce = linkForce!;
      const readyCamera = camera!;

      graph.lights?.([
        new AmbientLight(0xffffff, Math.PI),
        new DirectionalLight(0xffffff, 0.75 * Math.PI),
      ]);
      renderer?.setClearColor(GRAPH_BACKGROUND, 1);

      chargeForce?.strength(-46);
      graph.d3Force(
        "geo-x",
        forceX((node: ForceGraphNode) => node.geo_x ?? node.x ?? 0).strength(
          (node: ForceGraphNode) => (node.kind === "trope_occurrence" ? parsedGeographicStrength : 0),
        ),
      );
      graph.d3Force(
        "vertical-y",
        forceY((node: ForceGraphNode) => node.vertical_target_y ?? node.y ?? 0).strength(
          (node: ForceGraphNode) => (node.kind === "trope_occurrence" ? 0.9 : 0),
        ),
      );
      graph.d3Force(
        "geo-z",
        forceZ((node: ForceGraphNode) => node.geo_z ?? node.z ?? 0).strength(
          (node: ForceGraphNode) => (node.kind === "trope_occurrence" ? parsedGeographicStrength : 0),
        ),
      );
      graph.d3Force(
        "collision",
        forceCollide((node: ForceGraphNode) =>
          node.kind === "story_anchor"
            ? Math.max(ANCHOR_DOT_RADIUS * 1.3, parsedCollisionRadius * 0.48)
            : parsedCollisionRadius,
        ).strength(0.9),
      );

      if (typeof readyLinkForce.distance === "function" && typeof readyLinkForce.strength === "function") {
        readyLinkForce.distance((link: ForceGraphLink) => {
          if (link.kind === "anchor") {
            return 0;
          }
          if (link.kind === "sequence") {
            return Math.max(18, parsedVerticalSpacing);
          }
          return Math.max(42, parsedVerticalSpacing * 1.6);
        });
        readyLinkForce.strength((link: ForceGraphLink) => {
          if (link.kind === "anchor") {
            return link.strength * parsedGeographicStrength;
          }
          if (link.kind === "sequence") {
            return 0.55;
          }
          return link.strength * parsedSemanticStrength;
        });
        if (typeof readyLinkForce.iterations === "function") {
          readyLinkForce.iterations(2);
        }
      }

      readyCamera.near = 1;
      readyCamera.far = Math.max(6000, graphBounds.maxSpan * 30);
      readyCamera.updateProjectionMatrix?.();

      if (graphCanReheat) {
        graph.d3ReheatSimulation();
      }
      graph.refresh();

      cameraTimerId = window.setTimeout(() => {
        if (cancelled) {
          return;
        }
        const cameraDistance = Math.max(260, graphBounds.maxSpan * 1.25);
        graph.cameraPosition(
          {
            x: graphBounds.center.x + cameraDistance * 0.42,
            y: Math.max(120, graphBounds.center.y + cameraDistance * 0.56),
            z: graphBounds.center.z + cameraDistance * 1.06,
          },
          {
            x: graphBounds.center.x,
            y: Math.max(18, graphBounds.center.y * 0.45),
            z: graphBounds.center.z,
          },
          700,
        );
      }, 180);
    };

    applyGraphTuning();

    return () => {
      cancelled = true;
      if (timerId) {
        window.clearTimeout(timerId);
      }
      if (cameraTimerId) {
        window.clearTimeout(cameraTimerId);
      }
    };
  }, [
    forceGraphData,
    parsedCollisionRadius,
    graphCanReheat,
    parsedGeographicStrength,
    parsedSemanticStrength,
    parsedVerticalSpacing,
    renderMode,
  ]);

  useEffect(() => {
    if (!forceGraphData) {
      return;
    }

    let cancelled = false;
    let timerId: number | undefined;

    const attachMapPlane = (attempt = 0) => {
      if (cancelled) {
        return;
      }

      const graph = graphRef.current;
      if (!graph) {
        if (attempt < 20) {
          timerId = window.setTimeout(() => attachMapPlane(attempt + 1), 120);
        }
        return;
      }

      const scene = graph.scene();
      if (!scene) {
        if (attempt < 20) {
          timerId = window.setTimeout(() => attachMapPlane(attempt + 1), 120);
        }
        return;
      }

      const existingPlane = scene.getObjectByName(MAP_PLANE_NAME);
      if (existingPlane instanceof Mesh) {
        const existingMaterial = existingPlane.material;
        if (Array.isArray(existingMaterial)) {
          existingMaterial.forEach((material) => material.dispose());
        } else {
          existingMaterial.dispose();
          if ("map" in existingMaterial && existingMaterial.map) {
            existingMaterial.map.dispose();
          }
        }
        existingPlane.geometry.dispose();
        scene.remove(existingPlane);
      }

      const mapTexture = buildMapTexture(planeBounds, projectionContext);
      const planeMaterial = new MeshBasicMaterial({
        color: MAP_PLANE_COLOR,
        map: mapTexture,
      });
      const planeMesh = new Mesh(
        new PlaneGeometry(planeBounds.width, planeBounds.depth),
        planeMaterial,
      );
      planeMesh.name = MAP_PLANE_NAME;
      planeMesh.position.set(planeBounds.centerX, MAP_PLANE_ELEVATION, planeBounds.centerZ);
      planeMesh.rotation.x = -Math.PI / 2;
      planeMesh.raycast = () => null;
      scene.add(planeMesh);
      graph.refresh();
    };

    attachMapPlane();

    return () => {
      cancelled = true;
      if (timerId) {
        window.clearTimeout(timerId);
      }
    };
  }, [forceGraphData, planeBounds, projectionContext]);

  useEffect(() => {
    const graph = graphRef.current;
    if (!graph || !forceGraphData || !debugRequested) {
      setDebugSnapshot(null);
      return;
    }

    const camera = graph.camera() as
      | {
          position?: { x?: number; y?: number; z?: number };
          near?: number;
          far?: number;
        }
      | undefined;
    const graphBbox = graph.getGraphBbox?.() as
      | {
          x?: [number, number];
          y?: [number, number];
          z?: [number, number];
        }
      | null
      | undefined;
    const scene = graph.scene() as { children?: unknown[] } | undefined;

    setDebugSnapshot({
      graphRefReady: true,
      forceGraphDataReady: true,
      renderMode,
      nodeCount: forceGraphData.nodes.length,
      linkCount: forceGraphData.links.length,
      graphBounds,
      graphBbox: graphBbox ?? null,
      camera: camera?.position
        ? {
            x: camera.position.x,
            y: camera.position.y,
            z: camera.position.z,
            near: camera.near,
            far: camera.far,
          }
        : null,
      sceneChildCount: scene?.children?.length ?? null,
      viewport: size,
    });
  }, [debugRequested, forceGraphData, graphBounds, renderMode, size]);

  const runBuildGraph = async (nextQuery = query) => {
    setBusy(true);
    setError(null);
    setSelectedNode(null);
    hasSeenEngineTickRef.current = false;
    setGraphCanReheat(false);

    try {
      const response = await buildTropeSequenceGraph({
        query: nextQuery,
        similarity_threshold: parseNumericInput(similarityThreshold, DEFAULT_SIMILARITY_THRESHOLD),
        max_stories: parseNumericInput(maxStories, DEFAULT_MAX_STORIES),
        max_links_per_node: parseNumericInput(maxLinksPerNode, DEFAULT_MAX_LINKS_PER_NODE),
        vertical_spacing: parseNumericInput(verticalSpacing, DEFAULT_VERTICAL_SPACING),
      });
      setGraphResponse(response);
      setForceGraphData(toForceGraphData(response));
      console.info("[TropeForce3D] graph built", {
        renderMode,
        nodeCount: response.nodes.length,
        linkCount: response.links.length,
        warningCount: response.warnings.length,
      });
    } catch (caughtError) {
      setError(getErrorMessage(caughtError));
      setGraphResponse(null);
      setForceGraphData(null);
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    if (!autoBuildRequested || hasAutoBuiltRef.current || !routeQuery || busy) {
      return;
    }
    hasAutoBuiltRef.current = true;
    void runBuildGraph(routeQuery);
  }, [autoBuildRequested, busy, routeQuery]);

  const handleBuildGraph = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    await runBuildGraph(query);
  };

  return (
    <div className="page-stack">
      <section className="panel panel-experimental">
        <div className="panel-header">
          <div className="stack">
            <p className="eyebrow">Experimental visualization</p>
            <div className="stack experimental-heading-copy">
              <h2>Trope sequence force 3D</h2>
              <p className="muted">
                Geographic anchors stay fixed while trope occurrences can drift slightly under semantic attraction.
              </p>
            </div>
          </div>
          {graphResponse ? (
            <div className="stat-card stat-card-highlight">
              <span className="stat-label">Selected trope</span>
              <strong>{graphResponse.layout_basis.selected_trope.text}</strong>
              <span className="muted">Auto-selected from the current query unless explicitly provided.</span>
            </div>
          ) : null}
        </div>

        <form className="stack" onSubmit={handleBuildGraph}>
          <div className="experimental-control-grid">
            <label className="field experimental-field-span">
              <span>Query</span>
              <input
                className="input"
                onChange={(event) => setQuery(event.target.value)}
                placeholder="someone gets inside a coconut and drifts at sea"
                type="text"
                value={query}
              />
            </label>

            <label className="field">
              <span>Similarity threshold</span>
              <input
                className="range-input"
                max="0.98"
                min="0.4"
                onChange={(event) => setSimilarityThreshold(event.target.value)}
                step="0.01"
                type="range"
                value={similarityThreshold}
              />
              <span className="muted">{rounded(parseNumericInput(similarityThreshold, DEFAULT_SIMILARITY_THRESHOLD))}</span>
            </label>

            <label className="field">
              <span>Max stories</span>
              <input
                className="input"
                min="1"
                onChange={(event) => setMaxStories(event.target.value)}
                step="1"
                type="number"
                value={maxStories}
              />
            </label>

            <label className="field">
              <span>Max semantic links per node</span>
              <input
                className="input"
                min="0"
                onChange={(event) => setMaxLinksPerNode(event.target.value)}
                step="1"
                type="number"
                value={maxLinksPerNode}
              />
            </label>

            <label className="field">
              <span>Vertical spacing</span>
              <input
                className="range-input"
                max="96"
                min="16"
                onChange={(event) => setVerticalSpacing(event.target.value)}
                step="2"
                type="range"
                value={verticalSpacing}
              />
              <span className="muted">{rounded(parseNumericInput(verticalSpacing, DEFAULT_VERTICAL_SPACING), 0)} units</span>
            </label>

            <div className="field">
              <span>Build graph</span>
              <button className="button" disabled={busy || !query.trim()} type="submit">
                {busy ? "Building..." : "Build graph"}
              </button>
            </div>
          </div>
        </form>
      </section>

      <section className="panel">
        <div className="panel-header">
          <div>
            <h3>Force tuning</h3>
            <p className="muted">
              These controls rebalance the live simulation without changing which stories are in the graph.
            </p>
          </div>
          <div className="legend-row">
            <span className="legend-item">
              <span className="legend-dot legend-dot-anchor-node" />
              anchor node
            </span>
            <span className="legend-item">
              <span className="legend-dot legend-dot-occurrence-node" />
              trope occurrence node
            </span>
            <span className="legend-item">
              <span className="legend-line legend-line-sequence" />
              sequence link
            </span>
            <span className="legend-item">
              <span className="legend-line legend-line-semantic" />
              semantic link
            </span>
            <span className="legend-item">
              <span className="legend-line legend-line-anchor-link" />
              geographic tension
            </span>
          </div>
        </div>

        <div className="experimental-force-grid">
          <label className="field">
            <span>Geographic anchoring strength</span>
            <input
              className="range-input"
              max="0.5"
              min="0.05"
              onChange={(event) => setGeographicStrength(event.target.value)}
              step="0.01"
              type="range"
              value={geographicStrength}
            />
            <span className="muted">{rounded(parsedGeographicStrength)}</span>
          </label>

          <label className="field">
            <span>Semantic strength</span>
            <input
              className="range-input"
              max="1.2"
              min="0"
              onChange={(event) => setSemanticStrength(event.target.value)}
              step="0.01"
              type="range"
              value={semanticStrength}
            />
            <span className="muted">{rounded(parsedSemanticStrength)}</span>
          </label>

          <label className="field">
            <span>Collision radius</span>
            <input
              className="range-input"
              max="28"
              min="4"
              onChange={(event) => setCollisionRadius(event.target.value)}
              step="1"
              type="range"
              value={collisionRadius}
            />
            <span className="muted">{rounded(parsedCollisionRadius, 0)} units</span>
          </label>
        </div>
      </section>

      {error ? <section className="notice notice-error">{error}</section> : null}

      {!webglAvailable ? (
        <section className="notice notice-error">
          This browser does not appear to support WebGL for the 3D graph, so the viewport may render as a black box.
        </section>
      ) : null}

      {warnings.length ? (
        <section className="notice notice-warning stack">
          <strong className="notice-title">Graph warnings</strong>
          {warnings.map((warning) => (
            <p key={warning}>{warning}</p>
          ))}
        </section>
      ) : null}

      {debugRequested ? (
        <section className="panel">
          <h3>Debug snapshot</h3>
          <pre className="json-block">
            {JSON.stringify(
              debugSnapshot ?? {
                graphRefReady: Boolean(graphRef.current),
                forceGraphDataReady: Boolean(forceGraphData),
                renderMode,
                waiting: true,
              },
              null,
              2,
            )}
          </pre>
        </section>
      ) : null}

      <section className="experimental-layout">
        <div className="panel experimental-graph-panel">
          <div className="graph-meta-grid">
            <article className="stat-card">
              <span className="stat-label">Stories</span>
              <strong>{distinctStoryCount}</strong>
            </article>
            <article className="stat-card">
              <span className="stat-label">Occurrences</span>
              <strong>{occurrenceNodes.length}</strong>
            </article>
            <article className="stat-card">
              <span className="stat-label">Sequence links</span>
              <strong>{sequenceLinks.length}</strong>
            </article>
            <article className="stat-card">
              <span className="stat-label">Semantic links</span>
              <strong>{semanticLinks.length}</strong>
            </article>
          </div>

          <div className="experimental-viewport" ref={elementRef}>
            {forceGraphData && size.width > 0 && size.height > 0 ? (
              <ForceGraph3D<ForceGraphNode, ForceGraphLink>
                backgroundColor={GRAPH_BACKGROUND}
                enableNodeDrag={false}
                graphData={forceGraphData}
                height={size.height}
                cooldownTicks={120}
                warmupTicks={40}
                linkColor={(link: GraphLibLink) => linkColor(link as ForceGraphLink)}
                linkCurvature={(link: GraphLibLink) => ((link as ForceGraphLink).kind === "semantic" ? 0.08 : 0)}
                linkLabel={(link: GraphLibLink) =>
                  (link as ForceGraphLink).kind === "semantic"
                    ? `semantic similarity ${rounded((link as ForceGraphLink).similarity)}`
                    : (link as ForceGraphLink).kind
                }
                linkWidth={(link: GraphLibLink) => linkWidth(link as ForceGraphLink)}
                nodeLabel={(node: GraphLibNode) => formatNodeLabel(node as ForceGraphNode)}
                nodeThreeObject={(node: GraphLibNode) => createNodeObject(node as ForceGraphNode, selectedTropeId)}
                onEngineTick={() => {
                  if (!hasSeenEngineTickRef.current) {
                    hasSeenEngineTickRef.current = true;
                    setGraphCanReheat(true);
                  }
                }}
                onNodeClick={(node: GraphLibNode) => setSelectedNode(node as TropeSequenceGraphNode)}
                ref={graphRef}
                showNavInfo={false}
                width={size.width}
              />
            ) : (
              <div className="experimental-placeholder">
                <p>
                  {busy
                    ? "Building the graph..."
                    : "Build a graph to inspect how fixed geography and semantic drift play against each other."}
                </p>
              </div>
            )}
          </div>
        </div>

        <aside className="panel experimental-side-panel">
          <div className="stack">
            <h3>Inspector</h3>
            <p className="muted">Click an anchor or trope occurrence node to inspect its metadata.</p>
          </div>

          {selectedNode ? (
            <div className="stack">
              <div className="card subdued">
                <div className="card-row">
                  <div>
                    <h3>{selectedNode.kind === "story_anchor" ? selectedNode.story_title : selectedNode.trope_text}</h3>
                    <p className="muted">{selectedNode.story_title}</p>
                  </div>
                  <span className="pill">{selectedNode.kind === "story_anchor" ? "anchor" : "occurrence"}</span>
                </div>
                <dl className="detail-list">
                  {detailValue("Story id", selectedNode.story_id)}
                  {detailValue("Source row", selectedNode.source_row_number?.toString() ?? "unknown")}
                  {detailValue("Latitude", rounded(selectedNode.lat, 4))}
                  {detailValue("Longitude", rounded(selectedNode.lon, 4))}
                  {detailValue("X / Y / Z", `${rounded(selectedNode.x)}, ${rounded(selectedNode.y)}, ${rounded(selectedNode.z)}`)}
                  {selectedNode.kind === "story_anchor"
                    ? detailValue("Occurrences", selectedNode.occurrence_count?.toString() ?? "0")
                    : null}
                  {selectedNode.kind === "trope_occurrence"
                    ? detailValue("Sequence index", ((selectedNode.sequence_index ?? 0) + 1).toString())
                    : null}
                  {selectedNode.kind === "trope_occurrence"
                    ? detailValue("Status", selectedNode.status ?? "unknown")
                    : null}
                  {selectedNode.kind === "trope_occurrence"
                    ? detailValue("Origin", selectedNode.origin ?? "unknown")
                    : null}
                  {selectedNode.kind === "trope_occurrence"
                    ? detailValue(
                        "Selected similarity",
                        rounded(selectedNode.selected_similarity_score),
                      )
                    : null}
                </dl>
              </div>
            </div>
          ) : graphResponse ? (
            <div className="card subdued">
              <h3>{graphResponse.layout_basis.selected_trope.text}</h3>
              <p className="muted">
                Current layout basis: {graphResponse.layout_basis.sequence_axis_label} with a threshold of{" "}
                {rounded(graphResponse.layout_basis.similarity_threshold)}.
              </p>
              <dl className="detail-list">
                {detailValue("Anchor nodes", anchorNodes.length.toString())}
                {detailValue("Occurrence nodes", occurrenceNodes.length.toString())}
                {detailValue("Anchor links", anchorLinks.length.toString())}
                {detailValue("Semantic links", semanticLinks.length.toString())}
              </dl>
            </div>
          ) : (
            <div className="card subdued">
              <p className="muted">
                No graph loaded yet. Start with a trope phrase, then inspect how vertical story chains drift under
                weak semantic attraction.
              </p>
            </div>
          )}
        </aside>
      </section>
    </div>
  );
}
