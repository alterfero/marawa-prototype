import { forceCenter, forceCollide, forceLink, forceManyBody, forceSimulation } from "d3-force-3d";
import { useCallback, useEffect, useMemo, useRef, useState, type PointerEvent, type RefObject, type WheelEvent } from "react";

import {
  ApiError,
  buildSemanticGraph,
  getErrorMessage,
  mergeUnconfirmedKeyword,
  mergeUnconfirmedTheme,
  mergeUnconfirmedTrope,
  updateKeywordConfirmationStatus,
  updateThemeConfirmationStatus,
  updateTropeConfirmationStatus,
} from "../api/client";
import type {
  SemanticGraphItemKind,
  SemanticGraphNode,
  SemanticGraphResponse,
  SemanticGraphScope,
} from "../api/types";
import { useDatasetMaintenance } from "../maintenance";

interface PageNotice {
  tone: "error" | "success";
  title: string;
  body?: string;
}

interface ForceNode extends SemanticGraphNode {
  x: number;
  y: number;
  vx?: number;
  vy?: number;
  fx?: number | null;
  fy?: number | null;
  radius: number;
}

interface ForceLink {
  source: string | ForceNode;
  target: string | ForceNode;
  similarity: number;
}

interface ForceSimulationInstance {
  stop: () => ForceSimulationInstance;
  alphaTarget: (value: number) => ForceSimulationInstance;
  restart: () => ForceSimulationInstance;
}

interface PanState {
  x: number;
  y: number;
}

interface PanDrag {
  pointerId: number;
  startX: number;
  startY: number;
  startPan: PanState;
}

const ITEM_KINDS: Array<{ value: SemanticGraphItemKind; label: string }> = [
  { value: "theme", label: "Themes" },
  { value: "trope", label: "Tropes" },
  { value: "keyword", label: "Keywords" },
];
const MINIMUM_SIMILARITY_THRESHOLD = 0.6;
const MAXIMUM_SIMILARITY_THRESHOLD = 1;

function nodeRadius(storyCount: number): number {
  return Math.min(34, Math.max(11, 9 + Math.sqrt(Math.max(storyCount, 0)) * 5));
}

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.max(minimum, Math.min(maximum, value));
}

function screenPoint(node: ForceNode, pan: PanState, zoom: number): { x: number; y: number } {
  return {
    x: node.x * zoom + pan.x,
    y: node.y * zoom + pan.y,
  };
}

function graphNeighborhood(graph: SemanticGraphResponse, rootNodeId: string, maximumDepth = 3): SemanticGraphResponse {
  if (!graph.nodes.some((node) => node.id === rootNodeId)) {
    return graph;
  }

  const neighborsByNodeId = new Map<string, Set<string>>();
  for (const link of graph.links) {
    const sourceNeighbors = neighborsByNodeId.get(link.source) ?? new Set<string>();
    sourceNeighbors.add(link.target);
    neighborsByNodeId.set(link.source, sourceNeighbors);
    const targetNeighbors = neighborsByNodeId.get(link.target) ?? new Set<string>();
    targetNeighbors.add(link.source);
    neighborsByNodeId.set(link.target, targetNeighbors);
  }

  const includedNodeIds = new Set([rootNodeId]);
  let frontier = [rootNodeId];
  for (let depth = 0; depth < maximumDepth; depth += 1) {
    const nextFrontier: string[] = [];
    for (const nodeId of frontier) {
      for (const neighborId of neighborsByNodeId.get(nodeId) ?? []) {
        if (!includedNodeIds.has(neighborId)) {
          includedNodeIds.add(neighborId);
          nextFrontier.push(neighborId);
        }
      }
    }
    frontier = nextFrontier;
    if (frontier.length === 0) {
      break;
    }
  }

  return {
    ...graph,
    nodes: graph.nodes.filter((node) => includedNodeIds.has(node.id)),
    links: graph.links.filter((link) => includedNodeIds.has(link.source) && includedNodeIds.has(link.target)),
  };
}

