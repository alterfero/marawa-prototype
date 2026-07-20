import { useEffect, useMemo, useRef, useState } from "react";

import {
  addStoryKeyword,
  addStoryTrope,
  ApiError,
  deleteStoryKeyword,
  deleteStoryTrope,
  getErrorMessage,
  getStories,
  getStory,
  replaceStoryKeyword,
  replaceStoryTrope,
  searchKeywords,
  searchTropes,
  updateStory,
  validateStoryTrope,
} from "../api/client";
import {
  applyLocationDraftToFields,
  buildLocationDraft,
  type LocationDraft,
  StoryFieldInput,
  StoryLocationPickerModal,
} from "../components/StoryFieldWidgets";
import {
  createEmptyStoryFieldFilter,
  normalizeStoryFieldFilters,
  serializeStoryFieldFilters,
  storyFieldFiltersAreComplete,
  storyMatchesFieldFilters,
  StoryFieldFilterBuilder,
  type StoryFieldFilter,
} from "../components/StoryFieldFilters";
import { TermCard } from "../components/TermCard";
import { TropeCard } from "../components/TropeCard";
import { roleAtLeast, useAuth } from "../auth";
import type { SearchItem, StoryCompleteness, StoryDetail, StorySummary, TropeSearchItem } from "../api/types";
import { LEGACY_METADATA_SECTIONS, normalizeDraftText } from "../constants/csv";
import { routeHref, useHashSearch } from "../router";

interface PageNotice {
  tone: "error" | "success";
  title: string;
  body?: string;
}

const ALL_COMPLETENESS_OPTIONS: StoryCompleteness[] = ["incomplete", "pending review", "complete"];

function completenessBadgeClassName(completeness: StoryCompleteness): string {
  return `story-completeness-${completeness.replace(/\s+/g, "-")}`;
}

function canSelectCompleteness(
  role: "guest" | "contributor" | "admin" | null | undefined,
  currentCompleteness: StoryCompleteness,
  nextCompleteness: StoryCompleteness,
): boolean {
  if (roleAtLeast(role, "admin")) {
    return true;
  }
  if (
    role === "contributor" &&
    currentCompleteness !== "complete" &&
    nextCompleteness !== "complete"
  ) {
    return true;
  }
  return false;
}

function extractConflictVersion(error: ApiError): number | null {
  const detail = error.detail;
  if (!detail || typeof detail !== "object") {
    return null;
  }
  const nestedDetails = (detail as { details?: unknown }).details;
  if (nestedDetails && typeof nestedDetails === "object") {
    const nestedVersion = (nestedDetails as { current_story_version?: unknown }).current_story_version;
    if (typeof nestedVersion === "number") {
      return nestedVersion;
    }
  }
  const currentStoryVersion = (detail as { current_story_version?: unknown }).current_story_version;
  return typeof currentStoryVersion === "number" ? currentStoryVersion : null;
}

function storyMatchesQuery(story: StorySummary, query: string): boolean {
  const marker = query.trim().toLowerCase();
  if (!marker) {
    return true;
  }
  return [story.title, story.summary, story.territory, story.source_row_number?.toString() || ""]
    .join(" ")
    .toLowerCase()
    .includes(marker);
}

function summarizeStory(detail: StoryDetail): string {
  return (
    detail.fields["Abstract (Eng)"] ||
    detail.fields["Abstract (Fr)"] ||
    detail.fields["1-sentence summary"] ||
    "No summary available."
  );
}

function storyListPreview(story: StorySummary): string {
  if (story.summary) {
    return story.summary;
  }
  if (story.territory) {
    return story.territory;
  }
  return `${story.trope_count} tropes · ${story.keyword_count} keywords`;
}

function storyFieldsChanged(current: Record<string, string>, baseline: Record<string, string>): boolean {
  const allKeys = new Set([...Object.keys(current), ...Object.keys(baseline)]);
  for (const key of allKeys) {
    if ((current[key] || "") !== (baseline[key] || "")) {
      return true;
    }
  }
  return false;
}


