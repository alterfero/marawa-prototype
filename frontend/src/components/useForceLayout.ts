import { forceCenter, forceCollide, forceLink, forceManyBody, forceSimulation, forceX, forceY, type Simulation } from "d3-force-3d";
import { useCallback, useEffect, useMemo, useRef, useState, type RefObject } from "react";
import type { SemanticGraphLink } from "../api/types";

export type PositionedNode<T> = T & {
  x: number; y: number; radius: number;
  vx?: number; vy?: number; fx?: number | null; fy?: number | null;
};

type Size = { width: number; height: number };

export function useElementSize<T extends HTMLElement>(): [RefObject<T>, Size] {
  const ref = useRef<T>(null);
  const [size, setSize] = useState({ width: 760, height: 600 });
  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    const update = () => setSize({ width: Math.max(1, element.clientWidth), height: Math.max(1, element.clientHeight) });
    update();
    const observer = new ResizeObserver(update);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  return [ref, size];
}

export function useForceLayout<T extends { id: string }>(
  nodes: T[], links: SemanticGraphLink[], radius: (node: T) => number, { width, height }: Size,
  confineIsolatedNodes = false,
) {
  type Node = PositionedNode<T>;
  type Link = { source: string | Node; target: string | Node; similarity: number };
  const simulationRef = useRef<Simulation<Node> | null>(null);
  const layoutRef = useRef<Node[]>([]);
  const data = useRef({ nodes, links });
  data.current = { nodes, links };
  const [, setRevision] = useState(0);
  const redraw = useCallback(() => setRevision(r => r + 1), []);
  const topology = useMemo(() => [
    nodes.map(n => `${n.id}:${radius(n)}`).sort().join("|"),
    links.map(l => `${l.source}:${l.target}:${l.similarity}`).sort().join("|"),
  ].join(";"), [nodes, links, radius]);

  useEffect(() => {
    const prior = new Map(layoutRef.current.map(n => [n.id, n]));
    const { nodes, links } = data.current;
    const hasPrior = nodes.some(n => prior.has(n.id));
    const positioned = nodes.map((node, i) => {
      const old = prior.get(node.id);
      const angle = i * 2.399963229728653;
      const distance = 70 + Math.sqrt(i + 1) * 22;
      return { ...node, radius: radius(node), vx: 0, vy: 0,
        x: old?.x ?? width / 2 + Math.cos(angle) * distance,
        y: old?.y ?? height / 2 + Math.sin(angle) * distance };
    });
    layoutRef.current = positioned;
    const simulation = forceSimulation<Node>(positioned, 2)
      .force("link", forceLink<Node, Link>(links.map(l => ({ ...l })))
        .id(n => n.id)
        .distance(l => Math.max((l.source as Node).radius + (l.target as Node).radius + 18, 280 - l.similarity * 200))
        .strength(l => 0.25 + l.similarity * 0.68))
      .force("charge", forceManyBody<Node>().strength(-155))
      .force("collide", forceCollide<Node>(n => n.radius + 7).strength(0.98))
      .force("center", forceCenter(width / 2, height / 2))
      .alpha(hasPrior ? 0.7 : 1)
      .alphaDecay(0.026);
    if (confineIsolatedNodes) {
      simulation.force("x", forceX<Node>(width / 2).strength(0.035))
        .force("y", forceY<Node>(height / 2).strength(0.035));
    }
    simulationRef.current = simulation;
    simulation.on("tick", redraw);
    redraw();
    return () => { simulation.stop(); };
  }, [topology, radius, width, height, confineIsolatedNodes, redraw]);

  const liveNodes = layoutRef.current;
  const nodesById = useMemo(() => new Map(liveNodes.map(n => [n.id, n])), [liveNodes]);
  useEffect(() => {
    const current = new Map(nodes.map(n => [n.id, n]));
    for (const positioned of layoutRef.current) Object.assign(positioned, current.get(positioned.id));
    redraw();
  }, [nodes, redraw]);
  return { liveNodes, nodesById, simulationRef, redraw };
}