function useElementSize<T extends HTMLElement>(): [RefObject<T>, { width: number; height: number }] {
  const ref = useRef<T>(null);
  const [size, setSize] = useState({ width: 760, height: 600 });

  useEffect(() => {
    const element = ref.current;
    if (!element) {
      return;
    }
    const updateSize = () => {
      const rect = element.getBoundingClientRect();
      setSize({ width: Math.max(320, rect.width), height: Math.max(400, rect.height) });
    };
    updateSize();
    const observer = new ResizeObserver(updateSize);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  return [ref, size];
}

function SemanticForceGraph({
  graph,
  disabled,
  focusedNodeId,
  onToggleStatus,
  onMerge,
  onFocusNeighborhood,
  onClearFocus,
}: {
  graph: SemanticGraphResponse;
  disabled: boolean;
  focusedNodeId: string | null;
  onToggleStatus: (node: SemanticGraphNode) => Promise<void>;
  onMerge: (source: SemanticGraphNode, target: SemanticGraphNode) => Promise<void>;
  onFocusNeighborhood: (nodeId: string) => void;
  onClearFocus: () => void;
}) {
  const [viewportRef, viewport] = useElementSize<HTMLDivElement>();
  const svgRef = useRef<SVGSVGElement>(null);
  const simulationRef = useRef<ForceSimulationInstance | null>(null);
  const layoutNodesRef = useRef<ForceNode[]>([]);
  const graphRef = useRef(graph);
  const animationFrameRef = useRef<number | null>(null);
  const draggingNodeIdRef = useRef<string | null>(null);
  const mergeDragRef = useRef(false);
  const dragStartPointRef = useRef<{ x: number; y: number } | null>(null);
  const nodeDragOffsetRef = useRef({ x: 0, y: 0 });
  const nodeWasDraggedRef = useRef(false);
  const panDragRef = useRef<PanDrag | null>(null);
  const [layoutRevision, setLayoutRevision] = useState(0);
  const [pan, setPan] = useState<PanState>({ x: 0, y: 0 });
  const [zoom, setZoom] = useState(1);
  const [hoveredNodeId, setHoveredNodeId] = useState<string | null>(null);
  const [dropTargetId, setDropTargetId] = useState<string | null>(null);
  const graphTopologyKey = [
    graph.nodes.map((node) => `${node.id}:${node.story_count}`).sort().join("|"),
    graph.links.map((link) => `${link.source}:${link.target}:${link.similarity}`).sort().join("|"),
  ].join(";");
  graphRef.current = graph;

  useEffect(() => {
    simulationRef.current?.stop();
    if (animationFrameRef.current !== null) {
      window.cancelAnimationFrame(animationFrameRef.current);
      animationFrameRef.current = null;
    }

    const currentGraph = graphRef.current;
    const priorNodes = new Map(layoutNodesRef.current.map((node) => [node.id, node]));
    const hasPriorLayout = currentGraph.nodes.some((node) => priorNodes.has(node.id));
    const forceNodes: ForceNode[] = currentGraph.nodes.map((node, index) => {
      const priorNode = priorNodes.get(node.id);
      const angle = index * 2.399963229728653;
      const distance = 70 + Math.sqrt(index + 1) * 22;
      return {
        ...node,
        radius: nodeRadius(node.story_count),
        x: priorNode?.x ?? viewport.width / 2 + Math.cos(angle) * distance,
        y: priorNode?.y ?? viewport.height / 2 + Math.sin(angle) * distance,
        vx: 0,
        vy: 0,
      };
    });
    const forceLinks: ForceLink[] = currentGraph.links.map((link) => ({ ...link }));
    layoutNodesRef.current = forceNodes;

    const simulation = forceSimulation(forceNodes, 2)
      .force(
        "link",
        forceLink<ForceNode, ForceLink>(forceLinks)
          .id((node: ForceNode) => node.id)
          .distance((link: ForceLink) => {
            const source = link.source as ForceNode;
            const target = link.target as ForceNode;
            return Math.max(source.radius + target.radius + 18, 280 - link.similarity * 200);
          })
          .strength((link: ForceLink) => 0.25 + link.similarity * 0.68),
      )
      .force("charge", forceManyBody<ForceNode>().strength(-155))
      .force("collide", forceCollide<ForceNode>((node: ForceNode) => node.radius + 7).strength(0.98))
      .force("center", forceCenter(viewport.width / 2, viewport.height / 2))
      .alpha(hasPriorLayout ? 0.18 : 1)
      .alphaDecay(hasPriorLayout ? 0.07 : 0.026);
    simulationRef.current = simulation;

    const renderTick = () => {
      if (animationFrameRef.current !== null) {
        return;
      }
      animationFrameRef.current = window.requestAnimationFrame(() => {
        animationFrameRef.current = null;
        setLayoutRevision((revision) => revision + 1);
      });
    };
    simulation.on("tick", renderTick);
    renderTick();

    return () => {
      simulation.stop();
      if (animationFrameRef.current !== null) {
        window.cancelAnimationFrame(animationFrameRef.current);
      }
    };
  }, [graphTopologyKey, viewport.height, viewport.width]);

  useEffect(() => {
    const latestNodes = new Map(graph.nodes.map((node) => [node.id, node]));
    for (const node of layoutNodesRef.current) {
      const latestNode = latestNodes.get(node.id);
      if (latestNode) {
        Object.assign(node, latestNode, { radius: nodeRadius(latestNode.story_count) });
      }
    }
    setLayoutRevision((revision) => revision + 1);
  }, [graph]);

  const liveNodes = layoutNodesRef.current;
  const nodesById = useMemo(() => new Map(liveNodes.map((node) => [node.id, node])), [liveNodes, layoutRevision]);
  const liveLinks = useMemo(
    () =>
      graph.links
        .map((link) => ({ ...link, sourceNode: nodesById.get(link.source), targetNode: nodesById.get(link.target) }))
        .filter(
          (link): link is typeof link & { sourceNode: ForceNode; targetNode: ForceNode } =>
            link.sourceNode !== undefined && link.targetNode !== undefined,
        ),
    [graph.links, nodesById],
  );
  const hoveredNode = hoveredNodeId ? nodesById.get(hoveredNodeId) ?? null : null;

  function graphPoint(event: { clientX: number; clientY: number }): { x: number; y: number } | null {
    const svg = svgRef.current;
    if (!svg) {
      return null;
    }
    const rect = svg.getBoundingClientRect();
    return {
      x: (event.clientX - rect.left - pan.x) / zoom,
      y: (event.clientY - rect.top - pan.y) / zoom,
    };
  }

  function findMergeTarget(source: ForceNode): ForceNode | null {
    if (source.confirmation_status !== "unconfirmed") {
      return null;
    }
    return (
      liveNodes.find((candidate) => {
        if (candidate.id === source.id) {
          return false;
        }
        const distance = Math.hypot(candidate.x - source.x, candidate.y - source.y);
        return distance <= candidate.radius + source.radius + 18;
      }) ?? null
    );
  }

  function startPan(event: PointerEvent<SVGRectElement>) {
    if (disabled) {
      return;
    }
    panDragRef.current = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      startPan: pan,
    };
    event.currentTarget.setPointerCapture(event.pointerId);
  }

  function startNodeDrag(event: PointerEvent<SVGGElement>, node: ForceNode) {
    if (disabled) {
      return;
    }
    const point = graphPoint(event);
    if (!point) {
      return;
    }
    event.stopPropagation();
    draggingNodeIdRef.current = node.id;
    mergeDragRef.current = event.shiftKey && node.confirmation_status === "unconfirmed";
    dragStartPointRef.current = point;
    nodeDragOffsetRef.current = { x: point.x - node.x, y: point.y - node.y };
    nodeWasDraggedRef.current = false;
    if (mergeDragRef.current) {
      // Keep the target still while the source is deliberately positioned over it.
      simulationRef.current?.stop();
    }
    setLayoutRevision((revision) => revision + 1);
    event.currentTarget.setPointerCapture(event.pointerId);
  }

  function handlePointerMove(event: PointerEvent<SVGSVGElement>) {
    const draggingNodeId = draggingNodeIdRef.current;
    if (draggingNodeId) {
      const point = graphPoint(event);
      const node = nodesById.get(draggingNodeId);
      if (!point || !node) {
        return;
      }
      const dragStartPoint = dragStartPointRef.current;
      if (!nodeWasDraggedRef.current && dragStartPoint && Math.hypot(point.x - dragStartPoint.x, point.y - dragStartPoint.y) >= 8) {
        nodeWasDraggedRef.current = true;
        if (!mergeDragRef.current) {
          simulationRef.current?.alphaTarget(0.24).restart();
        }
      }
      if (!nodeWasDraggedRef.current) {
        return;
      }
      const dragOffset = nodeDragOffsetRef.current;
      node.fx = point.x - dragOffset.x;
      node.fy = point.y - dragOffset.y;
      node.x = node.fx;
      node.y = node.fy;
      setDropTargetId(
        mergeDragRef.current && nodeWasDraggedRef.current ? findMergeTarget(node)?.id ?? null : null,
      );
      setLayoutRevision((revision) => revision + 1);
      return;
    }

    const panDrag = panDragRef.current;
    if (panDrag && panDrag.pointerId === event.pointerId) {
      setPan({
        x: panDrag.startPan.x + event.clientX - panDrag.startX,
        y: panDrag.startPan.y + event.clientY - panDrag.startY,
      });
    }
  }

  function finishPointerInteraction(event: PointerEvent<SVGSVGElement>) {
    const draggingNodeId = draggingNodeIdRef.current;
    if (draggingNodeId) {
      const source = nodesById.get(draggingNodeId);
      const wasMergeDrag = mergeDragRef.current;
      const wasDragged = nodeWasDraggedRef.current;
      const target =
        wasMergeDrag && source && wasDragged ? findMergeTarget(source) : null;
      if (source) {
        source.fx = null;
        source.fy = null;
      }
      draggingNodeIdRef.current = null;
      mergeDragRef.current = false;
      dragStartPointRef.current = null;
      nodeDragOffsetRef.current = { x: 0, y: 0 };
      nodeWasDraggedRef.current = false;
      setDropTargetId(null);
      if (!target && (wasDragged || wasMergeDrag)) {
        simulationRef.current?.alphaTarget(0).restart();
      }
      if (source && target) {
        void onMerge(source, target);
      }
    }
    if (panDragRef.current?.pointerId === event.pointerId) {
      panDragRef.current = null;
    }
  }

  function handleWheel(event: WheelEvent<SVGSVGElement>) {
    event.preventDefault();
    const nextZoom = clamp(zoom * (event.deltaY > 0 ? 0.9 : 1.11), 0.15, 2.5);
    setZoom(nextZoom);
  }

  const tooltipPoint = hoveredNode ? screenPoint(hoveredNode, pan, zoom) : null;
  const tooltipStyle = tooltipPoint
    ? {
        left: clamp(tooltipPoint.x + 18, 8, Math.max(8, viewport.width - 310)),
        top: clamp(tooltipPoint.y + 18, 8, Math.max(8, viewport.height - 210)),
      }
    : undefined;

  return (
    <div className="semantic-graph-viewport" ref={viewportRef}>
      <svg
        aria-label="Semantic similarity graph"
        className="semantic-graph-canvas"
        onPointerMove={handlePointerMove}
        onPointerUp={finishPointerInteraction}
        onPointerCancel={finishPointerInteraction}
        onWheel={handleWheel}
        ref={svgRef}
        role="img"
        viewBox={`0 0 ${viewport.width} ${viewport.height}`}
      >
        <rect className="semantic-graph-background" height={viewport.height} onPointerDown={startPan} width={viewport.width} x="0" y="0" />
        <g transform={`translate(${pan.x} ${pan.y}) scale(${zoom})`}>
          {liveLinks.map((link) => (
            <line
              className="semantic-graph-link"
              key={`${link.source}:${link.target}`}
              strokeWidth={0.8 + link.similarity * 2.6}
              x1={link.sourceNode.x}
              x2={link.targetNode.x}
              y1={link.sourceNode.y}
              y2={link.targetNode.y}
            />
          ))}
          {liveNodes.map((node) => {
            const isDragging = draggingNodeIdRef.current === node.id;
            const isMergeDragging = isDragging && mergeDragRef.current;
            const isDropTarget = dropTargetId === node.id;
            return (
              <g
                aria-label={`${node.text}; ${node.story_count} stories; ${node.confirmation_status}`}
                className={`semantic-graph-node semantic-graph-node-${node.confirmation_status} ${
                  isDragging ? "semantic-graph-node-dragging" : ""
                } ${isMergeDragging ? "semantic-graph-node-merge-dragging" : ""} ${isDropTarget ? "semantic-graph-node-drop-target" : ""}`}
                key={node.id}
                onDoubleClick={(event) => {
                  event.stopPropagation();
                  if (!disabled && !event.ctrlKey) {
                    void onToggleStatus(node);
                  }
                }}
                onClick={(event) => {
                  if (disabled || !event.ctrlKey) {
                    return;
                  }
                  event.preventDefault();
                  event.stopPropagation();
                  onFocusNeighborhood(node.id);
                }}
                onContextMenu={(event) => {
                  if (disabled || !event.ctrlKey) {
                    return;
                  }
                  event.preventDefault();
                  event.stopPropagation();
                  onFocusNeighborhood(node.id);
                }}
                onPointerDown={(event) => startNodeDrag(event, node)}
                onPointerEnter={() => setHoveredNodeId(node.id)}
                onPointerLeave={() => {
                  if (!draggingNodeIdRef.current) {
                    setHoveredNodeId((current) => (current === node.id ? null : current));
                  }
                }}
                role="button"
                tabIndex={0}
                transform={`translate(${node.x} ${node.y})`}
              >
                <circle r={node.radius} />
                {(isDragging || isDropTarget) && (
                  <text className="semantic-graph-label" textAnchor="middle" y={node.radius + 15}>
                    {node.text}
                  </text>
                )}
              </g>
            );
          })}
        </g>
      </svg>
      <div aria-label="Graph zoom controls" className="semantic-graph-zoom-controls" role="group">
        <button
          aria-label="Zoom in"
          className="semantic-graph-zoom-button"
          disabled={disabled}
          onClick={() => setZoom((currentZoom) => clamp(currentZoom * 1.2, 0.15, 2.5))}
          title="Zoom in"
          type="button"
        >
          +
        </button>
        <button
          aria-label="Zoom out"
          className="semantic-graph-zoom-button"
          disabled={disabled}
          onClick={() => setZoom((currentZoom) => clamp(currentZoom / 1.2, 0.15, 2.5))}
          title="Zoom out"
          type="button"
        >
          −
        </button>
        {focusedNodeId ? (
          <button
            className="semantic-graph-show-all-button"
            disabled={disabled}
            onClick={onClearFocus}
            type="button"
          >
            Full graph
          </button>
        ) : null}
      </div>
      {hoveredNode && tooltipStyle ? (
        <aside className="semantic-graph-tooltip" style={tooltipStyle}>
          <strong>{hoveredNode.text}</strong>
          <span className="semantic-graph-tooltip-summary">
            {hoveredNode.story_count} stor{hoveredNode.story_count === 1 ? "y" : "ies"} · {hoveredNode.confirmation_status}
          </span>
          <ul>
            {hoveredNode.stories.map((story) => (
              <li key={story.id}>
                <strong>{story.title}</strong>
                <span>{story.territory || "Territory not recorded"}</span>
              </li>
            ))}
            {hoveredNode.stories.length === 0 ? <li className="muted">No linked stories</li> : null}
          </ul>
        </aside>
      ) : null}
      <p className="semantic-graph-gesture-hint">
        Scroll or use +/− to zoom. Drag the background to pan. Control-click a node to show three levels of neighbors. Double-click to change a status. Shift-drag an unconfirmed node onto another node to merge.
      </p>
    </div>
  );
}

function displayItemKind(itemKind: SemanticGraphItemKind): string {
  return ITEM_KINDS.find((item) => item.value === itemKind)?.label ?? itemKind;
}

function isVersionConflict(error: unknown): boolean {
  return error instanceof ApiError && error.status === 409;
}

export function SemanticGraphsPage() {
  const maintenance = useDatasetMaintenance();
  const [itemKind, setItemKind] = useState<SemanticGraphItemKind>("theme");
  const [scope, setScope] = useState<SemanticGraphScope>("all");
  const [graph, setGraph] = useState<SemanticGraphResponse | null>(null);
  const [focusedNodeId, setFocusedNodeId] = useState<string | null>(null);
  const [similarityThreshold, setSimilarityThreshold] = useState(0.85);
  const [loading, setLoading] = useState(true);
  const [mutating, setMutating] = useState(false);
  const [notice, setNotice] = useState<PageNotice | null>(null);
  const [rebuildNeeded, setRebuildNeeded] = useState(false);
  const latestGraphRequestRef = useRef(0);

  const refreshGraph = useCallback(async () => {
    const requestId = latestGraphRequestRef.current + 1;
    latestGraphRequestRef.current = requestId;
    try {
      setLoading(true);
      const nextGraph = await buildSemanticGraph({
        item_kind: itemKind,
        scope,
        similarity_threshold: MINIMUM_SIMILARITY_THRESHOLD,
      });
      if (latestGraphRequestRef.current === requestId) {
        setGraph(nextGraph);
      }
    } catch (error) {
      if (latestGraphRequestRef.current === requestId) {
        setNotice({
          tone: "error",
          title: "Could not load the semantic graph",
          body: getErrorMessage(error),
        });
      }
    } finally {
      if (latestGraphRequestRef.current === requestId) {
        setLoading(false);
      }
    }
  }, [itemKind, scope]);

  useEffect(() => {
    void refreshGraph();
  }, [refreshGraph]);

  useEffect(() => {
    if (focusedNodeId && !graph?.nodes.some((node) => node.id === focusedNodeId)) {
      setFocusedNodeId(null);
    }
  }, [focusedNodeId, graph]);

  const thresholdGraph = useMemo(
    () =>
      graph
        ? {
            ...graph,
            links: graph.links.filter((link) => link.similarity >= similarityThreshold),
          }
        : null,
    [graph, similarityThreshold],
  );
  const displayedGraph = useMemo(
    () => (thresholdGraph && focusedNodeId ? graphNeighborhood(thresholdGraph, focusedNodeId) : thresholdGraph),
    [focusedNodeId, thresholdGraph],
  );

  async function handleToggleStatus(node: SemanticGraphNode) {
    if (mutating || maintenance.active) {
      return;
    }
    const nextStatus = node.confirmation_status === "canonical" ? "unconfirmed" : "canonical";
    try {
      setMutating(true);
      setNotice(null);
      let updatedNode: Pick<SemanticGraphNode, "id" | "version" | "confirmation_status" | "story_count">;
      if (itemKind === "trope") {
        const response = await updateTropeConfirmationStatus(node.id, {
          expected_trope_version: node.version,
          confirmation_status: nextStatus,
        });
        updatedNode = response.trope;
      } else if (itemKind === "theme") {
        const response = await updateThemeConfirmationStatus(node.id, {
          expected_theme_version: node.version,
          confirmation_status: nextStatus,
        });
        updatedNode = response.theme;
      } else {
        const response = await updateKeywordConfirmationStatus(node.id, {
          expected_keyword_version: node.version,
          confirmation_status: nextStatus,
        });
        updatedNode = response.keyword;
      }
      setGraph((currentGraph) => {
        if (!currentGraph) {
          return currentGraph;
        }
        if (currentGraph.scope === "canonical" && updatedNode.confirmation_status === "unconfirmed") {
          return {
            ...currentGraph,
            nodes: currentGraph.nodes.filter((graphNode) => graphNode.id !== updatedNode.id),
            links: currentGraph.links.filter(
              (link) => link.source !== updatedNode.id && link.target !== updatedNode.id,
            ),
          };
        }
        return {
          ...currentGraph,
          nodes: currentGraph.nodes.map((graphNode) =>
            graphNode.id === updatedNode.id
              ? {
                  ...graphNode,
                  confirmation_status: updatedNode.confirmation_status,
                  story_count: updatedNode.story_count,
                  version: updatedNode.version,
                }
              : graphNode,
          ),
        };
      });
      setNotice({
        tone: "success",
        title: `${node.text} is now ${nextStatus}.`,
      });
    } catch (error) {
      setNotice({
        tone: "error",
        title: isVersionConflict(error) ? "This node was changed elsewhere" : "Could not update node status",
        body: isVersionConflict(error) ? "The graph was refreshed with the current version." : getErrorMessage(error),
      });
      await refreshGraph();
    } finally {
      setMutating(false);
    }
  }

  async function handleMerge(source: SemanticGraphNode, target: SemanticGraphNode) {
    if (mutating || maintenance.active || source.confirmation_status !== "unconfirmed") {
      return;
    }
    try {
      setMutating(true);
      setNotice(null);
      let affectedStoryCount: number;
      if (itemKind === "trope") {
        const result = await mergeUnconfirmedTrope({
          source_trope_id: source.id,
          expected_source_trope_version: source.version,
          target_trope_id: target.id,
        });
        affectedStoryCount = result.affected_story_count;
      } else if (itemKind === "theme") {
        const result = await mergeUnconfirmedTheme({
          source_theme_id: source.id,
          expected_source_theme_version: source.version,
          target_theme_id: target.id,
        });
        affectedStoryCount = result.affected_story_count;
      } else {
        const result = await mergeUnconfirmedKeyword({
          source_keyword_id: source.id,
          expected_source_keyword_version: source.version,
          target_keyword_id: target.id,
        });
        affectedStoryCount = result.affected_story_count;
      }
      setRebuildNeeded(true);
      setFocusedNodeId((currentFocusedNodeId) =>
        currentFocusedNodeId === source.id ? target.id : currentFocusedNodeId,
      );
      setNotice({
        tone: "success",
        title: `Merged ${source.text} into ${target.text}.`,
        body: `${affectedStoryCount} stor${affectedStoryCount === 1 ? "y was" : "ies were"} reassigned.`,
      });
      await refreshGraph();
    } catch (error) {
      setNotice({
        tone: "error",
        title: isVersionConflict(error) ? "This node was changed elsewhere" : "Could not merge nodes",
        body: isVersionConflict(error) ? "The graph was refreshed with the current version." : getErrorMessage(error),
      });
      await refreshGraph();
    } finally {
      setMutating(false);
    }
  }

  const interactionDisabled = loading || mutating || maintenance.active;
  return (
    <section className="page-stack semantic-graphs-page">
      <section className="panel panel-experimental">
        <div className="panel-header semantic-graphs-header">
          <div className="stack experimental-heading-copy">
            <p className="eyebrow">Admin view</p>
            <h2>Semantic graphs</h2>
            <p className="muted">
              Explore semantic proximity between {displayItemKind(itemKind).toLowerCase()}. Node size reflects the number of linked stories.
            </p>
          </div>
          <div className="semantic-graph-legend" aria-label="Graph legend">
            <span className="legend-item"><i className="semantic-legend-node semantic-legend-node-canonical" />Canonical</span>
            <span className="legend-item"><i className="semantic-legend-node semantic-legend-node-unconfirmed" />Unconfirmed</span>
          </div>
        </div>
        <div className="semantic-graph-controls">
          <div className="field">
            <span className="map-view-label">Items</span>
            <div className="similarity-scope-switch" role="group" aria-label="Graph item type">
              {ITEM_KINDS.map((item) => (
                <button
                  aria-pressed={itemKind === item.value}
                  className={itemKind === item.value ? "similarity-scope-switch-option-active" : undefined}
                  disabled={loading || mutating}
                  key={item.value}
                  onClick={() => setItemKind(item.value)}
                  type="button"
                >
                  {item.label}
                </button>
              ))}
            </div>
          </div>
          <div className="field">
            <span className="map-view-label">Show</span>
            <div className="similarity-scope-switch" role="group" aria-label="Graph item status scope">
              <button
                aria-pressed={scope === "canonical"}
                className={scope === "canonical" ? "similarity-scope-switch-option-active" : undefined}
                disabled={loading || mutating}
                onClick={() => setScope("canonical")}
                type="button"
              >
                Canonical only
              </button>
              <button
                aria-pressed={scope === "all"}
                className={scope === "all" ? "similarity-scope-switch-option-active" : undefined}
                disabled={loading || mutating}
                onClick={() => setScope("all")}
                type="button"
              >
                All items
              </button>
            </div>
          </div>
          <div className="field semantic-graph-threshold-control">
            <label className="map-view-label" htmlFor="semantic-graph-similarity-threshold">
              Edge similarity <output>{similarityThreshold.toFixed(2)}</output>
            </label>
            <input
              aria-describedby="semantic-graph-similarity-threshold-help"
              disabled={loading || mutating}
              id="semantic-graph-similarity-threshold"
              max={MAXIMUM_SIMILARITY_THRESHOLD}
              min={MINIMUM_SIMILARITY_THRESHOLD}
              onChange={(event) => setSimilarityThreshold(Number(event.target.value))}
              step="0.01"
              type="range"
              value={similarityThreshold}
            />
            <span className="muted semantic-graph-threshold-help" id="semantic-graph-similarity-threshold-help">
              Hide edges below this similarity.
            </span>
          </div>
        </div>
      </section>

      {rebuildNeeded ? (
        <section className="notice notice-warning" role="status">
          <strong className="notice-title">Rebuild needed to take all changes into account</strong>
          <p>Use Rebuild in the sidebar to refresh semantic artifacts after a merge.</p>
        </section>
      ) : null}

      {notice ? (
        <section className={`notice notice-${notice.tone}`} role="status">
          <strong className="notice-title">{notice.title}</strong>
          {notice.body ? <p>{notice.body}</p> : null}
        </section>
      ) : null}

      <section className="panel semantic-graph-panel">
        {loading && !displayedGraph ? <p className="semantic-graph-placeholder">Loading semantic graph…</p> : null}
        {!loading && displayedGraph && displayedGraph.nodes.length === 0 ? (
          <p className="semantic-graph-placeholder">No {displayItemKind(itemKind).toLowerCase()} are available for this view.</p>
        ) : null}
        {displayedGraph && displayedGraph.nodes.length > 0 ? (
          <>
            {displayedGraph.warnings.map((warning) => (
              <p className="notice notice-warning" key={warning} role="status">{warning}</p>
            ))}
            <SemanticForceGraph
              disabled={interactionDisabled}
              focusedNodeId={focusedNodeId}
              graph={displayedGraph}
              onClearFocus={() => setFocusedNodeId(null)}
              onFocusNeighborhood={setFocusedNodeId}
              onMerge={handleMerge}
              onToggleStatus={handleToggleStatus}
            />
            <div className="semantic-graph-footer">
              <span>{displayedGraph.nodes.length} nodes{focusedNodeId ? " in focus" : ""}</span>
              <span>{displayedGraph.links.length} semantic links</span>
              <span>{displayedGraph.model_name}</span>
            </div>
          </>
        ) : null}
      </section>
    </section>
  );
}
