import { FormEvent, useEffect, useState } from "react";

import {
  ApiError,
  clearDatasetData,
  getDatasetExportUrl,
  getDatasetStatus,
  getErrorMessage,
  getJob,
  uploadDataset,
} from "../api/client";
import type { DatasetStatus, EmbeddingStatus, JobDetail, JobSummary } from "../api/types";

const TERMINAL_JOB_STATUSES = new Set(["succeeded", "failed", "cancelled"]);
const JOB_POLL_INTERVAL_MS = 2000;

interface PageNotice {
  tone: "error" | "success" | "warning";
  title: string;
  body?: string;
}

function isLikelyDevProxyFailure(error: ApiError): boolean {
  const detailText =
    typeof error.detail === "string" ? error.detail : error.detail && typeof error.detail === "object" ? JSON.stringify(error.detail) : "";

  return (
    import.meta.env.DEV &&
    [404, 500, 502, 503, 504].includes(error.status) &&
    (error.message === "The request failed." ||
      /proxy|econnrefused|socket hang up|upstream|bad gateway/i.test(detailText) ||
      /<!doctype html>|<html/i.test(detailText))
  );
}

function isBackendUnavailable(error: unknown): boolean {
  if (error instanceof ApiError) {
    return [502, 503, 504].includes(error.status) || isLikelyDevProxyFailure(error);
  }
  return error instanceof TypeError;
}

function buildRequestNotice(requestLabel: string, error: unknown): PageNotice {
  if (isBackendUnavailable(error)) {
    return {
      tone: "error",
      title: `${requestLabel} could not reach the backend`,
      body: import.meta.env.DEV
        ? "The FastAPI backend could not be reached through the Vite dev proxy. Start the backend on http://127.0.0.1:8000, or set VITE_BACKEND_ORIGIN before running npm run dev."
        : "The FastAPI backend could not be reached. Start the backend and try again.",
    };
  }

  if (error instanceof ApiError) {
    return {
      tone: "error",
      title: `${requestLabel} failed`,
      body: getErrorMessage(error),
    };
  }

  return {
    tone: "error",
    title: `${requestLabel} failed`,
    body: getErrorMessage(error),
  };
}

function formatJobStatus(job: JobSummary | JobDetail | null): string {
  if (!job) {
    return "none";
  }
  return job.status.split("_").join(" ");
}

function formatEmbeddingState(status: EmbeddingStatus | null | undefined): string {
  if (!status) {
    return "unknown";
  }

  switch (status.state) {
    case "ready":
      return "Ready and current";
    case "running":
      return "Rebuilding now";
    case "queued":
      return "Rebuild queued";
    case "stale":
      return "Ready but stale";
    case "failed":
      return "Rebuild failed";
    case "missing":
      return "Not built yet";
    default:
      return status.state.split("_").join(" ");
  }
}

function embeddingSummary(status: EmbeddingStatus | null | undefined, activeDatasetVersion: number | null | undefined): string {
  if (!status) {
    return "Embedding status is not available yet.";
  }

  switch (status.state) {
    case "ready":
      return `Artifacts are ready for active dataset version ${activeDatasetVersion ?? "n/a"}.`;
    case "running":
      return "A rebuild job is running. Existing artifacts may still be usable, but they are not current yet.";
    case "queued":
      return "A rebuild job is queued. Similarity search will become current when that job succeeds.";
    case "stale":
      return "Embeddings exist, but they were built for an older dataset version.";
    case "failed":
      return "The latest rebuild failed, so embeddings are not current.";
    case "missing":
      return activeDatasetVersion == null
        ? "No active dataset yet."
        : "No successful embedding rebuild has completed for the active dataset yet.";
    default:
      return "Embedding status is available but not recognized by the current UI.";
  }
}

