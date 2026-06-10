import { useEffect, useMemo, useState } from "react";

import {
  deleteTrope,
  getCanonicalTropes,
  getErrorMessage,
  getJob,
  getNearDuplicateTropes,
  mergeTropes,
} from "../api/client";
import { TropeCard } from "../components/TropeCard";
import type {
  CanonicalTropeListItem,
  JobDetail,
  JobSummary,
  NearDuplicateTropeListResponse,
  NearDuplicateTropePair,
} from "../api/types";

const TERMINAL_JOB_STATUSES = new Set(["succeeded", "failed", "cancelled"]);
const JOB_POLL_INTERVAL_MS = 2000;

interface PageNotice {
  tone: "error" | "success";
  title: string;
  body?: string;
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

function explanationLabel(pair: NearDuplicateTropePair): string {
  const reason = typeof pair.metadata.reason === "string" ? pair.metadata.reason.split("_").join(" ") : "similarity cache";
  const threshold =
    typeof pair.metadata.threshold === "number" ? ` · threshold ${pair.metadata.threshold.toFixed(2)}` : "";
  return `${reason}${threshold}`;
}

function pairKey(pair: NearDuplicateTropePair): string {
  return `${pair.source_trope.id}:${pair.target_trope.id}`;
}

function nearDuplicateEmptyLabel(pairs: NearDuplicateTropeListResponse | null): string {
  if (!pairs || pairs.artifact_version === null) {
    return "No near-duplicate trope pairs are available yet. The similarity cache is not ready yet; wait for a rebuild job to finish, then refresh.";
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

export function CurationPage() {
  const [pairs, setPairs] = useState<NearDuplicateTropeListResponse | null>(null);
  const [unusedQuery, setUnusedQuery] = useState("");
  const [unusedTropes, setUnusedTropes] = useState<CanonicalTropeListItem[]>([]);
  const [pairDirections, setPairDirections] = useState<Record<string, PairDirection>>({});
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<PageNotice | null>(null);
  const [currentJobId, setCurrentJobId] = useState<string | null>(null);
  const [jobDetail, setJobDetail] = useState<JobDetail | null>(null);
  const [jobError, setJobError] = useState<string | null>(null);

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

  async function refresh() {
    try {
      setLoading(true);
      setNotice(null);
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

  async function handleMerge(pair: NearDuplicateTropePair) {
    const direction = pairDirections[pairKey(pair)] || "forward";
    const { source, target } = directionalPair(pair, direction);
    const affectedStoryCount = source.story_count;

    const confirmed = window.confirm(
      `Merge "${source.text}" into "${target.text}"?\n\nThis will update ${affectedStoryCount} stor${affectedStoryCount === 1 ? "y" : "ies"} in the active dataset and queue a rebuild job.`,
    );
    if (!confirmed) {
      return;
    }

    try {
      setBusy(true);
      setNotice(null);
      const result = await mergeTropes({
        source_trope_id: source.id,
        target_trope_id: target.id,
      });
      setCurrentJobId(result.queued_job.id);
      setJobDetail(null);
      setJobError(null);
      setNotice({
        tone: "success",
        title: "Merge queued",
        body: `Merged ${source.text} into ${target.text}. ${result.affected_story_count} affected stories will be rebuilt by job ${result.queued_job.id}.`,
      });
      await refresh();
    } catch (caughtError) {
      setNotice({
        tone: "error",
        title: "Merge failed",
        body: getErrorMessage(caughtError),
      });
    } finally {
      setBusy(false);
    }
  }

  async function handleDeleteUnusedTrope(trope: CanonicalTropeListItem) {
    const confirmed = window.confirm(`Delete unused trope "${trope.text}"? This will queue a rebuild job.`);
    if (!confirmed) {
      return;
    }

    try {
      setBusy(true);
      setNotice(null);
      const result = await deleteTrope(trope.id, false);
      setCurrentJobId(result.queued_job.id);
      setJobDetail(null);
      setJobError(null);
      setNotice({
        tone: "success",
        title: "Unused trope deleted",
        body: `Deleted ${trope.text}. Job ${result.queued_job.id} is ${result.queued_job.status}.`,
      });
      await refresh();
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

  return (
    <div className="page-stack">
      <section className="panel">
        <div className="panel-header">
          <div>
            <p className="eyebrow">Curation</p>
            <h1>Merge near-duplicate tropes and delete unused ones</h1>
          </div>
          <button className="button button-ghost" disabled={loading || busy} onClick={() => void refresh()} type="button">
            {loading ? "Loading..." : "Refresh"}
          </button>
        </div>
        <p className="muted">
          Review near-duplicate pairs from the similarity cache, choose which trope should merge into which target, and
          remove unused canonical tropes without exposing any story deletion path.
        </p>
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
          <div>
            <h2>Latest curation job</h2>
            <p className="muted">Job status is refreshed automatically after merge or delete actions.</p>
          </div>
          <span className="pill">{formatJobStatus(effectiveJob)}</span>
        </div>
        {effectiveJob ? (
          <div className="stack">
            <p className="mono">
              {effectiveJob.job_type} · {effectiveJob.id}
            </p>
            {isJobDetail(effectiveJob) ? (
              <p className="muted">
                Attempts {effectiveJob.attempts} · started {effectiveJob.started_at || "not yet"} · finished{" "}
                {effectiveJob.finished_at || "not yet"}
              </p>
            ) : null}
            {isJobDetail(effectiveJob) && effectiveJob.error_message ? (
              <p className="notice-inline">{effectiveJob.error_message}</p>
            ) : null}
          </div>
        ) : (
          <p className="muted">No curation job has been queued in this session yet.</p>
        )}
        {jobError ? <p className="notice-inline">Could not refresh job status: {jobError}</p> : null}
      </section>

      <section className="two-column-layout">
        <div className="panel">
          <div className="panel-header">
            <div>
              <h2>Near-duplicate trope pairs</h2>
              <p className="muted">
                {pairs?.model_name || "No model"} · artifact {pairs?.artifact_version ?? "n/a"}
              </p>
            </div>
            <span className="pill">{selectedPairCount} pairs</span>
          </div>

          <div className="stack">
            {pairs?.items.length ? (
              pairs.items.map((pair) => {
                const direction = pairDirections[pairKey(pair)] || "forward";
                const { source, target } = directionalPair(pair, direction);
                const affectedStoryCount = source.story_count;

                return (
                  <article className="card" key={pairKey(pair)}>
                    <div className="panel-header">
                      <div>
                        <h3>Similarity {pair.similarity_score.toFixed(2)}</h3>
                        <p className="muted">{explanationLabel(pair)}</p>
                      </div>
                      <button
                        className="button button-ghost"
                        disabled={busy}
                        onClick={() =>
                          setPairDirections((current) => ({
                            ...current,
                            [pairKey(pair)]: direction === "forward" ? "reverse" : "forward",
                          }))
                        }
                        type="button"
                      >
                        Swap direction
                      </button>
                    </div>

                    <div className="field-grid">
                      <div className="stack">
                        <p className="muted">Source trope</p>
                        <TropeCard
                          className="subdued"
                          meta="Stories affected by this merge."
                          trope={source}
                        />
                      </div>
                      <div className="stack">
                        <p className="muted">Target trope</p>
                        <TropeCard
                          className="subdued"
                          meta="Stories already tagged with this trope."
                          trope={target}
                        />
                      </div>
                    </div>

                    <p className="muted">
                      If merged in this direction, {affectedStoryCount} stor{affectedStoryCount === 1 ? "y" : "ies"} will be updated before the rebuild job runs.
                    </p>

                    <div className="button-row">
                      <button className="button" disabled={busy} onClick={() => void handleMerge(pair)} type="button">
                        Merge source into target
                      </button>
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
            <div>
              <h2>Unused tropes</h2>
              <p className="muted">Only zero-assignment tropes are shown here, so deleting them does not touch stories.</p>
            </div>
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
                  meta={trope.id}
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
    </div>
  );
}
