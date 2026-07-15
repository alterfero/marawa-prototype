import { useEffect, useMemo, useState } from "react";

import {
  createCanonicalTrope,
  deleteTrope,
  getCanonicalTropes,
  getErrorMessage,
  getJob,
  getNearDuplicateTropes,
  searchTropes,
  validateTropeMerges,
} from "../api/client";
import { TropeCard } from "../components/TropeCard";
import type {
  CanonicalTropeListItem,
  JobDetail,
  JobSummary,
  NearDuplicateTropeListResponse,
  NearDuplicateTropePair,
  TropeSearchItem,
  TropeSummary,
} from "../api/types";

const TERMINAL_JOB_STATUSES = new Set(["succeeded", "failed", "cancelled"]);
const JOB_POLL_INTERVAL_MS = 2000;

interface PageNotice {
  tone: "error" | "success";
  title: string;
  body?: string;
}

interface PendingMergeDecision {
  pair_id: string;
  source_trope_id: string;
  source_text: string;
  source_story_count: number;
  target_trope_id: string;
  target_text: string;
  target_story_count: number;
  similarity_score: number;
}

interface PairTargetOverride {
  id: string;
  text: string;
  story_count: number;
}

type PairDirection = "forward" | "reverse";

function formatJobStatus(job: JobSummary | JobDetail | null): string {
  if (!job) {
    return "none";
  }
  return job.status.split("_").join(" ");
}

function isJobDetail(job: JobSummary | JobDetail | null): job is JobDetail {
  return Boolean(job && "attempts" in job);
}

function pairKey(pair: NearDuplicateTropePair): string {
  return `${pair.source_trope.id}:${pair.target_trope.id}`;
}

function nearDuplicateEmptyLabel(pairs: NearDuplicateTropeListResponse | null): string {
  if (!pairs || pairs.artifact_version === null) {
    return "No near-duplicate trope pairs are available yet. Wait for a rebuild to finish, then refresh.";
  }
  return "No near-duplicate trope pairs are available for the current dataset.";
}

function directionalPair(pair: NearDuplicateTropePair, direction: PairDirection) {
  if (direction === "reverse") {
    return {
      source: pair.target_trope,
      target: pair.source_trope,
    };
  }
  return {
    source: pair.source_trope,
    target: pair.target_trope,
  };
}

function buildPendingMergeDecision(
  pairId: string,
  source: TropeSummary,
  target: TropeSummary,
  similarityScore: number,
): PendingMergeDecision {
  return {
    pair_id: pairId,
    source_trope_id: source.id,
    source_text: source.text,
    source_story_count: source.story_count,
    target_trope_id: target.id,
    target_text: target.text,
    target_story_count: target.story_count,
    similarity_score: similarityScore,
  };
}

function resolvePairSelection(
  pair: NearDuplicateTropePair,
  direction: PairDirection,
  targetOverride?: PairTargetOverride,
): { source: TropeSummary; target: TropeSummary } {
  const { source, target } = directionalPair(pair, direction);
  if (!targetOverride || targetOverride.id === source.id) {
    return { source, target };
  }
  return {
    source,
    target: {
      id: targetOverride.id,
      text: targetOverride.text,
      story_count: targetOverride.story_count,
    },
  };
}

