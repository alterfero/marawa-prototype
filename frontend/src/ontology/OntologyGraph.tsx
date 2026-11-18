import { useCallback, useEffect, useMemo, useState } from "react";
import { buildSemanticGraph, type OntologyEdit } from "../api/client";
import type { OntologyLink, OntologyNode, SemanticGraphLink, TropeOntology } from "../api/types";
import { OntologyGraphCanvas } from "./OntologyGraphCanvas";
import { SpecificityEditor } from "./SpecificityEditor";
import { droppedLink, linkKey, specificityLabel, specificityLevels } from "./relationships";

export function OntologyGraph({ graph, selected: selectedTrope, canEdit, busy, onSelect, save }: {
  graph: TropeOntology; selected: OntologyNode | null; canEdit: boolean; busy: boolean;
  onSelect: (id: string) => void; save: (edit: OntologyEdit) => Promise<boolean>;
}) {
  const [similarities, setSimilarities] = useState<SemanticGraphLink[] | null>(null);
  const [edgeId, setEdgeId] = useState<string | null>(null);
  const [notice, setNotice] = useState("");
  const [threshold, setThreshold] = useState(0.85);
  const [similarityError, setSimilarityError] = useState("");
  const [warnings, setWarnings] = useState<string[]>([]);
  const [canonicalOnly, setCanonicalOnly] = useState(true);
  const displayedGraph = useMemo(() => {
    const tropes = graph.tropes.filter(n => !canonicalOnly || n.confirmation_status === "canonical");
    const ids = new Set(tropes.map(n => n.id));
    return { ...graph, tropes, links: graph.links.filter(l => ids.has(l.child_id) && ids.has(l.parent_id)) };
  }, [graph, canonicalOnly]);
  const nodes = useMemo(() => new Map(displayedGraph.tropes.map(n => [n.id, n])), [displayedGraph.tropes]);
  const edges = useMemo(() => new Map(displayedGraph.links.map(l => [linkKey(l), l])), [displayedGraph.links]);
  const selected = selectedTrope ? nodes.get(selectedTrope.id) ?? null : null;
  const edge = edgeId ? edges.get(edgeId) : null;

  useEffect(() => {
    if (!canEdit) return;
    const controller = new AbortController();
    buildSemanticGraph({ item_kind: "trope", scope: "all", similarity_threshold: 0.6 }, controller.signal)
      .then(data => {
        if (!controller.signal.aborted) {
          setSimilarities(data.links);
          setWarnings(data.warnings);
        }
      })
      .catch(() => {
        if (!controller.signal.aborted) {
          setSimilarities([]);
          setSimilarityError("Could not load trope similarities. Reload the page to try again.");
        }
      });
    return () => controller.abort();
  }, [canEdit, graph.dataset_id]);

  const forces = useMemo(() => {
    return (similarities ?? []).filter(l => l.similarity >= threshold && nodes.has(l.source) && nodes.has(l.target));
  }, [similarities, nodes, threshold]);

  useEffect(() => { setEdgeId(null); setNotice(""); }, [selected?.id]);

  function selectEdge(link: OntologyLink | null) {
    setEdgeId(link && linkKey(link));
    setNotice("");
  }

  async function connect(source: OntologyNode, target: OntologyNode) {
    if (!canEdit || busy || source.id === target.id) return;
    const link = droppedLink(source, target);
    const id = linkKey(link);
    if (edges.has(id)) {
      setEdgeId(id);
      setNotice("These tropes already have this link.");
      return;
    }
    setNotice("");
    if (await save(link)) setEdgeId(id);
  }

  const remove = useCallback(async () => {
    if (canEdit && !busy && edge && await save({ ...edge, status: "rejected" })) {
      setEdgeId(null);
      setNotice("Link deleted.");
    }
  }, [canEdit, busy, edge, save]);

  useEffect(() => {
    if (!canEdit || busy) return;
    function handleShortcut(event: KeyboardEvent) {
      if (event.defaultPrevented || event.repeat || event.isComposing || event.altKey || event.ctrlKey || event.metaKey) return;
      const target = event.target;
      if (target instanceof Element && target.closest("input, textarea, select, [contenteditable]:not([contenteditable='false']), [role='textbox']")) return;
      if (edge && (event.key === "Delete" || event.key === "Backspace")) {
        event.preventDefault();
        void remove();
      } else if (!edge && selected && event.shiftKey && (event.key === "ArrowUp" || event.key === "ArrowDown")) {
        event.preventDefault();
        const level = selected.specificity_level + (event.key === "ArrowUp" ? -1 : 1);
        if (level < 1 || level > 4) return;
        setNotice("");
        void save({ trope_id: selected.id, level });
      }
    }
    window.addEventListener("keydown", handleShortcut);
    return () => window.removeEventListener("keydown", handleShortcut);
  }, [canEdit, busy, edge, selected, remove, save]);

  return <section className="panel semantic-graph-panel ontology-graph" aria-label="Trope graph">
    <header className="panel-header">
      <div><h2>Trope graph</h2>
        <small>{displayedGraph.tropes.length.toLocaleString()} tropes · {displayedGraph.links.length.toLocaleString()} links</small></div>
      <label className="ontology-mode-switch">
        <input type="checkbox" role="switch" checked={canonicalOnly} onChange={e => {
          setCanonicalOnly(e.target.checked);
          setEdgeId(null);
          setNotice("");
        }} />
        Canonical tropes only
      </label>
      <div className="ontology-size-legend" aria-label="Marker size by specificity level">
        <span>Specificity</span>
        {specificityLevels.map(level => <span key={level}><i className={`ontology-level-${level}`} />{level}</span>)}
      </div>
    </header>
    <label className="field semantic-graph-threshold-control">
      <span>Similarity threshold <output>{threshold.toFixed(2)}</output></span>
      <input type="range" min="0.6" max="1" step="0.01" value={threshold}
        onChange={e => setThreshold(Number(e.target.value))} />
    </label>
    <p className="ontology-graph-help">Nearby tropes are connected by semantic similarity. Faint lines show similarity.
      Arrows show ontology links from narrower to broader tropes.</p>
    {similarityError && <p role="alert">{similarityError}</p>}
    {warnings.map(warning => <p role="status" key={warning}>{warning}</p>)}
    {similarities !== null && !similarityError && forces.length === 0 &&
      <p role="status">No similarity links meet this threshold. Lower the threshold to include more connections.</p>}
    {canEdit && similarities === null ? <p role="status">Loading semantic positions…</p>
      : displayedGraph.tropes.length ? <OntologyGraphCanvas graph={displayedGraph} forces={forces} selectedId={selected?.id ?? null}
      selectedEdge={edge ?? null} canWrite={canEdit && !busy} onDrop={(a, b) => void connect(a, b)}
      onSelectNode={id => { selectEdge(null); onSelect(id); }} onSelectEdge={selectEdge} />
      : <p>{canonicalOnly ? "No canonical tropes. Turn off “Canonical tropes only” to display all tropes." : "No tropes in the active dataset."}</p>}
    <p className="ontology-graph-help">Drag the background to pan. Scroll to zoom.
      {canEdit && <> Select a trope, then press <kbd>Shift</kbd> + <kbd>↑</kbd> to move one level toward level 1
        (less specific), or <kbd>Shift</kbd> + <kbd>↓</kbd> toward level 4 (more specific).
        Shift-drag one marker onto another to link them. At the same level, the drop target is the broader trope.
        Select a link and press <kbd>Delete</kbd> or <kbd>Backspace</kbd> to remove it.</>}
    </p>
    <div className="ontology-inspector" aria-label="Graph selection">
      {edge ? <>
        <div><small>Selected link · {edge.status}</small>
          <p><strong>{nodes.get(edge.child_id)?.text}</strong> → <strong>{nodes.get(edge.parent_id)?.text}</strong></p>
          <small>Narrower → broader</small></div>
        {canEdit && <button type="button" className="button button-ghost" disabled={busy} onClick={() => void remove()}>Delete link</button>}
      </> : selected ? <>
        <div><small>Selected trope</small><h3>{selected.text}</h3><p>{specificityLabel(selected)}</p></div>
        {canEdit && <SpecificityEditor node={selected} busy={busy} onSave={level => void save({ trope_id: selected.id, level })} />}
      </> : <p>Select a marker to {canEdit ? "edit" : "see"} its specificity, or select a link to inspect its direction.</p>}
    </div>
    {(notice || busy || (canEdit && similarities === null)) && <small role="status">
      {notice || (busy ? "Updating ontology…" : "Loading semantic positions…")}
    </small>}
  </section>;
}
