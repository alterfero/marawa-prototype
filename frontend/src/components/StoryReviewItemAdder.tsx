import { useEffect, useState } from "react";

import { searchKeywords, searchThemes, searchTropes } from "../api/client";
import type { SearchItem, StoryDetail } from "../api/types";
import { normalizeDraftText } from "../constants/csv";
import { TermCard } from "./TermCard";

export type StoryItemKind = "theme" | "trope" | "keyword";
export type StoryItemInput = { text: string } | { id: string };

const itemKinds = [
  { kind: "theme", label: "Theme", plural: "Themes", field: "themes" },
  { kind: "trope", label: "Trope", plural: "Tropes", field: "tropes" },
  { kind: "keyword", label: "Keyword", plural: "Keywords", field: "keywords" },
] as const;

export function StoryReviewItemAdder({
  story,
  disabled,
  onAdd,
}: {
  story: StoryDetail | null;
  disabled: boolean;
  onAdd: (kind: StoryItemKind, input: StoryItemInput) => Promise<boolean>;
}) {
  const [kind, setKind] = useState<StoryItemKind>("theme");
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchItem[]>([]);
  const [searchStatus, setSearchStatus] = useState<"idle" | "loading" | "ready" | "error">("idle");
  const itemKind = itemKinds.find((item) => item.kind === kind)!;
  const assignedItems = story?.[itemKind.field] ?? [];
  const assignedIds = new Set(assignedItems.map((item) => item.id));
  const alreadyAssigned = assignedItems.some((item) => normalizeDraftText(item.text) === normalizeDraftText(query));
  const exactMatch = results.find((item) => normalizeDraftText(item.text) === normalizeDraftText(query));
  const interactionDisabled = disabled || !story;

  useEffect(() => {
    setResults([]);
    if (!query.trim()) {
      setSearchStatus("idle");
      return;
    }
    let cancelled = false;
    setSearchStatus("loading");
    const timer = window.setTimeout(() => {
      const search = kind === "theme" ? searchThemes : kind === "trope" ? searchTropes : searchKeywords;
      void search({ query: query.trim(), limit: 8, include_string_matches: true }).then((result) => {
        if (cancelled) return;
        const candidates = [...result.string_match_items, ...result.items];
        setResults(candidates.filter((candidate, index) => candidates.findIndex((other) => other.id === candidate.id) === index));
        setSearchStatus("ready");
      }).catch(() => {
        if (!cancelled) setSearchStatus("error");
      });
    }, 250);
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [kind, query]);

  function changeQuery(value: string) {
    setQuery(value);
    setResults([]);
    setSearchStatus(value.trim() ? "loading" : "idle");
  }

  async function addItem(input: StoryItemInput) {
    if (interactionDisabled) return;
    if (await onAdd(kind, input)) changeQuery("");
  }

  return (
    <section className="card page-stack" aria-label="Add items to story">
      <div>
        <h3>Create or add story items</h3>
        <p className="muted">Create a new theme, trope, or keyword and add it to this story immediately. You can also choose an existing item from the suggestions.</p>
      </div>

      <form className="page-stack" onSubmit={(event) => {
        event.preventDefault();
        if (query.trim() && !alreadyAssigned) void addItem(exactMatch ? { id: exactMatch.id } : { text: query.trim() });
      }}>
        <label className="field">
          <span>Item type</span>
          <select className="input" disabled={interactionDisabled} value={kind} onChange={(event) => {
            setKind(event.target.value as StoryItemKind);
            changeQuery("");
          }}>
            {itemKinds.map((item) => <option key={item.kind} value={item.kind}>{item.label}</option>)}
          </select>
        </label>
        <label className="field">
          <span>{itemKind.label} text</span>
          <input
            className="input"
            disabled={interactionDisabled}
            onChange={(event) => changeQuery(event.target.value)}
            placeholder={`Type a new ${kind} or search existing ones`}
            value={query}
          />
        </label>
        <div className="button-row wrap-row">
          <button className="button" disabled={interactionDisabled || !query.trim() || alreadyAssigned} type="submit">
            {exactMatch ? `Add existing ${kind}` : `Create and add ${kind}`}
          </button>
          {alreadyAssigned ? <span className="muted">This {kind} is already assigned.</span> : null}
        </div>
      </form>

      {story ? (
        <details>
          <summary>Current story items: {story.themes.length} themes, {story.tropes.length} tropes, {story.keywords.length} keywords</summary>
          <div className="field-grid">
            {itemKinds.map((item) => (
              <div className="page-stack" key={item.kind}>
                <strong>{item.plural} ({story[item.field].length})</strong>
                <div className="button-row wrap-row">
                  {story[item.field].length ? story[item.field].map((term) => (
                    <span className="pill" key={term.id}>{term.text}</span>
                  )) : <span className="muted">None yet</span>}
                </div>
              </div>
            ))}
          </div>
        </details>
      ) : null}

      {query.trim() ? (
        <div className="page-stack" aria-live="polite">
          <h4>Similar existing {kind}s</h4>
          {searchStatus === "loading" ? <p className="muted">Searching {kind}s...</p> : null}
          {searchStatus === "error" ? <p className="muted">Could not search {kind}s. You can still add the text above.</p> : null}
          {searchStatus === "ready" && results.length === 0 ? <p className="muted">No similar {kind}s found.</p> : null}
          {results.map((candidate) => (
            <TermCard
              key={candidate.id}
              term={candidate}
              actions={(
                <button
                  className="button button-secondary"
                  disabled={interactionDisabled || assignedIds.has(candidate.id)}
                  onClick={() => void addItem({ id: candidate.id })}
                  type="button"
                >
                  {assignedIds.has(candidate.id) ? "Already assigned" : `Use existing ${kind}`}
                </button>
              )}
            />
          ))}
        </div>
      ) : null}
    </section>
  );
}
