import { FormEvent, useEffect, useState } from "react";

import {
  addStoryKeyword,
  addStoryTrope,
  ApiError,
  createStory,
  deleteStoryKeyword,
  deleteStoryTrope,
  getDatasetStatus,
  getErrorMessage,
  getStory,
  searchKeywords,
  searchTropes,
  updateStory,
} from "../api/client";
import {
  applyLocationDraftToFields,
  buildLocationDraft,
  isValidRecordingDate,
  type LocationDraft,
  StoryFieldInput,
  StoryLocationPickerModal,
} from "../components/StoryFieldWidgets";
import { TermCard } from "../components/TermCard";
import { TropeCard } from "../components/TropeCard";
import { buildBlankStoryFields, DATE_OF_RECORDING_FIELD, LEGACY_METADATA_SECTIONS, normalizeDraftText } from "../constants/csv";
import type { CreateStoryResponse, DatasetStatus, SearchItem, StoryDetail } from "../api/types";
import { useDatasetMaintenance } from "../maintenance";
import { routeHref, useHashSearch } from "../router";

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

function extractStoryConflictVersion(error: ApiError): number | null {
  const detail = error.detail;
  if (!detail || typeof detail !== "object") {
    return null;
  }
  const nestedDetails = (detail as { details?: unknown }).details;
  if (!nestedDetails || typeof nestedDetails !== "object") {
    return null;
  }
  const currentStoryVersion = (nestedDetails as { current_story_version?: unknown }).current_story_version;
  return typeof currentStoryVersion === "number" ? currentStoryVersion : null;
}

function isDatasetMaintenanceError(error: ApiError): boolean {
  return Boolean(error.detail && typeof error.detail === "object" && (error.detail as { code?: unknown }).code === "dataset_maintenance_in_progress");
}

function buildErrorNotice(title: string, error: unknown): PageNotice {
  return {
    tone: "error",
    title,
    body: getErrorMessage(error),
  };
}

