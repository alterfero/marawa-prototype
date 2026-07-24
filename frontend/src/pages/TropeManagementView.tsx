import { type KeyboardEvent, useEffect, useMemo, useRef, useState } from "react";

import {
  ApiError,
  deleteTrope,
  getCanonicalTropes,
  getErrorMessage,
  getStories,
  mergeTropes,
  searchTropes,
  updateCanonicalTrope,
  updateTropeConfirmationStatus,
} from "../api/client";
import { ExplorationFilterSetTropePicker } from "../components/ExplorationFilterSetTropePicker";
import { StorySummaryCard } from "../components/StorySummaryCard";
import { TropeCard } from "../components/TropeCard";
import {
  createEmptyStoryFieldFilter,
  filterStoriesBySelectedTropes,
  normalizeStoryFieldFilters,
  serializeStoryFieldFilters,
  storyMatchesFieldFilters,
  StoryFieldFilterBuilder,
  type StoryFieldFilter,
} from "../components/StoryFieldFilters";
import type {
  CanonicalTropeListItem,
  ExplorationAppliedTropeFilter,
  StorySummary,
  TropeConfirmationStatus,
  TropeSearchItem,
} from "../api/types";
import { normalizeDraftText } from "../constants/csv";
import { routeHref, useHashSearch } from "../router";

interface PageNotice {
  tone: "error" | "success";
  title: string;
  body?: string;
}

function serializeSelectedTropes(tropes: ExplorationAppliedTropeFilter[]): string {
  return JSON.stringify(
    tropes
      .map((trope) => trope.id)
      .sort(),
  );
}

function confirmationStatusLabel(status: TropeConfirmationStatus): string {
  return status === "confirmed" ? "Confirmed" : "Unconfirmed";
}

function isTropeVersionConflict(error: unknown): boolean {
  if (!(error instanceof ApiError) || error.status !== 409) {
    return false;
  }
  const detail = error.detail;
  if (!detail || typeof detail !== "object") {
    return false;
  }
  return (detail as { code?: unknown }).code === "trope_version_conflict";
}

