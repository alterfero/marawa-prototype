import { type KeyboardEvent, useEffect, useMemo, useState } from "react";

import {
  ApiError,
  deleteTheme,
  getCanonicalThemes,
  getErrorMessage,
  getSimilarUnconfirmedThemes,
  getStories,
  getThemeDetail,
  mergeThemes,
  mergeUnconfirmedTheme,
  searchThemes,
  updateCanonicalTheme,
  updateThemeConfirmationStatus,
} from "../api/client";
import { ConfirmationStatusSwitch } from "../components/ConfirmationStatusSwitch";
import { StorySummaryCard } from "../components/StorySummaryCard";
import { TermCard } from "../components/TermCard";
import type {
  CanonicalThemeListItem,
  SimilarUnconfirmedThemeListResponse,
  StorySummary,
  ThemeDetail,
  ThemeConfirmationStatus,
  ThemeSearchItem,
} from "../api/types";
import { routeHref, useHashSearch } from "../router";
import { useDatasetMaintenance } from "../maintenance";


interface PageNotice {
  tone: "error" | "success";
  title: string;
  body?: string;
}

type ConfirmationStatusTheme = Pick<
  CanonicalThemeListItem,
  "id" | "version" | "text" | "confirmation_status" | "story_count"
>;


function confirmationStatusLabel(status: ThemeConfirmationStatus): string {
  return status === "canonical" ? "Canonical" : "Unconfirmed";
}


function isThemeVersionConflict(error: unknown): boolean {
  if (!(error instanceof ApiError) || error.status !== 409 || !error.detail || typeof error.detail !== "object") {
    return false;
  }
  return (error.detail as { code?: unknown }).code === "theme_version_conflict";
}


