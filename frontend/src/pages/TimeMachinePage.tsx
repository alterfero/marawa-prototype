import { useEffect, useState } from "react";

import {
  getErrorMessage,
  getJob,
  getTimeMachineSnapshot,
  getTimeMachineSnapshots,
  restoreTimeMachineSnapshot,
} from "../api/client";
import type { JobDetail, TimeMachineSnapshot, TimeMachineSnapshotValueDifference } from "../api/types";
import { useDatasetMaintenance } from "../maintenance";

const TERMINAL_JOB_STATUSES = new Set(["succeeded", "failed", "cancelled"]);
const POLL_INTERVAL_MS = 2000;

function formatDate(value: string): string {
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

function formatSize(value: number | null): string {
  if (value === null) {
    return "—";
  }
  if (value < 1024) {
    return `${value} B`;
  }
  if (value < 1024 * 1024) {
    return `${(value / 1024).toFixed(1)} KB`;
  }
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

function deltaLabel(value: number | null): string {
  if (value === null) {
    return "—";
  }
  return value > 0 ? `+${value}` : String(value);
}

function reasonLabel(reason: string): string {
  return reason === "pre_restore" ? "Automatic safety checkpoint" : "Successful rebuild";
}

function ChangeList({ difference, title }: { difference: TimeMachineSnapshotValueDifference; title: string }) {
  const hasChanges = difference.current_only.length > 0 || difference.checkpoint_only.length > 0;
  const itemLabel = (text: string, count: number) => (count > 1 ? `${text} ×${count}` : text);

  return (
    <article className="snapshot-change-card">
      <h4>{title}</h4>
      {!hasChanges ? <p className="muted">No changes.</p> : null}
      {difference.current_only.length > 0 ? (
        <div className="snapshot-change-group">
          <strong>Current only — will be removed</strong>
          <ul className="snapshot-change-list">
            {difference.current_only.map((item) => <li key={`current-${item.text}`}>{itemLabel(item.text, item.count)}</li>)}
          </ul>
        </div>
      ) : null}
      {difference.checkpoint_only.length > 0 ? (
        <div className="snapshot-change-group">
          <strong>Checkpoint only — will be restored</strong>
          <ul className="snapshot-change-list">
            {difference.checkpoint_only.map((item) => <li key={`checkpoint-${item.text}`}>{itemLabel(item.text, item.count)}</li>)}
          </ul>
        </div>
      ) : null}
    </article>
  );
}

export function TimeMachinePanel() {
  const maintenance = useDatasetMaintenance();
  const [snapshots, setSnapshots] = useState<TimeMachineSnapshot[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selectedSnapshot, setSelectedSnapshot] = useState<TimeMachineSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [notice, setNotice] = useState<{ tone: "error" | "success"; text: string } | null>(null);
  const [restoreBusy, setRestoreBusy] = useState(false);
  const [restoreJob, setRestoreJob] = useState<JobDetail | null>(null);

  async function loadSnapshots() {
    const next = await getTimeMachineSnapshots();
    setSnapshots(next);
    setSelectedId((current) => current && next.some((snapshot) => snapshot.id === current) ? current : next[0]?.id ?? null);
  }

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        await loadSnapshots();
        if (!cancelled) {
          setNotice(null);
        }
      } catch (error) {
        if (!cancelled) {
          setNotice({ tone: "error", text: `Could not load recovery points: ${getErrorMessage(error)}` });
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!selectedId) {
      setSelectedSnapshot(null);
      return;
    }
    let cancelled = false;
    void (async () => {
      try {
        const next = await getTimeMachineSnapshot(selectedId);
        if (!cancelled) {
          setSelectedSnapshot(next);
        }
      } catch (error) {
        if (!cancelled) {
          setNotice({ tone: "error", text: `Could not load this recovery point: ${getErrorMessage(error)}` });
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [selectedId]);

  useEffect(() => {
    if (!restoreJob || TERMINAL_JOB_STATUSES.has(restoreJob.status)) {
      return;
    }
    let cancelled = false;
    const timerId = window.setTimeout(() => {
      void (async () => {
        try {
          const next = await getJob(restoreJob.id);
          if (cancelled) {
            return;
          }
          setRestoreJob(next);
          if (TERMINAL_JOB_STATUSES.has(next.status)) {
            setRestoreBusy(false);
            await loadSnapshots();
            setNotice({
              tone: next.status === "succeeded" ? "success" : "error",
              text:
                next.status === "succeeded"
                  ? "Recovery completed. The restored dataset is now active; the former dataset was preserved as an archived revision."
                  : `Recovery finished with status ${next.status}: ${next.error_message || "see job details for more information."}`,
            });
          }
        } catch (error) {
          if (!cancelled) {
            setRestoreBusy(false);
            setNotice({ tone: "error", text: `Could not refresh recovery status: ${getErrorMessage(error)}` });
          }
        }
      })();
    }, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      window.clearTimeout(timerId);
    };
  }, [restoreJob]);

  async function handleRestore() {
    if (!selectedSnapshot || selectedSnapshot.status !== "ready") {
      return;
    }
    const confirmed = window.confirm(
      `Recover to checkpoint #${selectedSnapshot.sequence} from ${formatDate(selectedSnapshot.created_at)}?\n\n` +
        "The current dataset will be checkpointed first. Recovery rebuilds the selected data before making it active.",
    );
    if (!confirmed) {
      return;
    }

    try {
      setRestoreBusy(true);
      setNotice(null);
      const response = await restoreTimeMachineSnapshot(selectedSnapshot.id);
      setRestoreJob({
        id: response.job_id,
        dataset_id: null,
        job_type: "restore_snapshot",
        status: response.job_status,
        attempts: 0,
        payload: {},
        result: {},
        started_at: null,
        finished_at: null,
        error_message: null,
      });
      setNotice({
        tone: "success",
        text: `Safety checkpoint #${response.safety_snapshot.sequence} was created. Recovery is now queued.`,
      });
    } catch (error) {
      setRestoreBusy(false);
      setNotice({ tone: "error", text: `Recovery could not be started: ${getErrorMessage(error)}` });
    }
  }

  const recoveryLocked = maintenance.active || restoreBusy;
  const difference = selectedSnapshot?.difference_from_current;

  return (
    <div className="page-stack time-machine-panel">
      <section className="panel">
        <div className="panel-header">
          <div>
            <h2>Time Machine</h2>
            <p className="muted">Recover a verified dataset checkpoint. This never overwrites the current revision directly.</p>
          </div>
          <button className="button button-ghost" disabled={loading || restoreBusy} onClick={() => void loadSnapshots()} type="button">
            Refresh
          </button>
        </div>
        <p className="muted">A checkpoint is created after every successful rebuild. The current dataset is checkpointed automatically before a recovery begins.</p>
      </section>

      {notice ? (
        <section className={`notice ${notice.tone === "error" ? "notice-error" : "notice-success"}`}>
          <strong className="notice-title">{notice.tone === "error" ? "Time Machine needs attention" : "Time Machine"}</strong>
          <p>{notice.text}</p>
        </section>
      ) : null}

      {restoreJob ? (
        <section className="panel time-machine-job">
          <strong>Recovery job: {restoreJob.status.replace("_", " ")}</strong>
          <span className="muted">{restoreJob.id}</span>
        </section>
      ) : null}

      <div className="time-machine-layout">
        <section className="panel stack">
          <h2>Recovery points</h2>
          {loading ? <p className="muted">Loading checkpoints…</p> : null}
          {!loading && snapshots.length === 0 ? <p className="muted">No successful rebuild has created a checkpoint yet.</p> : null}
          <div className="snapshot-list">
            {snapshots.map((snapshot) => (
              <button
                className={`snapshot-list-item ${snapshot.id === selectedId ? "snapshot-list-item-active" : ""}`}
                key={snapshot.id}
                onClick={() => setSelectedId(snapshot.id)}
                type="button"
              >
                <span>
                  <strong>Checkpoint #{snapshot.sequence}</strong>
                  <small>{formatDate(snapshot.created_at)}</small>
                </span>
                <small>{reasonLabel(snapshot.reason)}</small>
              </button>
            ))}
          </div>
        </section>

        <section className="panel stack">
          <h2>Checkpoint preview</h2>
          {!selectedSnapshot ? <p className="muted">Select a checkpoint to inspect it.</p> : null}
          {selectedSnapshot ? (
            <>
              <div className="time-machine-heading">
                <div>
                  <h3>Checkpoint #{selectedSnapshot.sequence}</h3>
                  <p className="muted">{formatDate(selectedSnapshot.created_at)} · {reasonLabel(selectedSnapshot.reason)}</p>
                </div>
                <span className={`snapshot-status snapshot-status-${selectedSnapshot.status}`}>{selectedSnapshot.status}</span>
              </div>
              <div className="stats-grid time-machine-counts">
                <article className="stat-card"><span className="stat-label">Stories</span><strong>{selectedSnapshot.counts.stories}</strong></article>
                <article className="stat-card"><span className="stat-label">Tropes</span><strong>{selectedSnapshot.counts.tropes}</strong></article>
                <article className="stat-card"><span className="stat-label">Themes</span><strong>{selectedSnapshot.counts.themes}</strong></article>
                <article className="stat-card"><span className="stat-label">Keywords</span><strong>{selectedSnapshot.counts.keywords}</strong></article>
              </div>
              <div className="snapshot-details">
                <span>Archive size <strong>{formatSize(selectedSnapshot.content_length)}</strong></span>
                <span>Source rebuild <strong>{selectedSnapshot.source_job_id ? "recorded" : "safety checkpoint"}</strong></span>
                {difference ? <span>Current dataset version <strong>{difference.current_dataset_version ?? "none"}</strong></span> : null}
              </div>
              {difference ? (
                <div className="snapshot-diff">
                  <strong>Difference from the current dataset</strong>
                  <span>Stories {deltaLabel(difference.story_count_delta)}</span>
                  <span>Tropes {deltaLabel(difference.trope_count_delta)}</span>
                  <span>Themes {deltaLabel(difference.theme_count_delta)}</span>
                  <span>Keywords {deltaLabel(difference.keyword_count_delta)}</span>
                </div>
              ) : null}
              {difference?.changes ? (
                <section className="snapshot-changes" aria-label="Detailed checkpoint differences">
                  <div>
                    <h3>Detailed differences</h3>
                    <p className="muted">These names describe what would change if this checkpoint is recovered.</p>
                  </div>
                  <div className="snapshot-change-grid">
                    <ChangeList difference={difference.changes.stories} title="Stories" />
                    <ChangeList difference={difference.changes.tropes} title="Tropes" />
                    <ChangeList difference={difference.changes.themes} title="Themes" />
                    <ChangeList difference={difference.changes.keywords} title="Keywords" />
                  </div>
                </section>
              ) : null}
              <button
                className="button button-danger"
                disabled={recoveryLocked || selectedSnapshot.status !== "ready"}
                onClick={() => void handleRestore()}
                type="button"
              >
                {restoreBusy ? "Recovery in progress…" : "Recover this checkpoint"}
              </button>
              {maintenance.active ? <p className="muted">{maintenance.message}</p> : null}
            </>
          ) : null}
        </section>
      </div>
    </div>
  );
}