export function TropeManagementView() {
  const hashSearch = useHashSearch();
  const nextFilterIdRef = useRef(1);
  const [stories, setStories] = useState<StorySummary[]>([]);
  const [tropes, setTropes] = useState<CanonicalTropeListItem[]>([]);
  const [selectedTropeId, setSelectedTropeId] = useState<string | null>(null);
  const [editingTropeId, setEditingTropeId] = useState<string | null>(null);
  const [editingTropeQuery, setEditingTropeQuery] = useState("");
  const [editingTropeResults, setEditingTropeResults] = useState<TropeSearchItem[]>([]);
  const [editingTropeSearchStatus, setEditingTropeSearchStatus] = useState<"idle" | "loading" | "ready">("idle");
  const [draftFilters, setDraftFilters] = useState<StoryFieldFilter[]>([]);
  const [appliedFilters, setAppliedFilters] = useState<StoryFieldFilter[]>([]);
  const [tropeQuery, setTropeQuery] = useState("");
  const [draftSelectedTropes, setDraftSelectedTropes] = useState<ExplorationAppliedTropeFilter[]>([]);
  const [appliedSelectedTropes, setAppliedSelectedTropes] = useState<ExplorationAppliedTropeFilter[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<PageNotice | null>(null);
  const selectedTropeParam = new URLSearchParams(hashSearch).get("selected_trope_id");
  const editingTrope = tropes.find((trope) => trope.id === editingTropeId) ?? null;

  function resetTropeEditor() {
    setEditingTropeId(null);
    setEditingTropeQuery("");
    setEditingTropeResults([]);
    setEditingTropeSearchStatus("idle");
  }

  function handleStartEditingTrope(trope: CanonicalTropeListItem) {
    setSelectedTropeId(trope.id);
    setEditingTropeId(trope.id);
    setEditingTropeQuery(trope.text);
    setEditingTropeResults([]);
    setEditingTropeSearchStatus("idle");
  }

  async function refresh(options?: { clearNotice?: boolean }) {
    try {
      setLoading(true);
      if (options?.clearNotice !== false) {
        setNotice(null);
      }
      const [storiesResponse, tropeResponse] = await Promise.all([
        getStories(),
        getCanonicalTropes({ limit: 5000, include_story_ids: true }),
      ]);
      setStories(storiesResponse.items);
      setTropes(tropeResponse);
    } catch (caughtError) {
      setNotice({
        tone: "error",
        title: "Could not load trope management data",
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
        } catch (caughtError) {
          if (cancelled) {
            return;
          }
          setEditingTropeResults([]);
          setEditingTropeSearchStatus("ready");
          setNotice({
            tone: "error",
            title: "Could not search replacement tropes",
            body: getErrorMessage(caughtError),
          });
        }
      })();
    }, 250);

    return () => {
      cancelled = true;
      window.clearTimeout(timeoutId);
    };
  }, [editingTropeId, editingTropeQuery]);

  const storiesMatchingDraftSelectedTropes = useMemo(
    () => filterStoriesBySelectedTropes(stories, draftSelectedTropes),
    [draftSelectedTropes, stories],
  );

  useEffect(() => {
    const normalizedDraftFilters = normalizeStoryFieldFilters(draftFilters, storiesMatchingDraftSelectedTropes);
    if (serializeStoryFieldFilters(normalizedDraftFilters) !== serializeStoryFieldFilters(draftFilters)) {
      setDraftFilters(normalizedDraftFilters);
    }
  }, [draftFilters, storiesMatchingDraftSelectedTropes]);

  const appliedSelectedTropeIds = useMemo(
    () => new Set(appliedSelectedTropes.map((trope) => trope.id)),
    [appliedSelectedTropes],
  );
  const storiesMatchingAppliedSelectedTropes = useMemo(
    () => filterStoriesBySelectedTropes(stories, appliedSelectedTropes),
    [appliedSelectedTropes, stories],
  );
  const hardFilteredStories = useMemo(
    () => storiesMatchingAppliedSelectedTropes.filter((story) => storyMatchesFieldFilters(story, appliedFilters)),
    [appliedFilters, storiesMatchingAppliedSelectedTropes],
  );
  const hardFilteredStoryIds = useMemo(
    () => new Set(hardFilteredStories.map((story) => story.id)),
    [hardFilteredStories],
  );
  const hasAppliedSelectedTropes = appliedSelectedTropes.length > 0;
  const hasAppliedHardFilters = appliedFilters.length > 0;

  const visibleTropes = useMemo(
    () =>
      tropes.filter((trope) => {
        if (hasAppliedSelectedTropes && !appliedSelectedTropeIds.has(trope.id)) {
          return false;
        }
        if (!hasAppliedHardFilters) {
          return true;
        }
        return (trope.story_ids ?? []).some((storyId) => hardFilteredStoryIds.has(storyId));
      }),
    [appliedSelectedTropeIds, hardFilteredStoryIds, hasAppliedHardFilters, hasAppliedSelectedTropes, tropes],
  );

  useEffect(() => {
    if (visibleTropes.length === 0) {
      setSelectedTropeId(null);
      return;
    }

    if (selectedTropeParam && visibleTropes.some((trope) => trope.id === selectedTropeParam)) {
      setSelectedTropeId((current) => (current === selectedTropeParam ? current : selectedTropeParam));
      return;
    }

    if (selectedTropeId && visibleTropes.some((trope) => trope.id === selectedTropeId)) {
      return;
    }
    setSelectedTropeId(visibleTropes[0]?.id || null);
  }, [selectedTropeId, selectedTropeParam, visibleTropes]);

  useEffect(() => {
    if (editingTropeId && !tropes.some((trope) => trope.id === editingTropeId)) {
      resetTropeEditor();
    }
  }, [editingTropeId, tropes]);

  const selectedTrope = visibleTropes.find((trope) => trope.id === selectedTropeId) ?? null;
  const storiesById = useMemo(() => new Map(stories.map((story) => [story.id, story])), [stories]);
  const selectedTropeStories = useMemo(
    () =>
      (selectedTrope?.story_ids ?? [])
        .map((storyId) => storiesById.get(storyId))
        .filter((story): story is StorySummary => Boolean(story)),
    [selectedTrope, storiesById],
  );
  const displayedSelectedTropeStories = useMemo(
    () =>
      hasAppliedHardFilters
        ? selectedTropeStories.filter((story) => hardFilteredStoryIds.has(story.id))
        : selectedTropeStories,
    [hardFilteredStoryIds, hasAppliedHardFilters, selectedTropeStories],
  );

  const hasPendingChanges =
    serializeStoryFieldFilters(draftFilters) !== serializeStoryFieldFilters(appliedFilters) ||
    serializeSelectedTropes(draftSelectedTropes) !== serializeSelectedTropes(appliedSelectedTropes);

  function toggleSelectedTrope(trope: ExplorationAppliedTropeFilter) {
    setDraftSelectedTropes((current) =>
      current.some((item) => item.id === trope.id)
        ? current.filter((item) => item.id !== trope.id)
        : [...current, trope],
    );
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

  function applyFilters() {
    setAppliedFilters(
      draftFilters.map((filter) => ({
        ...filter,
        selectedValues: [...filter.selectedValues],
      })),
    );
    setAppliedSelectedTropes(
      draftSelectedTropes.map((trope) => ({
        ...trope,
      })),
    );
  }

  function clearFilters() {
    setDraftFilters([]);
    setAppliedFilters([]);
    setTropeQuery("");
    setDraftSelectedTropes([]);
    setAppliedSelectedTropes([]);
  }

  function handleTropeRowKeyDown(event: KeyboardEvent<HTMLElement>, tropeId: string) {
    if (loading || busy) {
      return;
    }
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      setSelectedTropeId(tropeId);
    }
  }

  async function handleTropeVersionConflict(title: string) {
    await refresh({ clearNotice: false });
    resetTropeEditor();
    setNotice({
      tone: "error",
      title,
      body: "This trope changed in another browser session. The list has been refreshed with the latest version.",
    });
  }

  async function handleRenameTrope(trope: CanonicalTropeListItem) {
    if (!editingTropeQuery.trim()) {
      return;
    }

    try {
      setBusy(true);
      setNotice(null);
      await updateCanonicalTrope({
        trope_id: trope.id,
        expected_trope_version: trope.version,
        text: editingTropeQuery.trim(),
      });
      setSelectedTropeId(trope.id);
      await refresh({ clearNotice: false });
      resetTropeEditor();
      setNotice({
        tone: "success",
        title: "Trope edited",
        body: "The canonical trope text was updated everywhere it is used.",
      });
    } catch (caughtError) {
      if (isTropeVersionConflict(caughtError)) {
        await handleTropeVersionConflict("Could not edit trope");
        return;
      }
      setNotice({
        tone: "error",
        title: "Could not edit trope",
        body: getErrorMessage(caughtError),
      });
    } finally {
      setBusy(false);
    }
  }

  async function handleMergeEditedTrope(sourceTrope: CanonicalTropeListItem, targetTropeId: string) {
    try {
      setBusy(true);
      setNotice(null);
      setSelectedTropeId(targetTropeId);
      const result = await mergeTropes({
        source_trope_id: sourceTrope.id,
        target_trope_id: targetTropeId,
      });
      await refresh({ clearNotice: false });
      resetTropeEditor();
      setNotice({
        tone: "success",
        title: "Trope edited",
        body: `Merged this trope into an existing canonical trope across ${result.affected_story_count} stor${
          result.affected_story_count === 1 ? "y" : "ies"
        }. Run Rebuild in the menu when you want fresh derived artifacts.`,
      });
    } catch (caughtError) {
      setNotice({
        tone: "error",
        title: "Could not merge trope",
        body: getErrorMessage(caughtError),
      });
    } finally {
      setBusy(false);
    }
  }

  async function handleDeleteTrope(trope: CanonicalTropeListItem) {
    const confirmed = window.confirm(
      trope.story_count > 0
        ? `Delete trope "${trope.text}" from all ${trope.story_count} stor${
            trope.story_count === 1 ? "y" : "ies"
          } and remove the canonical trope?\n\nRebuilds are manual, so use Rebuild in the menu afterward if you want fresh derived artifacts.`
        : `Delete unused trope "${trope.text}"?\n\nRebuilds are manual, so use Rebuild in the menu afterward if you want fresh derived artifacts.`,
    );
    if (!confirmed) {
      return;
    }

    try {
      setBusy(true);
      setNotice(null);
      if (selectedTropeId === trope.id) {
        setSelectedTropeId(null);
      }
      const result = await deleteTrope(trope.id, trope.story_count > 0);
      await refresh({ clearNotice: false });
      resetTropeEditor();
      setNotice({
        tone: "success",
        title: "Trope deleted",
        body:
          result.affected_story_count > 0
            ? `Deleted the canonical trope and removed it from ${result.affected_story_count} stor${
                result.affected_story_count === 1 ? "y" : "ies"
              }. Run Rebuild in the menu when you want fresh derived artifacts.`
            : "Deleted the unused canonical trope.",
      });
    } catch (caughtError) {
      setNotice({
        tone: "error",
        title: "Could not delete trope",
        body: getErrorMessage(caughtError),
      });
    } finally {
      setBusy(false);
    }
  }

  async function handleUpdateConfirmationStatus(nextStatus: TropeConfirmationStatus) {
    if (!selectedTrope) {
      return;
    }

    try {
      setBusy(true);
      const response = await updateTropeConfirmationStatus(selectedTrope.id, {
        expected_trope_version: selectedTrope.version,
        confirmation_status: nextStatus,
      });
      setTropes((current) =>
        current.map((trope) =>
          trope.id === selectedTrope.id
            ? {
                ...trope,
                ...response.trope,
                story_ids: trope.story_ids,
              }
            : trope,
        ),
      );
      setNotice({
        tone: "success",
        title: "Trope updated",
        body: `Confirmation status set to ${confirmationStatusLabel(nextStatus).toLowerCase()}.`,
      });
    } catch (caughtError) {
      if (isTropeVersionConflict(caughtError)) {
        await handleTropeVersionConflict("Could not update trope confirmation");
      } else {
        setNotice({
          tone: "error",
          title: "Could not update trope confirmation",
          body: getErrorMessage(caughtError),
        });
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="page-stack">
      <section className="panel">
        <div className="panel-header">
          <div>
            <h1>Trope management</h1>
            <p className="muted">
              Browse canonical tropes, filter them with semantic and hard story filters, and confirm them independently from contributor review.
            </p>
          </div>
        </div>
      </section>

      {notice ? (
        <section className={`notice ${notice.tone === "error" ? "notice-error" : "notice-success"}`}>
          <strong className="notice-title">{notice.title}</strong>
          {notice.body ? <p>{notice.body}</p> : null}
        </section>
      ) : null}

      <section className="two-column-layout">
        <aside className="panel story-browser-panel trope-management-browser">
          <div className="panel-header">
            <h2>Tropes</h2>
            <div className="button-row">
              <span className="pill">
                {visibleTropes.length}/{tropes.length}
              </span>
              <button className="button button-ghost" disabled={loading || busy} onClick={() => void refresh()} type="button">
                Refresh
              </button>
            </div>
          </div>

          <StoryFieldFilterBuilder
            activeCount={appliedFilters.length + appliedSelectedTropes.length}
            appliedFilters={appliedFilters}
            clearDisabled={
              draftFilters.length === 0 &&
              appliedFilters.length === 0 &&
              draftSelectedTropes.length === 0 &&
              appliedSelectedTropes.length === 0 &&
              !tropeQuery.trim()
            }
            draftFilters={draftFilters}
            hasPendingChanges={hasPendingChanges}
            loading={loading || busy}
            onAddFilter={addDraftFilter}
            onApplyFilters={applyFilters}
            onClearFilters={clearFilters}
            onRemoveFilter={removeDraftFilter}
            onUpdateFilterField={updateDraftFilterField}
            onUpdateFilterValues={updateDraftFilterValues}
            stories={storiesMatchingDraftSelectedTropes}
          >
            <div className="stack">
              <ExplorationFilterSetTropePicker
                loading={loading || busy}
                onQueryChange={setTropeQuery}
                onToggleTrope={toggleSelectedTrope}
                query={tropeQuery}
                selectedTropes={draftSelectedTropes}
              />
              {draftSelectedTropes.length > 0 && storiesMatchingDraftSelectedTropes.length === 0 ? (
                <p className="muted">
                  No stories match the selected tropes yet, so no hard filters are available for this view.
                </p>
              ) : null}
            </div>
          </StoryFieldFilterBuilder>

          <div className="list story-browser-list">
            {loading ? <p className="muted">Loading tropes...</p> : null}
            {!loading && visibleTropes.length === 0 ? <p className="muted">No tropes match the current filters.</p> : null}
            {visibleTropes.map((trope) => {
              const isEditing = editingTropeId === trope.id;
              return (
                <article
                  aria-pressed={trope.id === selectedTropeId}
                  className={`list-row trope-management-row ${trope.id === selectedTropeId ? "list-row-active" : ""}`.trim()}
                  key={trope.id}
                  onClick={() => {
                    if (!loading && !busy) {
                      setSelectedTropeId(trope.id);
                    }
                  }}
                  onKeyDown={(event) => handleTropeRowKeyDown(event, trope.id)}
                  role="button"
                  tabIndex={loading || busy ? -1 : 0}
                >
                  <div className="card-row trope-management-row-top">
                    <div className="trope-management-row-title">
                      <strong>{trope.text}</strong>
                      <span className="muted">
                        {trope.story_count} stor{trope.story_count === 1 ? "y" : "ies"}
                      </span>
                    </div>
                    <div
                      className="trope-management-row-actions"
                      onClick={(event) => event.stopPropagation()}
                      onKeyDown={(event) => event.stopPropagation()}
                    >
                      <span
                        className={`story-completeness-badge trope-confirmation-badge trope-confirmation-${trope.confirmation_status}`}
                      >
                        {confirmationStatusLabel(trope.confirmation_status)}
                      </span>
                      <div className="button-row">
                        <button
                          className="button button-ghost"
                          disabled={loading || busy}
                          onClick={() => {
                            if (isEditing) {
                              resetTropeEditor();
                              return;
                            }
                            handleStartEditingTrope(trope);
                          }}
                          type="button"
                        >
                          {isEditing ? "Cancel edit" : "Edit"}
                        </button>
                        <button
                          className="button button-danger"
                          disabled={loading || busy}
                          onClick={() => void handleDeleteTrope(trope)}
                          type="button"
                        >
                          Delete
                        </button>
                      </div>
                    </div>
                  </div>

                  {isEditing ? (
                    <div
                      className="card subdued trope-card-editor"
                      onClick={(event) => event.stopPropagation()}
                      onKeyDown={(event) => event.stopPropagation()}
                    >
                      <label className="field">
                        <span>Edit trope</span>
                        <input
                          className="input"
                          disabled={loading || busy}
                          onChange={(event) => setEditingTropeQuery(event.target.value)}
                          placeholder="Type a replacement trope or reuse a similar existing one"
                          value={editingTropeQuery}
                        />
                      </label>

                      <div className="button-row wrap-row">
                        <button
                          className="button"
                          disabled={
                            loading ||
                            busy ||
                            !editingTropeQuery.trim() ||
                            (editingTrope ? editingTropeQuery.trim() === editingTrope.text : true)
                          }
                          onClick={() => void handleRenameTrope(trope)}
                          type="button"
                        >
                          Save typed trope
                        </button>
                        <button className="button button-ghost" disabled={loading || busy} onClick={resetTropeEditor} type="button">
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
                          const sameNormalizedText = normalizeDraftText(item.text) === normalizeDraftText(trope.text);
                          return (
                            <TropeCard
                              compact
                              key={`edit-${trope.id}-${item.id}`}
                              onOpen={() => setSelectedTropeId(item.id)}
                              trope={item}
                              actions={
                                <button
                                  className="button button-ghost"
                                  disabled={loading || busy || isCurrentTrope}
                                  onClick={() => void handleMergeEditedTrope(trope, item.id)}
                                  type="button"
                                >
                                  {isCurrentTrope ? "Current trope" : sameNormalizedText ? "Merge duplicate" : "Use existing trope"}
                                </button>
                              }
                            />
                          );
                        })}
                      </div>
                    </div>
                  ) : null}
                </article>
              );
            })}
          </div>
        </aside>

        <div className="page-stack review-detail-column">
          {!selectedTrope ? (
            <section className="panel">
              <p className="muted">Choose a trope to inspect it.</p>
            </section>
          ) : null}

          {selectedTrope ? (
            <section className="panel">
              <div className="panel-header">
                <div>
                  <h2>{selectedTrope.text}</h2>
                  <p className="muted">
                    {selectedTrope.story_count} stor{selectedTrope.story_count === 1 ? "y" : "ies"} total
                    {hasAppliedHardFilters ? ` · ${displayedSelectedTropeStories.length} shown with current hard filters` : ""}
                  </p>
                </div>
                <span className={`story-completeness-badge trope-confirmation-badge trope-confirmation-${selectedTrope.confirmation_status}`}>
                  {confirmationStatusLabel(selectedTrope.confirmation_status)}
                </span>
              </div>

              <article className="card subdued trope-management-confirmation-card">
                <div className="trope-management-confirmation-controls">
                  <button
                    aria-pressed={selectedTrope.confirmation_status === "unconfirmed"}
                    className={`button ${
                      selectedTrope.confirmation_status === "unconfirmed"
                        ? "trope-confirmation-toggle-active trope-confirmation-toggle-unconfirmed"
                        : "button-ghost"
                    }`}
                    disabled={busy || selectedTrope.confirmation_status === "unconfirmed"}
                    onClick={() => void handleUpdateConfirmationStatus("unconfirmed")}
                    type="button"
                  >
                    Unconfirmed
                  </button>
                  <button
                    aria-pressed={selectedTrope.confirmation_status === "confirmed"}
                    className={`button ${
                      selectedTrope.confirmation_status === "confirmed"
                        ? "trope-confirmation-toggle-active trope-confirmation-toggle-confirmed"
                        : "button-ghost"
                    }`}
                    disabled={busy || selectedTrope.confirmation_status === "confirmed"}
                    onClick={() => void handleUpdateConfirmationStatus("confirmed")}
                    type="button"
                  >
                    Confirmed
                  </button>
                </div>
              </article>

              <div className="panel-header">
                <h3>Stories</h3>
                <span className="pill">{displayedSelectedTropeStories.length}</span>
              </div>

              <div className="list story-browser-list trope-management-story-list">
                {displayedSelectedTropeStories.length === 0 ? (
                  <p className="muted">
                    {selectedTrope.story_count === 0
                      ? "No stories currently use this trope in the active dataset."
                      : "No stories for this trope match the current hard filters."}
                  </p>
                ) : null}
                {displayedSelectedTropeStories.map((story) => (
                  <StorySummaryCard
                    key={story.id}
                    onClick={() => {
                      window.location.hash = routeHref("/stories", { selected_story_id: story.id });
                    }}
                    story={story}
                  />
                ))}
              </div>
            </section>
          ) : null}
        </div>
      </section>
    </div>
  );
}
