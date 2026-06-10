import { useEffect, useState } from "react";

import {
  addStoryTrope,
  ApiError,
  deleteStoryTrope,
  getErrorMessage,
  getStories,
  getStory,
  searchTropes,
  validateStoryTrope,
} from "../api/client";
import { TropeCard } from "../components/TropeCard";
import type { StoryDetail, StorySummary, TropeSearchItem } from "../api/types";
import { routeHref, useHashSearch } from "../router";

interface PageNotice {
  tone: "error" | "success";
  title: string;
  body?: string;
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
  return [
    story.title,
    story.summary,
    story.territory,
    story.source_row_number?.toString() || "",
  ]
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

function explanationLabel(item: TropeSearchItem): string {
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

export function ReviewPage() {
  const hashSearch = useHashSearch();
  const [stories, setStories] = useState<StorySummary[]>([]);
  const [selectedStoryId, setSelectedStoryId] = useState<string | null>(null);
  const [detail, setDetail] = useState<StoryDetail | null>(null);
  const [storyQuery, setStoryQuery] = useState("");
  const [tropeQuery, setTropeQuery] = useState("");
  const [tropeResults, setTropeResults] = useState<TropeSearchItem[]>([]);
  const [tropeSearchStatus, setTropeSearchStatus] = useState<"idle" | "loading" | "ready">("idle");
  const [storiesLoading, setStoriesLoading] = useState(true);
  const [storyLoading, setStoryLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<PageNotice | null>(null);
  const selectedStoryParam = new URLSearchParams(hashSearch).get("selected_story_id");

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
      }
    } catch (caughtError) {
      setNotice({
        tone: "error",
        title: "Could not load stories",
        body: getErrorMessage(caughtError),
      });
    }
  }

  useEffect(() => {
    void refresh(selectedStoryParam);
  }, []);

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
    if (!selectedStoryId) {
      return;
    }

    void loadStory(selectedStoryId).catch((caughtError) => {
      setNotice({
        tone: "error",
        title: "Could not load story",
        body: getErrorMessage(caughtError),
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
        } catch (caughtError) {
          if (cancelled) {
            return;
          }
          setTropeResults([]);
          setTropeSearchStatus("ready");
          setNotice({
            tone: "error",
            title: "Could not search tropes",
            body: getErrorMessage(caughtError),
          });
        }
      })();
    }, 250);

    return () => {
      cancelled = true;
      window.clearTimeout(timeoutId);
    };
  }, [tropeQuery]);

  async function handleStoryConflict(error: ApiError, storyId: string) {
    const currentVersion = extractConflictVersion(error);
    await refresh(storyId);
    setNotice({
      tone: "error",
      title: "Story updated elsewhere",
      body:
        currentVersion === null
          ? "This story changed in another browser session. The latest version has been reloaded."
          : `This story changed in another browser session. Reloaded story version ${currentVersion}. Review the refreshed tropes and try again.`,
    });
  }

  async function runStoryMutation(
    storyId: string,
    action: () => Promise<unknown>,
    successNotice: PageNotice,
  ) {
    try {
      setBusy(true);
      setNotice(null);
      await action();
      await refresh(storyId);
      setNotice(successNotice);
    } catch (caughtError) {
      if (caughtError instanceof ApiError && caughtError.status === 409) {
        await handleStoryConflict(caughtError, storyId);
        return;
      }
      setNotice({
        tone: "error",
        title: "Mutation failed",
        body: getErrorMessage(caughtError),
      });
    } finally {
      setBusy(false);
    }
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

  const filteredStories = stories.filter((story) => storyMatchesQuery(story, storyQuery));
  const assignedTropeIds = new Set(detail?.tropes.map((trope) => trope.id) ?? []);
  const interactionDisabled = busy || storiesLoading || storyLoading;

  return (
    <div className="page-stack">
      <section className="panel">
        <div className="panel-header">
          <div>
            <p className="eyebrow">Review</p>
            <h1>Inspect stories and manage trope assignments</h1>
          </div>
          <button className="button button-ghost" disabled={storiesLoading || storyLoading} onClick={() => void refresh()} type="button">
            {storiesLoading || storyLoading ? "Loading..." : "Refresh"}
          </button>
        </div>
        <p className="muted">
          Filter stories locally, inspect the selected story, search for similar existing tropes, and apply review
          changes against the backend version checks.
        </p>
      </section>

      {notice && (
        <section className={`notice ${notice.tone === "error" ? "notice-error" : "notice-success"}`}>
          <strong className="notice-title">{notice.title}</strong>
          {notice.body ? <p>{notice.body}</p> : null}
        </section>
      )}

      <section className="two-column-layout">
        <aside className="panel">
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
          <div className="list">
            {storiesLoading ? <p className="muted">Loading stories...</p> : null}
            {!storiesLoading && filteredStories.length === 0 ? <p className="muted">No stories match the current search.</p> : null}
            {filteredStories.map((story) => (
              <button
                className={`list-row ${story.id === selectedStoryId ? "list-row-active" : ""}`}
                disabled={storiesLoading}
                key={story.id}
                onClick={() => {
                  window.location.hash = routeHref("/review", { selected_story_id: story.id });
                }}
                type="button"
              >
                <strong>{story.title || `Story ${story.source_row_number ?? "?"}`}</strong>
                <span className="muted">
                  {story.territory || "No territory"} · v{story.version}
                </span>
                <span className="muted">
                  {story.summary || `${story.trope_count} tropes · ${story.keyword_count} keywords`}
                </span>
              </button>
            ))}
          </div>
        </aside>

        <div className="page-stack review-detail-column">
          <section className="panel">
            <div className="panel-header">
              <div>
                <h2>{detail?.fields["Story title (Eng)"] || detail?.id || "No story selected"}</h2>
                {detail ? (
                  <p className="muted">
                    Story version {detail.version} · source row {detail.source_row_number ?? "n/a"}
                  </p>
                ) : null}
              </div>
            </div>

            {detail ? (
              <div className="page-stack">
                {storyLoading ? <p className="muted">Loading story details...</p> : null}
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
                      Pages {detail.fields["pages"] || "n/a"} · recorder {detail.fields["date of recording"] || "n/a"}
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
                      <>
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
                    }
                  />
                ))
              ) : (
                <p className="muted">No tropes on this story yet.</p>
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
              {!tropeQuery.trim() ? <p className="muted">Start typing to search the existing trope index.</p> : null}
              {tropeQuery.trim() && tropeSearchStatus === "loading" ? <p className="muted">Searching tropes...</p> : null}
              {tropeQuery.trim() && tropeSearchStatus === "ready" && tropeResults.length === 0 ? (
                <p className="muted">No similar tropes were returned for this query.</p>
              ) : null}
              {tropeResults.map((item) => {
                const alreadyAssigned = assignedTropeIds.has(item.id);
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
        </div>
      </section>
    </div>
  );
}
