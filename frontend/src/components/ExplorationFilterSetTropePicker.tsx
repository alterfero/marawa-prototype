import { useEffect, useMemo, useState } from "react";

import { getErrorMessage, searchTropes } from "../api/client";
import type { ExplorationAppliedTropeFilter, TropeSearchItem } from "../api/types";

type SearchStatus = "idle" | "loading" | "ready";

function selectedTropeForCandidate(candidate: TropeSearchItem): ExplorationAppliedTropeFilter {
  return {
    id: candidate.id,
    text: candidate.text,
    story_count: candidate.story_count,
  };
}

export function ExplorationFilterSetTropePicker({
  loading,
  query,
  selectedTropes,
  onQueryChange,
  onToggleTrope,
}: {
  loading: boolean;
  query: string;
  selectedTropes: ExplorationAppliedTropeFilter[];
  onQueryChange: (value: string) => void;
  onToggleTrope: (trope: ExplorationAppliedTropeFilter) => void;
}) {
  const [results, setResults] = useState<TropeSearchItem[]>([]);
  const [searchStatus, setSearchStatus] = useState<SearchStatus>("idle");
  const [searchError, setSearchError] = useState<string | null>(null);
  const selectedTropeIds = useMemo(() => new Set(selectedTropes.map((trope) => trope.id)), [selectedTropes]);
  const trimmedQuery = query.trim();
  const unselectedResults = useMemo(
    () => results.filter((candidate) => !selectedTropeIds.has(candidate.id)),
    [results, selectedTropeIds],
  );

  useEffect(() => {
    if (!trimmedQuery) {
      setResults([]);
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
          const result = await searchTropes({ query: trimmedQuery, limit: 8 });
          if (cancelled) {
            return;
          }
          setResults(result.items);
          setSearchStatus("ready");
        } catch (caughtError) {
          if (cancelled) {
            return;
          }
          setResults([]);
          setSearchStatus("ready");
          setSearchError(getErrorMessage(caughtError));
        }
      })();
    }, 250);

    return () => {
      cancelled = true;
      window.clearTimeout(timeoutId);
    };
  }, [trimmedQuery]);

  function handleSelectAllTropes() {
    unselectedResults.forEach((candidate) => {
      onToggleTrope(selectedTropeForCandidate(candidate));
    });
  }

  return (
    <section className="stack exploration-trope-filter-builder">
      <label className="field">
        <span>Semantic trope filter</span>
        <input
          className="input"
          disabled={loading}
          onChange={(event) => onQueryChange(event.target.value)}
          placeholder="Describe the trope cluster for this set"
          value={query}
        />
      </label>
      <p className="muted">
        Search the vectorized trope index, then select one or more tropes to include in this set.
      </p>

      {selectedTropes.length > 0 ? (
        <div className="stack">
          <strong>Selected tropes</strong>
          <div className="tag-list">
            {selectedTropes.map((trope) => (
              <button
                className="pill exploration-selected-trope-chip"
                disabled={loading}
                key={trope.id}
                onClick={() => onToggleTrope(trope)}
                type="button"
              >
                {trope.text}
              </button>
            ))}
          </div>
        </div>
      ) : null}

      {trimmedQuery ? (
        <div className="story-filter-value-panel">
          <div className="card-row">
            <div className="story-filter-value-summary">
              {searchStatus === "loading" ? "Searching related tropes..." : "Suggested tropes"}
            </div>
            {results.length > 0 ? (
              <button
                className="button button-ghost"
                disabled={loading || unselectedResults.length === 0}
                onClick={handleSelectAllTropes}
                type="button"
              >
                Select all tropes
              </button>
            ) : null}
          </div>
          {searchError ? <p className="notice-inline">{searchError}</p> : null}
          {results.length > 0 ? (
            <div className="story-filter-value-list" role="group" aria-label="Suggested tropes">
              {results.map((candidate) => {
                const selected = selectedTropeIds.has(candidate.id);
                return (
                  <label
                    className={`story-filter-value-option ${selected ? "story-filter-value-option-selected" : ""}`}
                    key={candidate.id}
                  >
                    <input
                      checked={selected}
                      className="story-filter-value-checkbox"
                      disabled={loading}
                      onChange={() => onToggleTrope(selectedTropeForCandidate(candidate))}
                      type="checkbox"
                    />
                    <span className="exploration-trope-filter-option-text">
                      <strong>{candidate.text}</strong>
                      <span className="muted">
                        {candidate.story_count} stor{candidate.story_count === 1 ? "y" : "ies"} · score{" "}
                        {candidate.score.toFixed(2)}
                      </span>
                    </span>
                  </label>
                );
              })}
            </div>
          ) : null}
          {searchStatus === "ready" && !searchError && results.length === 0 ? (
            <p className="muted">No similar tropes were returned for this query.</p>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
