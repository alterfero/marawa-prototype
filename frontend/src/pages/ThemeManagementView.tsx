import { type KeyboardEvent, useEffect, useMemo, useState } from "react";

import {
  ApiError,
  deleteTheme,
  getCanonicalThemes,
  getErrorMessage,
  getStories,
  getThemeDetail,
  mergeUnconfirmedTheme,
  updateCanonicalTheme,
  updateThemeConfirmationStatus,
} from "../api/client";
import { StorySummaryCard } from "../components/StorySummaryCard";
import { TermCard } from "../components/TermCard";
import type { CanonicalThemeListItem, StorySummary, ThemeDetail, ThemeConfirmationStatus } from "../api/types";
import { routeHref, useHashSearch } from "../router";


interface PageNotice {
  tone: "error" | "success";
  title: string;
  body?: string;
}


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
  const hashSearch = useHashSearch();
  const [themes, setThemes] = useState<CanonicalThemeListItem[]>([]);
  const [stories, setStories] = useState<StorySummary[]>([]);
  const [selectedThemeId, setSelectedThemeId] = useState<string | null>(null);
  const [selectedThemeDetail, setSelectedThemeDetail] = useState<ThemeDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [editingThemeId, setEditingThemeId] = useState<string | null>(null);
  const [editingThemeText, setEditingThemeText] = useState("");
  const [mergingThemeId, setMergingThemeId] = useState<string | null>(null);
  const [mergeTargetThemeId, setMergeTargetThemeId] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<PageNotice | null>(null);
  const selectedThemeParam = new URLSearchParams(hashSearch).get("selected_theme_id");

  const selectedTheme = themes.find((theme) => theme.id === selectedThemeId) ?? null;
  const canonicalThemes = useMemo(
    () => themes.filter((theme) => theme.confirmation_status === "canonical"),
    [themes],
  );
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

  async function handleUpdateConfirmationStatus(theme: CanonicalThemeListItem, nextStatus: ThemeConfirmationStatus) {
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

  function renderConfirmationActions(theme: CanonicalThemeListItem) {
    return (
      <div className="trope-management-confirmation-controls">
        <button
          aria-pressed={theme.confirmation_status === "unconfirmed"}
          className={`button ${
            theme.confirmation_status === "unconfirmed"
              ? "trope-confirmation-toggle-active trope-confirmation-toggle-unconfirmed"
              : "button-ghost"
          }`}
          disabled={busy || theme.confirmation_status === "unconfirmed"}
          onClick={() => void handleUpdateConfirmationStatus(theme, "unconfirmed")}
          type="button"
        >
          Unconfirmed
        </button>
        <button
          aria-pressed={theme.confirmation_status === "canonical"}
          className={`button ${
            theme.confirmation_status === "canonical"
              ? "trope-confirmation-toggle-active trope-confirmation-toggle-canonical"
              : "button-ghost"
          }`}
          disabled={busy || theme.confirmation_status === "canonical"}
          onClick={() => void handleUpdateConfirmationStatus(theme, "canonical")}
          type="button"
        >
          Canonical
        </button>
      </div>
    );
  }

  return (
    <div className="page-stack">
      <section className="panel">
        <div className="panel-header">
          <div>
            <h1>Theme management</h1>
            <p className="muted">Manage canonical themes and their confirmation status. Themes are not vectorized or used for similarity search.</p>
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
              <span className="pill">{themes.length}</span>
              <button className="button button-ghost" disabled={loading || busy} onClick={() => void refresh()} type="button">
                Refresh
              </button>
            </div>
          </div>

          <div className="list story-browser-list">
            {loading ? <p className="muted">Loading themes...</p> : null}
            {!loading && themes.length === 0 ? <p className="muted">No themes are available in the active dataset.</p> : null}
            {themes.map((theme) => {
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
                          disabled={loading || busy}
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
                            disabled={loading || busy || mergeTargets.length === 0}
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
                          disabled={loading || busy}
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
                          disabled={loading || busy}
                          onChange={(event) => setEditingThemeText(event.target.value)}
                          value={editingThemeText}
                        />
                      </label>
                      <div className="button-row wrap-row">
                        <button
                          className="button"
                          disabled={loading || busy || !editingThemeText.trim() || editingThemeText.trim() === theme.text}
                          onClick={() => void handleRenameTheme(theme)}
                          type="button"
                        >
                          Save theme
                        </button>
                        <button className="button button-ghost" disabled={loading || busy} onClick={resetThemeEditor} type="button">
                          Cancel
                        </button>
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
                          disabled={loading || busy}
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
                          disabled={loading || busy || !mergeTargetThemeId}
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
                  <>
                    <span className={`story-completeness-badge trope-confirmation-badge trope-confirmation-${selectedTheme.confirmation_status}`}>
                      {confirmationStatusLabel(selectedTheme.confirmation_status)}
                    </span>
                    {renderConfirmationActions(selectedTheme)}
                  </>
                }
                className="subdued trope-management-selected-card"
                meta={`${selectedTheme.story_count} stor${selectedTheme.story_count === 1 ? "y" : "ies"} total`}
                term={selectedTheme}
              />

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
            </section>
          ) : null}
        </div>
      </section>
    </div>
  );
}
