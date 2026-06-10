import { FormEvent, useEffect, useState } from "react";

import { ApiError, createStory, getDatasetStatus, getErrorMessage, searchKeywords, searchTropes } from "../api/client";
import { TermCard } from "../components/TermCard";
import { TropeCard } from "../components/TropeCard";
import { buildBlankStoryFields, KEYWORD_FIELD, LEGACY_METADATA_SECTIONS, LONG_TEXT_FIELDS, normalizeDraftText } from "../constants/csv";
import type { DatasetStatus, SearchItem } from "../api/types";

interface PageNotice {
  tone: "error" | "success" | "warning";
  title: string;
  body?: string;
}

interface DraftTerm {
  id: string;
  text: string;
  story_count: number;
}

function extractConflictVersion(error: ApiError): number | null {
  const detail = error.detail;
  if (!detail || typeof detail !== "object") {
    return null;
  }
  const nestedDetails = (detail as { details?: unknown }).details;
  if (!nestedDetails || typeof nestedDetails !== "object") {
    return null;
  }
  const currentDatasetVersion = (nestedDetails as { current_dataset_version?: unknown }).current_dataset_version;
  return typeof currentDatasetVersion === "number" ? currentDatasetVersion : null;
}

function explanationLabel(item: SearchItem): string {
  const flags = [];
  if (item.explanation.matched_query_exactly) {
    flags.push("exact match");
  }
  if (item.explanation.cache_hit) {
    flags.push("cached");
  }
  if (item.explanation.near_duplicate) {
    flags.push("near duplicate");
  }
  return flags.length ? flags.join(" · ") : item.explanation.method.split("_").join(" ");
}

function buildErrorNotice(title: string, error: unknown): PageNotice {
  return {
    tone: "error",
    title,
    body: getErrorMessage(error),
  };
}

