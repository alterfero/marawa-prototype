import { useEffect, useMemo, useState } from "react";

import { editTropeOntology, getTropeOntology } from "../api/client";
import type { TropeOntology } from "../api/types";
import { OntologyRelations } from "../components/OntologyRelations";
import { OntologyGraph } from "../ontology/OntologyGraph";
import { SpecificityEditor } from "../ontology/SpecificityEditor";
import { ratingLabels, specificityLabel, specificityLevels as levels } from "../ontology/relationships";
import { routeHref, useHashSearch } from "../router";
import "../ontology/ontology.css";

const pageSize = 60;

export function OntologyPage({ canEdit }: { canEdit: boolean }) {
  const params = useHashSearch();
  const selected = new URLSearchParams(params).get("trope");
  const [graph, setGraph] = useState<TropeOntology | null>(null);
  const [revision, setRevision] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [level, setLevel] = useState("");
  const [source, setSource] = useState("");
  const [scope, setScope] = useState("all");
  const [page, setPage] = useState(0);
  const [graphMode, setGraphMode] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    setBusy(true);
    getTropeOntology(controller.signal).then(setGraph).catch((e: Error) => {
      if (!controller.signal.aborted) setError(e.message);
    }).finally(() => { if (!controller.signal.aborted) setBusy(false); });
    return () => controller.abort();
  }, [revision]);

  const nodes = useMemo(() => new Map(graph?.tropes.map(t => [t.id, t])), [graph]);
  const filtered = useMemo(() => {
    if (!graph) return [];
    const allowed = scope === "root" ? new Set(graph.root_children)
      : scope === "pending" ? new Set(graph.links.filter(l => l.status === "pending").map(l => l.child_id)) : null;
    return graph.tropes.filter(t => (!allowed || allowed.has(t.id)) && (!level || t.specificity_level === Number(level))
      && (!source || t.specificity_source === source) && t.text.toLowerCase().includes(query.trim().toLowerCase()));
  }, [graph, query, level, source, scope]);
  const node = selected ? nodes.get(selected) : null;
  const lastPage = Math.max(0, Math.ceil(filtered.length / pageSize) - 1);
  const currentPage = Math.min(page, lastPage);

  async function save(edit: Parameters<typeof editTropeOntology>[2]) {
    if (!graph || busy || !canEdit) return false;
    setBusy(true);
    setError("");
    try {
      await editTropeOntology(graph.dataset_id, graph.version, edit);
      return true;
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save this change.");
      return false;
    } finally {
      setRevision(value => value + 1);
    }
  }

  const catalogue = graph && (
      <section className="panel">
        <div className="panel-header"><h2>Catalogue</h2></div>
        <div className="ontology-filters">
          <label>Find a trope <input type="search" value={query} onChange={e => { setQuery(e.target.value); setPage(0); }} /></label>
          <label>Specificity <select value={level} onChange={e => { setLevel(e.target.value); setPage(0); }}>
            <option value="">All levels</option>{levels.map(l => <option key={l} value={l}>Level {l}</option>)}
          </select></label>
          <label>Rating <select value={source} onChange={e => { setSource(e.target.value); setPage(0); }}>
            <option value="">All sources</option>{Object.entries(ratingLabels).map(([value, label]) =>
              <option key={value} value={value}>{label}</option>)}
          </select></label>
          <label>Show <select value={scope} onChange={e => { setScope(e.target.value); setPage(0); }}>
            <option value="all">All tropes</option><option value="root">Directly under root</option>
            {canEdit && <option value="pending">Pending broader links</option>}
          </select></label>
        </div>
        <label>Matching tropes ({filtered.length.toLocaleString()})
          <select aria-label="Matching tropes" className="ontology-catalogue" size={7}
            value={selected ?? ""} onChange={e => { window.location.hash = routeHref("/ontology", { trope: e.target.value }); }}>
            <option value="" disabled>Choose a trope</option>
            {filtered.slice(currentPage * pageSize, (currentPage + 1) * pageSize).map(t =>
              <option value={t.id} key={t.id}>{t.text} · {specificityLabel(t)}</option>)}
          </select>
        </label>
        {lastPage > 0 && <div className="button-row">
          <button disabled={currentPage === 0} onClick={() => setPage(currentPage - 1)}>Previous</button>
          <span>Page {currentPage + 1} of {lastPage + 1}</span>
          <button disabled={currentPage >= lastPage} onClick={() => setPage(currentPage + 1)}>Next</button>
        </div>}
      </section>
  );

  return <main className="page-stack">
    <header className="panel-header">
      <h1>Trope ontology</h1>
      <label className="ontology-mode-switch">
        <input type="checkbox" role="switch" checked={graphMode} onChange={e => setGraphMode(e.target.checked)} />
        Use graph-based management UI
      </label>
    </header>
    {error && <p role="alert" className="notice-inline">{error} <button onClick={() => { setError(""); setRevision(r => r + 1); }}>Reload</button></p>}
    {!graph && busy && <p role="status">Loading tropes…</p>}
    {graph && <>
      {graphMode ? <details className="ontology-catalogue-drawer">
        <summary>Find a trope in the catalogue</summary>{catalogue}
      </details> : catalogue}
      {selected && !node && <p>This trope is unavailable in the active dataset.</p>}
      {graphMode ? <OntologyGraph key={graph.dataset_id} graph={graph} selected={node ?? null} canEdit={canEdit} busy={busy}
        onSelect={id => { window.location.hash = routeHref("/ontology", { trope: id }); }} save={save} />
      : node && <section className="panel ontology-hierarchy" aria-label="Trope relationships" key={node.id}>
        <OntologyRelations direction="broader" graph={graph} node={node} nodes={nodes} canEdit={canEdit} busy={busy} save={save} />
        <section className="ontology-active" aria-label="Active trope">
          <header>
            <p className="eyebrow">Active trope</p>
            <h2>{node.text}</h2>
            <p>{specificityLabel(node)}</p>
          </header>
          <a href={routeHref("/exploration", { selected_trope_id: node.id })}>See on map</a>
          {canEdit && <SpecificityEditor node={node} busy={busy} onSave={level => void save({ trope_id: node.id, level })} />}
        </section>
        <OntologyRelations direction="narrower" graph={graph} node={node} nodes={nodes} canEdit={canEdit} busy={busy} save={save} />
      </section>}
    </>}
  </main>;
}
