import { useDeferredValue, useEffect, useMemo, useState } from "react";
import { getErrorMessage, getTmiCatalogue } from "../api/client";
import { ancestorPath, buildTmiModel } from "../tmi/layout";
import { TmiCanvas } from "../tmi/TmiCanvas";
import type { TmiCatalogue, TmiNode } from "../tmi/types";
import "../tmi/tmi.css";

const number = (value: number) => value.toLocaleString("en");

export function TmiPage() {
  const [catalogue, setCatalogue] = useState<TmiCatalogue | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [focusId, setFocusId] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const deferredQuery = useDeferredValue(query);
  const [searchOpen, setSearchOpen] = useState(false);
  const [activeResult, setActiveResult] = useState(0);
  const [childLimit, setChildLimit] = useState(60);
  const model = useMemo(() => catalogue ? buildTmiModel(catalogue) : null, [catalogue]);
  const searchable = useMemo(() => catalogue?.nodes.map((node) => ({ node,
    content: [node.text, ...node.alternate_texts].join(" ").toLowerCase() })) ?? [], [catalogue]);
  const results = useMemo(() => {
    const q = deferredQuery.trim().toLowerCase().replace(/\.$/, "");
    if (!q) return { total: 0, items: [] as TmiNode[] };
    const terms = q.split(/\s+/);
    const matches = searchable.filter(({ node, content }) => node.code.toLowerCase().includes(q) || terms.every((term) => content.includes(term)));
    const score = (node: TmiNode) => node.code.toLowerCase() === q ? 0 : node.code.toLowerCase().startsWith(q) ? 1 : 2;
    matches.sort((a, b) => score(a.node) - score(b.node));
    return { total: matches.length, items: matches.slice(0, 40).map(({ node }) => node) };
  }, [deferredQuery, searchable]);

  useEffect(() => {
    const controller = new AbortController();
    setError(null);
    getTmiCatalogue(controller.signal).then((data) => {
      if (!controller.signal.aborted) setCatalogue(data);
    }).catch((reason: unknown) => {
      if (!controller.signal.aborted) setError(getErrorMessage(reason));
    });
    return () => controller.abort();
  }, [attempt]);

  const selected = focusId ? model?.byId.get(focusId) : null;
  const path = focusId && model ? ancestorPath(model, focusId) : [];
  const children = focusId ? model?.children.get(focusId) ?? [] : [];

  function focus(id: string | null) {
    setFocusId(id);
    setChildLimit(60);
    setSearchOpen(false);
  }

  return <section className="tmi-page">
    <header className="tmi-heading">
      <div>
        <p className="eyebrow">REFERENCE COLLECTION <span className="tmi-admin-badge">ADMIN ONLY</span></p>
        <h2>The Thompson atlas<span>.</span></h2>
        <p className="tmi-heading-subtitle">Follow the branches of a world of stories.</p>
      </div>
      {catalogue ? <div className="tmi-heading-stats" aria-label="Catalogue totals">
        <div><strong>{number(catalogue.stats.trope_count)}</strong><span>TMI tropes</span></div>
        <div><strong>{catalogue.stats.chapter_count}</strong><span>chapters</span></div>
      </div> : null}
    </header>

    {error ? <div className="notice notice-error" role="alert"><strong>Could not open the TMI atlas</strong><p>{error}</p>
      <button className="button" type="button" onClick={() => setAttempt((value) => value + 1)}>Try again</button></div> : null}

    {!catalogue && !error ? <div className="tmi-loading" role="status"><div className="tmi-loading-orbit" /><strong>Unfolding the index…</strong><span>Preparing the chapters and their branches.</span></div> : null}

    {catalogue && model ? <div className="tmi-stage">
      <div className="tmi-stage-topbar">
        <div className="tmi-search">
          <span aria-hidden="true" className="tmi-search-icon">⌕</span>
          <input aria-label="Search TMI by code or trope" placeholder="Find a trope or code…" role="combobox" autoComplete="off"
            aria-expanded={searchOpen && Boolean(query.trim())} aria-controls="tmi-search-results" aria-autocomplete="list"
            aria-activedescendant={searchOpen && results.items[activeResult] ? `tmi-result-${activeResult}` : undefined}
            value={query} onChange={(event) => { setQuery(event.target.value); setSearchOpen(true); setActiveResult(0); }}
            onFocus={() => setSearchOpen(true)} onKeyDown={(event) => {
              if (event.key === "Escape") setSearchOpen(false);
              if (event.key === "ArrowDown" || event.key === "ArrowUp") {
                event.preventDefault();
                setSearchOpen(true);
                const next = Math.max(0, Math.min(results.items.length - 1, activeResult + (event.key === "ArrowDown" ? 1 : -1)));
                setActiveResult(next);
                document.getElementById(`tmi-result-${next}`)?.scrollIntoView({ block: "nearest" });
              }
              if (event.key === "Enter" && results.items[activeResult]) { event.preventDefault(); focus(results.items[activeResult].id); }
            }} />
          {query ? <button type="button" aria-label="Clear search" onClick={() => { setQuery(""); setSearchOpen(false); }}>×</button> : <kbd>CODE / TEXT</kbd>}
          {searchOpen && query.trim() ? <div className="tmi-search-dropdown">
            <div id="tmi-search-results" role="listbox" aria-label="TMI search results">
              {results.items.map((node, i) => <button type="button" role="option" aria-selected={i === activeResult}
                id={`tmi-result-${i}`} key={node.id} onClick={() => focus(node.id)} onMouseEnter={() => setActiveResult(i)}>
                <span style={{ color: model.colors.get(node.chapter) }}>{node.code}</span><span>{node.text}</span>
              </button>)}
            </div>
            <p role="status">{results.total ? `${number(results.total)} matches${results.total > 40 ? " · showing the first 40; refine your search" : ""}` : "No matching entries. Try a different word or code."}</p>
          </div> : null}
        </div>
        <div className="tmi-collection-label"><span>THOMPSON</span><span>MELLMANN 2020 · REFERENCE ATLAS</span></div>
      </div>
      <div className="tmi-stage-body">
        <div className="tmi-main-graph">
          {focusId ? <nav className="tmi-breadcrumb" aria-label="TMI ancestor path">
            <button type="button" onClick={() => focus(null)}>Index</button>
            {path.map((node) => <span key={node.id}><i aria-hidden="true">›</i><button type="button" title={node.text}
              aria-current={node.id === focusId ? "location" : undefined} onClick={() => focus(node.id)}>{node.code}</button></span>)}
          </nav> : null}
          <TmiCanvas model={model} focusId={focusId} onFocus={focus} />
        </div>
        <aside className="tmi-inspector" aria-label={selected ? "Selected TMI entry" : "TMI chapter directory"}>
          {selected ? <>
            <div className="tmi-inspector-eyebrow"><span>{selected.kind === "trope" ? "TMI TROPE" : selected.kind === "gap" ? "UNLISTED INTERMEDIATE" : "SOURCE HEADING"}</span>
              <button type="button" aria-label="Close branch and return to the index" onClick={() => focus(null)}>×</button></div>
            <div className="tmi-entry-code" style={{ color: model.colors.get(selected.chapter) }}>{selected.code}</div>
            <h3>{selected.text}</h3>
            {selected.edition === "first" ? <p className="tmi-edition-label">FIRST-EDITION ENTRY</p> : null}
            {selected.kind === "trope" ? <section className="tmi-bibliography" aria-label="Sources for this trope">
              <h4>Sources <span>AS LISTED IN THE CSV</span></h4>
              {selected.bibliographies.length ? selected.bibliographies.map((reference) => <p key={reference}>{reference}</p>)
                : <p className="tmi-no-sources">No sources listed in the CSV for this trope.</p>}
            </section> : null}
            <div className="tmi-entry-metrics"><span><strong>{number(model.tropeCounts.get(selected.id) ?? 0)}</strong> tropes in branch</span>
              <span><strong>{children.length}</strong> direct branches</span></div>
            {selected.kind === "gap" ? <p className="tmi-entry-note">The child codes refer to this intermediate code, but its description is absent from the source CSV.</p> : null}
            {selected.parent_id ? <button className="tmi-parent-button" type="button" onClick={() => focus(selected.parent_id)}>
              ↑ Back to {selected.parent_id}<span>{model.byId.get(selected.parent_id)?.text}</span></button> : null}
            <div className="tmi-children-heading">{children.length ? "FOLLOW A BRANCH" : "A FINAL LEAF"}</div>
            {children.length ? <div className="tmi-branch-list">
              {children.slice(0, childLimit).map((id) => {
                const child = model.byId.get(id)!;
                return <button type="button" key={id} onClick={() => focus(id)}>
                  <span className="tmi-child-code" style={{ color: model.colors.get(child.chapter) }}>{child.code}</span>
                  <span>{child.text}</span><span className="tmi-child-arrow" aria-hidden="true">↗</span>
                </button>;
              })}
              {children.length > childLimit ? <button className="tmi-more" type="button" onClick={() => setChildLimit((limit) => limit + 60)}>
                Show more ({children.length - childLimit} remaining)</button> : null}
            </div> : <p className="tmi-leaf-note">This is the end of this branch in the supplied index. Follow the path above to explore its relatives.</p>}
            {selected.source_rows.length ? <details className="tmi-entry-source" key={selected.id}><summary>CSV record{selected.source_rows.length > 1 ? "s" : ""}</summary>
              <p>{catalogue.source.filename}, row{selected.source_rows.length > 1 ? "s" : ""} {selected.source_rows.join(", ")} (header is row 1).</p>
              {selected.first_edition_code ? <p>First-edition code: {selected.first_edition_code}</p> : null}
              {selected.source_texts.map((text) => <blockquote key={text}>{text}</blockquote>)}
            </details> : null}
          </> : <>
            <p className="tmi-inspector-eyebrow">START ANYWHERE</p>
            <h3>Every circle opens<br />another story.</h3>
            <p className="tmi-introduction">Choose a chapter, then travel from its broad divisions to the smallest details.</p>
            <div className="tmi-mini-legend"><span><i className="tmi-legend-circle" />An entry</span><span><i className="tmi-legend-sticker">12</i>A cluster</span></div>
            <div className="tmi-chapter-list">
              {model.roots.map((id) => <button key={id} type="button" onClick={() => focus(id)}>
                <span className="tmi-chapter-letter" style={{ color: model.colors.get(id), borderColor: `${model.colors.get(id)}55` }}>{id}</span>
                <span>{model.byId.get(id)!.text}<small>{number(model.tropeCounts.get(id) ?? 0)} tropes</small></span>
                <span className="tmi-child-arrow" aria-hidden="true">↗</span>
              </button>)}
            </div>
          </>}
        </aside>
      </div>
      <footer className="tmi-stage-footer"><span><i />Mellmann’s TMI reference collection</span><span>Source hierarchy · {number(catalogue.stats.source_rows)} original records</span></footer>
    </div> : null}

    {catalogue ? <details className="tmi-provenance"><summary>About the source and how to read this atlas</summary>
      <p>Descriptions, chapters, divisions, sections and bibliographies come from <a href={catalogue.source.url} target="_blank" rel="noreferrer">{catalogue.source.title}</a>. <a href={catalogue.source.doi} target="_blank" rel="noreferrer">Source publication ↗</a> · <a href={catalogue.source.license_url} target="_blank" rel="noreferrer">{catalogue.source.license}</a>.</p>
      <p>Branches follow the CSV’s editorial headings and dotted codes. Their depth is not a measured abstraction level. Heading terminology uses “trope”; descriptions and bibliographies retain the source wording, with the leading code displayed separately.</p>
      <p>Every CSV entry is included. First-edition entries are labelled, and entries without a revised-edition code remain separate within their published section. Dashed circles mark {number(catalogue.stats.gap_count)} intermediate codes missing from the CSV.</p>
      <p>The Sources section shows each entry’s bibliography exactly as supplied, including abbreviations and editorial symbols. References belonging to a parent entry can be read by following its ancestor path. CSV record details retain the original entry text and row numbers.</p>
      <p>Numbered stickers group overlapping markers at the current zoom. Click one to separate its entries; zoom out to see how they connect. Search and the branch directory provide keyboard access to every entry.</p>
    </details> : null}
  </section>;
}
