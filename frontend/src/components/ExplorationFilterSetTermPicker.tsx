import { useEffect, useMemo, useState } from "react";

import { getErrorMessage, searchKeywords, searchThemes, searchTropes } from "../api/client";
import type { ExplorationAppliedTermFilter, SearchItem } from "../api/types";

type SearchStatus = "idle" | "loading" | "ready";
export type ExplorationSemanticTermKind = "theme" | "trope" | "keyword";

const TERM_CONFIG: Record<
  ExplorationSemanticTermKind,
  {
    label: string;
    pluralLabel: string;
    placeholder: string;
    search: (payload: {
      query: string;
      limit?: number;
      include_string_matches?: boolean;
    }) => Promise<{ items: SearchItem[]; string_match_items: SearchItem[] }>;
  }
> = {
  theme: {
    label: "Theme",
    pluralLabel: "themes",
    placeholder: "Describe the theme cluster for this set",
    search: searchThemes,
  },
  trope: {
    label: "Trope",
    pluralLabel: "tropes",
    placeholder: "Describe the trope cluster for this set",
    search: searchTropes,
  },
  keyword: {
    label: "Keyword",
    pluralLabel: "keywords",
    placeholder: "Describe the keyword cluster for this set",
    search: searchKeywords,
  },
};

function selectedTermForCandidate(candidate: SearchItem): ExplorationAppliedTermFilter {
  return {
    id: candidate.id,
    text: candidate.text,
    story_count: candidate.story_count,
  };
}