export function CreateEntryPage() {
  const [datasetStatus, setDatasetStatus] = useState<DatasetStatus | null>(null);
  const [statusLoading, setStatusLoading] = useState(true);
  const [fields, setFields] = useState<Record<string, string>>(() => buildBlankStoryFields());
  const [draftKeywords, setDraftKeywords] = useState<DraftTerm[]>([]);
  const [keywordQuery, setKeywordQuery] = useState("");
  const [keywordResults, setKeywordResults] = useState<SearchItem[]>([]);
  const [keywordSearchStatus, setKeywordSearchStatus] = useState<"idle" | "loading" | "ready">("idle");
  const [draftTropes, setDraftTropes] = useState<DraftTerm[]>([]);
  const [tropeQuery, setTropeQuery] = useState("");
  const [tropeResults, setTropeResults] = useState<SearchItem[]>([]);
  const [tropeSearchStatus, setTropeSearchStatus] = useState<"idle" | "loading" | "ready">("idle");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<PageNotice | null>(null);

  const datasetVersion = datasetStatus?.active_dataset_version ?? null;
  const draftKeywordMarkers = new Set(draftKeywords.map((keyword) => normalizeDraftText(keyword.text)));
  const draftTropeMarkers = new Set(draftTropes.map((trope) => normalizeDraftText(trope.text)));

  async function loadStatus() {
    try {
      setStatusLoading(true);
      const nextStatus = await getDatasetStatus();
      setDatasetStatus(nextStatus);
      return nextStatus;
    } finally {
      setStatusLoading(false);
    }
  }

  function resetDraft() {
    setFields(buildBlankStoryFields());
    setDraftKeywords([]);
    setKeywordQuery("");
    setKeywordResults([]);
    setKeywordSearchStatus("idle");
    setDraftTropes([]);
    setTropeQuery("");
    setTropeResults([]);
    setTropeSearchStatus("idle");
  }

  useEffect(() => {
    void loadStatus().catch((caughtError) => {
      setNotice(buildErrorNotice("Could not load dataset status", caughtError));
    });
  }, []);

  useEffect(() => {
    const trimmedQuery = keywordQuery.trim();
    if (!trimmedQuery) {
      setKeywordResults([]);
      setKeywordSearchStatus("idle");
      return;
    }

    let cancelled = false;
    const timeoutId = window.setTimeout(() => {
      void (async () => {
        try {
          setKeywordSearchStatus("loading");
          const result = await searchKeywords({ query: trimmedQuery, limit: 8 });
          if (cancelled) {
            return;
          }
          setKeywordResults(result.items);
          setKeywordSearchStatus("ready");
        } catch (caughtError) {
          if (cancelled) {
            return;
          }
          setKeywordResults([]);
          setKeywordSearchStatus("ready");
          setNotice(buildErrorNotice("Could not search keywords", caughtError));
        }
      })();
    }, 250);

    return () => {
      cancelled = true;
      window.clearTimeout(timeoutId);
    };
  }, [keywordQuery]);

  useEffect(() => {
    const trimmedQuery = tropeQuery.trim();
    if (!trimmedQuery) {
      setTropeResults([]);
      setTropeSearchStatus("idle");
      return;
    }

    let cancelled = false;
    const timeoutId = window.setTimeout(() => {
      void (async () => {
        try {
          setTropeSearchStatus("loading");
          const result = await searchTropes({ query: trimmedQuery, limit: 8 });
          if (cancelled) {
            return;
          }
          setTropeResults(result.items);
          setTropeSearchStatus("ready");
        } catch (caughtError) {
          if (cancelled) {
            return;
          }
          setTropeResults([]);
          setTropeSearchStatus("ready");
          setNotice(buildErrorNotice("Could not search tropes", caughtError));
        }
      })();
    }, 250);

    return () => {
      cancelled = true;
      window.clearTimeout(timeoutId);
    };
  }, [tropeQuery]);

  function updateField(field: string, value: string) {
    setFields((current) => ({
      ...current,
      [field]: value,
    }));
  }

  function addDraftKeyword(nextKeyword: DraftTerm) {
    const marker = normalizeDraftText(nextKeyword.text);
    if (draftKeywordMarkers.has(marker)) {
      setNotice({
        tone: "warning",
        title: "Keyword already added",
        body: "This keyword is already in the draft entry.",
      });
      return;
    }

    setDraftKeywords((current) => [...current, nextKeyword]);
    setKeywordQuery("");
    setKeywordResults([]);
    setKeywordSearchStatus("idle");
    setNotice(null);
  }

  function addDraftTrope(nextTrope: DraftTerm) {
    const marker = normalizeDraftText(nextTrope.text);
    if (draftTropeMarkers.has(marker)) {
      setNotice({
        tone: "warning",
        title: "Trope already added",
        body: "This trope is already in the draft entry.",
      });
      return;
    }

    setDraftTropes((current) => [...current, nextTrope]);
    setTropeQuery("");
    setTropeResults([]);
    setTropeSearchStatus("idle");
    setNotice(null);
  }

  function handleUseExistingKeyword(item: SearchItem) {
    addDraftKeyword({
      id: item.id,
      text: item.text,
      story_count: item.story_count,
    });
  }

  function handleUseExistingTrope(item: SearchItem) {
    addDraftTrope({
      id: item.id,
      text: item.text,
      story_count: item.story_count,
    });
  }

  function handleKeepTypedKeyword() {
    const trimmedQuery = keywordQuery.trim();
    if (!trimmedQuery) {
      return;
    }

    addDraftKeyword({
      id: `draft:${normalizeDraftText(trimmedQuery)}`,
      text: trimmedQuery,
      story_count: 0,
    });
  }

  function handleKeepTypedTrope() {
    const trimmedQuery = tropeQuery.trim();
    if (!trimmedQuery) {
      return;
    }

    addDraftTrope({
      id: `draft:${normalizeDraftText(trimmedQuery)}`,
      text: trimmedQuery,
      story_count: 0,
    });
  }

  function handleDeleteDraftKeyword(keywordText: string) {
    const marker = normalizeDraftText(keywordText);
    setDraftKeywords((current) => current.filter((keyword) => normalizeDraftText(keyword.text) !== marker));
  }

  function handleDeleteDraftTrope(tropeText: string) {
    const marker = normalizeDraftText(tropeText);
    setDraftTropes((current) => current.filter((trope) => normalizeDraftText(trope.text) !== marker));
  }

  async function handleSave(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (datasetVersion == null) {
      setNotice({
        tone: "warning",
        title: "No active dataset",
        body: "Upload a dataset before creating a new entry.",
      });
      return;
    }

    try {
      setBusy(true);
      setNotice(null);

      const result = await createStory({
        expected_dataset_version: datasetVersion,
        fields,
        tropes: draftTropes.map((trope) => trope.text),
        keywords: draftKeywords.map((keyword) => keyword.text),
      });

      resetDraft();

      let refreshError: string | null = null;
      try {
        await loadStatus();
      } catch (caughtError) {
        refreshError = getErrorMessage(caughtError);
      }

      const storyLabel = result.story.fields["Story title (Eng)"] || result.story.id;
      setNotice({
        tone: refreshError ? "warning" : "success",
        title: refreshError ? "Entry saved, but status did not refresh" : "Entry saved",
        body: refreshError
          ? `${storyLabel} was created and rebuild job ${result.queued_job.id} was queued. Status refresh failed: ${refreshError}`
          : `${storyLabel} was created and rebuild job ${result.queued_job.id} is ${result.queued_job.status}.`,
      });
    } catch (caughtError) {
      if (caughtError instanceof ApiError && caughtError.status === 409) {
        const currentVersion = extractConflictVersion(caughtError);
        try {
          await loadStatus();
        } catch {
          // Keep the original conflict visible even if the refresh fails.
        }
        setNotice({
          tone: "error",
          title: "Dataset updated elsewhere",
          body:
            currentVersion === null
              ? "This dataset changed in another browser session. The latest dataset version has been reloaded."
              : `This dataset changed in another browser session. Reloaded dataset version ${currentVersion}. Review the draft and try again.`,
        });
        return;
      }

      setNotice(buildErrorNotice("Could not save the new entry", caughtError));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="page-stack" onSubmit={(event) => void handleSave(event)}>
      <section className="panel">
        <div className="panel-header">
          <div>
            <p className="eyebrow">Create</p>
            <h1>Create new entry</h1>
          </div>
          <div className="button-row wrap-row">
            <button className="button button-ghost" disabled={busy} onClick={() => resetDraft()} type="button">
              Reset draft
            </button>
            <button className="button button-ghost" disabled={busy || statusLoading} onClick={() => void loadStatus()} type="button">
              {statusLoading ? "Refreshing..." : "Refresh dataset"}
            </button>
            <button className="button" disabled={busy || datasetVersion == null} type="submit">
              {busy ? "Saving..." : "Save new entry"}
            </button>
          </div>
        </div>
        <p className="muted">
          Create a manual story inside the active dataset, keep the legacy CSV metadata fields intact, and queue a full
          rebuild after saving.
        </p>
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

      {datasetVersion == null ? (
        <section className="notice notice-warning">
          <strong className="notice-title">No active dataset loaded</strong>
          <p>Upload a CSV from the Dataset page before creating manual story entries.</p>
        </section>
      ) : null}

      <section className="two-column-layout create-entry-layout">
        <div className="page-stack">
          <section className="panel">
            <div className="panel-header">
              <div>
                <h2>Active dataset</h2>
                <p className="muted">New stories are appended to the current active dataset and exported with the legacy CSV schema.</p>
              </div>
            </div>
            <div className="stats-grid">
              <article className="stat-card">
                <span className="stat-label">Dataset Version</span>
                <strong>{datasetVersion ?? "none"}</strong>
              </article>
              <article className="stat-card">
                <span className="stat-label">Stories</span>
                <strong>{datasetStatus?.story_count ?? 0}</strong>
              </article>
              <article className="stat-card">
                <span className="stat-label">Tropes</span>
                <strong>{datasetStatus?.trope_count ?? 0}</strong>
              </article>
              <article className="stat-card">
                <span className="stat-label">Keywords</span>
                <strong>{datasetStatus?.keyword_count ?? 0}</strong>
              </article>
            </div>
          </section>

          {LEGACY_METADATA_SECTIONS.map((section) => (
            <section className="panel" key={section.title}>
              <div className="panel-header">
                <h2>{section.title}</h2>
              </div>
              <div className="create-field-grid">
                {section.fields.map((field) => {
                  const isLongText = LONG_TEXT_FIELDS.has(field);
                  return (
                    <label className={`field ${isLongText ? "field-span-full" : ""}`} key={field}>
                      <span>{field}</span>
                      {isLongText ? (
                        <textarea
                          className="input input-textarea"
                          disabled={busy}
                          onChange={(event) => updateField(field, event.target.value)}
                          rows={field === "1-sentence summary" ? 3 : 5}
                          value={fields[field] || ""}
                        />
                      ) : (
                        <input
                          className="input"
                          disabled={busy}
                          onChange={(event) => updateField(field, event.target.value)}
                          value={fields[field] || ""}
                        />
                      )}
                    </label>
                  );
                })}
              </div>
            </section>
          ))}
        </div>

        <div className="page-stack">
          <section className="panel">
            <div className="panel-header">
              <div>
                <h2>Current keywords</h2>
                <p className="muted">The export still writes these values into the legacy `{KEYWORD_FIELD}` column.</p>
              </div>
              <span className="pill">{draftKeywords.length} selected</span>
            </div>
            <div className="stack">
              {draftKeywords.length ? (
                draftKeywords.map((keyword) => (
                  <TermCard
                    key={keyword.id}
                    meta={keyword.id.startsWith("draft:") ? "New keyword typed in this draft." : "Existing keyword reused."}
                    minimumStoryCount={0}
                    term={keyword}
                    actions={
                      <button
                        className="button button-danger"
                        disabled={busy}
                        onClick={() => handleDeleteDraftKeyword(keyword.text)}
                        type="button"
                      >
                        Delete
                      </button>
                    }
                  />
                ))
              ) : (
                <p className="muted">No keywords on this draft yet.</p>
              )}
            </div>
          </section>

          <section className="panel">
            <div className="panel-header">
              <div>
                <h2>Add keyword</h2>
                <p className="muted">Search similar keywords before keeping your typed text.</p>
              </div>
            </div>

            <label className="field">
              <span>Keyword query</span>
              <input
                className="input"
                disabled={busy}
                onChange={(event) => setKeywordQuery(event.target.value)}
                placeholder="Type a keyword to search for similar existing keywords"
                value={keywordQuery}
              />
            </label>

            <div className="card subdued">
              <div className="card-row">
                <div>
                  <h3>Keep typed keyword</h3>
                  <p className="muted">
                    Use this when the typed keyword should stay as written, even if you do not select an existing result.
                  </p>
                </div>
                <button className="button" disabled={busy || !keywordQuery.trim()} onClick={() => handleKeepTypedKeyword()} type="button">
                  Keep typed keyword
                </button>
              </div>
            </div>

            <div className="stack">
              <div className="panel-header">
                <h3>Similar existing keywords</h3>
                <span className="pill">
                  {keywordSearchStatus === "loading" ? "searching" : `${keywordResults.length} results`}
                </span>
              </div>
              {!keywordQuery.trim() ? <p className="muted">Start typing to search the existing keyword index.</p> : null}
              {keywordQuery.trim() && keywordSearchStatus === "loading" ? <p className="muted">Searching keywords...</p> : null}
              {keywordQuery.trim() && keywordSearchStatus === "ready" && keywordResults.length === 0 ? (
                <p className="muted">No similar keywords were returned for this query.</p>
              ) : null}
              {keywordResults.map((item) => {
                const alreadyAssigned = draftKeywordMarkers.has(normalizeDraftText(item.text));
                return (
                  <TermCard
                    key={item.id}
                    meta={`${explanationLabel(item)} · ${item.explanation.model_name} · dim ${
                      item.explanation.vector_dimension ?? "n/a"
                    }`}
                    subtitle={`score ${item.score.toFixed(2)}`}
                    term={item}
                    actions={
                      <button
                        className="button button-ghost"
                        disabled={busy || alreadyAssigned}
                        onClick={() => handleUseExistingKeyword(item)}
                        type="button"
                      >
                        {alreadyAssigned ? "Already added" : "Use existing keyword"}
                      </button>
                    }
                  />
                );
              })}
            </div>
          </section>

          <section className="panel">
            <div className="panel-header">
              <h2>Current tropes</h2>
              <span className="pill">{draftTropes.length} selected</span>
            </div>
            <div className="stack">
              {draftTropes.length ? (
                draftTropes.map((trope) => (
                  <TropeCard
                    key={trope.id}
                    meta={trope.id.startsWith("draft:") ? "New trope typed in this draft." : "Existing canonical trope reused."}
                    minimumStoryCount={0}
                    trope={trope}
                    actions={
                      <button
                        className="button button-danger"
                        disabled={busy}
                        onClick={() => handleDeleteDraftTrope(trope.text)}
                        type="button"
                      >
                        Delete
                      </button>
                    }
                  />
                ))
              ) : (
                <p className="muted">No tropes on this draft yet.</p>
              )}
            </div>
          </section>

          <section className="panel">
            <div className="panel-header">
              <div>
                <h2>Add trope</h2>
                <p className="muted">Search similar tropes before keeping your typed text.</p>
              </div>
            </div>

            <label className="field">
              <span>Trope query</span>
              <input
                className="input"
                disabled={busy}
                onChange={(event) => setTropeQuery(event.target.value)}
                placeholder="Type a trope to search for similar existing tropes"
                value={tropeQuery}
              />
            </label>

            <div className="card subdued">
              <div className="card-row">
                <div>
                  <h3>Keep typed trope</h3>
                  <p className="muted">
                    Use this when the typed trope should stay as written, even if you do not select an existing result.
                  </p>
                </div>
                <button className="button" disabled={busy || !tropeQuery.trim()} onClick={() => handleKeepTypedTrope()} type="button">
                  Keep typed trope
                </button>
              </div>
            </div>

            <div className="stack">
              <div className="panel-header">
                <h3>Similar existing tropes</h3>
                <span className="pill">
                  {tropeSearchStatus === "loading" ? "searching" : `${tropeResults.length} results`}
                </span>
              </div>
              {!tropeQuery.trim() ? <p className="muted">Start typing to search the existing trope index.</p> : null}
              {tropeQuery.trim() && tropeSearchStatus === "loading" ? <p className="muted">Searching tropes...</p> : null}
              {tropeQuery.trim() && tropeSearchStatus === "ready" && tropeResults.length === 0 ? (
                <p className="muted">No similar tropes were returned for this query.</p>
              ) : null}
              {tropeResults.map((item) => {
                const alreadyAssigned = draftTropeMarkers.has(normalizeDraftText(item.text));
                return (
                  <TropeCard
                    key={item.id}
                    meta={`${explanationLabel(item)} · ${item.explanation.model_name} · dim ${
                      item.explanation.vector_dimension ?? "n/a"
                    }`}
                    subtitle={`score ${item.score.toFixed(2)}`}
                    trope={item}
                    actions={
                      <button
                        className="button button-ghost"
                        disabled={busy || alreadyAssigned}
                        onClick={() => handleUseExistingTrope(item)}
                        type="button"
                      >
                        {alreadyAssigned ? "Already added" : "Use existing trope"}
                      </button>
                    }
                  />
                );
              })}
            </div>
          </section>
        </div>
      </section>
    </form>
  );
}