export function DatasetPage() {
  const [status, setStatus] = useState<DatasetStatus | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [fileInputKey, setFileInputKey] = useState(0);
  const [busy, setBusy] = useState(false);
  const [clearing, setClearing] = useState(false);
  const [backendUnavailable, setBackendUnavailable] = useState(false);
  const [statusNotice, setStatusNotice] = useState<PageNotice | null>(null);
  const [actionNotice, setActionNotice] = useState<PageNotice | null>(null);
  const [currentJobId, setCurrentJobId] = useState<string | null>(null);
  const [jobDetail, setJobDetail] = useState<JobDetail | null>(null);
  const [jobError, setJobError] = useState<string | null>(null);

  async function loadStatus() {
    try {
      const nextStatus = await getDatasetStatus();
      setStatus(nextStatus);
      setBackendUnavailable(false);
      setCurrentJobId(nextStatus.latest_job?.id ?? null);
      if (!nextStatus.latest_job) {
        setJobDetail(null);
      }
      return nextStatus;
    } catch (caughtError) {
      setBackendUnavailable(isBackendUnavailable(caughtError));
      throw caughtError;
    }
  }

  useEffect(() => {
    void (async () => {
      try {
        await loadStatus();
        setStatusNotice(null);
      } catch (caughtError) {
        setStatusNotice(buildRequestNotice("GET /api/dataset/status", caughtError));
      }
    })();
  }, []);

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
            await loadStatus();
            setStatusNotice(null);
          } catch (caughtError) {
            if (!cancelled) {
              setStatusNotice(buildRequestNotice("GET /api/dataset/status", caughtError));
            }
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
        const message = `GET /api/jobs/${currentJobId} failed: ${getErrorMessage(caughtError)}`;
        setJobError(message);
        setStatusNotice({
          tone: "warning",
          title: "Could not refresh rebuild status",
          body: message,
        });
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

  async function handleRefresh() {
    try {
      setStatusNotice(null);
      await loadStatus();
      setStatusNotice(null);
    } catch (caughtError) {
      setStatusNotice(buildRequestNotice("GET /api/dataset/status", caughtError));
    }
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!file) {
      setActionNotice({
        tone: "error",
        title: "No file selected",
        body: "Choose a CSV file before uploading.",
      });
      return;
    }

    if (
      status?.active_dataset_version != null &&
      !window.confirm("Uploading a new CSV will replace the current active dataset. Continue?")
    ) {
      return;
    }

    try {
      setBusy(true);
      setActionNotice(null);

      const result = await uploadDataset(file);
      setCurrentJobId(result.latest_job.id);
      setJobDetail(null);
      setJobError(null);
      setFile(null);
      setFileInputKey((value) => value + 1);

      try {
        await loadStatus();
        setStatusNotice(null);
        setActionNotice({
          tone: "success",
          title: "Dataset uploaded",
          body: `The active dataset is being rebuilt. Job ${result.latest_job.id} is ${result.latest_job.status}.`,
        });
      } catch (caughtError) {
        setActionNotice({
          tone: "warning",
          title: "Dataset uploaded",
          body: `The CSV upload succeeded and queued rebuild job ${result.latest_job.id}, but GET /api/dataset/status failed afterwards: ${getErrorMessage(
            caughtError,
          )}. Use Refresh to try again.`,
        });
      }
    } catch (caughtError) {
      setActionNotice(buildRequestNotice("POST /api/dataset/upload", caughtError));
    } finally {
      setBusy(false);
    }
  }

  async function handleClearData() {
    if (status?.active_dataset_version == null) {
      return;
    }

    if (
      !window.confirm(
        "This will permanently remove the current dataset, stories, trope assignments, jobs, and computed artifacts. Continue?",
      )
    ) {
      return;
    }

    try {
      setClearing(true);
      setActionNotice(null);

      const emptyStatus = await clearDatasetData();
      setStatus(emptyStatus);
      setBackendUnavailable(false);
      setStatusNotice(null);
      setCurrentJobId(emptyStatus.latest_job?.id ?? null);
      setJobDetail(null);
      setJobError(null);
      setFile(null);
      setFileInputKey((value) => value + 1);
      setActionNotice({
        tone: "success",
        title: "Data cleared",
        body: "The dataset and all derived data were removed. The app is back in its empty initial state.",
      });
    } catch (caughtError) {
      setActionNotice(buildRequestNotice("DELETE /api/dataset", caughtError));
    } finally {
      setClearing(false);
    }
  }

  const effectiveJob = jobDetail ?? (status?.latest_job ?? null);
  const latestRebuildStatus = formatJobStatus(effectiveJob);
  const notice = actionNotice ?? statusNotice;

  return (
    <div className="page-stack">
      <section className="panel">
        <div className="panel-header">
          <div>
            <p className="eyebrow">Dataset</p>
            <h1>Load and inspect the active dataset</h1>
          </div>
          <button className="button button-ghost" onClick={() => void handleRefresh()} type="button">
            Refresh
          </button>
        </div>
        <p className="muted">
          Upload a legacy-compatible CSV, confirm replacement when needed, and export the currently active dataset.
        </p>
        <div className="stats-grid">
          <article className="stat-card">
            <span className="stat-label">Stories</span>
            <strong>{backendUnavailable && !status ? "—" : status?.story_count ?? 0}</strong>
          </article>
          <article className="stat-card">
            <span className="stat-label">Tropes</span>
            <strong>{backendUnavailable && !status ? "—" : status?.trope_count ?? 0}</strong>
          </article>
          <article className="stat-card">
            <span className="stat-label">Keywords</span>
            <strong>{backendUnavailable && !status ? "—" : status?.keyword_count ?? 0}</strong>
          </article>
          <article className="stat-card">
            <span className="stat-label">Dataset Version</span>
            <strong>{backendUnavailable && !status ? "—" : status?.active_dataset_version ?? "none"}</strong>
          </article>
          <article className="stat-card">
            <span className="stat-label">Latest Rebuild</span>
            <strong>{backendUnavailable && !status ? "unavailable" : latestRebuildStatus}</strong>
          </article>
        </div>
        <article className="card subdued">
          <div className="panel-header">
            <div>
              <h2>Embeddings</h2>
              <p className="muted">
                {backendUnavailable && !status
                  ? "Backend unavailable"
                  : status?.embedding_status.model_name || "No embedding model reported"}
              </p>
            </div>
            <span className="pill">
              {backendUnavailable && !status ? "unavailable" : formatEmbeddingState(status?.embedding_status)}
            </span>
          </div>
          <p className="muted">
            {backendUnavailable && !status
              ? "Start the backend to load embedding readiness and currentness."
              : embeddingSummary(status?.embedding_status, status?.active_dataset_version)}
          </p>
          <div className="stats-grid">
            <article className="stat-card">
              <span className="stat-label">Ready</span>
              <strong>{backendUnavailable && !status ? "—" : status?.embedding_status.ready ? "yes" : "no"}</strong>
            </article>
            <article className="stat-card">
              <span className="stat-label">Current</span>
              <strong>{backendUnavailable && !status ? "—" : status?.embedding_status.current ? "yes" : "no"}</strong>
            </article>
            <article className="stat-card">
              <span className="stat-label">Built For Dataset</span>
              <strong>{backendUnavailable && !status ? "—" : status?.embedding_status.rebuilt_dataset_version ?? "none"}</strong>
            </article>
            <article className="stat-card">
              <span className="stat-label">Artifact Version</span>
              <strong>{backendUnavailable && !status ? "—" : status?.embedding_status.artifact_version ?? "none"}</strong>
            </article>
            <article className="stat-card">
              <span className="stat-label">Indexed Tropes</span>
              <strong>{backendUnavailable && !status ? "—" : status?.embedding_status.indexed_trope_count ?? 0}</strong>
            </article>
            <article className="stat-card">
              <span className="stat-label">Indexed Keywords</span>
              <strong>{backendUnavailable && !status ? "—" : status?.embedding_status.indexed_keyword_count ?? 0}</strong>
            </article>
          </div>
          {!backendUnavailable && status?.embedding_status.last_error_message ? (
            <p className="notice-inline">Latest rebuild error: {status.embedding_status.last_error_message}</p>
          ) : null}
        </article>
      </section>

      {notice && (
        <section
          className={`notice ${
            notice.tone === "error" ? "notice-error" : notice.tone === "warning" ? "notice-warning" : "notice-success"
          }`}
        >
          <strong className="notice-title">{notice.title}</strong>
          {notice.body ? <p>{notice.body}</p> : null}
        </section>
      )}

      <section className="panel split-panel">
        <div className="stack">
          <h2>Upload CSV</h2>
          <form className="stack" onSubmit={(event) => void handleSubmit(event)}>
            <label className="field">
              <span>Legacy CSV file</span>
              <input
                accept=".csv,text/csv"
                className="input"
                key={fileInputKey}
                onChange={(event) => setFile(event.target.files?.[0] || null)}
                type="file"
              />
            </label>
            {status?.active_dataset_version != null ? (
              <p className="muted">Uploading will replace the current active dataset after confirmation.</p>
            ) : (
              <p className="muted">No active dataset yet. The first upload will create one.</p>
            )}
            <button className="button" disabled={busy || clearing || backendUnavailable} type="submit">
              {busy ? "Uploading..." : "Upload dataset"}
            </button>
          </form>
        </div>

        <div className="stack">
          <h2>Export CSV</h2>
          <p className="muted">Export the current active dataset using the exact legacy column names and order.</p>
          {status?.active_dataset_version ? (
            <a className="button button-ghost" href={getDatasetExportUrl()}>
              Download export
            </a>
          ) : (
            <span aria-disabled="true" className="button button-ghost button-disabled">
              Download export
            </span>
          )}
        </div>
      </section>

      <section className="panel stack">
        <h2>Clear data</h2>
        <p className="muted">
          Remove the current dataset and re-initialize the app to its empty starting state before leaving this page.
        </p>
        <div className="button-row wrap-row">
          <button
            className="button button-danger"
            disabled={busy || clearing || backendUnavailable || status?.active_dataset_version == null}
            onClick={() => void handleClearData()}
            type="button"
          >
            {clearing ? "Clearing..." : "Clear data"}
          </button>
        </div>
        {jobError ? <p className="notice-inline">Could not refresh rebuild status: {jobError}</p> : null}
      </section>
    </div>
  );
}