export function StoriesPage({ canEdit }: { canEdit: boolean }) {
  const { user } = useAuth();
  const hashSearch = useHashSearch();
  const nextFilterIdRef = useRef(1);
  const [stories, setStories] = useState<StorySummary[]>([]);
  const [selectedStoryId, setSelectedStoryId] = useState<string | null>(null);
  const [detail, setDetail] = useState<StoryDetail | null>(null);
  const [fieldDraft, setFieldDraft] = useState<Record<string, string>>({});
  const [locationDraft, setLocationDraft] = useState<LocationDraft | null>(null);
  const [showFieldEditor, setShowFieldEditor] = useState(false);
  const [storyQuery, setStoryQuery] = useState("");
  const [draftFilters, setDraftFilters] = useState<StoryFieldFilter[]>([]);
  const [appliedFilters, setAppliedFilters] = useState<StoryFieldFilter[]>([]);
  const [tropeQuery, setTropeQuery] = useState("");
  const [tropeResults, setTropeResults] = useState<TropeSearchItem[]>([]);
  const [tropeSearchStatus, setTropeSearchStatus] = useState<"idle" | "loading" | "ready">("idle");
  const [editingTropeId, setEditingTropeId] = useState<string | null>(null);
  const [editingTropeQuery, setEditingTropeQuery] = useState("");
  const [editingTropeResults, setEditingTropeResults] = useState<TropeSearchItem[]>([]);
  const [editingTropeSearchStatus, setEditingTropeSearchStatus] = useState<"idle" | "loading" | "ready">("idle");
  const [keywordQuery, setKeywordQuery] = useState("");
  const [keywordResults, setKeywordResults] = useState<SearchItem[]>([]);
  const [keywordSearchStatus, setKeywordSearchStatus] = useState<"idle" | "loading" | "ready">("idle");
  const [editingKeywordId, setEditingKeywordId] = useState<string | null>(null);
  const [editingKeywordQuery, setEditingKeywordQuery] = useState("");
  const [editingKeywordResults, setEditingKeywordResults] = useState<SearchItem[]>([]);
  const [editingKeywordSearchStatus, setEditingKeywordSearchStatus] = useState<"idle" | "loading" | "ready">("idle");
  const [storiesLoading, setStoriesLoading] = useState(true);
  const [storyLoading, setStoryLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<PageNotice | null>(null);
  const selectedStoryParam = new URLSearchParams(hashSearch).get("selected_story_id");

  const filteredStories = stories.filter(
    (story) => storyMatchesQuery(story, storyQuery) && storyMatchesFieldFilters(story, appliedFilters),
  );
  const assignedTropeIds = new Set(detail?.tropes.map((trope) => trope.id) ?? []);
  const assignedKeywordIds = new Set(detail?.keywords.map((keyword) => keyword.id) ?? []);
  const editingTrope = detail?.tropes.find((trope) => trope.id === editingTropeId) ?? null;
  const editingKeyword = detail?.keywords.find((keyword) => keyword.id === editingKeywordId) ?? null;
  const interactionDisabled = busy || storiesLoading || storyLoading;
  const fieldsDirty = detail ? storyFieldsChanged(fieldDraft, detail.fields) : false;
  const draftFiltersAreComplete = storyFieldFiltersAreComplete(draftFilters);

  function resetTropeEditor() {
    setEditingTropeId(null);
    setEditingTropeQuery("");
    setEditingTropeResults([]);
    setEditingTropeSearchStatus("idle");
  }

  function resetKeywordEditor() {
    setEditingKeywordId(null);
    setEditingKeywordQuery("");
    setEditingKeywordResults([]);
    setEditingKeywordSearchStatus("idle");
  }

  function updateFieldDraft(field: string, value: string) {
    setFieldDraft((current) => ({
      ...current,
      [field]: value,
    }));
  }

  function addDraftFilter() {
    const nextId = nextFilterIdRef.current;
    nextFilterIdRef.current += 1;
    setDraftFilters((current) => [...current, createEmptyStoryFieldFilter(nextId)]);
  }

  function updateDraftFilterField(filterId: number, field: string) {
    setDraftFilters((current) =>
      current.map((filter) =>
        filter.id === filterId
          ? {
              ...filter,
              field,
              selectedValues: [],
            }
          : filter,
      ),
    );
  }

  function updateDraftFilterValues(filterId: number, selectedValues: string[]) {
    setDraftFilters((current) =>
      current.map((filter) =>
        filter.id === filterId
          ? {
              ...filter,
              selectedValues,
            }
          : filter,
      ),
    );
  }

  function removeDraftFilter(filterId: number) {
    setDraftFilters((current) => current.filter((filter) => filter.id !== filterId));
  }

  function applyDraftFilters() {
    if (!draftFiltersAreComplete) {
      return;
    }
    setAppliedFilters(
      draftFilters.map((filter) => ({
        ...filter,
        selectedValues: [...filter.selectedValues],
      })),
    );
  }

  function clearAllFilters() {
    setDraftFilters([]);
    setAppliedFilters([]);
  }

  function openLocationPicker() {
    setLocationDraft(buildLocationDraft(fieldDraft));
  }

  function closeLocationPicker() {
    setLocationDraft(null);
  }

  function applyLocationPicker() {
    if (!locationDraft) {
      return;
    }

    setFieldDraft((current) => applyLocationDraftToFields(current, locationDraft));
    setLocationDraft(null);
  }

  async function loadStories(preferredStoryId?: string | null) {
    try {
      setStoriesLoading(true);
      const result = await getStories();
      setStories(result.items);

      const nextStoryId =
        preferredStoryId && result.items.some((item) => item.id === preferredStoryId)
          ? preferredStoryId
          : result.items[0]?.id || null;
      setSelectedStoryId(nextStoryId);
      return nextStoryId;
    } finally {
      setStoriesLoading(false);
    }
  }

  async function loadStory(storyId: string) {
    try {
      setStoryLoading(true);
      const result = await getStory(storyId);
      setDetail(result);
      setFieldDraft(result.fields);
      return result;
    } finally {
      setStoryLoading(false);
    }
  }

  async function refresh(storyId?: string | null) {
    try {
      const nextStoryId = await loadStories(storyId ?? selectedStoryId);
      if (nextStoryId) {
        await loadStory(nextStoryId);
      } else {
        setDetail(null);
        setFieldDraft({});
      }
    } catch (error) {
      setNotice({
        tone: "error",
        title: "Could not load stories",
        body: getErrorMessage(error),
      });
    }
  }

  useEffect(() => {
    void refresh(selectedStoryParam);
  }, []);

  useEffect(() => {
    resetTropeEditor();
    resetKeywordEditor();
    setLocationDraft(null);
  }, [selectedStoryId]);

  useEffect(() => {
    if (!selectedStoryParam) {
      return;
    }

    if (stories.some((story) => story.id === selectedStoryParam)) {
      setSelectedStoryId((current) => (current === selectedStoryParam ? current : selectedStoryParam));
      return;
    }

    if (!storiesLoading) {
      void refresh(selectedStoryParam);
    }
  }, [selectedStoryParam, stories, storiesLoading]);

  useEffect(() => {
    const normalizedFilters = normalizeStoryFieldFilters(draftFilters, stories);
    if (serializeStoryFieldFilters(normalizedFilters) !== serializeStoryFieldFilters(draftFilters)) {
      setDraftFilters(normalizedFilters);
    }
  }, [draftFilters, stories]);

  useEffect(() => {
    if (!selectedStoryId) {
      return;
    }

    void loadStory(selectedStoryId).catch((error) => {
      setNotice({
        tone: "error",
        title: "Could not load story",
        body: getErrorMessage(error),
      });
    });
  }, [selectedStoryId]);

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
        } catch (error) {
          if (cancelled) {
            return;
          }
          setTropeResults([]);
          setTropeSearchStatus("ready");
          setNotice({
            tone: "error",
            title: "Could not search tropes",
            body: getErrorMessage(error),
          });
        }
      })();
    }, 250);

    return () => {
      cancelled = true;
      window.clearTimeout(timeoutId);
    };
  }, [tropeQuery]);

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
        } catch (error) {
          if (cancelled) {
            return;
          }
          setKeywordResults([]);
          setKeywordSearchStatus("ready");
          setNotice({
            tone: "error",
            title: "Could not search keywords",
            body: getErrorMessage(error),
          });
        }
      })();
    }, 250);

    return () => {
      cancelled = true;
      window.clearTimeout(timeoutId);
    };
  }, [keywordQuery]);

  useEffect(() => {
    if (!editingTropeId) {
      setEditingTropeResults([]);
      setEditingTropeSearchStatus("idle");
      return;
    }

    const trimmedQuery = editingTropeQuery.trim();
    if (!trimmedQuery) {
      setEditingTropeResults([]);
      setEditingTropeSearchStatus("idle");
      return;
    }

    let cancelled = false;
    const timeoutId = window.setTimeout(() => {
      void (async () => {
        try {
          setEditingTropeSearchStatus("loading");
          const result = await searchTropes({ query: trimmedQuery, limit: 8 });
          if (cancelled) {
            return;
          }
          setEditingTropeResults(result.items);
          setEditingTropeSearchStatus("ready");
        } catch (error) {
          if (cancelled) {
            return;
          }
          setEditingTropeResults([]);
          setEditingTropeSearchStatus("ready");
          setNotice({
            tone: "error",
            title: "Could not search replacement tropes",
            body: getErrorMessage(error),
          });
        }
      })();
    }, 250);

    return () => {
      cancelled = true;
      window.clearTimeout(timeoutId);
    };
  }, [editingTropeId, editingTropeQuery]);

  useEffect(() => {
    if (!editingKeywordId) {
      setEditingKeywordResults([]);
      setEditingKeywordSearchStatus("idle");
      return;
    }

    const trimmedQuery = editingKeywordQuery.trim();
    if (!trimmedQuery) {
      setEditingKeywordResults([]);
      setEditingKeywordSearchStatus("idle");
      return;
    }

    let cancelled = false;
    const timeoutId = window.setTimeout(() => {
      void (async () => {
        try {
          setEditingKeywordSearchStatus("loading");
          const result = await searchKeywords({ query: trimmedQuery, limit: 8 });
          if (cancelled) {
            return;
          }
          setEditingKeywordResults(result.items);
          setEditingKeywordSearchStatus("ready");
        } catch (error) {
          if (cancelled) {
            return;
          }
          setEditingKeywordResults([]);
          setEditingKeywordSearchStatus("ready");
          setNotice({
            tone: "error",
            title: "Could not search replacement keywords",
            body: getErrorMessage(error),
          });
        }
      })();
    }, 250);

    return () => {
      cancelled = true;
      window.clearTimeout(timeoutId);
    };
  }, [editingKeywordId, editingKeywordQuery]);

  useEffect(() => {
    if (editingTropeId && detail && !detail.tropes.some((trope) => trope.id === editingTropeId)) {
      resetTropeEditor();
    }
  }, [detail, editingTropeId]);

  useEffect(() => {
    if (editingKeywordId && detail && !detail.keywords.some((keyword) => keyword.id === editingKeywordId)) {
      resetKeywordEditor();
    }
  }, [detail, editingKeywordId]);

  async function handleStoryConflict(error: ApiError, storyId: string) {
    const currentVersion = extractConflictVersion(error);
    await refresh(storyId);
    setNotice({
      tone: "error",
      title: "Story updated elsewhere",
      body:
        currentVersion === null
          ? "This story changed in another browser session. The latest version has been reloaded."
          : `This story changed in another browser session. Reloaded story version ${currentVersion}. Review the refreshed story and try again.`,
    });
  }

  async function runStoryMutation(
    storyId: string,
    action: () => Promise<unknown>,
    successNotice: PageNotice,
    onSuccess?: () => void,
  ) {
    try {
      setBusy(true);
      setNotice(null);
      await action();
      await refresh(storyId);
      onSuccess?.();
      setNotice(successNotice);
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        await handleStoryConflict(error, storyId);
        return;
      }
      setNotice({
        tone: "error",
        title: "Mutation failed",
        body: getErrorMessage(error),
      });
    } finally {
      setBusy(false);
    }
  }

  async function handleSaveStoryFields() {
    if (!detail) {
      return;
    }
    await runStoryMutation(
      detail.id,
      () =>
        updateStory({
          story_id: detail.id,
          expected_story_version: detail.version,
          fields: fieldDraft,
        }),
      {
        tone: "success",
        title: "Story updated",
        body: "The story fields were saved.",
      },
    );
  }

  async function handleSetCompleteness(completeness: StoryCompleteness) {
    if (!detail || detail.completeness === completeness) {
      return;
    }

    await runStoryMutation(
      detail.id,
      () =>
        updateStory({
          story_id: detail.id,
          expected_story_version: detail.version,
          fields: {},
          completeness,
        }),
      {
        tone: "success",
        title: "Completeness updated",
        body: `Story completeness is now ${completeness}.`,
      },
    );
  }

  async function handleUseExistingTrope(tropeId: string) {
    if (!detail) {
      return;
    }
    await runStoryMutation(
      detail.id,
      () =>
        addStoryTrope(detail.id, {
          expected_story_version: detail.version,
          trope_id: tropeId,
        }),
      {
        tone: "success",
        title: "Trope assigned",
        body: "The existing canonical trope was added to the story.",
      },
    );
  }

  async function handleKeepTypedTrope() {
    if (!detail || !tropeQuery.trim()) {
      return;
    }
    await runStoryMutation(
      detail.id,
      () =>
        addStoryTrope(detail.id, {
          expected_story_version: detail.version,
          text: tropeQuery.trim(),
        }),
      {
        tone: "success",
        title: "Trope saved",
        body: "The typed trope was added to the story.",
      },
      () => {
        setTropeQuery("");
        setTropeResults([]);
        setTropeSearchStatus("idle");
      },
    );
  }

  async function handleKeepEditedTrope(tropeId: string) {
    if (!detail || !editingTropeQuery.trim()) {
      return;
    }
    await runStoryMutation(
      detail.id,
      () =>
        replaceStoryTrope(detail.id, tropeId, {
          expected_story_version: detail.version,
          text: editingTropeQuery.trim(),
        }),
      {
        tone: "success",
        title: "Trope edited",
        body: "The story trope was updated from the inline editor.",
      },
      resetTropeEditor,
    );
  }

  async function handleUseExistingEditedTrope(currentTropeId: string, replacementTropeId: string) {
    if (!detail) {
      return;
    }
    await runStoryMutation(
      detail.id,
      () =>
        replaceStoryTrope(detail.id, currentTropeId, {
          expected_story_version: detail.version,
          trope_id: replacementTropeId,
        }),
      {
        tone: "success",
        title: "Trope edited",
        body: "The story trope was replaced with an existing canonical trope.",
      },
      resetTropeEditor,
    );
  }

  async function handleValidateTrope(tropeId: string) {
    if (!detail) {
      return;
    }
    await runStoryMutation(
      detail.id,
      () => validateStoryTrope(detail.id, tropeId, detail.version),
      {
        tone: "success",
        title: "Trope validated",
        body: "The trope assignment is now validated.",
      },
    );
  }

  async function handleDeleteTrope(tropeId: string) {
    if (!detail) {
      return;
    }
    await runStoryMutation(
      detail.id,
      () => deleteStoryTrope(detail.id, tropeId, detail.version),
      {
        tone: "success",
        title: "Trope removed",
        body: "The story-trope assignment was hard-deleted.",
      },
    );
  }

  async function handleUseExistingKeyword(keywordId: string) {
    if (!detail) {
      return;
    }
    await runStoryMutation(
      detail.id,
      () =>
        addStoryKeyword(detail.id, {
          expected_story_version: detail.version,
          keyword_id: keywordId,
        }),
      {
        tone: "success",
        title: "Keyword assigned",
        body: "The existing canonical keyword was added to the story.",
      },
    );
  }

  async function handleKeepTypedKeyword() {
    if (!detail || !keywordQuery.trim()) {
      return;
    }
    await runStoryMutation(
      detail.id,
      () =>
        addStoryKeyword(detail.id, {
          expected_story_version: detail.version,
          text: keywordQuery.trim(),
        }),
      {
        tone: "success",
        title: "Keyword saved",
        body: "The typed keyword was added to the story.",
      },
      () => {
        setKeywordQuery("");
        setKeywordResults([]);
        setKeywordSearchStatus("idle");
      },
    );
  }

  async function handleKeepEditedKeyword(keywordId: string) {
    if (!detail || !editingKeywordQuery.trim()) {
      return;
    }
    await runStoryMutation(
      detail.id,
      () =>
        replaceStoryKeyword(detail.id, keywordId, {
          expected_story_version: detail.version,
          text: editingKeywordQuery.trim(),
        }),
      {
        tone: "success",
        title: "Keyword edited",
        body: "The story keyword was updated from the inline editor.",
      },
      resetKeywordEditor,
    );
  }

  async function handleUseExistingEditedKeyword(currentKeywordId: string, replacementKeywordId: string) {
    if (!detail) {
      return;
    }
    await runStoryMutation(
      detail.id,
      () =>
        replaceStoryKeyword(detail.id, currentKeywordId, {
          expected_story_version: detail.version,
          keyword_id: replacementKeywordId,
        }),
      {
        tone: "success",
        title: "Keyword edited",
        body: "The story keyword was replaced with an existing canonical keyword.",
      },
      resetKeywordEditor,
    );
  }

  async function handleDeleteKeyword(keywordId: string) {
    if (!detail) {
      return;
    }
    await runStoryMutation(
      detail.id,
      () => deleteStoryKeyword(detail.id, keywordId, detail.version),
      {
        tone: "success",
        title: "Keyword removed",
        body: "The story-keyword assignment was hard-deleted.",
      },
    );
  }

  const storyEditorSections = useMemo(() => LEGACY_METADATA_SECTIONS, []);

  return (
    <div className="page-stack">
      <section className="panel">
        <div className="panel-header">
          <div>
            <h1>{canEdit ? "Stories and story editing" : "Stories"}</h1>
            <p className="muted">
              {canEdit
                ? "Browse stories, update fields, and manage trope and keyword assignments."
                : "Browse story details, tropes, and keywords in read-only mode."}
            </p>
          </div>
          <button className="button button-ghost" disabled={storiesLoading || storyLoading} onClick={() => void refresh()} type="button">
            {storiesLoading || storyLoading ? "Loading..." : "Refresh"}
          </button>
        </div>
      </section>

      {notice ? (
        <section className={`notice ${notice.tone === "error" ? "notice-error" : "notice-success"}`}>
          <strong className="notice-title">{notice.title}</strong>
          {notice.body ? <p>{notice.body}</p> : null}
        </section>
      ) : null}

      <section className="two-column-layout">
        <aside className="panel story-browser-panel">
          <div className="panel-header">
            <h2>Stories</h2>
            <span className="pill">
              {filteredStories.length}/{stories.length}
            </span>
          </div>
          <label className="field">
            <span>Search stories</span>
            <input
              className="input"
              disabled={storiesLoading}
              onChange={(event) => setStoryQuery(event.target.value)}
              placeholder="Search by title, summary, territory, or row"
              value={storyQuery}
            />
          </label>
          <StoryFieldFilterBuilder
            appliedFilters={appliedFilters}
            draftFilters={draftFilters}
            loading={storiesLoading}
            onAddFilter={addDraftFilter}
            onApplyFilters={applyDraftFilters}
            onClearFilters={clearAllFilters}
            onRemoveFilter={removeDraftFilter}
            onUpdateFilterField={updateDraftFilterField}
            onUpdateFilterValues={updateDraftFilterValues}
            stories={stories}
          />
          <div className="list story-browser-list">
            {storiesLoading ? <p className="muted">Loading stories...</p> : null}
            {!storiesLoading && filteredStories.length === 0 ? <p className="muted">No stories match the current search and filters.</p> : null}
            {filteredStories.map((story) => (
              <button
                className={`list-row story-browser-row ${story.id === selectedStoryId ? "list-row-active" : ""}`}
                disabled={storiesLoading}
                key={story.id}
                onClick={() => {
                  window.location.hash = routeHref("/stories", { selected_story_id: story.id });
                }}
                type="button"
              >
                <div className="story-browser-row-top">
                  <strong className="story-browser-title">{story.title || `Story ${story.source_row_number ?? "?"}`}</strong>
                  <span className={`story-completeness-badge ${completenessBadgeClassName(story.completeness)}`}>
                    {story.completeness}
                  </span>
                </div>
                {!story.has_location ? <span className="story-list-alert">Location missing</span> : null}
                <span className="muted story-browser-preview">{storyListPreview(story)}</span>
              </button>
            ))}
          </div>
        </aside>

        <div className="page-stack review-detail-column">
          <section className="panel">
            <div className="panel-header">
              <div>
                <h2>{detail?.fields["Story title (Eng)"] || detail?.id || "No story selected"}</h2>
                {detail ? <p className="muted">Version {detail.version}</p> : null}
              </div>
              {detail ? <span className="pill">Row {detail.source_row_number ?? "n/a"}</span> : null}
            </div>

            {detail ? (
              <div className="page-stack">
                {storyLoading ? <p className="muted">Loading story details...</p> : null}
                <article className="card subdued story-completeness-card">
                  <div aria-label="Story completeness" className="story-completeness-switch" role="group">
                    {ALL_COMPLETENESS_OPTIONS.map((option) => {
                      const isCurrent = detail.completeness === option;
                      const selectable = canEdit && canSelectCompleteness(user?.role, detail.completeness, option);

                      return (
                        <button
                          aria-pressed={isCurrent}
                          className={`button story-completeness-option ${
                            isCurrent
                              ? `story-completeness-option-active ${completenessBadgeClassName(option)}`
                              : "button-ghost"
                          }`}
                          disabled={interactionDisabled || (!isCurrent && !selectable)}
                          key={option}
                          onClick={() => void handleSetCompleteness(option)}
                          type="button"
                        >
                          {option}
                        </button>
                      );
                    })}
                  </div>
                </article>

                <article className="card subdued">
                  <h3>Summary</h3>
                  <p>{summarizeStory(detail)}</p>
                </article>

                <div className="field-grid">
                  <article className="card subdued">
                    <h3>Territory</h3>
                    <p>{detail.fields["territory"] || "Not specified"}</p>
                  </article>
                  <article className="card subdued">
                    <h3>Language</h3>
                    <p>{detail.fields["original language"] || "Not specified"}</p>
                    <p className="muted">
                      Group {detail.fields["lg group"] || "n/a"} · publication {detail.fields["lg of publication"] || "n/a"}
                    </p>
                  </article>
                  <article className="card subdued">
                    <h3>Source</h3>
                    <p>{detail.fields["Source"] || detail.fields["Other source"] || "Not specified"}</p>
                    <p className="muted">
                      Pages {detail.fields["pages"] || "n/a"} · recording date {detail.fields["date of recording"] || "n/a"}
                    </p>
                  </article>
                  <article className="card subdued">
                    <h3>Story metadata</h3>
                    <p>{detail.fields["storyteller"] || "No storyteller listed"}</p>
                    <p className="muted">
                      Place {detail.fields["place of recording"] || "n/a"} · coord {detail.fields["space coord"] || "n/a"}
                    </p>
                  </article>
                </div>
              </div>
            ) : storyLoading ? (
              <p className="muted">Loading story details...</p>
            ) : (
              <p className="muted">Choose a story to load its details.</p>
            )}
          </section>

          {canEdit && detail ? (
            <section className="panel">
              <div className="panel-header">
                <div>
                  <h2>Story fields</h2>
                  <p className="muted">Edit legacy CSV fields directly while preserving column compatibility.</p>
                </div>
                <div className="button-row wrap-row">
                  <button className="button button-ghost" onClick={() => setShowFieldEditor((current) => !current)} type="button">
                    {showFieldEditor ? "Hide editor" : "Show editor"}
                  </button>
                  <button className="button" disabled={interactionDisabled || !fieldsDirty} onClick={() => void handleSaveStoryFields()} type="button">
                    Save story fields
                  </button>
                </div>
              </div>

              {showFieldEditor ? (
                <div className="page-stack">
                  {storyEditorSections.map((section) => (
                    <article className="card subdued" key={section.title}>
                      <div className="stack">
                        <h3>{section.title}</h3>
                        <div className="create-field-grid">
                          {section.fields.map((field) => {
                            return (
                              <StoryFieldInput
                                disabled={interactionDisabled}
                                field={field}
                                inputIdPrefix="story-fields"
                                key={field}
                                onChange={(value) => updateFieldDraft(field, value)}
                                onOpenLocationPicker={openLocationPicker}
                                value={fieldDraft[field] ?? ""}
                              />
                            );
                          })}
                        </div>
                      </div>
                    </article>
                  ))}
                </div>
              ) : (
                <p className="muted">Open the editor to update any imported CSV field on this story.</p>
              )}
            </section>
          ) : null}

          <section className="panel">
            <div className="panel-header">
              <h2>Current keywords</h2>
              {detail ? <span className="pill">{detail.keywords.length}</span> : null}
            </div>
            <div className="stack">
              {detail?.keywords.length ? (
                detail.keywords.map((keyword) => (
                  <TermCard
                    key={keyword.id}
                    term={{
                      id: keyword.id,
                      text: keyword.text,
                      story_count: 1,
                    }}
                    actions={
                      canEdit ? (
                        <>
                          <button
                            className="button button-ghost"
                            disabled={interactionDisabled}
                            onClick={() => {
                              if (editingKeywordId === keyword.id) {
                                resetKeywordEditor();
                                return;
                              }
                              setEditingKeywordId(keyword.id);
                              setEditingKeywordQuery(keyword.text);
                            }}
                            type="button"
                          >
                            {editingKeywordId === keyword.id ? "Cancel edit" : "Edit"}
                          </button>
                          <button
                            className="button button-danger"
                            disabled={interactionDisabled}
                            onClick={() => void handleDeleteKeyword(keyword.id)}
                            type="button"
                          >
                            Delete
                          </button>
                        </>
                      ) : undefined
                    }
                  >
                    {editingKeywordId === keyword.id && canEdit ? (
                      <div className="card subdued trope-card-editor">
                        <label className="field">
                          <span>Edit keyword</span>
                          <input
                            className="input"
                            disabled={interactionDisabled}
                            onChange={(event) => setEditingKeywordQuery(event.target.value)}
                            placeholder="Type a replacement keyword or reuse an existing one"
                            value={editingKeywordQuery}
                          />
                        </label>

                        <div className="button-row wrap-row">
                          <button
                            className="button"
                            disabled={
                              interactionDisabled ||
                              !editingKeywordQuery.trim() ||
                              (editingKeyword ? normalizeDraftText(editingKeywordQuery) === normalizeDraftText(editingKeyword.text) : true)
                            }
                            onClick={() => void handleKeepEditedKeyword(keyword.id)}
                            type="button"
                          >
                            Save typed keyword
                          </button>
                          <button className="button button-ghost" disabled={interactionDisabled} onClick={resetKeywordEditor} type="button">
                            Cancel
                          </button>
                        </div>

                        <div className="stack">
                          <div className="panel-header">
                            <h3>Similar existing keywords</h3>
                            <span className="pill">
                              {editingKeywordSearchStatus === "loading" ? "searching" : `${editingKeywordResults.length} results`}
                            </span>
                          </div>
                          {editingKeywordQuery.trim() && editingKeywordSearchStatus === "loading" ? (
                            <p className="muted">Searching keywords...</p>
                          ) : null}
                          {editingKeywordQuery.trim() &&
                          editingKeywordSearchStatus === "ready" &&
                          editingKeywordResults.length === 0 ? (
                            <p className="muted">No similar keywords were returned for this query.</p>
                          ) : null}
                          {editingKeywordResults.map((item) => {
                            const isCurrentKeyword = item.id === keyword.id;
                            const alreadyAssignedElsewhere = assignedKeywordIds.has(item.id) && item.id !== keyword.id;
                            return (
                              <TermCard
                                key={`edit-keyword-${keyword.id}-${item.id}`}
                                meta={`${item.story_count} stories`}
                                term={item}
                                actions={
                                  <button
                                    className="button button-ghost"
                                    disabled={interactionDisabled || isCurrentKeyword || alreadyAssignedElsewhere}
                                    onClick={() => void handleUseExistingEditedKeyword(keyword.id, item.id)}
                                    type="button"
                                  >
                                    {isCurrentKeyword ? "Current keyword" : alreadyAssignedElsewhere ? "Already assigned" : "Use existing keyword"}
                                  </button>
                                }
                              />
                            );
                          })}
                        </div>
                      </div>
                    ) : null}
                  </TermCard>
                ))
              ) : (
                <p className="muted">No keywords on this story yet.</p>
              )}
            </div>
          </section>

          {canEdit ? (
            <section className="panel">
              <div className="panel-header">
                <h2>Add keyword</h2>
              </div>

              <label className="field">
                <span>Keyword query</span>
                <input
                  className="input"
                  onChange={(event) => setKeywordQuery(event.target.value)}
                  placeholder="Type a keyword to search for similar existing keywords"
                  value={keywordQuery}
                />
              </label>

              <div className="card subdued">
                <div className="card-row">
                  <h3>Keep typed keyword</h3>
                  <button
                    className="button"
                    disabled={!detail || interactionDisabled || !keywordQuery.trim()}
                    onClick={() => void handleKeepTypedKeyword()}
                    type="button"
                  >
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
                  const alreadyAssigned = assignedKeywordIds.has(item.id);
                  return (
                    <TermCard
                      key={item.id}
                      meta={`${item.story_count} stories`}
                      term={item}
                      actions={
                        <button
                          className="button button-ghost"
                          disabled={!detail || interactionDisabled || alreadyAssigned}
                          onClick={() => void handleUseExistingKeyword(item.id)}
                          type="button"
                        >
                          {alreadyAssigned ? "Already assigned" : "Use existing keyword"}
                        </button>
                      }
                    />
                  );
                })}
              </div>
            </section>
          ) : null}

          <section className="panel">
            <div className="panel-header">
              <h2>Current tropes</h2>
            </div>
            <div className="stack">
              {detail?.tropes.length ? (
                detail.tropes.map((trope) => (
                  <TropeCard
                    key={trope.id}
                    trope={trope}
                    actions={
                      canEdit ? (
                        <>
                          <button
                            className="button button-ghost"
                            disabled={interactionDisabled}
                            onClick={() => {
                              if (editingTropeId === trope.id) {
                                resetTropeEditor();
                                return;
                              }
                              setEditingTropeId(trope.id);
                              setEditingTropeQuery(trope.text);
                            }}
                            type="button"
                          >
                            {editingTropeId === trope.id ? "Cancel edit" : "Edit"}
                          </button>
                          {trope.status !== "validated" ? (
                            <button
                              className="button button-ghost"
                              disabled={interactionDisabled}
                              onClick={() => void handleValidateTrope(trope.id)}
                              type="button"
                            >
                              Validate
                            </button>
                          ) : null}
                          <button
                            className="button button-danger"
                            disabled={interactionDisabled}
                            onClick={() => void handleDeleteTrope(trope.id)}
                            type="button"
                          >
                            Delete
                          </button>
                        </>
                      ) : undefined
                    }
                  >
                    {editingTropeId === trope.id && canEdit ? (
                      <div className="card subdued trope-card-editor">
                        <label className="field">
                          <span>Edit trope</span>
                          <input
                            className="input"
                            disabled={interactionDisabled}
                            onChange={(event) => setEditingTropeQuery(event.target.value)}
                            placeholder="Type a replacement trope or reuse a similar existing one"
                            value={editingTropeQuery}
                          />
                        </label>

                        <div className="button-row wrap-row">
                          <button
                            className="button"
                            disabled={
                              interactionDisabled ||
                              !editingTropeQuery.trim() ||
                              (editingTrope ? normalizeDraftText(editingTropeQuery) === normalizeDraftText(editingTrope.text) : true)
                            }
                            onClick={() => void handleKeepEditedTrope(trope.id)}
                            type="button"
                          >
                            Save typed trope
                          </button>
                          <button className="button button-ghost" disabled={interactionDisabled} onClick={resetTropeEditor} type="button">
                            Cancel
                          </button>
                        </div>

                        <div className="stack">
                          <div className="panel-header">
                            <h3>Similar existing tropes</h3>
                            <span className="pill">
                              {editingTropeSearchStatus === "loading" ? "searching" : `${editingTropeResults.length} results`}
                            </span>
                          </div>
                          {editingTropeQuery.trim() && editingTropeSearchStatus === "loading" ? (
                            <p className="muted">Searching tropes...</p>
                          ) : null}
                          {editingTropeQuery.trim() &&
                          editingTropeSearchStatus === "ready" &&
                          editingTropeResults.length === 0 ? (
                            <p className="muted">No similar tropes were returned for this query.</p>
                          ) : null}
                          {editingTropeResults.map((item) => {
                            const isCurrentTrope = item.id === trope.id;
                            const alreadyAssignedElsewhere = assignedTropeIds.has(item.id) && item.id !== trope.id;
                            return (
                              <TropeCard
                                compact
                                key={`edit-${trope.id}-${item.id}`}
                                trope={item}
                                actions={
                                  <button
                                    className="button button-ghost"
                                    disabled={interactionDisabled || isCurrentTrope || alreadyAssignedElsewhere}
                                    onClick={() => void handleUseExistingEditedTrope(trope.id, item.id)}
                                    type="button"
                                  >
                                    {isCurrentTrope ? "Current trope" : alreadyAssignedElsewhere ? "Already assigned" : "Use existing trope"}
                                  </button>
                                }
                              />
                            );
                          })}
                        </div>
                      </div>
                    ) : null}
                  </TropeCard>
                ))
              ) : (
                <p className="muted">No tropes on this story yet.</p>
              )}
            </div>
          </section>

          {canEdit ? (
            <section className="panel">
              <div className="panel-header">
                <h2>Add trope</h2>
              </div>

              <label className="field">
                <span>Trope query</span>
                <input
                  className="input"
                  onChange={(event) => setTropeQuery(event.target.value)}
                  placeholder="Type a trope to search for similar existing tropes"
                  value={tropeQuery}
                />
              </label>

              <div className="card subdued">
                <div className="card-row">
                  <h3>Keep typed trope</h3>
                  <button
                    className="button"
                    disabled={!detail || interactionDisabled || !tropeQuery.trim()}
                    onClick={() => void handleKeepTypedTrope()}
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
                    {tropeSearchStatus === "loading" ? "searching" : `${tropeResults.length} results`}
                  </span>
                </div>
                {tropeQuery.trim() && tropeSearchStatus === "loading" ? <p className="muted">Searching tropes...</p> : null}
                {tropeQuery.trim() && tropeSearchStatus === "ready" && tropeResults.length === 0 ? (
                  <p className="muted">No similar tropes were returned for this query.</p>
                ) : null}
                {tropeResults.map((item) => {
                  const alreadyAssigned = assignedTropeIds.has(item.id);
                  return (
                    <TropeCard
                      key={item.id}
                      trope={item}
                      actions={
                        <button
                          className="button button-ghost"
                          disabled={!detail || interactionDisabled || alreadyAssigned}
                          onClick={() => void handleUseExistingTrope(item.id)}
                          type="button"
                        >
                          {alreadyAssigned ? "Already assigned" : "Use existing trope"}
                        </button>
                      }
                    />
                  );
                })}
              </div>
            </section>
          ) : null}
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
    </div>
  );
}
