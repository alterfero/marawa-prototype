import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { KeyboardEvent, PointerEvent } from "react";
import {
  boundsOf, fitCamera, layoutTmiTree, MAX_ZOOM, MIN_ZOOM, ROOT_RADIUS,
  visibleMarkers, zoomAt, type Camera, type Marker, type TmiModel,
} from "./layout";

interface Props {
  model: TmiModel;
  focusId: string | null;
  onFocus: (id: string | null) => void;
}

const countLabel = (count: number) => count.toLocaleString("en");

function wrapLabel(context: CanvasRenderingContext2D, text: string, width: number, maxLines: number): string[] {
  const words = text.split(/\s+/);
  const lines: string[] = [];
  let line = "";
  for (const word of words) {
    if (line && context.measureText(`${line} ${word}`).width > width) {
      lines.push(line);
      line = word;
    } else line = line ? `${line} ${word}` : word;
  }
  if (line) lines.push(line);
  const result = lines.slice(0, maxLines);
  if (lines.length > maxLines) result[maxLines - 1] += "…";
  return result.map((value) => {
    let clipped = value;
    while (clipped.length > 1 && context.measureText(clipped).width > width) clipped = clipped.slice(0, -2) + "…";
    return clipped;
  });
}

export function TmiCanvas({ model, focusId, onFocus }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [size, setSize] = useState({ width: 800, height: 740 });
  const [camera, setCamera] = useState<Camera>({ x: 400, y: 370, scale: 0.19 });
  const cameraRef = useRef(camera);
  const [radius, setRadius] = useState(32);
  const [hover, setHover] = useState<{ marker: Marker; x: number; y: number } | null>(null);
  const [markerCount, setMarkerCount] = useState(0);
  const [clusterCount, setClusterCount] = useState(0);
  const [fullscreen, setFullscreen] = useState(false);
  const markersRef = useRef<Marker[]>([]);
  const animation = useRef(0);
  const pointers = useRef(new Map<number, { x: number; y: number }>());
  const dragStart = useRef({ x: 0, y: 0, moved: false });
  const layout = useMemo(() => layoutTmiTree(model, focusId), [model, focusId]);
  const positions = useMemo(() => new Map(layout.map((point) => [point.node.id, point])), [layout]);
  const defaultScale = Math.max(300, Math.min(size.width, size.height) * 0.35) / ROOT_RADIUS;

  const moveCamera = useCallback((next: Camera) => {
    cameraRef.current = next;
    setCamera(next);
  }, []);

  const flyTo = useCallback((next: Camera) => {
    cancelAnimationFrame(animation.current);
    setHover(null);
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      moveCamera(next);
      return;
    }
    const start = cameraRef.current;
    const started = performance.now();
    function tick(now: number) {
      const progress = Math.min(1, (now - started) / 480);
      const eased = 1 - Math.pow(1 - progress, 3);
      moveCamera({ x: start.x + (next.x - start.x) * eased, y: start.y + (next.y - start.y) * eased,
        scale: Math.exp(Math.log(start.scale) + (Math.log(next.scale) - Math.log(start.scale)) * eased) });
      if (progress < 1) animation.current = requestAnimationFrame(tick);
    }
    animation.current = requestAnimationFrame(tick);
  }, [moveCamera]);

  const fit = useCallback(() => flyTo(fitCamera(boundsOf(layout), size.width, size.height)), [flyTo, layout, size]);
  const reset = useCallback(() => {
    if (focusId) onFocus(null);
    else flyTo({ x: size.width / 2, y: size.height / 2, scale: defaultScale });
  }, [defaultScale, flyTo, focusId, onFocus, size]);
  const zoom = useCallback((factor: number) => {
    flyTo(zoomAt(cameraRef.current, factor, size.width / 2, size.height / 2));
  }, [flyTo, size]);

  useEffect(() => {
    const element = containerRef.current!;
    const observer = new ResizeObserver(([entry]) => {
      setSize({ width: entry.contentRect.width, height: entry.contentRect.height });
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    flyTo(focusId ? fitCamera(boundsOf(layout), size.width, size.height) :
      { x: size.width / 2, y: size.height / 2, scale: defaultScale });
  }, [defaultScale, flyTo, focusId, layout, size]);

  useEffect(() => () => cancelAnimationFrame(animation.current), []);
  useEffect(() => {
    const update = () => setFullscreen(Boolean(document.fullscreenElement));
    document.addEventListener("fullscreenchange", update);
    return () => document.removeEventListener("fullscreenchange", update);
  }, []);

  useEffect(() => {
    const canvas = canvasRef.current!;
    const wheel = (event: WheelEvent) => {
      event.preventDefault();
      cancelAnimationFrame(animation.current);
      setHover(null);
      const rect = canvas.getBoundingClientRect();
      const delta = event.deltaY * (event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? size.height : 1);
      moveCamera(zoomAt(cameraRef.current, Math.exp(-Math.max(-200, Math.min(200, delta)) * 0.003),
        event.clientX - rect.left, event.clientY - rect.top));
    };
    canvas.addEventListener("wheel", wheel, { passive: false });
    return () => canvas.removeEventListener("wheel", wheel);
  }, [moveCamera, size.height]);

  useEffect(() => {
    const frame = requestAnimationFrame(() => {
      const canvas = canvasRef.current!;
      const context = canvas.getContext("2d")!;
      const dpr = Math.min(2, window.devicePixelRatio || 1);
      if (canvas.width !== Math.round(size.width * dpr) || canvas.height !== Math.round(size.height * dpr)) {
        canvas.width = Math.round(size.width * dpr);
        canvas.height = Math.round(size.height * dpr);
      }
      context.setTransform(dpr, 0, 0, dpr, 0, 0);
      context.clearRect(0, 0, size.width, size.height);

      if (!focusId) {
        const ringRadius = ROOT_RADIUS * camera.scale;
        context.strokeStyle = "#d4b88530";
        context.lineWidth = 1;
        context.setLineDash([2, 7]);
        context.beginPath();
        context.arc(camera.x, camera.y, ringRadius, 0, Math.PI * 2);
        context.stroke();
        context.setLineDash([]);
        if (ringRadius > 85 && ringRadius < 1200) {
          context.textAlign = "center";
          context.fillStyle = "#e3cda4";
          context.font = `${Math.min(58, ringRadius * 0.22)}px Georgia, serif`;
          context.fillText("TMI", camera.x, camera.y + 1);
          context.fillStyle = "#9bb4b7";
          context.font = "10px 'Avenir Next', sans-serif";
          context.fillText("THE THOMPSON ATLAS", camera.x, camera.y + 26);
          context.fillStyle = "#708a91";
          context.fillText(`${model.roots.length} chapters · branches grow outward`, camera.x, camera.y + 47);
        }
      }

      const markers = visibleMarkers(layout, camera, size.width, size.height, radius);
      markersRef.current = markers;
      setMarkerCount(markers.length);
      setClusterCount(markers.filter((marker) => marker.members.length > 1).length);
      const markerById = new Map<string, number>();
      markers.forEach((marker, index) => marker.members.forEach((point) => markerById.set(point.node.id, index)));
      const drawn = new Set<string>();
      context.lineWidth = 1;
      for (let targetIndex = 0; targetIndex < markers.length; targetIndex++) {
        const target = markers[targetIndex];
        for (const point of target.members) {
          if (!point.node.parent_id) continue;
          const parent = positions.get(point.node.parent_id);
          if (!parent) continue;
          const sourceIndex = markerById.get(parent.node.id);
          if (sourceIndex === targetIndex) continue;
          const edgeKey = `${sourceIndex ?? parent.node.id}:${targetIndex}`;
          if (drawn.has(edgeKey)) continue;
          drawn.add(edgeKey);
          const source = sourceIndex !== undefined ? markers[sourceIndex] : {
            x: parent.x * camera.scale + camera.x, y: parent.y * camera.scale + camera.y,
          };
          const distance = Math.hypot(target.x - source.x, target.y - source.y);
          const color = model.colors.get(point.node.chapter) ?? "#9bb4b7";
          context.strokeStyle = `${color}48`;
          context.setLineDash(point.node.kind === "gap" || parent.node.kind === "gap" ? [3, 5] : []);
          context.beginPath();
          context.moveTo(source.x, source.y);
          context.bezierCurveTo(source.x + Math.cos(parent.angle) * distance * 0.45,
            source.y + Math.sin(parent.angle) * distance * 0.45,
            target.x - Math.cos(point.angle) * distance * 0.45,
            target.y - Math.sin(point.angle) * distance * 0.45, target.x, target.y);
          context.stroke();
        }
      }
      context.setLineDash([]);
      for (const marker of markers) {
        const node = marker.members[0].node;
        const bundled = marker.members.length > 1;
        const mixed = bundled && marker.members.some((point) => point.node.chapter !== node.chapter);
        const color = mixed ? "#cad6ce" : model.colors.get(node.chapter) ?? "#cad6ce";
        const selected = marker.members.some((point) => point.node.id === focusId);
        const r = marker.radius;
        if (bundled) {
          context.beginPath();
          context.arc(marker.x + 3, marker.y + 4, r, 0, Math.PI * 2);
          context.fillStyle = "#08151e";
          context.fill();
          context.strokeStyle = `${color}66`;
          context.stroke();
        }
        context.beginPath();
        context.arc(marker.x, marker.y, r, 0, Math.PI * 2);
        const gradient = context.createRadialGradient(marker.x - r / 3, marker.y - r / 3, 0, marker.x, marker.y, r);
        gradient.addColorStop(0, bundled ? "#2b414c" : "#203641");
        gradient.addColorStop(1, "#10222e");
        context.fillStyle = gradient;
        context.fill();
        context.strokeStyle = selected ? "#fff0cd" : `${color}${bundled ? "d0" : "a0"}`;
        context.lineWidth = selected ? 2.5 : 1.2;
        context.setLineDash(node.kind === "gap" && !bundled ? [3, 3] : []);
        context.stroke();
        context.setLineDash([]);
        if (selected) {
          context.beginPath();
          context.arc(marker.x, marker.y, r + 5, 0, Math.PI * 2);
          context.strokeStyle = "#fff0cd35";
          context.stroke();
        }
        context.textAlign = "center";
        if (bundled) {
          context.fillStyle = "#f3ebdb";
          context.font = `600 ${r > 24 ? 16 : 12}px 'Avenir Next', sans-serif`;
          context.fillText(countLabel(marker.members.length), marker.x, marker.y + 2);
          context.fillStyle = color;
          context.font = "8px 'Avenir Next', sans-serif";
          context.fillText("EXPAND", marker.x, marker.y + 14);
        } else {
          context.save();
          context.beginPath();
          context.arc(marker.x, marker.y, r - 3, 0, Math.PI * 2);
          context.clip();
          context.fillStyle = color;
          const codeSize = node.kind === "chapter" ? 13 : Math.min(9, (r * 1.55) / (node.code.length * 0.6));
          context.font = `600 ${codeSize}px 'Avenir Next', sans-serif`;
          context.fillText(node.code, marker.x, marker.y - r * 0.38);
          context.fillStyle = "#e5eae5";
          context.font = `${r < 25 ? 8 : 10}px 'Avenir Next', sans-serif`;
          const lines = wrapLabel(context, node.kind === "gap" ? "Unlisted code" : node.text, r * 1.5, r < 25 ? 2 : 3);
          lines.forEach((line, i) => {
            const baseline = -1 + i * 10;
            const availableWidth = 2 * Math.sqrt(Math.max(1, (r - 5) ** 2 - (baseline - 3) ** 2));
            context.fillText(wrapLabel(context, line, availableWidth, 1)[0] ?? "", marker.x, marker.y + baseline);
          });
          context.restore();
        }
      }
    });
    return () => cancelAnimationFrame(frame);
  }, [camera, focusId, layout, model, positions, radius, size]);

  const eventPoint = (event: PointerEvent<HTMLCanvasElement>) => {
    const rect = event.currentTarget.getBoundingClientRect();
    return { x: event.clientX - rect.left, y: event.clientY - rect.top };
  };
  const hitTest = (point: { x: number; y: number }) => markersRef.current.find((marker) =>
    Math.hypot(marker.x - point.x, marker.y - point.y) <= marker.radius + 3);

  function activate(marker: Marker) {
    setHover(null);
    if (marker.members.length === 1) {
      if (focusId === marker.members[0].node.id) fit();
      else onFocus(marker.members[0].node.id);
    } else {
      const next = fitCamera(boundsOf(marker.members), size.width, size.height, 110);
      const scale = Math.min(MAX_ZOOM, Math.max(cameraRef.current.scale * 1.8, next.scale));
      const centerX = (marker.x - cameraRef.current.x) / cameraRef.current.scale;
      const centerY = (marker.y - cameraRef.current.y) / cameraRef.current.scale;
      flyTo({ x: size.width / 2 - centerX * scale, y: size.height / 2 - centerY * scale, scale });
    }
  }

  function pointerDown(event: PointerEvent<HTMLCanvasElement>) {
    cancelAnimationFrame(animation.current);
    event.currentTarget.focus({ preventScroll: true });
    event.currentTarget.setPointerCapture(event.pointerId);
    const point = eventPoint(event);
    pointers.current.set(event.pointerId, point);
    dragStart.current = { ...point, moved: pointers.current.size > 1 };
    setHover(null);
  }

  function pointerMove(event: PointerEvent<HTMLCanvasElement>) {
    const point = eventPoint(event);
    const previous = pointers.current.get(event.pointerId);
    if (previous) {
      const before = [...pointers.current.values()];
      pointers.current.set(event.pointerId, point);
      if (pointers.current.size === 2) {
        const after = [...pointers.current.values()];
        const oldCenter = { x: (before[0].x + before[1].x) / 2, y: (before[0].y + before[1].y) / 2 };
        const newCenter = { x: (after[0].x + after[1].x) / 2, y: (after[0].y + after[1].y) / 2 };
        const ratio = Math.hypot(after[0].x - after[1].x, after[0].y - after[1].y) /
          Math.max(1, Math.hypot(before[0].x - before[1].x, before[0].y - before[1].y));
        const next = zoomAt(cameraRef.current, ratio, oldCenter.x, oldCenter.y);
        moveCamera({ ...next, x: next.x + newCenter.x - oldCenter.x, y: next.y + newCenter.y - oldCenter.y });
        dragStart.current.moved = true;
      } else {
        if (Math.hypot(point.x - dragStart.current.x, point.y - dragStart.current.y) > 4) dragStart.current.moved = true;
        if (dragStart.current.moved) moveCamera({ ...cameraRef.current,
          x: cameraRef.current.x + point.x - previous.x, y: cameraRef.current.y + point.y - previous.y });
      }
      return;
    }
    const marker = hitTest(point);
    event.currentTarget.style.cursor = marker ? "pointer" : "grab";
    setHover(marker ? { marker, ...point } : null);
  }

  function pointerUp(event: PointerEvent<HTMLCanvasElement>) {
    if (!dragStart.current.moved && pointers.current.size === 1) {
      const marker = hitTest(eventPoint(event));
      if (marker) activate(marker);
    }
    pointers.current.delete(event.pointerId);
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
  }

  function keyDown(event: KeyboardEvent<HTMLCanvasElement>) {
    const directions: Record<string, [number, number]> = { ArrowLeft: [80, 0], ArrowRight: [-80, 0], ArrowUp: [0, 80], ArrowDown: [0, -80] };
    if (directions[event.key]) {
      event.preventDefault();
      const [dx, dy] = directions[event.key];
      flyTo({ ...cameraRef.current, x: cameraRef.current.x + dx, y: cameraRef.current.y + dy });
    } else if (["+", "=", "-", "0", "Escape"].includes(event.key)) {
      event.preventDefault();
      if (event.key === "0" || event.key === "Escape") reset();
      else zoom(event.key === "-" ? 1 / 1.5 : 1.5);
    }
  }

  return (
    <div className="tmi-viewport" ref={containerRef}>
      <canvas aria-label="Interactive TMI tree. Drag to pan, scroll or use plus and minus to zoom. Use search or the chapter buttons to select a trope with the keyboard."
        aria-describedby="tmi-canvas-help" ref={canvasRef} tabIndex={0}
        onPointerDown={pointerDown} onPointerMove={pointerMove} onPointerUp={pointerUp}
        onPointerCancel={() => { pointers.current.clear(); dragStart.current.moved = true; }}
        onPointerLeave={() => setHover(null)} onKeyDown={keyDown} />
      <div className="tmi-canvas-caption">
        <span className="tmi-live-dot" />{focusId ? `BRANCH ${focusId}` : "THE COMPLETE INDEX"}
        <span>{countLabel(layout.length)} entries in this view</span>
      </div>
      <div className="tmi-view-actions">
        <button type="button" onClick={reset} title="Return to the chapter ring (0)">◎ <span>Chapter ring</span></button>
        <button type="button" onClick={fit} title="Fit every entry in the current view">⤢ <span>Fit tree</span></button>
        <button type="button" aria-label={fullscreen ? "Exit fullscreen" : "Enter fullscreen"} title="Fullscreen"
          onClick={() => {
            const action = document.fullscreenElement ? document.exitFullscreen() : containerRef.current?.closest(".tmi-stage")?.requestFullscreen();
            void action?.catch(() => { /* Fullscreen may be unavailable in embedded browsers. */ });
          }}>⛶</button>
      </div>
      {hover ? <div className="tmi-tooltip" role="tooltip" style={{
        left: Math.max(8, Math.min(size.width - 292, hover.x + 20)), top: Math.max(60, Math.min(size.height - 200, hover.y + 20)),
      }}>
        {hover.marker.members.length > 1 ? <>
          <strong>{countLabel(hover.marker.members.length)} overlapping entries</strong>
          <span>{hover.marker.members.slice(0, 4).map((point) => point.node.code).join(" · ")}{hover.marker.members.length > 4 ? " …" : ""}</span>
          <small>Click to zoom in and separate the markers.</small>
        </> : <>
          <strong>{hover.marker.members[0].node.code}</strong>
          <span>{hover.marker.members[0].node.text}</span>
          <small>Click to explore this branch through its final leaves.</small>
        </>}
      </div> : null}
      <div className="tmi-zoom-dock">
        <div className="tmi-zoom-row">
          <button type="button" aria-label="Zoom out" onClick={() => zoom(1 / 1.5)} disabled={camera.scale <= MIN_ZOOM}>−</button>
          <label><span>Zoom <output>{new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 0 }).format(camera.scale / defaultScale * 100)}%</output></span>
            <input aria-label="Graph zoom" type="range" min={Math.log2(MIN_ZOOM)} max={Math.log2(MAX_ZOOM)} step="0.05"
              value={Math.log2(camera.scale)} onChange={(event) => {
                cancelAnimationFrame(animation.current);
                setHover(null);
                moveCamera(zoomAt(cameraRef.current, 2 ** Number(event.target.value) / cameraRef.current.scale, size.width / 2, size.height / 2));
              }} /></label>
          <button type="button" aria-label="Zoom in" onClick={() => zoom(1.5)} disabled={camera.scale >= MAX_ZOOM}>+</button>
          <div className="tmi-dock-divider" />
          <label className="tmi-marker-slider"><span>Marker size</span><input aria-label="Marker size" type="range" min="20" max="46" value={radius}
            onChange={(event) => setRadius(Number(event.target.value))} /></label>
        </div>
        <p id="tmi-canvas-help">Drag to wander · Scroll to zoom · Click a circle to follow its tree</p>
      </div>
      <div className="tmi-render-status">{countLabel(markerCount)} visible markers · {countLabel(clusterCount)} clusters</div>
    </div>
  );
}
