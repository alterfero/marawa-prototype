import { useEffect, useMemo, useState } from "react";

import {
  canonicalizeKeywords,
  createCanonicalKeyword,
  deleteKeyword,
  deleteUnusedKeywords,
  getCanonicalKeywords,
  getErrorMessage,
  getKeywordDetail,
  getNearDuplicateKeywords,
  searchKeywords,
  updateKeywordConfirmationStatus,
  validateKeywordMerges,
} from "../api/client";
import { ConfirmationStatusSwitch } from "../components/ConfirmationStatusSwitch";
import { TermCard } from "../components/TermCard";
import type {
  CanonicalKeywordListItem,
  KeywordConfirmationStatus,
  NearDuplicateKeywordListResponse,
  NearDuplicateKeywordPair,
  SearchItem,
} from "../api/types";
import { useDatasetMaintenance } from "../maintenance";

interface PageNotice {
  tone: "error" | "success";
  title: string;
  body?: string;
}

interface PendingMergeDecision {
  pairId: string;
  source: CanonicalKeywordListItem;
  target: CanonicalKeywordListItem;
}

type PairDirection = "forward" | "reverse";

function pairKey(pair: NearDuplicateKeywordPair): string {
  return `${pair.source_keyword.id}:${pair.target_keyword.id}`;
}

function emptyPairsLabel(pairs: NearDuplicateKeywordListResponse | null): string {
  if (!pairs || pairs.artifact_version === null) {
    return "No near-duplicate keyword pairs are available yet. Run Rebuild, then refresh.";
  }
  return "No near-duplicate keyword pairs are available for the current dataset.";
}

