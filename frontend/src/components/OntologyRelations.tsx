import { useEffect, useMemo, useState } from "react";

import { editTropeOntology, searchTropes } from "../api/client";
import type { OntologyNode, SearchResponse, TropeOntology } from "../api/types";
import { SpecificityEditor } from "../ontology/SpecificityEditor";
import { exactWordMatches, relationChoices, relations, specificityLabel, type Direction } from "../ontology/relationships";
import { routeHref } from "../router";

type Edit = Parameters<typeof editTropeOntology>[2];

function SearchMatches({ query, candidates, levels, position, busy, onLink }: {
  query: string;
  candidates: OntologyNode[];
  levels: number[];
  position: string;
  busy: boolean;
  onLink?: (id: string) => void;
}) {
  const [result, setResult] = useState<SearchResponse | null>(null);
  const [failed, setFailed] = useState(false);
  const [exactLimit, setExactLimit] = useState(5);
  const exact = useMemo(() => exactWordMatches(candidates, query), [candidates, query]);
  const similar = useMemo(() => {
    const byId = new Map(candidates.map(t => [t.id, t]));
    for (const t of exact) byId.delete(t.id);
    return (result?.items ?? []).flatMap(item => {
      const trope = byId.get(item.id);
      return trope ? [trope] : [];
    });
  }, [result, candidates, exact]);

  useEffect(() => {
    const controller = new AbortController();
    const timer = window.setTimeout(async () => {
      try {
        const response = await searchTropes({ query, specificity_levels: levels, limit: 16 }, controller.signal);
        if (!controller.signal.aborted) setResult(response);
      } catch {
        if (!controller.signal.aborted) setFailed(true);
      }
    }, 250);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [query, levels]);

  function match(trope: OntologyNode) {
    return <li className="ontology-row" key={trope.id}>
      <div>{onLink ? <button type="button" className="ontology-match-name" disabled={busy}
        title={`Link ${position} the active trope`} onClick={() => onLink(trope.id)}>{trope.text}</button>
        : <a href={routeHref("/ontology", { trope: trope.id })}>{trope.text}</a>}
        <small>{specificityLabel(trope)}</small></div>
      {onLink && <button type="button" className="button button-ghost" disabled={busy}
        aria-label={`Link ${trope.text} ${position}`} onClick={() => onLink(trope.id)}>Link {position}</button>}
    </li>;
  }

  return <div className="ontology-matches">
    {exact.length > 0 && <div>
      <h4>Exact-word matches ({exact.length})</h4>
      <ul>{exact.slice(0, exactLimit).map(match)}</ul>
      {exact.length > exactLimit && <button type="button" className="button button-ghost"
        onClick={() => setExactLimit(n => n + 10)}>Show more exact matches</button>}
    </div>}
    {similar.length > 0 && <div>
      <h4>{result?.artifact_version == null ? "Related wording" : "Similarity matches"} ({similar.length})</h4>
      <ul>{similar.map(match)}</ul>
    </div>}
    <div className="ontology-search-status" role="status">
      {!result && !failed && "Searching for similar tropes…"}
      {failed && (exact.length ? "Similarity search is unavailable." : "No exact-word matches. Similarity search is unavailable.")}
      {result && !exact.length && !similar.length && "No matching tropes."}
    </div>
  </div>;
}

export function OntologyRelations({ direction, graph, node, nodes, canEdit, busy, save }: {
  direction: Direction;
  graph: TropeOntology;
  node: OntologyNode;
  nodes: Map<string, OntologyNode>;
  canEdit: boolean;
  busy: boolean;
  save: (edit: Edit) => Promise<boolean>;
}) {
  const [query, setQuery] = useState("");
  const { linked, candidates, levels } = useMemo(() => relationChoices(graph, node, direction), [graph, node, direction]);
  const { title, position, other } = relations[direction];
  const showRoot = direction === "broader" && graph.root_children.includes(node.id);
  function link(id: string) {
    const child_id = direction === "broader" ? node.id : id;
    const parent_id = direction === "broader" ? id : node.id;
    void save({ child_id, parent_id, status: "approved" });
  }

  return <section className="ontology-band" aria-label={title}>
    <h2>{title}</h2>
    {showRoot && <div className="ontology-row"><div>{graph.root.text}
      <small>Level {graph.root.specificity_level} · Ontology root</small></div></div>}
    {linked.length > 0 && <ul>{linked.map(edge => {
      const trope = nodes.get(edge[other]);
      if (!trope) return null;
      return <li className="ontology-row" key={trope.id}>
        <div><a href={routeHref("/ontology", { trope: trope.id })}>{trope.text}</a>
          <small>{specificityLabel(trope)} · {edge.status} link</small>
          {canEdit && <SpecificityEditor node={trope} busy={busy}
            onSave={level => void save({ trope_id: trope.id, level })} />}
        </div>
        {canEdit && <div className="ontology-actions">
          {edge.status !== "approved" && <button type="button" className="button button-ghost" disabled={busy}
            onClick={() => void save({ ...edge, status: "approved" })}>Approve link</button>}
          <button type="button" className="button button-ghost" disabled={busy}
            onClick={() => void save({ ...edge, status: "rejected" })}>Remove link</button>
        </div>}
      </li>;
    })}</ul>}
    {!linked.length && !showRoot && <p className="muted">No {direction} tropes linked yet.</p>}
    <label className="field ontology-search">
      <span>Find a {direction} trope</span>
      <input className="input" type="search" placeholder="Search by exact word or similarity"
        value={query} onChange={e => setQuery(e.target.value)} />
    </label>
    {query.trim() && <SearchMatches key={`${graph.version}:${query.trim()}`}
      query={query.trim()} candidates={candidates} levels={levels} position={position}
      busy={busy} onLink={canEdit ? link : undefined} />}
  </section>;
}
