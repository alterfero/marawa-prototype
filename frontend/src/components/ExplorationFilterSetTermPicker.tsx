import { useEffect, useId, useMemo, useState, type ReactNode } from "react";

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

function splitSimilarityQueries(value: string): string[] {
  const seen = new Set<string>();
  return value
    .split(",")
    .map((item) => item.normalize("NFC").replace(/\s+/g, " ").trim())
    .filter((item) => {
      const marker = item.toLocaleLowerCase();
      if (!marker || seen.has(marker)) {
        return false;
      }
      seen.add(marker);
      return true;
    });
}

function mergeSearchItems(itemGroups: SearchItem[][]): SearchItem[] {
  const itemsById = new Map<string, SearchItem>();
  itemGroups.flat().forEach((item) => {
    const existing = itemsById.get(item.id);
    if (!existing || item.score > existing.score) {
      itemsById.set(item.id, item);
    }
  });
  return [...itemsById.values()].sort(
    (left, right) => right.score - left.score || left.text.localeCompare(right.text) || left.id.localeCompare(right.id),
  );
}

function InstructionPopin({ label, children }: { label: string; children: ReactNode }) {
  return (
    <span className="exploration-instruction-popin">
      <button aria-label={label} className="exploration-instruction-popin-trigger" type="button">
        ?
      </button>
      <span className="exploration-instruction-popin-content" role="tooltip">
        {children}
      </span>
    </span>
  );
}

export function ExplorationFilterSetTermPicker({
  kind,
  loading,
  query,
  selectedTerms,
  allowMultipleQueries = false,
  selectedTermsScrollable = false,
  showSimilarityThreshold = true,
  onQueryChange,
  onToggleTerm,
}: {
  kind: ExplorationSemanticTermKind;
  loading: boolean;
  query: string;
  selectedTerms: ExplorationAppliedTermFilter[];
  allowMultipleQueries?: boolean;
  selectedTermsScrollable?: boolean;
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
  const queryInputId = useId();
  const config = TERM_CONFIG[kind];
  const selectedTermIds = useMemo(() => new Set(selectedTerms.map((term) => term.id)), [selectedTerms]);
  const similarityQueries = useMemo(() => {
    if (allowMultipleQueries) {
      return splitSimilarityQueries(query);
    }
    const normalizedQuery = query.normalize("NFC").replace(/\s+/g, " ").trim();
    return normalizedQuery ? [normalizedQuery] : [];
  }, [allowMultipleQueries, query]);
  const similarityQueryKey = similarityQueries.join("\u0000");
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
    if (similarityQueries.length === 0) {
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
          const searchResults = await Promise.all(
            similarityQueries.map((similarityQuery) =>
              config.search({
                query: similarityQuery,
                limit: 8,
                include_string_matches: alsoAddAllItemsWithString,
              }),
            ),
          );
          if (cancelled) {
            return;
          }
          setResults(mergeSearchItems(searchResults.map((result) => result.items)));
          setStringMatchResults(mergeSearchItems(searchResults.map((result) => result.string_match_items)));
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
  }, [alsoAddAllItemsWithString, config, similarityQueries, similarityQueryKey]);

  function handleSelectAllTerms() {
    unselectedResults.forEach((candidate) => {
      onToggleTerm(selectedTermForCandidate(candidate));
    });
  }

  return (
    <section className="stack exploration-semantic-term-filter-builder">
      <div className="field">
        <div className="exploration-semantic-term-label">
          <label htmlFor={queryInputId}>Semantic {config.label.toLowerCase()} filter</label>
          <InstructionPopin label={`${config.label} filter instructions`}>
            <span>Search the vectorized {config.pluralLabel} index, then select values to include in this set.</span>
            <span>Selections across themes, tropes, and keywords are combined additively.</span>
            {allowMultipleQueries ? (
              <span>Separate additive searches with commas; spaces around each search are ignored.</span>
            ) : null}
          </InstructionPopin>
        </div>
        <input
          className="input"
          disabled={loading}
          id={queryInputId}
          onChange={(event) => onQueryChange(event.target.value)}
          onBlur={allowMultipleQueries ? () => onQueryChange(similarityQueries.join(", ")) : undefined}
          placeholder={config.placeholder}
          value={query}
        />
      </div>
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
            disabled={loading || similarityQueries.length === 0}
            onChange={(event) => setAlsoAddAllItemsWithString(event.target.checked)}
            type="checkbox"
          />
          <span>also add all items with this string</span>
        </label>
      ) : null}

      {selectedTerms.length > 0 ? (
        <div className="stack">
          <strong>Selected {config.pluralLabel}</strong>
          <div className={selectedTermsScrollable ? "exploration-selected-term-list" : "tag-list"}>
            {selectedTerms.map((term) => (
              <button
                className="pill exploration-selected-term-chip"
                disabled={loading}
                key={term.id}
                onClick={() => onToggleTerm(term)}
                title={term.text}
                type="button"
              >
                {term.text}
              </button>
            ))}
          </div>
        </div>
      ) : null}

      {similarityQueries.length > 0 ? (
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
