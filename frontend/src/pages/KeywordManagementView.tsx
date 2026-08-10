import { type KeyboardEvent, useEffect, useMemo, useState } from "react";

import {
  ApiError,
  deleteKeyword,
  getCanonicalKeywords,
  getErrorMessage,
  getKeywordDetail,
  getSimilarUnconfirmedKeywords,
  getStories,
  mergeUnconfirmedKeyword,
  updateCanonicalKeyword,
  updateKeywordConfirmationStatus,
} from "../api/client";
import { ConfirmationStatusSwitch } from "../components/ConfirmationStatusSwitch";
import { StorySummaryCard } from "../components/StorySummaryCard";
import { TermCard } from "../components/TermCard";
import type {
  CanonicalKeywordListItem,
  KeywordConfirmationStatus,
  KeywordDetail,
  SimilarUnconfirmedKeywordListResponse,
  StorySummary,
} from "../api/types";
import { routeHref, useHashSearch } from "../router";
import { useDatasetMaintenance } from "../maintenance";

interface PageNotice {
  tone: "error" | "success";
  title: string;
  body?: string;
}

type SimilarKeywordFilter = "unconfirmed" | "all";

function confirmationStatusLabel(status: KeywordConfirmationStatus): string {
  return status === "canonical" ? "Canonical" : "Unconfirmed";
}

function isKeywordVersionConflict(error: unknown): boolean {
  if (!(error instanceof ApiError) || error.status !== 409 || !error.detail || typeof error.detail !== "object") {
    return false;
  }
  return (error.detail as { code?: unknown }).code === "keyword_version_conflict";
}

