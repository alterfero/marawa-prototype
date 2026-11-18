import { useEffect, useId, useRef, useState, type PointerEvent } from "react";
import type { OntologyLink, OntologyNode, SemanticGraphLink, TropeOntology } from "../api/types";
import { useElementSize, useForceLayout } from "../components/useForceLayout";
import { linkKey, markerRadius, specificityLabel } from "./relationships";

type Point = { x: number; y: number };
type Camera = Point & { scale: number };
type Drag = { pointer: number; start: Point; camera: Camera; moved: boolean; version: number } & (
  | { kind: "pan" } | { kind: "move" | "link"; id: string; origin: Point }
);

function zoomAt(camera: Camera, factor: number, focus: Point) {
  const scale = Math.max(0.03, Math.min(4, camera.scale * factor));
  const ratio = scale / camera.scale;
  return { x: focus.x - (focus.x - camera.x) * ratio, y: focus.y - (focus.y - camera.y) * ratio, scale };
}

export function OntologyGraphCanvas({ graph, forces, selectedId, selectedEdge, canWrite, onSelectNode, onSelectEdge, onDrop }: {
  graph: TropeOntology; forces: SemanticGraphLink[]; selectedId: string | null; selectedEdge: OntologyLink | null;
  canWrite: boolean; onSelectNode: (id: string) => void; onSelectEdge: (link: OntologyLink | null) => void;
  onDrop: (source: OntologyNode, target: OntologyNode) => void;
}) {
  const [viewportRef, size] = useElementSize<HTMLDivElement>();
  const { liveNodes, nodesById, simulationRef, redraw } = useForceLayout(graph.tropes, forces, markerRadius, size);
  const svg = useRef<SVGSVGElement>(null);
  const drag = useRef<Drag | null>(null);
  const locatedId = useRef<string | null>(null);
  const [hovered, setHovered] = useState<string | null>(null);
  const [hoveredEdge, setHoveredEdge] = useState<{ source: string; target: string; kind: "ontology" | "similarity" } | null>(null);
  const [preview, setPreview] = useState<(Point & { source: string; target?: string }) | null>(null);
  const [camera, setCamera] = useState<Camera>({ x: 0, y: 0, scale: 1 });
  const arrowId = useId().replace(/:/g, "");
  const activeEdgeId = selectedEdge && linkKey(selectedEdge);
  const tooltipId = useId();
  const edgeSource = hoveredEdge && nodesById.get(hoveredEdge.source);
  const edgeTarget = hoveredEdge && nodesById.get(hoveredEdge.target);

  // Bring a catalogue selection into view, while keeping an on-screen pick in place.
  useEffect(() => {
    const n = selectedId && nodesById.get(selectedId);
    if (!n || locatedId.current === selectedId) return;
    locatedId.current = selectedId;
    setCamera(c => {
      const x = n.x * c.scale + c.x, y = n.y * c.scale + c.y;
      if (c.scale >= 0.65 && x >= n.radius && x <= size.width - n.radius && y >= n.radius && y <= size.height - n.radius) return c;
      const scale = Math.max(1, c.scale);
      return { x: size.width / 2 - n.x * scale, y: size.height / 2 - n.y * scale, scale };
    });
  }, [selectedId, nodesById, size]);

  useEffect(() => {
    const element = svg.current;
    if (!element) return;
    function wheel(e: globalThis.WheelEvent) {
      e.preventDefault();
      if (drag.current) return;
      const rect = element!.getBoundingClientRect();
      setCamera(c => zoomAt(c, e.deltaY > 0 ? 0.9 : 1.11, { x: e.clientX - rect.left, y: e.clientY - rect.top }));
    }
    element.addEventListener("wheel", wheel, { passive: false });
    return () => element.removeEventListener("wheel", wheel);
  }, []);

  function fit() {
    if (!liveNodes.length) return;
    let left = Infinity, right = -Infinity, top = Infinity, bottom = -Infinity;
    for (const n of liveNodes) {
      left = Math.min(left, n.x - n.radius); right = Math.max(right, n.x + n.radius);
      top = Math.min(top, n.y - n.radius); bottom = Math.max(bottom, n.y + n.radius);
    }
    const scale = Math.min(1.5, (size.width - 32) / (right - left), (size.height - 32) / (bottom - top));
    setCamera({ scale, x: (size.width - (left + right) * scale) / 2, y: (size.height - (top + bottom) * scale) / 2 });
  }

  function graphPoint(e: PointerEvent<SVGSVGElement>): Point {
    const rect = e.currentTarget.getBoundingClientRect();
    return { x: (e.clientX - rect.left - camera.x) / camera.scale, y: (e.clientY - rect.top - camera.y) / camera.scale };
  }

  function dropTarget(point: Point, source: string) {
    // SVG paints the last marker on top when two markers overlap.
    for (let i = liveNodes.length - 1; i >= 0; i--) {
      const n = liveNodes[i];
      if (n.id !== source && Math.hypot(n.x - point.x, n.y - point.y) <= n.radius) return n;
    }
  }

  function begin(e: PointerEvent<SVGSVGElement>) {
    if (drag.current || e.button !== 0 || !e.isPrimary) return;
    const target = e.target as Element;
    if (target.closest("[data-edge]")) return;
    const id = target.closest("[data-node]")?.getAttribute("data-node");
    const node = id && nodesById.get(id);
    const start = { x: e.clientX, y: e.clientY };
    const base = { pointer: e.pointerId, start, camera, moved: false, version: graph.version };
    drag.current = node
      ? { ...base, kind: e.shiftKey && canWrite ? "link" : "move", id: node.id, origin: { x: node.x, y: node.y } }
      : { ...base, kind: "pan" };
    if (node) simulationRef.current?.stop();
    e.currentTarget.focus({ preventScroll: true });
    e.currentTarget.setPointerCapture(e.pointerId);
  }

  function move(e: PointerEvent<SVGSVGElement>) {
    const d = drag.current;
    if (!d || d.pointer !== e.pointerId) return;
    const dx = e.clientX - d.start.x, dy = e.clientY - d.start.y;
    if (!d.moved && Math.hypot(dx, dy) < 3) return;
    d.moved = true;
    if (d.kind === "pan") {
      setCamera({ ...d.camera, x: d.camera.x + dx, y: d.camera.y + dy });
    } else if (d.kind === "link") {
      const point = graphPoint(e);
      setPreview({ ...point, source: d.id, target: dropTarget(point, d.id)?.id });
    } else {
      const n = nodesById.get(d.id);
      if (n) {
        n.x = d.origin.x + dx / d.camera.scale; n.y = d.origin.y + dy / d.camera.scale;
        redraw();
      }
    }
  }

  function end(e: PointerEvent<SVGSVGElement>, cancelled = false) {
    const d = drag.current;
    if (!d || d.pointer !== e.pointerId) return;
    drag.current = null;
    setPreview(null);
    if (d.kind !== "pan") simulationRef.current?.restart();
    if (cancelled || d.version !== graph.version) return;
    if (d.kind === "pan") {
      if (!d.moved) onSelectEdge(null);
    } else if (!d.moved) {
      onSelectNode(d.id);
    } else if (d.kind === "link" && canWrite) {
      const source = nodesById.get(d.id), target = dropTarget(graphPoint(e), d.id);
      if (source && target) onDrop(source, target);
    }
  }

  const previewSource = preview && nodesById.get(preview.source);
  const center = { x: size.width / 2, y: size.height / 2 };
  const labelNodes = liveNodes.filter(n =>
    n.id !== hovered && ((!selectedEdge && n.id === selectedId) || preview?.target === n.id));
  const hoveredNode = hovered && nodesById.get(hovered);
  if (hoveredNode && !preview) labelNodes.push(hoveredNode);
  return <div ref={viewportRef} className="semantic-graph-viewport ontology-viewport">
    <svg ref={svg} role="group" aria-label="Ontology graph canvas" tabIndex={0} className="semantic-graph-canvas"
      viewBox={`0 0 ${size.width} ${size.height}`} onPointerDown={begin} onPointerMove={move} onPointerUp={e => end(e)}
      onPointerCancel={e => end(e, true)} onLostPointerCapture={e => end(e, true)}
      onKeyDown={e => {
        if (e.key === "Escape") onSelectEdge(null);
      }}>
      <defs><marker id={arrowId} markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto" markerUnits="userSpaceOnUse">
        <path d="M0 0 L8 4 L0 8 Z" fill="context-stroke" /></marker></defs>
      <rect className="semantic-graph-background" width={size.width} height={size.height} />
      <g transform={`translate(${camera.x} ${camera.y}) scale(${camera.scale})`}>
        {forces.map(link => {
          const a = nodesById.get(link.source), b = nodesById.get(link.target);
          const segment = a && b && { x1: a.x, y1: a.y, x2: b.x, y2: b.y };
          return segment && <g key={`${link.source}:${link.target}`}
            onPointerEnter={() => setHoveredEdge({ source: link.source, target: link.target, kind: "similarity" })}
            onPointerLeave={() => setHoveredEdge(null)}>
            <line {...segment} className="ontology-edge-hit" />
            <line {...segment} className="ontology-similarity-line" />
          </g>;
        })}
        {graph.links.map(link => {
          const a = nodesById.get(link.child_id), b = nodesById.get(link.parent_id);
          if (!a || !b) return null;
          const id = linkKey(link);
          const distance = Math.hypot(b.x - a.x, b.y - a.y) || 1;
          const ux = (b.x - a.x) / distance, uy = (b.y - a.y) / distance;
          const segment = { x1: a.x + ux * a.radius, y1: a.y + uy * a.radius,
            x2: b.x - ux * (b.radius + 1), y2: b.y - uy * (b.radius + 1) };
          return <g key={id} data-edge={id} className={`ontology-edge ontology-edge-${link.status}`} role="button"
            aria-label={`${a.text} to broader trope ${b.text}, ${link.status} link`} aria-pressed={id === activeEdgeId} tabIndex={0}
            aria-describedby={hoveredEdge?.source === a.id && hoveredEdge?.target === b.id ? tooltipId : undefined}
            onPointerEnter={() => setHoveredEdge({ source: a.id, target: b.id, kind: "ontology" })}
            onPointerLeave={() => setHoveredEdge(null)}
            onFocus={() => setHoveredEdge({ source: a.id, target: b.id, kind: "ontology" })}
            onBlur={() => setHoveredEdge(null)}
            onClick={e => { e.currentTarget.focus({ preventScroll: true }); onSelectEdge(link); }}
            onKeyDown={e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onSelectEdge(link); } }}>
            <line {...segment} className="ontology-edge-hit" />
            <line {...segment} className="ontology-edge-line" markerEnd={`url(#${arrowId})`} />
          </g>;
        })}
        {liveNodes.map(n => {
          const x = n.x * camera.scale + camera.x, y = n.y * camera.scale + camera.y, r = n.radius * camera.scale;
          if (x + r < 0 || y + r < 0 || x - r > size.width || y - r > size.height) return null;
          const active = !selectedEdge && n.id === selectedId;
          const target = preview?.target === n.id;
          return <g key={n.id} data-node={n.id} role="button" tabIndex={0} aria-pressed={active}
            aria-label={`${n.text}, ${specificityLabel(n)}`} transform={`translate(${n.x} ${n.y})`}
            className={`semantic-graph-node ontology-marker ontology-level-${n.specificity_level}${target ? " ontology-drop-target" : ""}`}
            onPointerEnter={() => setHovered(n.id)} onPointerLeave={() => setHovered(null)}
            onKeyDown={e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onSelectNode(n.id); } }}>
            <circle r={n.radius} />
          </g>;
        })}
        {preview && previewSource && <g className="ontology-drag-preview" aria-hidden="true">
          <line x1={previewSource.x} y1={previewSource.y} x2={preview.x} y2={preview.y} />
          <circle cx={preview.x} cy={preview.y} r={previewSource.radius} />
        </g>}
        <g className="ontology-foreground-labels" aria-hidden="true" pointerEvents="none">
          {labelNodes.map(n => <text key={n.id} className="semantic-graph-label" textAnchor="middle"
            x={n.x} y={n.y + n.radius + 15}>{n.text}</text>)}
        </g>
      </g>
    </svg>
    {edgeSource && edgeTarget && !drag.current &&
      (hoveredEdge?.kind === "similarity" || graph.links.some(l => l.child_id === edgeSource.id && l.parent_id === edgeTarget.id)) &&
      <aside id={tooltipId} role="tooltip"
      className="semantic-graph-tooltip ontology-edge-tooltip">
      <small>{hoveredEdge?.kind === "ontology" ? "Ontology link · narrower → broader" : "Similarity connection"}</small>
      <div><strong>{edgeSource.text}</strong><span>{specificityLabel(edgeSource)}</span></div>
      <div><strong>{edgeTarget.text}</strong><span>{specificityLabel(edgeTarget)}</span></div>
    </aside>}
    <div className="semantic-graph-zoom-controls" role="group" aria-label="Graph zoom controls">
      <button className="semantic-graph-zoom-button" aria-label="Zoom in" onClick={() => setCamera(c => zoomAt(c, 1.25, center))}>+</button>
      <button className="semantic-graph-zoom-button" aria-label="Zoom out" onClick={() => setCamera(c => zoomAt(c, 0.8, center))}>−</button>
      <button className="semantic-graph-show-all-button" onClick={fit}>Fit graph</button>
    </div>
  </div>;
}