export function ExplorationFilterSetTermPicker({
  kind,
  loading,
  query,
  selectedTerms,
  showSimilarityThreshold = true,
  onQueryChange,
  onToggleTerm,
}: {
  kind: ExplorationSemanticTermKind;
  loading: boolean;
  query: string;
  selectedTerms: ExplorationAppliedTermFilter[];
  showSimilarityThreshold?: boolean;
  onQueryChange: (value: string) => void;
  onToggleTerm: (term: ExplorationAppliedTermFilter) => void;
}) {
  const [results, setResults] = useState<SearchItem[]>([]);
  const [stringMatchResults, setStringMatchResults] = useState<SearchItem[]>([]);
  const [searchStatus, setSearchStatus] = useState<SearchStatus>("idle");
  const [searchError, setSearchError] = useState<string | null>(null);
  const [similarityThreshold, setSimilarityThreshold] = useState(0.6);
  const [alsoAddAllItemsWithString, setAlsoAddAllItemsWithString] = useState(false);
  const config = TERM_CONFIG[kind];
  const selectedTermIds = useMemo(() => new Set(selectedTerms.map((term) => term.id)), [selectedTerms]);
  const trimmedQuery = query.trim();
  const resultsMeetingThreshold = useMemo(
    () => results.filter((candidate) => candidate.score >= similarityThreshold),
    [results, similarityThreshold],
  );
  const visibleResults = useMemo(() => {
    const seenIds = new Set<string>();
    return [...resultsMeetingThreshold, ...(alsoAddAllItemsWithString ? stringMatchResults : [])].filter(
      (candidate) => {
        if (seenIds.has(candidate.id)) {
          return false;
        }
        seenIds.add(candidate.id);
        return true;
      },
    );
  }, [alsoAddAllItemsWithString, resultsMeetingThreshold, stringMatchResults]);
  const unselectedResults = useMemo(
    () => visibleResults.filter((candidate) => !selectedTermIds.has(candidate.id)),
    [selectedTermIds, visibleResults],
  );

  useEffect(() => {
    if (!trimmedQuery) {
      setResults([]);
      setStringMatchResults([]);
      setSearchStatus("idle");
      setSearchError(null);
      return;
    }

    let cancelled = false;
    const timeoutId = window.setTimeout(() => {
      void (async () => {
        try {
          setSearchStatus("loading");
          setSearchError(null);
          const result = await config.search({
            query: trimmedQuery,
            limit: 8,
            include_string_matches: alsoAddAllItemsWithString,
          });
          if (cancelled) {
            return;
          }
          setResults(result.items);
          setStringMatchResults(result.string_match_items);
          setSearchStatus("ready");
        } catch (caughtError) {
          if (cancelled) {
            return;
          }
          setResults([]);
          setStringMatchResults([]);
          setSearchStatus("ready");
          setSearchError(getErrorMessage(caughtError));
        }
      })();
    }, 250);

    return () => {
      cancelled = true;
      window.clearTimeout(timeoutId);
    };
  }, [alsoAddAllItemsWithString, config, trimmedQuery]);

  function handleSelectAllTerms() {
    unselectedResults.forEach((candidate) => {
      onToggleTerm(selectedTermForCandidate(candidate));
    });
  }

  return (
    <section className="stack exploration-semantic-term-filter-builder">
      <label className="field">
        <span>Semantic {config.label.toLowerCase()} filter</span>
        <input
          className="input"
          disabled={loading}
          onChange={(event) => onQueryChange(event.target.value)}
          placeholder={config.placeholder}
          value={query}
        />
      </label>
      <p className="muted">
        Search the vectorized {config.pluralLabel} index, then select values to include in this set.
      </p>
      {showSimilarityThreshold ? (
        <label className="field exploration-semantic-term-similarity-threshold">
          <div className="card-row">
            <span>Similarity threshold</span>
            <span className="pill">{similarityThreshold.toFixed(2)}</span>
          </div>
          <input
            className="range-input"
            disabled={loading}
            max="0.95"
            min="0.5"
            onChange={(event) => setSimilarityThreshold(Number(event.target.value))}
            step="0.01"
            type="range"
            value={similarityThreshold}
          />
        </label>
      ) : null}
      {showSimilarityThreshold ? (
        <label className="exploration-add-string-matches-checkbox">
          <input
            checked={alsoAddAllItemsWithString}
            disabled={loading || !trimmedQuery}
            onChange={(event) => setAlsoAddAllItemsWithString(event.target.checked)}
            type="checkbox"
          />
          <span>also add all items with this string</span>
        </label>
      ) : null}

      {selectedTerms.length > 0 ? (
        <div className="stack">
          <strong>Selected {config.pluralLabel}</strong>
          <div className="tag-list">
            {selectedTerms.map((term) => (
              <button
                className="pill exploration-selected-term-chip"
                disabled={loading}
                key={term.id}
                onClick={() => onToggleTerm(term)}
                type="button"
              >
                {term.text}
              </button>
            ))}
          </div>
        </div>
      ) : null}

      {trimmedQuery ? (
        <div className="story-filter-value-panel">
          <div className="card-row">
            <div className="story-filter-value-summary">
              {searchStatus === "loading"
                ? `Searching related ${config.pluralLabel}...`
                : `Suggested ${config.pluralLabel} (${visibleResults.length})`}
            </div>
            {visibleResults.length > 0 ? (
              <button
                className="button button-ghost"
                disabled={loading || unselectedResults.length === 0}
                onClick={handleSelectAllTerms}
                type="button"
              >
                Select all {config.pluralLabel}
              </button>
            ) : null}
          </div>
          {searchError ? <p className="notice-inline">{searchError}</p> : null}
          {visibleResults.length > 0 ? (
            <div className="story-filter-value-list" role="group" aria-label={`Suggested ${config.pluralLabel}`}>
              {visibleResults.map((candidate) => {
                const selected = selectedTermIds.has(candidate.id);
                return (
                  <label
                    className={`story-filter-value-option ${selected ? "story-filter-value-option-selected" : ""}`}
                    key={candidate.id}
                  >
                    <input
                      checked={selected}
                      className="story-filter-value-checkbox"
                      disabled={loading}
                      onChange={() => onToggleTerm(selectedTermForCandidate(candidate))}
                      type="checkbox"
                    />
                    <span className="exploration-semantic-term-filter-option-text">
                      <strong>{candidate.text}</strong>
                      <span className="muted">
                        {candidate.story_count} stor{candidate.story_count === 1 ? "y" : "ies"} · score {candidate.score.toFixed(2)}
                      </span>
                    </span>
                  </label>
                );
              })}
            </div>
          ) : null}
          {searchStatus === "ready" && !searchError && visibleResults.length === 0 ? (
            <p className="muted">
              {results.length > 0
                ? `No ${config.pluralLabel} meet the current similarity threshold.`
                : `No similar ${config.pluralLabel} were returned for this query.`}
            </p>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