export function CurationPage() {
  const [pairs, setPairs] = useState<NearDuplicateTropeListResponse | null>(null);
  const [unusedQuery, setUnusedQuery] = useState("");
  const [unusedTropes, setUnusedTropes] = useState<CanonicalTropeListItem[]>([]);
  const [pairDirections, setPairDirections] = useState<Record<string, PairDirection>>({});
  const [targetOverrides, setTargetOverrides] = useState<Record<string, PairTargetOverride>>({});
  const [pendingMerges, setPendingMerges] = useState<PendingMergeDecision[]>([]);
  const [editingPairId, setEditingPairId] = useState<string | null>(null);
  const [editingTargetQuery, setEditingTargetQuery] = useState("");
  const [editingTargetResults, setEditingTargetResults] = useState<TropeSearchItem[]>([]);
  const [editingTargetSearchStatus, setEditingTargetSearchStatus] = useState<"idle" | "loading" | "ready">("idle");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<PageNotice | null>(null);
  const [modalNotice, setModalNotice] = useState<PageNotice | null>(null);
  const [currentJobId, setCurrentJobId] = useState<string | null>(null);
  const [jobDetail, setJobDetail] = useState<JobDetail | null>(null);
  const [jobError, setJobError] = useState<string | null>(null);

  function resetTargetEditor() {
    setEditingPairId(null);
    setEditingTargetQuery("");
    setEditingTargetResults([]);
    setEditingTargetSearchStatus("idle");
    setModalNotice(null);
  }

  async function loadPairs() {
    const result = await getNearDuplicateTropes();
    setPairs(result);
  }

  async function loadUnusedTropes(query = unusedQuery) {
    const result = await getCanonicalTropes({
      unused_only: true,
      q: query,
      limit: 100,
    });
    setUnusedTropes(result);
  }

  async function refresh(options?: { clearNotice?: boolean }) {
    try {
      setLoading(true);
      if (options?.clearNotice !== false) {
        setNotice(null);
      }
      await Promise.all([loadPairs(), loadUnusedTropes()]);
    } catch (caughtError) {
      setNotice({
        tone: "error",
        title: "Could not load curation data",
        body: getErrorMessage(caughtError),
      });
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void refresh();
  }, []);

  useEffect(() => {
    const timeoutId = window.setTimeout(() => {
      void loadUnusedTropes(unusedQuery).catch((caughtError) => {
        setNotice({
          tone: "error",
          title: "Could not load unused tropes",
          body: getErrorMessage(caughtError),
        });
      });
    }, 250);

    return () => {
      window.clearTimeout(timeoutId);
    };
  }, [unusedQuery]);

  useEffect(() => {
    if (!editingPairId) {
      setEditingTargetResults([]);
      setEditingTargetSearchStatus("idle");
      return;
    }

    const trimmedQuery = editingTargetQuery.trim();
    if (!trimmedQuery) {
      setEditingTargetResults([]);
      setEditingTargetSearchStatus("idle");
      return;
    }

    let cancelled = false;
    const timeoutId = window.setTimeout(() => {
      void (async () => {
        try {
          setEditingTargetSearchStatus("loading");
          const result = await searchTropes({ query: trimmedQuery, limit: 8 });
          if (cancelled) {
            return;
          }
          setEditingTargetResults(result.items);
          setEditingTargetSearchStatus("ready");
        } catch (caughtError) {
          if (cancelled) {
            return;
          }
          setEditingTargetResults([]);
          setEditingTargetSearchStatus("ready");
          setModalNotice({
            tone: "error",
            title: "Could not search target tropes",
            body: getErrorMessage(caughtError),
          });
        }
      })();
    }, 250);

    return () => {
      cancelled = true;
      window.clearTimeout(timeoutId);
    };
  }, [editingPairId, editingTargetQuery]);

  useEffect(() => {
    if (!editingPairId || !pairs?.items.some((pair) => pairKey(pair) === editingPairId)) {
      resetTargetEditor();
    }
  }, [editingPairId, pairs]);

  useEffect(() => {
    if (!currentJobId) {
      setJobError(null);
      return;
    }

    let cancelled = false;
    let timerId: number | undefined;

    const pollJob = async () => {
      try {
        const nextJob = await getJob(currentJobId);
        if (cancelled) {
          return;
        }
        setJobDetail(nextJob);
        setJobError(null);

        if (TERMINAL_JOB_STATUSES.has(nextJob.status)) {
          try {
            await refresh();
          } catch {
            // refresh already sets notice on failure
          }
          return;
        }

        timerId = window.setTimeout(() => {
          void pollJob();
        }, JOB_POLL_INTERVAL_MS);
      } catch (caughtError) {
        if (cancelled) {
          return;
        }
        setJobError(getErrorMessage(caughtError));
      }
    };

    void pollJob();

    return () => {
      cancelled = true;
      if (timerId) {
        window.clearTimeout(timerId);
      }
    };
  }, [currentJobId]);

  function handleStageMerge(pair: NearDuplicateTropePair) {
    const pairId = pairKey(pair);
    const direction = pairDirections[pairId] || "forward";
    const { source, target } = resolvePairSelection(pair, direction, targetOverrides[pairId]);
    const nextDecision = buildPendingMergeDecision(pairId, source, target, pair.similarity_score);

    setPendingMerges((current) => {
      if (current.some((merge) => merge.pair_id === nextDecision.pair_id)) {
        return current;
      }
      if (current.some((merge) => merge.source_trope_id === nextDecision.source_trope_id)) {
        return current;
      }
      return [...current, nextDecision];
    });
    setNotice(null);
  }

  function handleStartEditingTarget(pairId: string, currentTarget: TropeSummary) {
    setEditingPairId(pairId);
    setEditingTargetQuery(currentTarget.text);
    setEditingTargetResults([]);
    setEditingTargetSearchStatus("idle");
    setNotice(null);
    setModalNotice(null);
  }

  function applyPairTargetSelection(pair: NearDuplicateTropePair, nextTarget: TropeSummary): boolean {
    const pairId = pairKey(pair);
    const direction = pairDirections[pairId] || "forward";
    const defaultPairTerms = directionalPair(pair, direction);
    const source = defaultPairTerms.source;
    const defaultTarget = defaultPairTerms.target;

    if (nextTarget.id === source.id) {
      setModalNotice({
        tone: "error",
        title: "Target must differ from source",
        body: "Choose or create a different trope for this merge target.",
      });
      return false;
    }

    if (nextTarget.id === defaultTarget.id) {
      handleResetPairTarget(pairId, source, defaultTarget, pair.similarity_score);
      return true;
    }

    handleUpdatePairTarget(pairId, source, nextTarget, pair.similarity_score);
    return true;
  }

  function handleUpdatePairTarget(
    pairId: string,
    source: TropeSummary,
    target: TropeSummary,
    similarityScore: number,
  ) {
    setTargetOverrides((current) => ({
      ...current,
      [pairId]: {
        id: target.id,
        text: target.text,
        story_count: target.story_count,
      },
    }));
    setPendingMerges((current) =>
      current.map((merge) =>
        merge.pair_id === pairId ? buildPendingMergeDecision(pairId, source, target, similarityScore) : merge,
      ),
    );
  }

  function handleResetPairTarget(
    pairId: string,
    source: TropeSummary,
    defaultTarget: TropeSummary,
    similarityScore: number,
  ) {
    setTargetOverrides((current) => {
      if (!(pairId in current)) {
        return current;
      }
      const next = { ...current };
      delete next[pairId];
      return next;
    });
    setPendingMerges((current) =>
      current.map((merge) =>
        merge.pair_id === pairId ? buildPendingMergeDecision(pairId, source, defaultTarget, similarityScore) : merge,
      ),
    );
  }

  function handleRemovePendingMerge(pairId: string) {
    setPendingMerges((current) => current.filter((merge) => merge.pair_id !== pairId));
    setNotice(null);
  }

  function handleClearPendingMerges() {
    setPendingMerges([]);
    setNotice(null);
  }

  async function handleKeepTypedTarget() {
    if (!editingPairId || !editingTargetQuery.trim() || !pairs) {
      return;
    }

    const pair = pairs.items.find((item) => pairKey(item) === editingPairId);
    if (!pair) {
      return;
    }

    try {
      setBusy(true);
      setNotice(null);
      const result = await createCanonicalTrope(editingTargetQuery.trim());
      if (!applyPairTargetSelection(pair, result.trope)) {
        return;
      }
      resetTargetEditor();
      setNotice({
        tone: "success",
        title: result.created ? "Target trope created" : "Existing trope reused",
        body: result.created
          ? `Created ${result.trope.text} and set it as the merge target.`
          : `Set ${result.trope.text} as the merge target.`,
      });
    } catch (caughtError) {
      setModalNotice({
        tone: "error",
        title: "Could not set merge target",
        body: getErrorMessage(caughtError),
      });
    } finally {
      setBusy(false);
    }
  }

  async function handleValidatePendingMerges() {
    if (!pendingMerges.length) {
      return;
    }

    const confirmed = window.confirm(
      `Validate ${pendingMerges.length} merge decision${pendingMerges.length === 1 ? "" : "s"}?\n\nThis will apply the selected merges in one batch. Rebuilds are manual, so run Rebuild from the menu when you want fresh derived artifacts.`,
    );
    if (!confirmed) {
      return;
    }

    try {
      setBusy(true);
      setNotice(null);
      const result = await validateTropeMerges({
        merges: pendingMerges.map((merge) => ({
          source_trope_id: merge.source_trope_id,
          target_trope_id: merge.target_trope_id,
        })),
      });
      setCurrentJobId(result.queued_job?.id ?? null);
      setJobDetail(null);
      setJobError(null);
      setPendingMerges([]);
      setNotice({
        tone: "success",
        title: "Merge batch applied",
        body: result.queued_job
          ? `Validated ${result.merge_count} merge decision${result.merge_count === 1 ? "" : "s"} affecting ${result.affected_story_count} stor${result.affected_story_count === 1 ? "y" : "ies"}. Rebuild job ${result.queued_job.id} is now ${result.queued_job.status}.`
          : `Validated ${result.merge_count} merge decision${result.merge_count === 1 ? "" : "s"} affecting ${result.affected_story_count} stor${result.affected_story_count === 1 ? "y" : "ies"}. Run Rebuild in the menu when you want fresh derived artifacts.`,
      });
      await refresh({ clearNotice: false });
    } catch (caughtError) {
      setNotice({
        tone: "error",
        title: "Merge validation failed",
        body: getErrorMessage(caughtError),
      });
    } finally {
      setBusy(false);
    }
  }

  async function handleDeleteUnusedTrope(trope: CanonicalTropeListItem) {
    const confirmed = window.confirm(
      `Delete unused trope "${trope.text}"?\n\nRebuilds are manual, so use Rebuild in the menu afterward if you want fresh derived artifacts.`,
    );
    if (!confirmed) {
      return;
    }

    try {
      setBusy(true);
      setNotice(null);
      const result = await deleteTrope(trope.id, false);
      setCurrentJobId(result.queued_job?.id ?? null);
      setJobDetail(null);
      setJobError(null);
      setNotice({
        tone: "success",
        title: "Unused trope deleted",
        body: result.queued_job
          ? `Deleted ${trope.text}. Job ${result.queued_job.id} is ${result.queued_job.status}.`
          : `Deleted ${trope.text}. Run Rebuild in the menu when you want fresh derived artifacts.`,
      });
      await refresh({ clearNotice: false });
    } catch (caughtError) {
      setNotice({
        tone: "error",
        title: "Delete failed",
        body: getErrorMessage(caughtError),
      });
    } finally {
      setBusy(false);
    }
  }

  const effectiveJob = jobDetail ?? null;
  const selectedPairCount = pairs?.items.length ?? 0;
  const unusedCountLabel = useMemo(() => `${unusedTropes.length} unused`, [unusedTropes.length]);
  const pendingSourceIds = useMemo(() => new Set(pendingMerges.map((merge) => merge.source_trope_id)), [pendingMerges]);
  const editingPair = editingPairId && pairs ? pairs.items.find((pair) => pairKey(pair) === editingPairId) ?? null : null;
  const editingPairDirection = editingPair ? pairDirections[pairKey(editingPair)] || "forward" : "forward";
  const editingDefaultSelection = editingPair ? directionalPair(editingPair, editingPairDirection) : null;
  const editingSelection = editingPair
    ? resolvePairSelection(editingPair, editingPairDirection, targetOverrides[pairKey(editingPair)])
    : null;

  return (
    <div className="page-stack">
      <section className="panel">
        <div className="panel-header">
          <div>
            <h1>Merge near-duplicate tropes and delete unused ones</h1>
          </div>
          <button className="button button-ghost" disabled={loading || busy} onClick={() => void refresh()} type="button">
            {loading ? "Loading..." : "Refresh"}
          </button>
        </div>
      </section>

      {notice && (
        <section className={`notice ${notice.tone === "error" ? "notice-error" : "notice-success"}`}>
          <strong className="notice-title">{notice.title}</strong>
          {notice.body ? <p>{notice.body}</p> : null}
        </section>
      )}

      {loading ? (
        <section className="panel">
          <p className="muted">Loading curation data...</p>
        </section>
      ) : null}

      <section className="panel">
        <div className="panel-header">
          <h2>Latest curation job</h2>
          <span className="pill">{formatJobStatus(effectiveJob)}</span>
        </div>
        {effectiveJob ? (
          isJobDetail(effectiveJob) && effectiveJob.error_message ? <p className="notice-inline">{effectiveJob.error_message}</p> : null
        ) : (
          <p className="muted">No curation job has been queued in this session yet.</p>
        )}
        {jobError ? <p className="notice-inline">Could not refresh job status: {jobError}</p> : null}
      </section>

      <section className="panel">
        <div className="panel-header">
          <h2>Pending merge batch</h2>
          <span className="pill">{pendingMerges.length} pending</span>
        </div>

        {pendingMerges.length ? (
          <>
            <div className="stack">
              {pendingMerges.map((merge) => (
                <article className="card" key={merge.pair_id}>
                  <div className="panel-header">
                    <div>
                      <h3>
                        Merge {merge.source_text} into {merge.target_text}
                      </h3>
                      <p className="muted">
                        {merge.source_story_count} stor{merge.source_story_count === 1 ? "y" : "ies"} affected
                      </p>
                    </div>
                    <button
                      className="button button-ghost"
                      disabled={busy}
                      onClick={() => handleRemovePendingMerge(merge.pair_id)}
                      type="button"
                    >
                      Remove
                    </button>
                  </div>
                </article>
              ))}
            </div>

            <div className="button-row wrap-row">
              <button className="button button-ghost" disabled={busy} onClick={handleClearPendingMerges} type="button">
                Clear batch
              </button>
              <button className="button" disabled={busy} onClick={() => void handleValidatePendingMerges()} type="button">
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
            <h2>Near-duplicate trope pairs</h2>
            <span className="pill">{selectedPairCount} pairs</span>
          </div>

          <div className="stack">
            {pairs?.items.length ? (
              pairs.items.map((pair) => {
                const pairId = pairKey(pair);
                const direction = pairDirections[pairId] || "forward";
                const { source, target } = resolvePairSelection(pair, direction, targetOverrides[pairId]);
                const affectedStoryCount = source.story_count;
                const pendingDecision = pendingMerges.find((merge) => merge.pair_id === pairId);
                const sourceAlreadyPending = pendingSourceIds.has(source.id);
                const canStagePair = !pendingDecision && !sourceAlreadyPending;

                return (
                  <article className="card" key={pairId}>
                    <div className="panel-header">
                      <h3>Similarity {pair.similarity_score.toFixed(2)}</h3>
                      <button
                        className="button button-ghost"
                        disabled={busy || Boolean(pendingDecision)}
                        onClick={() =>
                          {
                            setPairDirections((current) => ({
                              ...current,
                              [pairId]: direction === "forward" ? "reverse" : "forward",
                            }));
                            setTargetOverrides((current) => {
                              if (!(pairId in current)) {
                                return current;
                              }
                              const next = { ...current };
                              delete next[pairId];
                              return next;
                            });
                            if (editingPairId === pairId) {
                              resetTargetEditor();
                            }
                          }
                        }
                        type="button"
                      >
                        Swap direction
                      </button>
                    </div>

                    <div className="field-grid">
                      <div className="stack">
                        <strong>Source</strong>
                        <TropeCard className="subdued" trope={source} />
                      </div>
                      <div className="stack">
                        <strong>Target</strong>
                        <TropeCard
                          className="subdued"
                          trope={target}
                          actions={
                            <button
                              className="button button-ghost"
                              disabled={busy}
                              onClick={() => {
                                handleStartEditingTarget(pairId, target);
                              }}
                              type="button"
                            >
                              Edit
                            </button>
                          }
                        />
                      </div>
                    </div>

                    <p className="muted">
                      {affectedStoryCount} stor{affectedStoryCount === 1 ? "y" : "ies"} affected
                    </p>

                    <div className="button-row">
                      {pendingDecision ? (
                        <button
                          className="button button-ghost"
                          disabled={busy}
                          onClick={() => handleRemovePendingMerge(pendingDecision.pair_id)}
                          type="button"
                        >
                          Remove from batch
                        </button>
                      ) : (
                        <button
                          className="button"
                          disabled={busy || !canStagePair}
                          onClick={() => handleStageMerge(pair)}
                          type="button"
                        >
                          {sourceAlreadyPending ? "Source already in batch" : "Add merge to batch"}
                        </button>
                      )}
                    </div>
                  </article>
                );
              })
            ) : (
              <p className="muted">{nearDuplicateEmptyLabel(pairs)}</p>
            )}
          </div>
        </div>

        <aside className="panel">
          <div className="panel-header">
            <h2>Unused tropes</h2>
            <span className="pill">{unusedCountLabel}</span>
          </div>

          <label className="field">
            <span>Search unused tropes</span>
            <input
              className="input"
              onChange={(event) => setUnusedQuery(event.target.value)}
              placeholder="Filter unused tropes by text"
              value={unusedQuery}
            />
          </label>

          <div className="stack">
            {unusedTropes.length ? (
              unusedTropes.map((trope) => (
                <TropeCard
                  key={trope.id}
                  minimumStoryCount={0}
                  trope={trope}
                  actions={
                    <button
                      className="button button-danger"
                      disabled={busy}
                      onClick={() => void handleDeleteUnusedTrope(trope)}
                      type="button"
                    >
                      Delete unused trope
                    </button>
                  }
                />
              ))
            ) : (
              <p className="muted">No unused tropes match the current filter.</p>
            )}
          </div>
        </aside>
      </section>

      {editingPair && editingSelection && editingDefaultSelection ? (
        <div className="modal-backdrop" onClick={resetTargetEditor} role="presentation">
          <section
            aria-labelledby="curation-target-modal-title"
            aria-modal="true"
            className="modal-shell"
            onClick={(event) => event.stopPropagation()}
            role="dialog"
          >
            <div className="panel-header">
              <h2 id="curation-target-modal-title">Edit merge target</h2>
              <button className="button button-ghost" disabled={busy} onClick={resetTargetEditor} type="button">
                Close
              </button>
            </div>

            {modalNotice ? (
              <section className={`notice ${modalNotice.tone === "error" ? "notice-error" : "notice-success"}`}>
                <strong className="notice-title">{modalNotice.title}</strong>
                {modalNotice.body ? <p>{modalNotice.body}</p> : null}
              </section>
            ) : null}

            <div className="field-grid">
              <div className="stack">
                <strong>Source</strong>
                <TropeCard className="subdued" trope={editingSelection.source} />
              </div>
              <div className="stack">
                <strong>Target</strong>
                <TropeCard className="subdued" trope={editingSelection.target} />
              </div>
            </div>

            <label className="field">
              <span>Target trope query</span>
              <input
                className="input"
                disabled={busy}
                onChange={(event) => setEditingTargetQuery(event.target.value)}
                placeholder="Type a target trope to search or create"
                value={editingTargetQuery}
              />
            </label>

            <div className="card subdued">
              <div className="card-row">
                <h3>Keep typed trope</h3>
                <button
                  className="button"
                  disabled={busy || !editingTargetQuery.trim()}
                  onClick={() => void handleKeepTypedTarget()}
                  type="button"
                >
                  Keep typed trope
                </button>
              </div>
            </div>

            <div className="stack">
              <div className="panel-header">
                <h3>Similar existing tropes</h3>
                <span className="pill">
                  {editingTargetSearchStatus === "loading" ? "searching" : `${editingTargetResults.length} results`}
                </span>
              </div>
              {editingTargetQuery.trim() && editingTargetSearchStatus === "loading" ? <p className="muted">Searching tropes...</p> : null}
              {editingTargetQuery.trim() && editingTargetSearchStatus === "ready" && editingTargetResults.length === 0 ? (
                <p className="muted">No similar tropes were returned for this query.</p>
              ) : null}
              <div className="modal-story-list">
                {editingTargetResults.map((item) => {
                  const isCurrentTarget = item.id === editingSelection.target.id;
                  const isSourceTrope = item.id === editingSelection.source.id;
                  return (
                    <TropeCard
                      key={`curation-modal-${editingPairId}-${item.id}`}
                      trope={item}
                      actions={
                        <button
                          className="button button-ghost"
                          disabled={busy || isCurrentTarget || isSourceTrope}
                          onClick={() => {
                            if (
                              applyPairTargetSelection(editingPair, {
                                id: item.id,
                                text: item.text,
                                story_count: item.story_count,
                              })
                            ) {
                              resetTargetEditor();
                              setNotice({
                                tone: "success",
                                title: "Merge target updated",
                                body: `Set ${item.text} as the merge target for this pair.`,
                              });
                            }
                          }}
                          type="button"
                        >
                          {isCurrentTarget ? "Current target" : isSourceTrope ? "Source trope" : "Use existing trope"}
                        </button>
                      }
                    />
                  );
                })}
              </div>
            </div>

            <div className="button-row wrap-row">
              {targetOverrides[pairKey(editingPair)] ? (
                <button
                  className="button button-ghost"
                  disabled={busy}
                  onClick={() =>
                    {
                      handleResetPairTarget(
                        pairKey(editingPair),
                        editingDefaultSelection.source,
                        editingDefaultSelection.target,
                        editingPair.similarity_score,
                      );
                      resetTargetEditor();
                      setNotice({
                        tone: "success",
                        title: "Merge target reset",
                        body: `Restored ${editingDefaultSelection.target.text} as the merge target.`,
                      });
                    }
                  }
                  type="button"
                >
                  Reset target
                </button>
              ) : null}
              <button className="button button-ghost" disabled={busy} onClick={resetTargetEditor} type="button">
                Done
              </button>
            </div>
          </section>
        </div>
      ) : null}
    </div>
  );
}