export function ThemeManagementView() {
  const maintenance = useDatasetMaintenance();
  const hashSearch = useHashSearch();
  const [themes, setThemes] = useState<CanonicalThemeListItem[]>([]);
  const [themeQuery, setThemeQuery] = useState("");
  const [stories, setStories] = useState<StorySummary[]>([]);
  const [selectedThemeId, setSelectedThemeId] = useState<string | null>(null);
  const [selectedThemeDetail, setSelectedThemeDetail] = useState<ThemeDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [similarThemeFilter, setSimilarThemeFilter] = useState<"unconfirmed" | "all">("unconfirmed");
  const [similarityThreshold, setSimilarityThreshold] = useState(0.6);
  const [similarThemes, setSimilarThemes] = useState<SimilarUnconfirmedThemeListResponse | null>(null);
  const [similarThemesLoading, setSimilarThemesLoading] = useState(false);
  const [editingThemeId, setEditingThemeId] = useState<string | null>(null);
  const [editingThemeText, setEditingThemeText] = useState("");
  const [editingThemeResults, setEditingThemeResults] = useState<ThemeSearchItem[]>([]);
  const [editingThemeSearchStatus, setEditingThemeSearchStatus] = useState<"idle" | "loading" | "ready">("idle");
  const [mergingThemeId, setMergingThemeId] = useState<string | null>(null);
  const [mergeTargetThemeId, setMergeTargetThemeId] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<PageNotice | null>(null);
  const mutationDisabled = busy || maintenance.active;
  const selectedThemeParam = new URLSearchParams(hashSearch).get("selected_theme_id");

  const selectedTheme = themes.find((theme) => theme.id === selectedThemeId) ?? null;
  const canonicalThemes = useMemo(
    () => themes.filter((theme) => theme.confirmation_status === "canonical"),
    [themes],
  );
  const filteredThemes = useMemo(() => {
    const query = themeQuery.trim().toLocaleLowerCase();
    return query ? themes.filter((theme) => theme.text.toLocaleLowerCase().includes(query)) : themes;
  }, [themeQuery, themes]);
  const storiesById = useMemo(() => new Map(stories.map((story) => [story.id, story])), [stories]);
  const selectedThemeStories = useMemo(
    () =>
      (selectedThemeDetail?.stories ?? [])
        .map((story) => storiesById.get(story.id))
        .filter((story): story is StorySummary => Boolean(story)),
    [selectedThemeDetail, storiesById],
  );

  function resetThemeEditor() {
    setEditingThemeId(null);
    setEditingThemeText("");
    setEditingThemeResults([]);
    setEditingThemeSearchStatus("idle");
  }

  function resetThemeMerge() {
    setMergingThemeId(null);
    setMergeTargetThemeId("");
  }

  async function refresh(options?: { clearNotice?: boolean }) {
    try {
      setLoading(true);
      if (options?.clearNotice !== false) {
        setNotice(null);
      }
      const [themeResponse, storiesResponse] = await Promise.all([
        getCanonicalThemes({ limit: 5000 }),
        getStories(),
      ]);
      setThemes(themeResponse);
      setStories(storiesResponse.items);
    } catch (caughtError) {
      setNotice({
        tone: "error",
        title: "Could not load theme management data",
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
    if (themes.length === 0) {
      setSelectedThemeId(null);
      return;
    }
    if (selectedThemeParam && themes.some((theme) => theme.id === selectedThemeParam)) {
      setSelectedThemeId((current) => (current === selectedThemeParam ? current : selectedThemeParam));
      return;
    }
    if (selectedThemeId && themes.some((theme) => theme.id === selectedThemeId)) {
      return;
    }
    setSelectedThemeId(themes[0].id);
  }, [selectedThemeId, selectedThemeParam, themes]);

  useEffect(() => {
    if (editingThemeId && !themes.some((theme) => theme.id === editingThemeId)) {
      resetThemeEditor();
    }
  }, [editingThemeId, themes]);

  useEffect(() => {
    const query = editingThemeText.trim();
    if (!editingThemeId || !query) {
      setEditingThemeResults([]);
      setEditingThemeSearchStatus("idle");
      return;
    }
    let cancelled = false;
    const timeoutId = window.setTimeout(() => {
      void (async () => {
        try {
          setEditingThemeSearchStatus("loading");
          const response = await searchThemes({ query, limit: 8 });
          if (!cancelled) {
            setEditingThemeResults(response.items);
            setEditingThemeSearchStatus("ready");
          }
        } catch (caughtError) {
          if (!cancelled) {
            setEditingThemeResults([]);
            setEditingThemeSearchStatus("ready");
            setNotice({
              tone: "error",
              title: "Could not search replacement themes",
              body: getErrorMessage(caughtError),
            });
          }
        }
      })();
    }, 250);
    return () => {
      cancelled = true;
      window.clearTimeout(timeoutId);
    };
  }, [editingThemeId, editingThemeText]);

  useEffect(() => {
    if (mergingThemeId && !themes.some((theme) => theme.id === mergingThemeId)) {
      resetThemeMerge();
    }
  }, [mergingThemeId, themes]);

  useEffect(() => {
    if (!selectedThemeId) {
      setSelectedThemeDetail(null);
      setDetailLoading(false);
      return;
    }

    let cancelled = false;
    setDetailLoading(true);
    setSelectedThemeDetail(null);
    void (async () => {
      try {
        const detail = await getThemeDetail(selectedThemeId);
        if (!cancelled) {
          setSelectedThemeDetail(detail);
        }
      } catch (caughtError) {
        if (!cancelled) {
          setNotice({
            tone: "error",
            title: "Could not load theme details",
            body: getErrorMessage(caughtError),
          });
        }
      } finally {
        if (!cancelled) {
          setDetailLoading(false);
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [selectedThemeId]);

  useEffect(() => {
    if (!selectedTheme) {
      setSimilarThemes(null);
      setSimilarThemesLoading(false);
      return;
    }

    let cancelled = false;
    void (async () => {
      try {
        setSimilarThemesLoading(true);
        const response = await getSimilarUnconfirmedThemes(selectedTheme.id, {
          minimum_similarity: similarityThreshold,
          include_canonical: similarThemeFilter === "all",
        });
        if (!cancelled) {
          setSimilarThemes(response);
        }
      } catch (caughtError) {
        if (!cancelled) {
          setSimilarThemes(null);
          setNotice({
            tone: "error",
            title: "Could not load similar themes",
            body: getErrorMessage(caughtError),
          });
        }
      } finally {
        if (!cancelled) {
          setSimilarThemesLoading(false);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [selectedTheme, similarityThreshold, similarThemeFilter]);

  function handleThemeRowKeyDown(event: KeyboardEvent<HTMLElement>, themeId: string) {
    if (loading || busy) {
      return;
    }
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      setSelectedThemeId(themeId);
    }
  }

  async function handleThemeVersionConflict(title: string) {
    await refresh({ clearNotice: false });
    resetThemeEditor();
    setNotice({
      tone: "error",
      title,
      body: "This theme changed in another browser session. The list has been refreshed with the latest version.",
    });
  }

  async function handleRenameTheme(theme: CanonicalThemeListItem) {
    const text = editingThemeText.trim();
    if (!text) {
      return;
    }
    try {
      setBusy(true);
      setNotice(null);
      await updateCanonicalTheme({
        theme_id: theme.id,
        expected_theme_version: theme.version,
        text,
      });
      await refresh({ clearNotice: false });
      resetThemeEditor();
      setNotice({
        tone: "success",
        title: "Theme edited",
        body: "The canonical theme text was updated everywhere it is used.",
      });
    } catch (caughtError) {
      if (isThemeVersionConflict(caughtError)) {
        await handleThemeVersionConflict("Could not edit theme");
      } else {
        setNotice({
          tone: "error",
          title: "Could not edit theme",
          body: getErrorMessage(caughtError),
        });
      }
    } finally {
      setBusy(false);
    }
  }

  async function handleDeleteTheme(theme: CanonicalThemeListItem) {
    const shouldDelete = window.confirm(
      theme.story_count > 0
        ? `Delete theme "${theme.text}" from all ${theme.story_count} stor${theme.story_count === 1 ? "y" : "ies"} and remove the canonical theme?`
        : `Delete unused theme "${theme.text}"?`,
    );
    if (!shouldDelete) {
      return;
    }

    try {
      setBusy(true);
      setNotice(null);
      if (selectedThemeId === theme.id) {
        setSelectedThemeId(null);
      }
      const result = await deleteTheme({
        theme_id: theme.id,
        expected_theme_version: theme.version,
        remove_from_all_stories: theme.story_count > 0,
      });
      await refresh({ clearNotice: false });
      resetThemeEditor();
      resetThemeMerge();
      setNotice({
        tone: "success",
        title: "Theme deleted",
        body:
          result.affected_story_count > 0
            ? `Deleted the canonical theme and removed it from ${result.affected_story_count} stor${
                result.affected_story_count === 1 ? "y" : "ies"
              }.`
            : "Deleted the unused canonical theme.",
      });
    } catch (caughtError) {
      if (isThemeVersionConflict(caughtError)) {
        await handleThemeVersionConflict("Could not delete theme");
      } else {
        setNotice({
          tone: "error",
          title: "Could not delete theme",
          body: getErrorMessage(caughtError),
        });
      }
    } finally {
      setBusy(false);
    }
  }

  async function handleMergeTheme(sourceTheme: CanonicalThemeListItem) {
    if (!mergeTargetThemeId) {
      return;
    }
    const targetTheme = canonicalThemes.find((theme) => theme.id === mergeTargetThemeId);
    if (!targetTheme) {
      return;
    }

    try {
      setBusy(true);
      setNotice(null);
      if (selectedThemeId === sourceTheme.id) {
        setSelectedThemeId(null);
      }
      const result = await mergeUnconfirmedTheme({
        source_theme_id: sourceTheme.id,
        expected_source_theme_version: sourceTheme.version,
        target_theme_id: targetTheme.id,
      });
      await refresh({ clearNotice: false });
      resetThemeEditor();
      resetThemeMerge();
      setNotice({
        tone: "success",
        title: "Theme merged",
        body: `Merged “${sourceTheme.text}” into canonical theme “${result.target_theme.text}” across ${result.affected_story_count} stor${
          result.affected_story_count === 1 ? "y" : "ies"
        }.`,
      });
    } catch (caughtError) {
      if (isThemeVersionConflict(caughtError)) {
        await handleThemeVersionConflict("Could not merge theme");
      } else {
        setNotice({
          tone: "error",
          title: "Could not merge theme",
          body: getErrorMessage(caughtError),
        });
      }
    } finally {
      setBusy(false);
    }
  }

  async function handleMergeSimilarTheme(sourceTheme: ConfirmationStatusTheme) {
    if (!selectedTheme || selectedTheme.confirmation_status !== "canonical") {
      return;
    }
    if (!window.confirm(`Replace "${sourceTheme.text}" with "${selectedTheme.text}" in every story that uses it?`)) {
      return;
    }
    try {
      setBusy(true);
      setNotice(null);
      await mergeUnconfirmedTheme({
        source_theme_id: sourceTheme.id,
        expected_source_theme_version: sourceTheme.version,
        target_theme_id: selectedTheme.id,
      });
      await refresh({ clearNotice: false });
      setNotice({
        tone: "success",
        title: "Themes merged",
        body: `Replaced ${sourceTheme.text} with ${selectedTheme.text} across its assigned stories.`,
      });
    } catch (caughtError) {
      if (isThemeVersionConflict(caughtError)) {
        await handleThemeVersionConflict("Could not merge theme");
      } else {
        setNotice({ tone: "error", title: "Could not merge theme", body: getErrorMessage(caughtError) });
      }
    } finally {
      setBusy(false);
    }
  }

  async function handleMergeThemeInto(
    sourceTheme: ConfirmationStatusTheme,
    targetTheme: Pick<ConfirmationStatusTheme, "id" | "text" | "story_count">,
  ) {
    if (!window.confirm(`Replace "${sourceTheme.text}" with "${targetTheme.text}" in every story that uses it?`)) {
      return;
    }
    try {
      setBusy(true);
      setNotice(null);
      setSelectedThemeId(targetTheme.id);
      const result = await mergeThemes({ source_theme_id: sourceTheme.id, target_theme_id: targetTheme.id });
      await refresh({ clearNotice: false });
      resetThemeEditor();
      setNotice({
        tone: "success",
        title: "Themes merged",
        body: `Replaced ${sourceTheme.text} with ${targetTheme.text} across ${result.affected_story_count} stor${result.affected_story_count === 1 ? "y" : "ies"}.`,
      });
    } catch (caughtError) {
      setNotice({ tone: "error", title: "Could not merge themes", body: getErrorMessage(caughtError) });
    } finally {
      setBusy(false);
    }
  }

  async function handleUpdateConfirmationStatus(theme: ConfirmationStatusTheme, nextStatus: ThemeConfirmationStatus) {
    try {
      setBusy(true);
      const response = await updateThemeConfirmationStatus(theme.id, {
        expected_theme_version: theme.version,
        confirmation_status: nextStatus,
      });
      setThemes((current) => current.map((item) => (item.id === response.theme.id ? response.theme : item)));
      setNotice({
        tone: "success",
        title: "Theme updated",
        body: `Confirmation status set to ${confirmationStatusLabel(nextStatus).toLowerCase()}.`,
      });
    } catch (caughtError) {
      if (isThemeVersionConflict(caughtError)) {
        await handleThemeVersionConflict("Could not update theme confirmation");
      } else {
        setNotice({
          tone: "error",
          title: "Could not update theme confirmation",
          body: getErrorMessage(caughtError),
        });
      }
    } finally {
      setBusy(false);
    }
  }

  function renderConfirmationActions(theme: ConfirmationStatusTheme) {
    return (
      <ConfirmationStatusSwitch
        ariaLabel="Theme confirmation status"
        disabled={mutationDisabled}
        onChange={(nextStatus) => void handleUpdateConfirmationStatus(theme, nextStatus)}
        value={theme.confirmation_status}
      />
    );
  }

  return (
    <div className="page-stack">
      <section className="panel">
        <div className="panel-header">
          <div>
            <h1>Theme management</h1>
            <p className="muted">Browse canonical themes, inspect their stories, and compare them using embedding similarity.</p>
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
            <h2>Themes</h2>
            <div className="button-row">
              <span className="pill">{filteredThemes.length}</span>
              <button className="button button-ghost" disabled={loading || busy} onClick={() => void refresh()} type="button">
                Refresh
              </button>
            </div>
          </div>

          <label className="field">
            <span>Filter themes</span>
            <input
              className="input"
              onChange={(event) => setThemeQuery(event.target.value)}
              placeholder="Enter text to filter themes"
              value={themeQuery}
            />
          </label>

          <div className="list story-browser-list">
            {loading ? <p className="muted">Loading themes...</p> : null}
            {!loading && themes.length === 0 ? <p className="muted">No themes are available in the active dataset.</p> : null}
            {!loading && themes.length > 0 && filteredThemes.length === 0 ? <p className="muted">No themes match the current filter.</p> : null}
            {filteredThemes.map((theme) => {
              const isEditing = editingThemeId === theme.id;
              const isMerging = mergingThemeId === theme.id;
              const mergeTargets = canonicalThemes.filter((candidate) => candidate.id !== theme.id);
              return (
                <article
                  aria-pressed={theme.id === selectedThemeId}
                  className={`list-row trope-management-row ${theme.id === selectedThemeId ? "list-row-active" : ""}`.trim()}
                  key={theme.id}
                  onClick={() => {
                    if (!loading && !busy) {
                      setSelectedThemeId(theme.id);
                    }
                  }}
                  onKeyDown={(event) => handleThemeRowKeyDown(event, theme.id)}
                  role="button"
                  tabIndex={loading || busy ? -1 : 0}
                >
                  <div className="card-row trope-management-row-top">
                    <div className="trope-management-row-title">
                      <strong>{theme.text}</strong>
                      <span className="muted">
                        {theme.story_count} stor{theme.story_count === 1 ? "y" : "ies"}
                      </span>
                    </div>
                    <div
                      className="trope-management-row-actions"
                      onClick={(event) => event.stopPropagation()}
                      onKeyDown={(event) => event.stopPropagation()}
                    >
                      <span className={`story-completeness-badge trope-confirmation-badge trope-confirmation-${theme.confirmation_status}`}>
                        {confirmationStatusLabel(theme.confirmation_status)}
                      </span>
                      <div className="button-row">
                        <button
                          className="button button-ghost"
                          disabled={loading || mutationDisabled}
                          onClick={() => {
                            resetThemeMerge();
                            if (isEditing) {
                              resetThemeEditor();
                              return;
                            }
                            setSelectedThemeId(theme.id);
                            setEditingThemeId(theme.id);
                            setEditingThemeText(theme.text);
                          }}
                          type="button"
                        >
                          {isEditing ? "Cancel edit" : "Edit"}
                        </button>
                        {theme.confirmation_status === "unconfirmed" ? (
                          <button
                            className="button button-ghost"
                            disabled={loading || mutationDisabled || mergeTargets.length === 0}
                            onClick={() => {
                              if (isMerging) {
                                resetThemeMerge();
                                return;
                              }
                              resetThemeEditor();
                              setSelectedThemeId(theme.id);
                              setMergingThemeId(theme.id);
                              setMergeTargetThemeId(mergeTargets[0]?.id ?? "");
                            }}
                            title={mergeTargets.length === 0 ? "Mark another theme canonical before merging." : undefined}
                            type="button"
                          >
                            {isMerging ? "Cancel merge" : "Merge with..."}
                          </button>
                        ) : null}
                        <button
                          className="button button-danger"
                          disabled={loading || mutationDisabled}
                          onClick={() => void handleDeleteTheme(theme)}
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
                        <span>Edit theme</span>
                        <input
                          className="input"
                          disabled={loading || mutationDisabled}
                          onChange={(event) => setEditingThemeText(event.target.value)}
                          placeholder="Type a replacement theme or reuse a similar existing one"
                          value={editingThemeText}
                        />
                      </label>
                      <div className="button-row wrap-row">
                        <button
                          className="button"
                          disabled={loading || mutationDisabled || !editingThemeText.trim() || editingThemeText.trim() === theme.text}
                          onClick={() => void handleRenameTheme(theme)}
                          type="button"
                        >
                          Save theme
                        </button>
                        <button className="button button-ghost" disabled={loading || busy} onClick={resetThemeEditor} type="button">
                          Cancel
                        </button>
                      </div>
                      <div className="stack">
                        <div className="panel-header">
                          <h3>Similar existing themes</h3>
                          <span className="pill">
                            {editingThemeSearchStatus === "loading" ? "searching" : `${editingThemeResults.length} results`}
                          </span>
                        </div>
                        {editingThemeText.trim() && editingThemeSearchStatus === "loading" ? <p className="muted">Searching themes...</p> : null}
                        {editingThemeText.trim() && editingThemeSearchStatus === "ready" && editingThemeResults.length === 0 ? (
                          <p className="muted">No similar themes were returned for this query.</p>
                        ) : null}
                        {editingThemeResults.map((candidate) => {
                          const isCurrentTheme = candidate.id === theme.id;
                          return (
                            <TermCard
                              key={`edit-${theme.id}-${candidate.id}`}
                              meta={`Similarity ${candidate.score.toFixed(2)}`}
                              term={candidate}
                              actions={
                                <button
                                  className="button button-ghost"
                                  disabled={loading || mutationDisabled || isCurrentTheme}
                                  onClick={() => void handleMergeThemeInto(theme, candidate)}
                                  type="button"
                                >
                                  {isCurrentTheme ? "Current theme" : "Use existing theme"}
                                </button>
                              }
                            />
                          );
                        })}
                      </div>
                    </div>
                  ) : null}

                  {isMerging ? (
                    <div
                      className="card subdued trope-card-editor"
                      onClick={(event) => event.stopPropagation()}
                      onKeyDown={(event) => event.stopPropagation()}
                    >
                      <label className="field">
                        <span>Merge with canonical theme</span>
                        <select
                          className="input"
                          disabled={loading || mutationDisabled}
                          onChange={(event) => setMergeTargetThemeId(event.target.value)}
                          value={mergeTargetThemeId}
                        >
                          {mergeTargets.map((candidate) => (
                            <option key={candidate.id} value={candidate.id}>
                              {candidate.text} ({candidate.story_count} {candidate.story_count === 1 ? "story" : "stories"})
                            </option>
                          ))}
                        </select>
                      </label>
                      <div className="button-row wrap-row">
                        <button
                          className="button"
                          disabled={loading || mutationDisabled || !mergeTargetThemeId}
                          onClick={() => void handleMergeTheme(theme)}
                          type="button"
                        >
                          Merge themes
                        </button>
                        <button className="button button-ghost" disabled={loading || busy} onClick={resetThemeMerge} type="button">
                          Cancel
                        </button>
                      </div>
                    </div>
                  ) : null}
                </article>
              );
            })}
          </div>
        </aside>

        <div className="page-stack review-detail-column">
          {!selectedTheme ? (
            <section className="panel">
              <p className="muted">Choose a theme to inspect it.</p>
            </section>
          ) : null}

          {selectedTheme ? (
            <section className="panel">
              <TermCard
                actions={
                  <span className={`story-completeness-badge trope-confirmation-badge trope-confirmation-${selectedTheme.confirmation_status}`}>
                    {confirmationStatusLabel(selectedTheme.confirmation_status)}
                  </span>
                }
                className="subdued trope-management-selected-card"
                meta={`${selectedTheme.story_count} stor${selectedTheme.story_count === 1 ? "y" : "ies"} total`}
                term={selectedTheme}
              >
                <div className="theme-management-confirmation-control">
                  {renderConfirmationActions(selectedTheme)}
                </div>
              </TermCard>

              <div className="panel-header">
                <h3>Stories</h3>
                <span className="pill">{detailLoading ? "loading" : selectedThemeStories.length}</span>
              </div>
              <div className="list story-browser-list trope-management-story-list">
                {detailLoading ? <p className="muted">Loading stories...</p> : null}
                {!detailLoading && selectedThemeStories.length === 0 ? (
                  <p className="muted">No stories currently use this theme in the active dataset.</p>
                ) : null}
                {selectedThemeStories.map((story) => (
                  <StorySummaryCard
                    key={story.id}
                    onClick={() => {
                      window.location.hash = routeHref("/stories", { selected_story_id: story.id });
                    }}
                    story={story}
                  />
                ))}
              </div>

              <div className="trope-management-similar-section">
                <div className="panel-header">
                  <div>
                    <h3>Similar themes</h3>
                    <p className="muted">Candidates are ordered by embedding similarity to the selected theme.</p>
                  </div>
                  <span className="pill">{similarThemesLoading ? "loading" : `${similarThemes?.total ?? 0} results`}</span>
                </div>
                <div aria-label="Similar theme results" className="similarity-scope-switch" role="group">
                  <button
                    aria-pressed={similarThemeFilter === "unconfirmed"}
                    className={similarThemeFilter === "unconfirmed" ? "similarity-scope-switch-option-active" : undefined}
                    disabled={loading || busy}
                    onClick={() => setSimilarThemeFilter("unconfirmed")}
                    type="button"
                  >
                    unconfirmed
                  </button>
                  <button
                    aria-pressed={similarThemeFilter === "all"}
                    className={similarThemeFilter === "all" ? "similarity-scope-switch-option-active" : undefined}
                    disabled={loading || busy}
                    onClick={() => setSimilarThemeFilter("all")}
                    type="button"
                  >
                    all
                  </button>
                </div>
                <label className="field trope-management-similarity-threshold" htmlFor="theme-management-similarity-threshold">
                  <div className="card-row"><strong>Similarity threshold</strong><span className="pill">{similarityThreshold.toFixed(2)}</span></div>
                  <input
                    className="range-input"
                    disabled={loading || busy}
                    id="theme-management-similarity-threshold"
                    max="1"
                    min="0"
                    onChange={(event) => setSimilarityThreshold(Number(event.target.value))}
                    step="0.01"
                    type="range"
                    value={similarityThreshold}
                  />
                </label>
                {similarThemesLoading ? <p className="muted">Loading similar themes...</p> : null}
                {!similarThemesLoading && similarThemes?.artifact_version === null ? (
                  <p className="muted">No current theme embeddings are available. Run Rebuild, then refresh this view.</p>
                ) : null}
                {!similarThemesLoading && similarThemes?.artifact_version !== null && similarThemes?.items.length === 0 ? (
                  <p className="muted">
                    {similarThemeFilter === "unconfirmed" ? "No unconfirmed themes meet the current threshold." : "No similar themes meet the current threshold."}
                  </p>
                ) : null}
                <div className="list trope-management-similar-list">
                  {similarThemes?.items.map((theme) => (
                    <TermCard
                      className="trope-management-similar-trope-card"
                      key={theme.id}
                      meta={`Similarity ${theme.similarity_score.toFixed(2)}`}
                      term={theme}
                    >
                      <div className="theme-management-similar-actions">
                          {renderConfirmationActions(theme)}
                          {selectedTheme.confirmation_status === "canonical" && theme.confirmation_status === "unconfirmed" ? (
                            <button className="button" disabled={mutationDisabled} onClick={() => void handleMergeSimilarTheme(theme)} type="button">
                              Replace with selected theme
                            </button>
                          ) : null}
                      </div>
                    </TermCard>
                  ))}
                </div>
              </div>
            </section>
          ) : null}
        </div>
      </section>
    </div>
  );
}