export function KeywordManagementView() {
  const maintenance = useDatasetMaintenance();
  const hashSearch = useHashSearch();
  const [keywords, setKeywords] = useState<CanonicalKeywordListItem[]>([]);
  const [keywordQuery, setKeywordQuery] = useState("");
  const [stories, setStories] = useState<StorySummary[]>([]);
  const [selectedKeywordId, setSelectedKeywordId] = useState<string | null>(null);
  const [selectedKeywordDetail, setSelectedKeywordDetail] = useState<KeywordDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [similarKeywordFilter, setSimilarKeywordFilter] = useState<SimilarKeywordFilter>("unconfirmed");
  const [similarityThreshold, setSimilarityThreshold] = useState(0.6);
  const [similarKeywords, setSimilarKeywords] = useState<SimilarUnconfirmedKeywordListResponse | null>(null);
  const [similarKeywordsLoading, setSimilarKeywordsLoading] = useState(false);
  const [editingKeywordId, setEditingKeywordId] = useState<string | null>(null);
  const [editingKeywordText, setEditingKeywordText] = useState("");
  const [mergingKeywordId, setMergingKeywordId] = useState<string | null>(null);
  const [mergeTargetKeywordId, setMergeTargetKeywordId] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<PageNotice | null>(null);
  const mutationDisabled = busy || maintenance.active;
  const selectedKeywordParam = new URLSearchParams(hashSearch).get("selected_keyword_id");

  const selectedKeyword = keywords.find((keyword) => keyword.id === selectedKeywordId) ?? null;
  const canonicalKeywords = useMemo(
    () => keywords.filter((keyword) => keyword.confirmation_status === "canonical"),
    [keywords],
  );
  const filteredKeywords = useMemo(() => {
    const query = keywordQuery.trim().toLocaleLowerCase();
    return query ? keywords.filter((keyword) => keyword.text.toLocaleLowerCase().includes(query)) : keywords;
  }, [keywordQuery, keywords]);
  const storiesById = useMemo(() => new Map(stories.map((story) => [story.id, story])), [stories]);
  const selectedKeywordStories = useMemo(
    () =>
      (selectedKeywordDetail?.stories ?? [])
        .map((story) => storiesById.get(story.id))
        .filter((story): story is StorySummary => Boolean(story)),
    [selectedKeywordDetail, storiesById],
  );

  function resetKeywordEditor() {
    setEditingKeywordId(null);
    setEditingKeywordText("");
  }

  function resetKeywordMerge() {
    setMergingKeywordId(null);
    setMergeTargetKeywordId("");
  }

  async function refresh(options?: { clearNotice?: boolean }) {
    try {
      setLoading(true);
      if (options?.clearNotice !== false) {
        setNotice(null);
      }
      const [keywordResponse, storiesResponse] = await Promise.all([
        getCanonicalKeywords({ limit: 5000 }),
        getStories(),
      ]);
      setKeywords(keywordResponse);
      setStories(storiesResponse.items);
    } catch (caughtError) {
      setNotice({
        tone: "error",
        title: "Could not load keyword management data",
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
    if (keywords.length === 0) {
      setSelectedKeywordId(null);
      return;
    }
    if (selectedKeywordParam && keywords.some((keyword) => keyword.id === selectedKeywordParam)) {
      setSelectedKeywordId((current) => (current === selectedKeywordParam ? current : selectedKeywordParam));
      return;
    }
    if (selectedKeywordId && keywords.some((keyword) => keyword.id === selectedKeywordId)) {
      return;
    }
    setSelectedKeywordId(keywords[0].id);
  }, [keywords, selectedKeywordId, selectedKeywordParam]);

  useEffect(() => {
    if (editingKeywordId && !keywords.some((keyword) => keyword.id === editingKeywordId)) {
      resetKeywordEditor();
    }
  }, [editingKeywordId, keywords]);

  useEffect(() => {
    if (mergingKeywordId && !keywords.some((keyword) => keyword.id === mergingKeywordId)) {
      resetKeywordMerge();
    }
  }, [keywords, mergingKeywordId]);

  useEffect(() => {
    if (!selectedKeywordId) {
      setSelectedKeywordDetail(null);
      setDetailLoading(false);
      return;
    }

    let cancelled = false;
    setDetailLoading(true);
    setSelectedKeywordDetail(null);
    void (async () => {
      try {
        const detail = await getKeywordDetail(selectedKeywordId);
        if (!cancelled) {
          setSelectedKeywordDetail(detail);
        }
      } catch (caughtError) {
        if (!cancelled) {
          setNotice({
            tone: "error",
            title: "Could not load keyword details",
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
  }, [selectedKeywordId]);

  useEffect(() => {
    if (!selectedKeyword) {
      setSimilarKeywords(null);
      setSimilarKeywordsLoading(false);
      return;
    }

    let cancelled = false;
    void (async () => {
      try {
        setSimilarKeywordsLoading(true);
        const response = await getSimilarUnconfirmedKeywords(selectedKeyword.id, {
          minimum_similarity: similarityThreshold,
          include_canonical: similarKeywordFilter === "all",
        });
        if (!cancelled) {
          setSimilarKeywords(response);
        }
      } catch (caughtError) {
        if (!cancelled) {
          setSimilarKeywords(null);
          setNotice({
            tone: "error",
            title: "Could not load similar keywords",
            body: getErrorMessage(caughtError),
          });
        }
      } finally {
        if (!cancelled) {
          setSimilarKeywordsLoading(false);
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [selectedKeyword, similarityThreshold, similarKeywordFilter]);

  function handleKeywordRowKeyDown(event: KeyboardEvent<HTMLElement>, keywordId: string) {
    if (loading || busy) {
      return;
    }
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      setSelectedKeywordId(keywordId);
    }
  }

  async function handleKeywordVersionConflict(title: string) {
    await refresh({ clearNotice: false });
    resetKeywordEditor();
    setNotice({
      tone: "error",
      title,
      body: "This keyword changed in another browser session. The list has been refreshed with the latest version.",
    });
  }

  async function handleRenameKeyword(keyword: CanonicalKeywordListItem) {
    const text = editingKeywordText.trim();
    if (!text) {
      return;
    }
    try {
      setBusy(true);
      setNotice(null);
      await updateCanonicalKeyword({
        keyword_id: keyword.id,
        expected_keyword_version: keyword.version,
        text,
      });
      await refresh({ clearNotice: false });
      resetKeywordEditor();
      setNotice({
        tone: "success",
        title: "Keyword edited",
        body: "The canonical keyword text was updated everywhere it is used.",
      });
    } catch (caughtError) {
      if (isKeywordVersionConflict(caughtError)) {
        await handleKeywordVersionConflict("Could not edit keyword");
      } else {
        setNotice({ tone: "error", title: "Could not edit keyword", body: getErrorMessage(caughtError) });
      }
    } finally {
      setBusy(false);
    }
  }

  async function handleDeleteKeyword(keyword: CanonicalKeywordListItem) {
    const shouldDelete = window.confirm(
      keyword.story_count > 0
        ? `Delete keyword "${keyword.text}" from all ${keyword.story_count} stor${keyword.story_count === 1 ? "y" : "ies"} and remove the canonical keyword?`
        : `Delete unused keyword "${keyword.text}"?`,
    );
    if (!shouldDelete) {
      return;
    }

    try {
      setBusy(true);
      setNotice(null);
      if (selectedKeywordId === keyword.id) {
        setSelectedKeywordId(null);
      }
      const result = await deleteKeyword({
        keyword_id: keyword.id,
        expected_keyword_version: keyword.version,
        remove_from_all_stories: keyword.story_count > 0,
      });
      await refresh({ clearNotice: false });
      resetKeywordEditor();
      resetKeywordMerge();
      setNotice({
        tone: "success",
        title: "Keyword deleted",
        body:
          result.affected_story_count > 0
            ? `Deleted the canonical keyword and removed it from ${result.affected_story_count} stor${
                result.affected_story_count === 1 ? "y" : "ies"
              }.`
            : "Deleted the unused canonical keyword.",
      });
    } catch (caughtError) {
      if (isKeywordVersionConflict(caughtError)) {
        await handleKeywordVersionConflict("Could not delete keyword");
      } else {
        setNotice({ tone: "error", title: "Could not delete keyword", body: getErrorMessage(caughtError) });
      }
    } finally {
      setBusy(false);
    }
  }

  async function handleMergeKeyword(sourceKeyword: CanonicalKeywordListItem) {
    if (!mergeTargetKeywordId) {
      return;
    }
    const targetKeyword = canonicalKeywords.find((keyword) => keyword.id === mergeTargetKeywordId);
    if (!targetKeyword) {
      return;
    }

    try {
      setBusy(true);
      setNotice(null);
      if (selectedKeywordId === sourceKeyword.id) {
        setSelectedKeywordId(null);
      }
      const result = await mergeUnconfirmedKeyword({
        source_keyword_id: sourceKeyword.id,
        expected_source_keyword_version: sourceKeyword.version,
        target_keyword_id: targetKeyword.id,
      });
      await refresh({ clearNotice: false });
      resetKeywordEditor();
      resetKeywordMerge();
      setNotice({
        tone: "success",
        title: "Keyword merged",
        body: `Merged “${sourceKeyword.text}” into canonical keyword “${result.target_keyword.text}” across ${result.affected_story_count} stor${
          result.affected_story_count === 1 ? "y" : "ies"
        }.`,
      });
    } catch (caughtError) {
      if (isKeywordVersionConflict(caughtError)) {
        await handleKeywordVersionConflict("Could not merge keyword");
      } else {
        setNotice({ tone: "error", title: "Could not merge keyword", body: getErrorMessage(caughtError) });
      }
    } finally {
      setBusy(false);
    }
  }

  async function handleUpdateConfirmationStatus(keyword: CanonicalKeywordListItem, nextStatus: KeywordConfirmationStatus) {
    try {
      setBusy(true);
      const response = await updateKeywordConfirmationStatus(keyword.id, {
        expected_keyword_version: keyword.version,
        confirmation_status: nextStatus,
      });
      setKeywords((current) => current.map((item) => (item.id === response.keyword.id ? response.keyword : item)));
      setNotice({
        tone: "success",
        title: "Keyword updated",
        body: `Confirmation status set to ${confirmationStatusLabel(nextStatus).toLowerCase()}.`,
      });
    } catch (caughtError) {
      if (isKeywordVersionConflict(caughtError)) {
        await handleKeywordVersionConflict("Could not update keyword confirmation");
      } else {
        setNotice({
          tone: "error",
          title: "Could not update keyword confirmation",
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
            <h1>Keyword management</h1>
            <p className="muted">Manage canonical keywords and their confirmation status.</p>
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
            <h2>Keywords</h2>
            <div className="button-row">
              <span className="pill">{filteredKeywords.length}</span>
              <button className="button button-ghost" disabled={loading || busy} onClick={() => void refresh()} type="button">
                Refresh
              </button>
            </div>
          </div>

          <label className="field">
            <span>Filter keywords</span>
            <input
              className="input"
              onChange={(event) => setKeywordQuery(event.target.value)}
              placeholder="Enter text to filter keywords"
              value={keywordQuery}
            />
          </label>

          <div className="list story-browser-list">
            {loading ? <p className="muted">Loading keywords...</p> : null}
            {!loading && keywords.length === 0 ? <p className="muted">No keywords are available in the active dataset.</p> : null}
            {!loading && keywords.length > 0 && filteredKeywords.length === 0 ? <p className="muted">No keywords match the current filter.</p> : null}
            {filteredKeywords.map((keyword) => {
              const isEditing = editingKeywordId === keyword.id;
              const isMerging = mergingKeywordId === keyword.id;
              const mergeTargets = canonicalKeywords.filter((candidate) => candidate.id !== keyword.id);
              return (
                <article
                  aria-pressed={keyword.id === selectedKeywordId}
                  className={`list-row trope-management-row ${keyword.id === selectedKeywordId ? "list-row-active" : ""}`.trim()}
                  key={keyword.id}
                  onClick={() => {
                    if (!loading && !busy) {
                      setSelectedKeywordId(keyword.id);
                    }
                  }}
                  onKeyDown={(event) => handleKeywordRowKeyDown(event, keyword.id)}
                  role="button"
                  tabIndex={loading || busy ? -1 : 0}
                >
                  <div className="card-row trope-management-row-top">
                    <div className="trope-management-row-title">
                      <strong>{keyword.text}</strong>
                      <span className="muted">
                        {keyword.story_count} stor{keyword.story_count === 1 ? "y" : "ies"}
                      </span>
                    </div>
                    <div
                      className="trope-management-row-actions"
                      onClick={(event) => event.stopPropagation()}
                      onKeyDown={(event) => event.stopPropagation()}
                    >
                      <span className={`story-completeness-badge trope-confirmation-badge trope-confirmation-${keyword.confirmation_status}`}>
                        {confirmationStatusLabel(keyword.confirmation_status)}
                      </span>
                      <div className="button-row">
                        <button
                          className="button button-ghost"
                          disabled={loading || mutationDisabled}
                          onClick={() => {
                            resetKeywordMerge();
                            if (isEditing) {
                              resetKeywordEditor();
                              return;
                            }
                            setSelectedKeywordId(keyword.id);
                            setEditingKeywordId(keyword.id);
                            setEditingKeywordText(keyword.text);
                          }}
                          type="button"
                        >
                          {isEditing ? "Cancel edit" : "Edit"}
                        </button>
                        {keyword.confirmation_status === "unconfirmed" ? (
                          <button
                            className="button button-ghost"
                            disabled={loading || mutationDisabled || mergeTargets.length === 0}
                            onClick={() => {
                              if (isMerging) {
                                resetKeywordMerge();
                                return;
                              }
                              resetKeywordEditor();
                              setSelectedKeywordId(keyword.id);
                              setMergingKeywordId(keyword.id);
                              setMergeTargetKeywordId(mergeTargets[0]?.id ?? "");
                            }}
                            title={mergeTargets.length === 0 ? "Mark another keyword canonical before merging." : undefined}
                            type="button"
                          >
                            {isMerging ? "Cancel merge" : "Merge with..."}
                          </button>
                        ) : null}
                        <button
                          className="button button-danger"
                          disabled={loading || mutationDisabled}
                          onClick={() => void handleDeleteKeyword(keyword)}
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
                        <span>Edit keyword</span>
                        <input
                          className="input"
                          disabled={loading || mutationDisabled}
                          onChange={(event) => setEditingKeywordText(event.target.value)}
                          value={editingKeywordText}
                        />
                      </label>
                      <div className="button-row wrap-row">
                        <button
                          className="button"
                          disabled={loading || mutationDisabled || !editingKeywordText.trim() || editingKeywordText.trim() === keyword.text}
                          onClick={() => void handleRenameKeyword(keyword)}
                          type="button"
                        >
                          Save keyword
                        </button>
                        <button className="button button-ghost" disabled={loading || busy} onClick={resetKeywordEditor} type="button">
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
                        <span>Merge with canonical keyword</span>
                        <select
                          className="input"
                          disabled={loading || mutationDisabled}
                          onChange={(event) => setMergeTargetKeywordId(event.target.value)}
                          value={mergeTargetKeywordId}
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
                          disabled={loading || mutationDisabled || !mergeTargetKeywordId}
                          onClick={() => void handleMergeKeyword(keyword)}
                          type="button"
                        >
                          Merge keywords
                        </button>
                        <button className="button button-ghost" disabled={loading || busy} onClick={resetKeywordMerge} type="button">
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
          {!selectedKeyword ? (
            <section className="panel">
              <p className="muted">Choose a keyword to inspect it.</p>
            </section>
          ) : null}

          {selectedKeyword ? (
            <section className="panel">
              <TermCard
                actions={
                  <span className={`story-completeness-badge trope-confirmation-badge trope-confirmation-${selectedKeyword.confirmation_status}`}>
                    {confirmationStatusLabel(selectedKeyword.confirmation_status)}
                  </span>
                }
                className="subdued trope-management-selected-card"
                meta={`${selectedKeyword.story_count} stor${selectedKeyword.story_count === 1 ? "y" : "ies"} total`}
                term={selectedKeyword}
              >
                <div className="keyword-confirmation-control">
                  <ConfirmationStatusSwitch
                    ariaLabel="Keyword confirmation status"
                    className="keyword-confirmation-status-switch"
                    disabled={mutationDisabled}
                    onChange={(nextStatus) => void handleUpdateConfirmationStatus(selectedKeyword, nextStatus)}
                    value={selectedKeyword.confirmation_status}
                  />
                </div>
              </TermCard>

              <div className="panel-header">
                <h3>Stories</h3>
                <span className="pill">{detailLoading ? "loading" : selectedKeywordStories.length}</span>
              </div>
              <div className="list story-browser-list trope-management-story-list">
                {detailLoading ? <p className="muted">Loading stories...</p> : null}
                {!detailLoading && selectedKeywordStories.length === 0 ? (
                  <p className="muted">No stories currently use this keyword in the active dataset.</p>
                ) : null}
                {selectedKeywordStories.map((story) => (
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
                    <h3>Similar keywords</h3>
                    <p className="muted">Candidates are ordered by embedding similarity to the selected keyword.</p>
                  </div>
                  <span className="pill">
                    {similarKeywordsLoading ? "loading" : `${similarKeywords?.total ?? 0} results`}
                  </span>
                </div>

                <div className="trope-management-similar-filter-control">
                  <div aria-label="Similar keyword results" className="similarity-scope-switch" role="group">
                    <button
                      aria-pressed={similarKeywordFilter === "unconfirmed"}
                      className={similarKeywordFilter === "unconfirmed" ? "similarity-scope-switch-option-active" : undefined}
                      disabled={loading || busy}
                      onClick={() => setSimilarKeywordFilter("unconfirmed")}
                      type="button"
                    >
                      unconfirmed
                    </button>
                    <button
                      aria-pressed={similarKeywordFilter === "all"}
                      className={similarKeywordFilter === "all" ? "similarity-scope-switch-option-active" : undefined}
                      disabled={loading || busy}
                      onClick={() => setSimilarKeywordFilter("all")}
                      type="button"
                    >
                      all
                    </button>
                  </div>
                </div>

                <label className="field trope-management-similarity-threshold" htmlFor="keyword-management-similarity-threshold">
                  <div className="card-row">
                    <strong>Similarity threshold</strong>
                    <span className="pill">{similarityThreshold.toFixed(2)}</span>
                  </div>
                  <input
                    className="range-input"
                    disabled={busy || loading}
                    id="keyword-management-similarity-threshold"
                    max="1"
                    min="0"
                    onChange={(event) => setSimilarityThreshold(Number(event.target.value))}
                    step="0.01"
                    type="range"
                    value={similarityThreshold}
                  />
                </label>

                {similarKeywordsLoading ? <p className="muted">Loading similar keywords...</p> : null}
                {!similarKeywordsLoading && similarKeywords?.artifact_version === null ? (
                  <p className="muted">No current keyword embeddings are available. Run Rebuild, then refresh this view.</p>
                ) : null}
                {!similarKeywordsLoading &&
                similarKeywords?.artifact_version !== null &&
                similarKeywords?.items.length === 0 ? (
                  <p className="muted">
                    {similarKeywordFilter === "unconfirmed"
                      ? "No unconfirmed keywords meet the current threshold."
                      : "No similar keywords meet the current threshold."}
                  </p>
                ) : null}

                <div className="list trope-management-similar-list">
                  {similarKeywords?.items.map((keyword) => (
                    <TermCard
                      className="trope-management-similar-trope-card"
                      key={keyword.id}
                      meta={`Similarity ${keyword.similarity_score.toFixed(2)}`}
                      term={keyword}
                      actions={
                        <button className="button button-ghost" onClick={() => setSelectedKeywordId(keyword.id)} type="button">
                          View keyword
                        </button>
                      }
                    />
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