export function CreateEntryPage() {
  const maintenance = useDatasetMaintenance();
  const hashSearch = useHashSearch();
  const [datasetStatus, setDatasetStatus] = useState<DatasetStatus | null>(null);
  const [statusLoading, setStatusLoading] = useState(true);
  const [fields, setFields] = useState<Record<string, string>>(() => buildBlankStoryFields());
  const [locationDraft, setLocationDraft] = useState<LocationDraft | null>(null);
  const [draftKeywords, setDraftKeywords] = useState<DraftTerm[]>([]);
  const [keywordQuery, setKeywordQuery] = useState("");
  const [keywordResults, setKeywordResults] = useState<SearchItem[]>([]);
  const [keywordSearchStatus, setKeywordSearchStatus] = useState<"idle" | "loading" | "ready">("idle");
  const [draftTropes, setDraftTropes] = useState<DraftTerm[]>([]);
  const [tropeQuery, setTropeQuery] = useState("");
  const [tropeResults, setTropeResults] = useState<SearchItem[]>([]);
  const [tropeSearchStatus, setTropeSearchStatus] = useState<"idle" | "loading" | "ready">("idle");
  const [savedStory, setSavedStory] = useState<StoryDetail | null>(null);
  const [entryLoading, setEntryLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<PageNotice | null>(null);
  const storyIdParam = new URLSearchParams(hashSearch).get("story_id");

  const datasetVersion = datasetStatus?.active_dataset_version ?? null;
  const draftKeywordMarkers = new Set(draftKeywords.map((keyword) => normalizeDraftText(keyword.text)));
  const draftTropeMarkers = new Set(draftTropes.map((trope) => normalizeDraftText(trope.text)));
  const recordingDate = fields[DATE_OF_RECORDING_FIELD] || "";
  const hasInvalidRecordingDate = Boolean(recordingDate) && !isValidRecordingDate(recordingDate);
  const cannotCreateStory = savedStory === null && (statusLoading || datasetVersion == null);
  const interactionDisabled = busy || entryLoading;

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
    setLocationDraft(null);
    setDraftKeywords([]);
    setKeywordQuery("");
    setKeywordResults([]);
    setKeywordSearchStatus("idle");
    setDraftTropes([]);
    setTropeQuery("");
    setTropeResults([]);
    setTropeSearchStatus("idle");
    setSavedStory(null);
    if (storyIdParam) {
      window.location.hash = routeHref("/create");
    }
  }

  useEffect(() => {
    void loadStatus().catch((caughtError) => {
      setNotice(buildErrorNotice("Could not load dataset status", caughtError));
    });
  }, []);

  useEffect(() => {
    if (!storyIdParam) {
      setEntryLoading(false);
      resetDraft();
      return;
    }

    let cancelled = false;
    setEntryLoading(true);
    void getStory(storyIdParam)
      .then((story) => {
        if (!cancelled) {
          loadSavedStoryIntoDraft(story);
        }
      })
      .catch((caughtError) => {
        if (!cancelled) {
          setNotice(buildErrorNotice("Could not load the new entry", caughtError));
        }
      })
      .finally(() => {
        if (!cancelled) {
          setEntryLoading(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [storyIdParam]);

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

  function openLocationPicker() {
    setLocationDraft(buildLocationDraft(fields));
  }

  function closeLocationPicker() {
    setLocationDraft(null);
  }

  function applyLocationPicker(nextLocationDraft: LocationDraft) {
    setFields((current) => applyLocationDraftToFields(current, nextLocationDraft));
    setLocationDraft(null);
  }

  function renderMetadataField(field: string) {
    return (
      <StoryFieldInput
        disabled={interactionDisabled}
        field={field}
        inputIdPrefix="create-entry"
        key={field}
        onChange={(value) => updateField(field, value)}
        onOpenLocationPicker={openLocationPicker}
        value={fields[field] || ""}
      />
    );
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

  function fieldsForSection(section: (typeof LEGACY_METADATA_SECTIONS)[number]): Record<string, string> {
    return Object.fromEntries(section.fields.map((field) => [field, fields[field] || ""]));
  }

  function isSectionSaveDisabled(section: (typeof LEGACY_METADATA_SECTIONS)[number]): boolean {
    const includesRecordingDate = section.fields.includes(DATE_OF_RECORDING_FIELD);
    return interactionDisabled || maintenance.active || cannotCreateStory || (includesRecordingDate && hasInvalidRecordingDate);
  }

  async function syncStoryTerms(initialStory: StoryDetail): Promise<StoryDetail> {
    let story = initialStory;

    for (const trope of story.tropes) {
      if (draftTropeMarkers.has(normalizeDraftText(trope.text))) {
        continue;
      }
      const result = await deleteStoryTrope(story.id, trope.id, story.version);
      story = {
        ...story,
        version: result.story_version,
        tropes: story.tropes.filter((currentTrope) => currentTrope.id !== trope.id),
      };
      setSavedStory(story);
    }

    for (const draftTrope of draftTropes) {
      if (story.tropes.some((trope) => normalizeDraftText(trope.text) === normalizeDraftText(draftTrope.text))) {
        continue;
      }
      const result = await addStoryTrope(story.id, {
        expected_story_version: story.version,
        text: draftTrope.text,
      });
      story = {
        ...story,
        version: result.story_version,
        tropes: [...story.tropes, result.trope],
      };
      setSavedStory(story);
    }

    for (const keyword of story.keywords) {
      if (draftKeywordMarkers.has(normalizeDraftText(keyword.text))) {
        continue;
      }
      const result = await deleteStoryKeyword(story.id, keyword.id, story.version);
      story = {
        ...story,
        version: result.story_version,
        keywords: story.keywords.filter((currentKeyword) => currentKeyword.id !== keyword.id),
      };
      setSavedStory(story);
    }

    for (const draftKeyword of draftKeywords) {
      if (story.keywords.some((keyword) => normalizeDraftText(keyword.text) === normalizeDraftText(draftKeyword.text))) {
        continue;
      }
      const result = await addStoryKeyword(story.id, {
        expected_story_version: story.version,
        text: draftKeyword.text,
      });
      story = {
        ...story,
        version: result.story_version,
        keywords: [...story.keywords, result.keyword],
      };
      setSavedStory(story);
    }

    return story;
  }

  function loadSavedStoryIntoDraft(story: StoryDetail) {
    setSavedStory(story);
    setFields(story.fields);
    setDraftTropes(
      story.tropes.map((trope) => ({
        id: trope.id,
        text: trope.text,
        story_count: trope.story_count,
      })),
    );
    setDraftKeywords(
      story.keywords.map((keyword) => ({
        id: keyword.id,
        text: keyword.text,
        story_count: 0,
      })),
    );
  }

  async function saveFields({
    fieldsToSave,
    successTitle,
    successBody,
    includeTerms,
  }: {
    fieldsToSave: Record<string, string>;
    successTitle: string;
    successBody: string;
    includeTerms: boolean;
  }) {
    if (DATE_OF_RECORDING_FIELD in fieldsToSave && hasInvalidRecordingDate) {
      setNotice({
        tone: "error",
        title: "Invalid recording date",
        body: "Enter the recording date as a valid YYYY-MM-DD value before saving.",
      });
      return;
    }
    if (savedStory === null && datasetVersion == null) {
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

      let result: CreateStoryResponse;
      if (savedStory) {
        result = await updateStory({
          story_id: savedStory.id,
          expected_story_version: savedStory.version,
          fields: fieldsToSave,
        });
      } else {
        if (datasetVersion === null) {
          return;
        }
        result = await createStory({
          expected_dataset_version: datasetVersion,
          fields: fieldsToSave,
          tropes: includeTerms ? draftTropes.map((trope) => trope.text) : [],
          keywords: includeTerms ? draftKeywords.map((keyword) => keyword.text) : [],
        });
      }

      setSavedStory(result.story);
      if (includeTerms && savedStory) {
        await syncStoryTerms(result.story);
      }

      let refreshError: string | null = null;
      try {
        await loadStatus();
      } catch (caughtError) {
        refreshError = getErrorMessage(caughtError);
      }

      setNotice({
        tone: refreshError ? "warning" : "success",
        title: refreshError ? `${successTitle}, but status did not refresh` : successTitle,
        body: refreshError ? `${successBody} Status refresh failed: ${refreshError}` : successBody,
      });
    } catch (caughtError) {
      if (caughtError instanceof ApiError && caughtError.status === 409) {
        if (isDatasetMaintenanceError(caughtError)) {
          setNotice({
            tone: "warning",
            title: "Dataset changes are paused",
            body: getErrorMessage(caughtError),
          });
          return;
        }
        if (savedStory) {
          const currentStoryVersion = extractStoryConflictVersion(caughtError);
          try {
            const currentStory = await getStory(savedStory.id);
            loadSavedStoryIntoDraft(currentStory);
          } catch {
            // Keep the original conflict visible even if the refresh fails.
          }
          setNotice({
            tone: "error",
            title: "Entry updated elsewhere",
            body:
              currentStoryVersion === null
                ? "This entry changed in another browser session. Its latest saved values were loaded; review them and try again."
                : `This entry changed in another browser session. Version ${currentStoryVersion} was loaded; review it and try again.`,
          });
          return;
        }
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

      setNotice(buildErrorNotice("Could not save the entry", caughtError));
    } finally {
      setBusy(false);
    }
  }

  async function handleSaveSection(section: (typeof LEGACY_METADATA_SECTIONS)[number]) {
    await saveFields({
      fieldsToSave: fieldsForSection(section),
      successTitle: `${section.title} saved`,
      successBody: savedStory
        ? "Your changes were saved. You can continue entering the rest of this story."
        : "An incomplete entry was created and saved. You can continue entering the rest of this story.",
      includeTerms: false,
    });
  }

  async function handleSaveAll(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await saveFields({
      fieldsToSave: fields,
      successTitle: "Entry saved",
      successBody: "All story fields, tropes, and keywords were saved. You can continue editing this entry or reset the draft to create another one.",
      includeTerms: true,
    });
  }

  return (
    <form className="page-stack" onSubmit={(event) => void handleSaveAll(event)}>
      <section className="panel">
        <div className="panel-header">
          <div>
            <h1>Create new entry</h1>
          </div>
          <div className="button-row wrap-row">
            <button className="button button-ghost" disabled={interactionDisabled} onClick={() => resetDraft()} type="button">
              {savedStory ? "Start another entry" : "Reset draft"}
            </button>
            <button className="button button-ghost" disabled={interactionDisabled || statusLoading} onClick={() => void loadStatus()} type="button">
              {statusLoading ? "Refreshing..." : "Refresh dataset"}
            </button>
            <button className="button" disabled={interactionDisabled || maintenance.active || cannotCreateStory || hasInvalidRecordingDate} type="submit">
              {busy ? "Saving..." : savedStory ? "Save all changes" : "Save new entry"}
            </button>
          </div>
        </div>
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
              <h2>Active dataset</h2>
              {savedStory ? <span className="pill">Entry saved · incomplete</span> : null}
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
                {section.fields.map((field) => renderMetadataField(field))}
              </div>
              <div className="button-row">
                <span className="muted">Save this section before moving on.</span>
                <button
                  className="button"
                  disabled={isSectionSaveDisabled(section)}
                  onClick={() => void handleSaveSection(section)}
                  type="button"
                >
                  {busy ? "Saving..." : `Save ${section.title}`}
                </button>
              </div>
            </section>
          ))}
        </div>

        <div className="page-stack">
          <section className="panel">
            <div className="panel-header">
              <h2>Current keywords</h2>
              <span className="pill">{draftKeywords.length} selected</span>
            </div>
            <div className="stack">
              {draftKeywords.length ? (
                draftKeywords.map((keyword) => (
                  <TermCard
                    key={keyword.id}
                    term={keyword}
                    actions={
                      <button
                        className="button button-danger"
                        disabled={interactionDisabled}
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
              <h2>Add keyword</h2>
            </div>

            <label className="field">
              <span>Keyword query</span>
              <input
                className="input"
                disabled={interactionDisabled}
                onChange={(event) => setKeywordQuery(event.target.value)}
                placeholder="Type a keyword to search for similar existing keywords"
                value={keywordQuery}
              />
            </label>

            <div className="card subdued">
              <div className="card-row">
                <h3>Keep typed keyword</h3>
                <button className="button" disabled={interactionDisabled || !keywordQuery.trim()} onClick={() => handleKeepTypedKeyword()} type="button">
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
              {keywordQuery.trim() && keywordSearchStatus === "loading" ? <p className="muted">Searching keywords...</p> : null}
              {keywordQuery.trim() && keywordSearchStatus === "ready" && keywordResults.length === 0 ? (
                <p className="muted">No similar keywords were returned for this query.</p>
              ) : null}
              {keywordResults.map((item) => {
                const alreadyAssigned = draftKeywordMarkers.has(normalizeDraftText(item.text));
                return (
                  <TermCard
                    key={item.id}
                    term={item}
                    actions={
                      <button
                        className="button button-ghost"
                        disabled={interactionDisabled || alreadyAssigned}
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
                    minimumStoryCount={0}
                    trope={trope}
                    actions={
                      <button
                        className="button button-danger"
                        disabled={interactionDisabled}
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
              <h2>Add trope</h2>
            </div>

            <label className="field">
              <span>Trope query</span>
              <input
                className="input"
                disabled={interactionDisabled}
                onChange={(event) => setTropeQuery(event.target.value)}
                placeholder="Type a trope to search for similar existing tropes"
                value={tropeQuery}
              />
            </label>

            <div className="card subdued">
              <div className="card-row">
                <h3>Keep typed trope</h3>
                <button className="button" disabled={interactionDisabled || !tropeQuery.trim()} onClick={() => handleKeepTypedTrope()} type="button">
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
              {tropeQuery.trim() && tropeSearchStatus === "loading" ? <p className="muted">Searching tropes...</p> : null}
              {tropeQuery.trim() && tropeSearchStatus === "ready" && tropeResults.length === 0 ? (
                <p className="muted">No similar tropes were returned for this query.</p>
              ) : null}
              {tropeResults.map((item) => {
                const alreadyAssigned = draftTropeMarkers.has(normalizeDraftText(item.text));
                return (
                  <TropeCard
                    key={item.id}
                    trope={item}
                    actions={
                      <button
                        className="button button-ghost"
                        disabled={interactionDisabled || alreadyAssigned}
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

      {locationDraft ? (
        <StoryLocationPickerModal
          busy={interactionDisabled}
          locationDraft={locationDraft}
          onApply={applyLocationPicker}
          onCancel={closeLocationPicker}
          onChange={setLocationDraft}
        />
      ) : null}
    </form>
  );
}