export function KeywordCurationPage() {
  const maintenance = useDatasetMaintenance();
  const [pairs, setPairs] = useState<NearDuplicateKeywordListResponse | null>(null);
  const [unusedKeywords, setUnusedKeywords] = useState<CanonicalKeywordListItem[]>([]);
  const [unusedQuery, setUnusedQuery] = useState("");
  const [pairDirections, setPairDirections] = useState<Record<string, PairDirection>>({});
  const [targetOverrides, setTargetOverrides] = useState<Record<string, CanonicalKeywordListItem>>({});
  const [pendingMerges, setPendingMerges] = useState<PendingMergeDecision[]>([]);
  const [editingPairId, setEditingPairId] = useState<string | null>(null);
  const [targetQuery, setTargetQuery] = useState("");
  const [targetResults, setTargetResults] = useState<SearchItem[]>([]);
  const [targetSearchStatus, setTargetSearchStatus] = useState<"idle" | "loading" | "ready">("idle");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<PageNotice | null>(null);
  const [editorNotice, setEditorNotice] = useState<PageNotice | null>(null);
  const mutationDisabled = busy || maintenance.active;

  async function loadPairs() {
    setPairs(await getNearDuplicateKeywords());
  }

  async function loadKeywords(query = unusedQuery) {
    const unused = await getCanonicalKeywords({ unused_only: true, q: query, limit: 100 });
    setUnusedKeywords(unused);
  }

  async function refresh(options?: { clearNotice?: boolean }) {
    try {
      setLoading(true);
      if (options?.clearNotice !== false) {
        setNotice(null);
      }
      await Promise.all([loadPairs(), loadKeywords()]);
    } catch (caughtError) {
      setNotice({ tone: "error", title: "Could not load keyword curation data", body: getErrorMessage(caughtError) });
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void refresh();
  }, []);

  useEffect(() => {
    const timeoutId = window.setTimeout(() => {
      void loadKeywords(unusedQuery).catch((caughtError) => {
        setNotice({ tone: "error", title: "Could not load unused keywords", body: getErrorMessage(caughtError) });
      });
    }, 250);
    return () => window.clearTimeout(timeoutId);
  }, [unusedQuery]);

  useEffect(() => {
    if (!editingPairId || !targetQuery.trim()) {
      setTargetResults([]);
      setTargetSearchStatus("idle");
      return;
    }

    let cancelled = false;
    const timeoutId = window.setTimeout(() => {
      void (async () => {
        try {
          setTargetSearchStatus("loading");
          const result = await searchKeywords({ query: targetQuery.trim(), limit: 8 });
          if (!cancelled) {
            setTargetResults(result.items);
            setTargetSearchStatus("ready");
          }
        } catch (caughtError) {
          if (!cancelled) {
            setTargetResults([]);
            setTargetSearchStatus("ready");
            setEditorNotice({ tone: "error", title: "Could not search target keywords", body: getErrorMessage(caughtError) });
          }
        }
      })();
    }, 250);
    return () => {
      cancelled = true;
      window.clearTimeout(timeoutId);
    };
  }, [editingPairId, targetQuery]);

  const pendingSourceIds = useMemo(() => new Set(pendingMerges.map((merge) => merge.source.id)), [pendingMerges]);
  const editingPair = editingPairId ? pairs?.items.find((pair) => pairKey(pair) === editingPairId) ?? null : null;
  const editingSelection = editingPair ? selectedPairTerms(editingPair) : null;

  function selectedPairTerms(pair: NearDuplicateKeywordPair) {
    const direction = pairDirections[pairKey(pair)] ?? "forward";
    const { source, target } =
      direction === "reverse"
        ? { source: pair.target_keyword, target: pair.source_keyword }
        : { source: pair.source_keyword, target: pair.target_keyword };
    return {
      source,
      target: targetOverrides[pairKey(pair)] ?? target,
    };
  }

  function resetTargetEditor() {
    setEditingPairId(null);
    setTargetQuery("");
    setTargetResults([]);
    setTargetSearchStatus("idle");
    setEditorNotice(null);
  }

  function swapPairDirection(pair: NearDuplicateKeywordPair) {
    const id = pairKey(pair);
    setPairDirections((current) => ({
      ...current,
      [id]: current[id] === "reverse" ? "forward" : "reverse",
    }));
    setTargetOverrides((current) => {
      if (!(id in current)) {
        return current;
      }
      const next = { ...current };
      delete next[id];
      return next;
    });
    if (editingPairId === id) {
      resetTargetEditor();
    }
  }

  function stageMerge(pair: NearDuplicateKeywordPair) {
    const { source, target } = selectedPairTerms(pair);
    const id = pairKey(pair);
    setPendingMerges((current) => {
      if (current.some((merge) => merge.pairId === id || merge.source.id === source.id)) {
        return current;
      }
      return [...current, { pairId: id, source, target }];
    });
    setNotice(null);
  }

  function setPairTarget(pair: NearDuplicateKeywordPair, target: CanonicalKeywordListItem) {
    const { source } = selectedPairTerms(pair);
    if (target.id === source.id) {
      setEditorNotice({
        tone: "error",
        title: "Target must differ from source",
        body: "Choose or create a different keyword for this merge target.",
      });
      return false;
    }
    const id = pairKey(pair);
    setTargetOverrides((current) => ({ ...current, [id]: target }));
    setPendingMerges((current) => current.map((merge) => (merge.pairId === id ? { ...merge, target } : merge)));
    return true;
  }

  async function useExistingTarget(pair: NearDuplicateKeywordPair, keywordId: string) {
    try {
      setBusy(true);
      const target = await getKeywordDetail(keywordId);
      if (setPairTarget(pair, target)) {
        resetTargetEditor();
        setNotice({ tone: "success", title: "Merge target updated", body: `Set ${target.text} as the merge target.` });
      }
    } catch (caughtError) {
      setEditorNotice({ tone: "error", title: "Could not set merge target", body: getErrorMessage(caughtError) });
    } finally {
      setBusy(false);
    }
  }

  async function createTarget(pair: NearDuplicateKeywordPair) {
    if (!targetQuery.trim()) {
      return;
    }
    try {
      setBusy(true);
      const result = await createCanonicalKeyword(targetQuery.trim());
      if (setPairTarget(pair, result.keyword)) {
        resetTargetEditor();
        setNotice({
          tone: "success",
          title: result.created ? "Target keyword created" : "Existing keyword reused",
          body: `Set ${result.keyword.text} as the merge target.`,
        });
      }
    } catch (caughtError) {
      setEditorNotice({ tone: "error", title: "Could not set merge target", body: getErrorMessage(caughtError) });
    } finally {
      setBusy(false);
    }
  }

  async function keepBoth(pair: NearDuplicateKeywordPair) {
    const { source, target } = selectedPairTerms(pair);
    try {
      setBusy(true);
      await canonicalizeKeywords({
        keywords: [
          { keyword_id: source.id, expected_keyword_version: source.version },
          { keyword_id: target.id, expected_keyword_version: target.version },
        ],
      });
      setPendingMerges((current) => current.filter((merge) => merge.pairId !== pairKey(pair)));
      setNotice({
        tone: "success",
        title: "Both keywords are canonical",
        body: `Marked ${source.text} and ${target.text} as canonical.`,
      });
      await refresh({ clearNotice: false });
    } catch (caughtError) {
      setNotice({ tone: "error", title: "Could not mark both keywords as canonical", body: getErrorMessage(caughtError) });
    } finally {
      setBusy(false);
    }
  }

  async function updateTargetStatus(target: CanonicalKeywordListItem, nextStatus: KeywordConfirmationStatus) {
    try {
      setBusy(true);
      await updateKeywordConfirmationStatus(target.id, {
        expected_keyword_version: target.version,
        confirmation_status: nextStatus,
      });
      await refresh({ clearNotice: false });
      setNotice({ tone: "success", title: "Keyword updated", body: `Confirmation status set to ${nextStatus}.` });
    } catch (caughtError) {
      setNotice({ tone: "error", title: "Could not update keyword confirmation", body: getErrorMessage(caughtError) });
    } finally {
      setBusy(false);
    }
  }

  function startEditingTarget(pair: NearDuplicateKeywordPair, target: CanonicalKeywordListItem) {
    setEditingPairId(pairKey(pair));
    setTargetQuery(target.text);
    setEditorNotice(null);
  }

  async function deleteTargetKeyword(pair: NearDuplicateKeywordPair, target: CanonicalKeywordListItem) {
    const shouldDelete = window.confirm(
      target.story_count > 0
        ? `Delete keyword "${target.text}" from all ${target.story_count} stor${target.story_count === 1 ? "y" : "ies"}?`
        : `Delete unused keyword "${target.text}"?`,
    );
    if (!shouldDelete) {
      return;
    }

    try {
      setBusy(true);
      await deleteKeyword({
        keyword_id: target.id,
        expected_keyword_version: target.version,
        remove_from_all_stories: target.story_count > 0,
      });
      setPendingMerges((current) => current.filter((merge) => merge.source.id !== target.id && merge.target.id !== target.id));
      setTargetOverrides((current) =>
        Object.fromEntries(Object.entries(current).filter(([, override]) => override.id !== target.id)),
      );
      if (editingPairId === pairKey(pair)) {
        resetTargetEditor();
      }
      setNotice({ tone: "success", title: "Keyword deleted", body: `Deleted ${target.text}.` });
      await refresh({ clearNotice: false });
    } catch (caughtError) {
      setNotice({ tone: "error", title: "Could not delete keyword", body: getErrorMessage(caughtError) });
    } finally {
      setBusy(false);
    }
  }

  async function validateMerges() {
    if (!pendingMerges.length || !window.confirm(`Validate ${pendingMerges.length} keyword merge decision${pendingMerges.length === 1 ? "" : "s"}?`)) {
      return;
    }
    try {
      setBusy(true);
      const result = await validateKeywordMerges({
        merges: pendingMerges.map((merge) => ({
          source_keyword_id: merge.source.id,
          target_keyword_id: merge.target.id,
        })),
      });
      setPendingMerges([]);
      setTargetOverrides({});
      setNotice({
        tone: "success",
        title: "Keyword merge batch applied",
        body: `Validated ${result.merge_count} merge decision${result.merge_count === 1 ? "" : "s"} affecting ${result.affected_story_count} stor${result.affected_story_count === 1 ? "y" : "ies"}.`,
      });
      await refresh({ clearNotice: false });
    } catch (caughtError) {
      setNotice({ tone: "error", title: "Keyword merge validation failed", body: getErrorMessage(caughtError) });
    } finally {
      setBusy(false);
    }
  }

  async function deleteUnusedKeyword(keyword: CanonicalKeywordListItem) {
    if (!window.confirm(`Delete unused keyword "${keyword.text}"?`)) {
      return;
    }
    try {
      setBusy(true);
      await deleteKeyword({
        keyword_id: keyword.id,
        expected_keyword_version: keyword.version,
        remove_from_all_stories: false,
      });
      setNotice({ tone: "success", title: "Unused keyword deleted", body: `Deleted ${keyword.text}.` });
      await refresh({ clearNotice: false });
    } catch (caughtError) {
      setNotice({ tone: "error", title: "Could not delete keyword", body: getErrorMessage(caughtError) });
    } finally {
      setBusy(false);
    }
  }

  async function deleteAllUnusedKeywords() {
    if (!window.confirm("Delete all unused keywords? This permanently removes every keyword without story assignments.")) {
      return;
    }
    try {
      setBusy(true);
      const result = await deleteUnusedKeywords();
      setNotice({
        tone: "success",
        title: "Unused keywords deleted",
        body: result.deleted_keyword_count ? `Deleted ${result.deleted_keyword_count} unused keywords.` : "There were no unused keywords to delete.",
      });
      await refresh({ clearNotice: false });
    } catch (caughtError) {
      setNotice({ tone: "error", title: "Could not delete unused keywords", body: getErrorMessage(caughtError) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="page-stack">
      <section className="panel">
        <div className="panel-header">
          <div>
            <h1>Keyword curation</h1>
            <p className="muted">Merge near-duplicate keywords, confirm terms to keep, and delete unused keywords.</p>
          </div>
          <button className="button button-ghost" disabled={loading || busy} onClick={() => void refresh()} type="button">
            {loading ? "Loading..." : "Refresh"}
          </button>
        </div>
      </section>

      {notice ? (
        <section className={`notice ${notice.tone === "error" ? "notice-error" : "notice-success"}`}>
          <strong className="notice-title">{notice.title}</strong>
          {notice.body ? <p>{notice.body}</p> : null}
        </section>
      ) : null}

      <section className="panel">
        <div className="panel-header">
          <h2>Pending merge batch</h2>
          <span className="pill">{pendingMerges.length} pending</span>
        </div>
        {pendingMerges.length ? (
          <>
            <div className="stack">
              {pendingMerges.map((merge) => (
                <article className="card" key={merge.pairId}>
                  <div className="panel-header">
                    <div>
                      <h3>Merge {merge.source.text} into {merge.target.text}</h3>
                      <p className="muted">{merge.source.story_count} stor{merge.source.story_count === 1 ? "y" : "ies"} affected</p>
                    </div>
                    <button
                      className="button button-ghost"
                      disabled={mutationDisabled}
                      onClick={() => setPendingMerges((current) => current.filter((item) => item.pairId !== merge.pairId))}
                      type="button"
                    >
                      Remove
                    </button>
                  </div>
                </article>
              ))}
            </div>
            <div className="button-row wrap-row">
              <button className="button button-ghost" disabled={mutationDisabled} onClick={() => setPendingMerges([])} type="button">
                Clear batch
              </button>
              <button className="button" disabled={mutationDisabled} onClick={() => void validateMerges()} type="button">
                Validate all merges
              </button>
            </div>
          </>
        ) : (
          <p className="muted">No merge decisions are staged yet.</p>
        )}
      </section>

      <section className="two-column-layout">
        <div className="panel">
          <div className="panel-header">
            <h2>Near-duplicate keyword pairs</h2>
            <span className="pill">{pairs?.total ?? 0} pairs</span>
          </div>
          <div className="stack">
            {pairs?.items.length ? (
              pairs.items.map((pair) => {
                const id = pairKey(pair);
                const { source, target } = selectedPairTerms(pair);
                const isPending = pendingMerges.some((merge) => merge.pairId === id);
                const canStage = !isPending && !pendingSourceIds.has(source.id);
                return (
                  <article className="card" key={id}>
                    <div className="panel-header">
                      <h3>Similarity {pair.similarity_score.toFixed(2)}</h3>
                      {target.confirmation_status !== "canonical" ? (
                        <button
                          className="button button-ghost"
                          disabled={mutationDisabled || isPending}
                          onClick={() => swapPairDirection(pair)}
                          type="button"
                        >
                          Swap source and target
                        </button>
                      ) : null}
                    </div>
                    <div className="field-grid">
                      <div className="stack"><strong>Source</strong><TermCard className="subdued" term={source} /></div>
                      <div className="stack">
                        <strong>Target</strong>
                        <TermCard
                          className="subdued"
                          term={target}
                        >
                          <div className="keyword-confirmation-control">
                            <ConfirmationStatusSwitch
                              ariaLabel={`Confirmation status for ${target.text}`}
                              className="keyword-confirmation-status-switch"
                              disabled={mutationDisabled}
                              onChange={(nextStatus) => void updateTargetStatus(target, nextStatus)}
                              value={target.confirmation_status}
                            />
                            <div className="button-row wrap-row keyword-curation-target-actions">
                              <button
                                className="button button-ghost"
                                disabled={mutationDisabled}
                                onClick={() => startEditingTarget(pair, target)}
                                type="button"
                              >
                                Edit
                              </button>
                              <button
                                className="button button-danger"
                                disabled={mutationDisabled}
                                onClick={() => void deleteTargetKeyword(pair, target)}
                                type="button"
                              >
                                Delete
                              </button>
                            </div>
                          </div>
                        </TermCard>
                      </div>
                    </div>
                    <p className="muted">{source.story_count} stor{source.story_count === 1 ? "y" : "ies"} affected</p>
                    <div className="button-row wrap-row">
                      <button
                        className="button"
                        disabled={mutationDisabled || !canStage}
                        onClick={() => stageMerge(pair)}
                        type="button"
                      >
                        {isPending ? "In merge batch" : pendingSourceIds.has(source.id) ? "Source already in batch" : "Add merge to batch"}
                      </button>
                      <button className="button button-ghost" disabled={mutationDisabled} onClick={() => void keepBoth(pair)} type="button">
                        Keep both
                      </button>
                      <button
                        className="button button-ghost"
                        disabled={mutationDisabled}
                        onClick={() => startEditingTarget(pair, target)}
                        type="button"
                      >
                        Change target
                      </button>
                    </div>
                  </article>
                );
              })
            ) : (
              <p className="muted">{emptyPairsLabel(pairs)}</p>
            )}
          </div>
        </div>

        <aside className="panel">
          <div className="panel-header">
            <h2>Unused keywords</h2>
            <button className="button button-danger" disabled={mutationDisabled} onClick={() => void deleteAllUnusedKeywords()} type="button">
              Delete all unused
            </button>
          </div>
          <label className="field">
            <span>Search unused keywords</span>
            <input className="input" onChange={(event) => setUnusedQuery(event.target.value)} placeholder="Filter unused keywords" value={unusedQuery} />
          </label>
          <div className="stack">
            {unusedKeywords.length ? (
              unusedKeywords.map((keyword) => (
                <TermCard
                  key={keyword.id}
                  term={keyword}
                  actions={<button className="button button-danger" disabled={mutationDisabled} onClick={() => void deleteUnusedKeyword(keyword)} type="button">Delete</button>}
                />
              ))
            ) : <p className="muted">No unused keywords match the current filter.</p>}
          </div>
        </aside>
      </section>

      {editingPair ? (
        <div className="modal-backdrop" onClick={resetTargetEditor} role="presentation">
          <section aria-labelledby="keyword-curation-target-title" aria-modal="true" className="modal-shell" onClick={(event) => event.stopPropagation()} role="dialog">
            <div className="panel-header">
              <h2 id="keyword-curation-target-title">Edit merge target</h2>
              <button className="button button-ghost" disabled={mutationDisabled} onClick={resetTargetEditor} type="button">Close</button>
            </div>
            {editorNotice ? (
              <section className={`notice ${editorNotice.tone === "error" ? "notice-error" : "notice-success"}`}>
                <strong className="notice-title">{editorNotice.title}</strong>
                {editorNotice.body ? <p>{editorNotice.body}</p> : null}
              </section>
            ) : null}
            <label className="field">
              <span>Target keyword query</span>
              <input className="input" disabled={mutationDisabled} onChange={(event) => setTargetQuery(event.target.value)} placeholder="Type a target keyword to search or create" value={targetQuery} />
            </label>
            <div className="button-row wrap-row">
              <button className="button" disabled={mutationDisabled || !targetQuery.trim()} onClick={() => void createTarget(editingPair)} type="button">Keep typed keyword</button>
              <button
                className="button button-ghost"
                disabled={mutationDisabled}
                onClick={() => {
                  setTargetOverrides((current) => {
                    const next = { ...current };
                    delete next[pairKey(editingPair)];
                    return next;
                  });
                  resetTargetEditor();
                }}
                type="button"
              >
                Reset target
              </button>
            </div>
            <div className="stack">
              <div className="panel-header"><h3>Similar existing keywords</h3><span className="pill">{targetSearchStatus === "loading" ? "searching" : `${targetResults.length} results`}</span></div>
              {targetSearchStatus === "loading" ? <p className="muted">Searching keywords...</p> : null}
              {targetQuery.trim() && targetSearchStatus === "ready" && targetResults.length === 0 ? <p className="muted">No similar keywords were returned for this query.</p> : null}
              <div className="modal-story-list">
                {targetResults.map((result) => (
                  <TermCard
                    key={result.id}
                    meta={`Similarity ${result.score.toFixed(2)}`}
                    term={result}
                    actions={
                      <button
                        className="button button-ghost"
                        disabled={mutationDisabled || result.id === editingSelection?.source.id}
                        onClick={() => void useExistingTarget(editingPair, result.id)}
                        type="button"
                      >
                        {result.id === editingSelection?.source.id ? "Source keyword" : "Use existing keyword"}
                      </button>
                    }
                  />
                ))}
              </div>
            </div>
          </section>
        </div>
      ) : null}
    </div>
  );
}
