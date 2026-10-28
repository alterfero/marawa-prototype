import { useEffect, useRef, useState } from "react";

import {
  addStoryKeyword,
  addStoryTheme,
  addStoryTrope,
  ApiError,
  approveReviewItem,
  getErrorMessage,
  getReviewStories,
  getStory,
  getStoryReviewItems,
  rejectReviewItem,
  resolveStoryDataReview,
  resolveStoryTermReview,
  searchKeywords,
  searchThemes,
  searchTropes,
} from "../api/client";
import type { ReviewItem, ReviewStatus, ReviewStory, SearchItem, StoryDetail } from "../api/types";
import { StoryReviewItemAdder } from "../components/StoryReviewItemAdder";
import type { StoryItemInput, StoryItemKind } from "../components/StoryReviewItemAdder";
import { TermCard } from "../components/TermCard";
import { getStoryFieldLabel, KEYWORD_FIELD, LONG_TEXT_FIELDS, splitThemeText, THEME_FIELD, TROPE_FIELD } from "../constants/csv";
import { useDatasetMaintenance } from "../maintenance";

interface PageNotice {
  tone: "error" | "success";
  title: string;
  body?: string;
}

interface StoryFieldReviewMetadata {
  field_name: string;
  previous_value: string;
  current_value: string;
}

interface StoryTropeReviewMetadata {
  assignment_action: string;
  previous_trope_id: string;
  previous_trope_text: string;
  previous_origin: string;
  previous_status: string;
  current_trope_id: string;
  current_trope_text: string;
  current_origin: string;
  current_status: string;
}

interface StoryKeywordReviewMetadata {
  assignment_action: string;
  previous_keyword_id: string;
  previous_keyword_text: string;
  current_keyword_id: string;
  current_keyword_text: string;
}

function readString(metadata: Record<string, unknown>, key: string): string {
  const value = metadata[key];
  return typeof value === "string" ? value : "";
}

function readNumber(metadata: Record<string, unknown>, key: string): number | null {
  const value = metadata[key];
  return typeof value === "number" ? value : null;
}

function resolutionMetadata(item: ReviewItem): Record<string, unknown> | null {
  const resolution = item.metadata.resolution;
  if (!resolution || typeof resolution !== "object") {
    return null;
  }
  return resolution as Record<string, unknown>;
}

function storyReviewTitle(item: ReviewItem): string {
  const metadataTitle = readString(item.metadata, "story_title");
  if (metadataTitle) {
    return metadataTitle;
  }
  return item.subject_preview?.title || item.subject_id;
}

function parseStoryFieldReview(item: ReviewItem | null): StoryFieldReviewMetadata | null {
  if (!item || item.subject_table !== "stories" || readString(item.metadata, "change_kind") !== "story_field") {
    return null;
  }
  return {
    field_name: readString(item.metadata, "field_name"),
    previous_value: readString(item.metadata, "previous_value"),
    current_value: readString(item.metadata, "current_value"),
  };
}

function isStoryDataReview(item: ReviewItem): boolean {
  const field = parseStoryFieldReview(item)?.field_name;
  return Boolean(field && ![THEME_FIELD, TROPE_FIELD, KEYWORD_FIELD].includes(field));
}

function parseStoryTropeReview(item: ReviewItem | null): StoryTropeReviewMetadata | null {
  if (item?.subject_table === "tropes") {
    return {
      assignment_action: "added", previous_trope_id: "", previous_trope_text: "", previous_origin: "", previous_status: "",
      current_trope_id: item.subject_id, current_trope_text: item.subject_preview?.text || readString(item.metadata, "text"),
      current_origin: "", current_status: "",
    };
  }
  if (!item || item.subject_table !== "stories" || readString(item.metadata, "change_kind") !== "story_trope") {
    return null;
  }
  return {
    assignment_action: readString(item.metadata, "assignment_action"),
    previous_trope_id: readString(item.metadata, "previous_trope_id"),
    previous_trope_text: readString(item.metadata, "previous_trope_text"),
    previous_origin: readString(item.metadata, "previous_origin"),
    previous_status: readString(item.metadata, "previous_status"),
    current_trope_id: readString(item.metadata, "current_trope_id"),
    current_trope_text: readString(item.metadata, "current_trope_text"),
    current_origin: readString(item.metadata, "current_origin"),
    current_status: readString(item.metadata, "current_status"),
  };
}

function parseStoryKeywordReview(item: ReviewItem | null): StoryKeywordReviewMetadata | null {
  if (item?.subject_table === "keywords") {
    return {
      assignment_action: "added", previous_keyword_id: "", previous_keyword_text: "",
      current_keyword_id: item.subject_id, current_keyword_text: item.subject_preview?.text || readString(item.metadata, "text"),
    };
  }
  if (!item || item.subject_table !== "stories" || readString(item.metadata, "change_kind") !== "story_keyword") {
    return null;
  }
  return {
    assignment_action: readString(item.metadata, "assignment_action"),
    previous_keyword_id: readString(item.metadata, "previous_keyword_id"),
    previous_keyword_text: readString(item.metadata, "previous_keyword_text"),
    current_keyword_id: readString(item.metadata, "current_keyword_id"),
    current_keyword_text: readString(item.metadata, "current_keyword_text"),
  };
}

function formatValue(value: string): string {
  return value.trim() ? value : "Empty";
}

function reviewStatusLabel(item: ReviewItem): string {
  return resolutionMetadata(item)?.decision === "edited" ? "edited" : item.status.replace(/_/g, " ");
}

function reviewHeadline(item: ReviewItem): string {
  if (item.subject_table === "tropes" || item.subject_table === "keywords") {
    return `${item.subject_table === "tropes" ? "Trope" : "Keyword"}: ${item.subject_preview?.text || readString(item.metadata, "text")}`;
  }
  const fieldReview = parseStoryFieldReview(item);
  if (fieldReview) {
    return getStoryFieldLabel(fieldReview.field_name);
  }

  const tropeReview = parseStoryTropeReview(item);
  if (tropeReview) {
    if (tropeReview.assignment_action === "added") {
      return `Added trope: ${tropeReview.current_trope_text || "Untitled trope"}`;
    }
    if (tropeReview.assignment_action === "replaced") {
      return `Replaced trope: ${tropeReview.previous_trope_text || "Unknown"} -> ${tropeReview.current_trope_text || "Unknown"}`;
    }
    if (tropeReview.assignment_action === "deleted") {
      return `Deleted trope: ${tropeReview.previous_trope_text || "Unknown"}`;
    }
    if (tropeReview.assignment_action === "validated") {
      return `Validated trope: ${tropeReview.current_trope_text || tropeReview.previous_trope_text || "Unknown"}`;
    }
  }

  const keywordReview = parseStoryKeywordReview(item);
  if (keywordReview) {
    if (keywordReview.assignment_action === "added") {
      return `Added keyword: ${keywordReview.current_keyword_text || "Untitled keyword"}`;
    }
    if (keywordReview.assignment_action === "replaced") {
      return `Replaced keyword: ${keywordReview.previous_keyword_text || "Unknown"} -> ${keywordReview.current_keyword_text || "Unknown"}`;
    }
    if (keywordReview.assignment_action === "deleted") {
      return `Deleted keyword: ${keywordReview.previous_keyword_text || "Unknown"}`;
    }
  }

  return storyReviewTitle(item);
}

function liveTropeText(detail: StoryDetail | null, review: StoryTropeReviewMetadata, editedTermId: string): string | null {
  if (!detail) {
    return null;
  }
  if (editedTermId) return detail.tropes.find((item) => item.id === editedTermId)?.text ?? null;
  const current = detail.tropes.find((item) => item.id === review.current_trope_id);
  if (current) {
    return current.text;
  }
  const previous = detail.tropes.find((item) => item.id === review.previous_trope_id);
  if (previous) {
    return previous.text;
  }
  return null;
}

function liveKeywordText(detail: StoryDetail | null, review: StoryKeywordReviewMetadata, editedTermId: string): string | null {
  if (!detail) {
    return null;
  }
  if (editedTermId) return detail.keywords.find((item) => item.id === editedTermId)?.text ?? null;
  const current = detail.keywords.find((item) => item.id === review.current_keyword_id);
  if (current) {
    return current.text;
  }
  const previous = detail.keywords.find((item) => item.id === review.previous_keyword_id);
  if (previous) {
    return previous.text;
  }
  return null;
}

function canInlineEdit(item: ReviewItem): boolean {
  if (item.status !== "pending") {
    return false;
  }
  if (parseStoryFieldReview(item)) {
    return true;
  }
  const tropeReview = parseStoryTropeReview(item);
  if (tropeReview) {
    return tropeReview.assignment_action === "added" || tropeReview.assignment_action === "replaced";
  }
  const keywordReview = parseStoryKeywordReview(item);
  if (keywordReview) {
    return keywordReview.assignment_action === "added" || keywordReview.assignment_action === "replaced";
  }
  return false;
}

function isLegacyStoryReviewItem(item: ReviewItem): boolean {
  return item.subject_table === "stories" && !parseStoryFieldReview(item) && !parseStoryTropeReview(item) && !parseStoryKeywordReview(item);
}

function editableDraftLabel(item: ReviewItem): string {
  const fieldReview = parseStoryFieldReview(item);
  if (fieldReview) {
    return getStoryFieldLabel(fieldReview.field_name);
  }
  if (parseStoryTropeReview(item)) {
    return "Trope text";
  }
  if (parseStoryKeywordReview(item)) {
    return "Keyword text";
  }
  return "Value";
}

function itemCreatedAtTimestamp(item: ReviewItem): number {
  return Date.parse(item.created_at);
}

function compareStoryItems(a: ReviewItem, b: ReviewItem): number {
  const typeWeight = (item: ReviewItem): number => {
    if (parseStoryFieldReview(item)) {
      return 0;
    }
    if (parseStoryTropeReview(item)) {
      return 1;
    }
    if (parseStoryKeywordReview(item)) {
      return 2;
    }
    return 3;
  };

  const weightDelta = typeWeight(a) - typeWeight(b);
  if (weightDelta !== 0) {
    return weightDelta;
  }

  const createdDelta = itemCreatedAtTimestamp(a) - itemCreatedAtTimestamp(b);
  if (createdDelta !== 0) {
    return createdDelta;
  }

  return reviewHeadline(a).localeCompare(reviewHeadline(b));
}

function ReviewValueCard({ label, value }: { label: string; value: string }) {
  return (
    <article className="card subdued">
      <span className="stat-label">{label}</span>
      <p className="review-value">{formatValue(value)}</p>
    </article>
  );
}

function StoryDataReviewCard({ items, story, disabled, onBusyChange, onResolved, onNotice }: {
  items: ReviewItem[];
  story: StoryDetail | null;
  disabled: boolean;
  onBusyChange: (busy: boolean) => void;
  onResolved: (resolved: ReviewItem[]) => Promise<void>;
  onNotice: (notice: PageNotice | null) => void;
}) {
  const pending = items.filter((item) => item.status === "pending" && isStoryDataReview(item));
  const changes = new Map<string, StoryFieldReviewMetadata>();
  [...pending].sort((a, b) => (readNumber(a.metadata, "story_version") ?? 0) - (readNumber(b.metadata, "story_version") ?? 0)
    || itemCreatedAtTimestamp(a) - itemCreatedAtTimestamp(b)).forEach((item) => {
    const field = parseStoryFieldReview(item)!;
    const earlier = changes.get(field.field_name);
    changes.set(field.field_name, { ...field, previous_value: earlier?.previous_value ?? field.previous_value });
  });
  const initialDraft = JSON.stringify(Object.fromEntries([...changes.values()].map((field) => [
    field.field_name, story?.fields[field.field_name] ?? field.current_value,
  ])));
  const [draft, setDraft] = useState<Record<string, string>>({});
  useEffect(() => { setDraft(JSON.parse(initialDraft)); }, [initialDraft]);
  const mutationDisabled = disabled || !story;

  async function decide(decision: "approve" | "reject") {
    if (mutationDisabled || !story) return;
    onBusyChange(true);
    onNotice(null);
    try {
      const resolved = await resolveStoryDataReview(story.id, decision, {
        expected_story_version: story.version,
        review_ids: pending.map((item) => item.id),
        ...(decision === "approve" ? { fields: draft } : {}),
      });
      await onResolved(resolved);
      onNotice({
        tone: "success",
        title: decision === "approve" ? "Story data approved" : "Story data rejected",
        body: decision === "approve"
          ? "All proposed story data was approved with your edits. Themes, tropes and keywords keep their separate reviews."
          : "The proposed story data was reverted. Themes, tropes and keywords keep their separate reviews.",
      });
    } catch (error) {
      onNotice({ tone: "error", title: `Could not ${decision} story data`, body: getErrorMessage(error) });
    } finally {
      onBusyChange(false);
    }
  }

  if (!pending.length) return null;
  return (
    <section className="card page-stack" aria-label="Story data review">
      <div className="panel-header">
        <div>
          <h3>Story data</h3>
          <p className="muted">Edit the proposed values below, then approve all story data together. Themes, tropes and keywords are reviewed separately.</p>
        </div>
        <span className="pill">{changes.size} field{changes.size === 1 ? "" : "s"}</span>
      </div>
      {[...changes.values()].map((field) => {
        const current = story?.fields[field.field_name] ?? field.current_value;
        return (
          <div className="page-stack" key={field.field_name}>
            <label className="field">
              <span>{getStoryFieldLabel(field.field_name)}</span>
              {LONG_TEXT_FIELDS.has(field.field_name) ? (
                <textarea className="input input-textarea" disabled={mutationDisabled} value={draft[field.field_name] ?? current}
                  onChange={(event) => setDraft((value) => ({ ...value, [field.field_name]: event.target.value }))} />
              ) : (
                <input className="input" disabled={mutationDisabled} value={draft[field.field_name] ?? current}
                  onChange={(event) => setDraft((value) => ({ ...value, [field.field_name]: event.target.value }))} />
              )}
            </label>
            <details>
              <summary>Compare with previous value</summary>
              <div className="field-grid">
                <ReviewValueCard label="Before contributor changes" value={field.previous_value} />
                <ReviewValueCard label="Latest contributor value" value={field.current_value} />
              </div>
            </details>
            {current !== field.current_value ? (
              <p className="notice notice-warning">This field changed after the proposal. The editor shows its current value; review it before approving.</p>
            ) : null}
          </div>
        );
      })}
      <p className="muted">Reject cancels all pending story data changes and discards your unsaved edits. For a new story, submitted data is cleared and the story record is retained.</p>
      <div className="button-row wrap-row">
        <button className="button" disabled={mutationDisabled} type="button" onClick={() => void decide("approve")}>Approve</button>
        <button className="button button-danger" disabled={mutationDisabled} type="button" onClick={() => void decide("reject")}>Reject</button>
      </div>
    </section>
  );
}

function StoryReviewChangeCard({
  item,
  storyDetail,
  storyDetailLoading,
  busy,
  onBusyChange: setBusy,
  onRefresh,
  onNotice,
}: {
  item: ReviewItem;
  storyDetail: StoryDetail | null;
  storyDetailLoading: boolean;
  busy: boolean;
  onBusyChange: (busy: boolean) => void;
  onRefresh: (resolved: ReviewItem) => Promise<void>;
  onNotice: (notice: PageNotice | null) => void;
}) {
  const maintenance = useDatasetMaintenance();
  const fieldReview = parseStoryFieldReview(item);
  const tropeReview = parseStoryTropeReview(item);
  const keywordReview = parseStoryKeywordReview(item);
  const legacyStoryReview = isLegacyStoryReviewItem(item);
  const [editableDraft, setEditableDraft] = useState("");
  const [selectedExistingTerm, setSelectedExistingTerm] = useState<SearchItem | null>(null);
  const [similarQuery, setSimilarQuery] = useState("");
  const [similarResults, setSimilarResults] = useState<SearchItem[]>([]);
  const [similarStatus, setSimilarStatus] = useState<"idle" | "loading" | "ready" | "error">("idle");
  const termKind = fieldReview?.field_name === THEME_FIELD ? "theme" : tropeReview ? "trope" : keywordReview ? "keyword" : null;
  const mutationDisabled = busy || maintenance.active || storyDetailLoading || !storyDetail;

  const liveFieldValue = fieldReview && storyDetail ? storyDetail.fields[fieldReview.field_name] || "" : null;
  const editedTermId = readString(resolutionMetadata(item) ?? {}, "edited_term_id");
  const liveTermValue = tropeReview
    ? liveTropeText(storyDetail, tropeReview, editedTermId)
    : keywordReview
      ? liveKeywordText(storyDetail, keywordReview, editedTermId)
      : null;

  useEffect(() => {
    setSelectedExistingTerm(null);
    setSimilarQuery("");
  }, [item.id]);

  useEffect(() => {
    setSelectedExistingTerm(null);
    if (fieldReview) {
      const value = liveFieldValue ?? fieldReview.current_value;
      setEditableDraft(fieldReview.field_name === THEME_FIELD ? splitThemeText(value).join("\n") : value);
      setSimilarQuery(fieldReview.field_name === THEME_FIELD ? splitThemeText(value)[0] || "" : "");
      return;
    }
    if (tropeReview) {
      const value = liveTermValue ?? tropeReview.current_trope_text;
      setEditableDraft(value);
      setSimilarQuery(value);
      return;
    }
    if (keywordReview) {
      const value = liveTermValue ?? keywordReview.current_keyword_text;
      setEditableDraft(value);
      setSimilarQuery(value);
      return;
    }
    setEditableDraft("");
  }, [item.id, liveFieldValue, liveTermValue, fieldReview?.field_name, fieldReview?.current_value, tropeReview?.current_trope_text, keywordReview?.current_keyword_text]);

  useEffect(() => {
    setSimilarResults([]);
    if (!termKind || item.status !== "pending" || !similarQuery.trim()) {
      setSimilarStatus("idle");
      return;
    }
    let cancelled = false;
    setSimilarStatus("loading");
    const timer = window.setTimeout(() => {
      const search = termKind === "theme" ? searchThemes : termKind === "trope" ? searchTropes : searchKeywords;
      void search({ query: similarQuery.trim(), limit: 8, include_string_matches: true }).then((result) => {
        if (cancelled) return;
        const candidates = [...result.string_match_items, ...result.items];
        setSimilarResults(candidates.filter((candidate, index) => candidates.findIndex((other) => other.id === candidate.id) === index));
        setSimilarStatus("ready");
      }).catch(() => {
        if (!cancelled) setSimilarStatus("error");
      });
    }, 250);
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [termKind, item.status, similarQuery]);

  function changeDraft(value: string) {
    setEditableDraft(value);
    setSelectedExistingTerm(null);
    if (termKind !== "theme") setSimilarQuery(value);
  }

  function selectExistingTerm(candidate: SearchItem) {
    if (termKind === "theme") {
      setEditableDraft((current) => [...new Set([...splitThemeText(current), candidate.text])].join("\n"));
    } else {
      setEditableDraft(candidate.text);
      setSelectedExistingTerm(candidate);
    }
  }

  async function handleApprove() {
    if (termKind && canInlineEdit(item)) {
      await handleEditAndApprove();
      return;
    }
    try {
      setBusy(true);
      onNotice(null);
      const resolved = (tropeReview || keywordReview) && storyDetail
        ? await resolveStoryTermReview(storyDetail.id, item.id, "approve", { expected_story_version: storyDetail.version })
        : await approveReviewItem(item.id, undefined, false, storyDetail?.version);
      await onRefresh(resolved);
      onNotice({
        tone: "success",
        title: "Change approved",
        body: `${reviewHeadline(item)} was approved.`,
      });
    } catch (error) {
      onNotice({
        tone: "error",
        title: "Could not approve change",
        body: getErrorMessage(error),
      });
    } finally {
      setBusy(false);
    }
  }

  async function handleEditAndApprove(keepAsTyped = false) {
    if (mutationDisabled || !storyDetail || !canInlineEdit(item)) {
      return;
    }

    try {
      setBusy(true);
      onNotice(null);

      if ((tropeReview || keywordReview) && !editableDraft.trim()) {
        onNotice({ tone: "error", title: `Enter a ${termKind} before approving` });
        return;
      }
      const edit = {
        text: editableDraft,
        ...(!keepAsTyped && selectedExistingTerm ? { existing_term_id: selectedExistingTerm.id } : {}),
      };
      const resolved = tropeReview || keywordReview
        ? await resolveStoryTermReview(storyDetail.id, item.id, "approve", { expected_story_version: storyDetail.version, edit })
        : await approveReviewItem(item.id, undefined, true, storyDetail.version, edit);
      await onRefresh(resolved);
      onNotice({
        tone: "success",
        title: keepAsTyped ? "Typed text saved and approved"
          : resolutionMetadata(resolved)?.decision === "edited" ? "Change edited and approved" : "Change approved",
        body: keepAsTyped
          ? `Your ${termKind} text was saved on this story and the contributor’s change was approved.`
          : termKind ? `The ${termKind} was saved on this story and approved.` : `${reviewHeadline(item)} was updated and approved.`,
      });
    } catch (error) {
      onNotice({
        tone: "error",
        title: "Could not save the edited change",
        body: getErrorMessage(error),
      });
    } finally {
      setBusy(false);
    }
  }

  async function handleReject() {
    try {
      setBusy(true);
      onNotice(null);
      const resolved = (tropeReview || keywordReview) && storyDetail
        ? await resolveStoryTermReview(storyDetail.id, item.id, "reject", { expected_story_version: storyDetail.version })
        : await rejectReviewItem({ review_id: item.id });
      await onRefresh(resolved);
      onNotice({
        tone: "success",
        title: "Change rejected",
        body: `${reviewHeadline(item)} was rejected.`,
      });
    } catch (error) {
      onNotice({
        tone: "error",
        title: "Could not reject change",
        body: getErrorMessage(error),
      });
    } finally {
      setBusy(false);
    }
  }

  const liveFieldMismatch =
    fieldReview && liveFieldValue !== null && liveFieldValue !== fieldReview.current_value;
  const liveTropeMismatch =
    tropeReview &&
    !storyDetailLoading &&
    liveTermValue !== null &&
    liveTermValue !== tropeReview.current_trope_text;
  const liveKeywordMismatch =
    keywordReview &&
    !storyDetailLoading &&
    liveTermValue !== null &&
    liveTermValue !== keywordReview.current_keyword_text;

  return (
    <article className="card review-change-card" aria-label={termKind ? `${termKind[0].toUpperCase()}${termKind.slice(1)} review` : undefined}>
      <div className="panel-header">
        <div>
          <h3>{legacyStoryReview ? "Legacy story review item" : item.status !== "pending" && liveTermValue
            ? `${tropeReview ? "Trope" : "Keyword"}: ${liveTermValue}` : reviewHeadline(item)}</h3>
          <p className="muted">{item.created_at}</p>
        </div>
        <span className="pill" role="status">{reviewStatusLabel(item)}</span>
      </div>

      {legacyStoryReview ? (
        <section className="notice notice-warning">
          <strong className="notice-title">This review item does not contain per-change metadata</strong>
          <p>
            It was created by the older story-level review flow, so the frontend cannot show the exact modified field or offer field-level editing for this item.
          </p>
          <p>
            Mutation kind: {readString(item.metadata, "latest_mutation_kind") || "unknown"} · Review type: {item.review_type}
          </p>
          <p>
            Restart the backend, then recreate the contributor change if you want it to appear here as an editable field/trope/keyword change card.
          </p>
        </section>
      ) : null}

      {fieldReview ? (
        <div className="field-grid">
          <ReviewValueCard label="Previous value" value={fieldReview.previous_value} />
          <ReviewValueCard label="Contributor change" value={fieldReview.current_value} />
          {storyDetailLoading ? (
            <article className="card subdued">
              <span className="stat-label">Current story value</span>
              <p className="muted">Loading story context...</p>
            </article>
          ) : liveFieldValue !== null ? (
            <ReviewValueCard label="Current story value" value={liveFieldValue} />
          ) : null}
        </div>
      ) : null}

      {tropeReview ? (
        <div className="field-grid">
          {tropeReview.previous_trope_text ? <ReviewValueCard label="Previous trope" value={tropeReview.previous_trope_text} /> : null}
          {tropeReview.current_trope_text ? <ReviewValueCard label="Contributor trope" value={tropeReview.current_trope_text} /> : null}
          {storyDetailLoading ? (
            <article className="card subdued">
              <span className="stat-label">Current story trope</span>
              <p className="muted">Loading story context...</p>
            </article>
          ) : (
            <ReviewValueCard label="Current story trope" value={liveTermValue || "Missing from the current story"} />
          )}
        </div>
      ) : null}

      {keywordReview ? (
        <div className="field-grid">
          {keywordReview.previous_keyword_text ? <ReviewValueCard label="Previous keyword" value={keywordReview.previous_keyword_text} /> : null}
          {keywordReview.current_keyword_text ? <ReviewValueCard label="Contributor keyword" value={keywordReview.current_keyword_text} /> : null}
          {storyDetailLoading ? (
            <article className="card subdued">
              <span className="stat-label">Current story keyword</span>
              <p className="muted">Loading story context...</p>
            </article>
          ) : (
            <ReviewValueCard label="Current story keyword" value={liveTermValue || "Missing from the current story"} />
          )}
        </div>
      ) : null}

      {item.status === "pending" && liveFieldMismatch ? (
        <section className="notice notice-warning">
          <strong className="notice-title">The live story field no longer matches the recorded contributor change</strong>
          <p>Approve as-is, edit the current story value, or reject if you want to revert only this change.</p>
        </section>
      ) : null}

      {item.status === "pending" && liveTropeMismatch ? (
        <section className="notice notice-warning">
          <strong className="notice-title">The live story trope no longer matches the recorded contributor change</strong>
          <p>Save your edits with Approve, or reject if this change should be reverted.</p>
        </section>
      ) : null}

      {item.status === "pending" && liveKeywordMismatch ? (
        <section className="notice notice-warning">
          <strong className="notice-title">The live story keyword no longer matches the recorded contributor change</strong>
          <p>Save your edits with Approve, or reject if this change should be reverted.</p>
        </section>
      ) : null}

      {item.status === "pending" ? (
        <div className="page-stack">
          {canInlineEdit(item) ? (
            <label className="field">
              <span>{editableDraftLabel(item)}</span>
              {fieldReview && LONG_TEXT_FIELDS.has(fieldReview.field_name) ? (
                <textarea className="input input-textarea" disabled={mutationDisabled || storyDetailLoading} onChange={(event) => changeDraft(event.target.value)} value={editableDraft} />
              ) : (
                <input className="input" disabled={mutationDisabled || storyDetailLoading} onChange={(event) => changeDraft(event.target.value)} value={editableDraft} />
              )}
            </label>
          ) : null}

          {termKind && canInlineEdit(item) ? (
            <div className="stack">
              <div className="card subdued">
                <div className="button-row wrap-row">
                  <button
                    className="button"
                    disabled={mutationDisabled || !editableDraft.trim()}
                    onClick={() => void handleEditAndApprove(true)}
                    type="button"
                  >
                    {busy ? "Working..." : "Keep as typed"}
                  </button>
                </div>
                <p className="muted">Save the {termKind} text above and approve this change. New text creates new {termKind}s; matching existing items are reused.</p>
              </div>
              <label className="field">
                <span>Find similar existing {termKind}s</span>
                <input className="input" disabled={mutationDisabled} value={similarQuery} placeholder={`Search for an existing ${termKind}`} onChange={(event) => setSimilarQuery(event.target.value)} />
              </label>
              {similarStatus === "loading" ? <p className="muted" role="status">Searching similar {termKind}s...</p> : null}
              {similarStatus === "error" ? <p className="notice notice-error" role="alert">Could not search similar {termKind}s. Change the query to retry.</p> : null}
              {similarStatus === "ready" && !similarResults.length ? <p className="muted">No similar {termKind}s found.</p> : null}
              {similarResults.map((candidate) => (
                <TermCard key={candidate.id} term={candidate} meta={`Similarity ${candidate.score.toFixed(2)} · ${candidate.story_count} stories`} actions={
                  <button className="button button-ghost" type="button" disabled={mutationDisabled} onClick={() => selectExistingTerm(candidate)}>{termKind === "theme" ? "Add existing theme to draft" : selectedExistingTerm?.id === candidate.id ? "Selected" : `Use existing ${termKind}`}</button>
                } />
              ))}
              {termKind === "theme" ? <p className="muted">One theme per line. Remove an unwanted theme from the draft, then add its replacement from search.</p> : null}
            </div>
          ) : null}

          <div className="button-row wrap-row">
            <button className="button" disabled={mutationDisabled} onClick={() => void handleApprove()} type="button">
              {busy ? "Working..." : "Approve"}
            </button>
            {canInlineEdit(item) && !termKind ? (
              <button className="button button-ghost" disabled={mutationDisabled || storyDetailLoading} onClick={() => void handleEditAndApprove()} type="button">
                {busy ? "Working..." : "Edit and approve"}
              </button>
            ) : null}
            <button className="button button-danger" disabled={mutationDisabled} onClick={() => void handleReject()} type="button">
              {busy ? "Working..." : "Reject"}
            </button>
          </div>
        </div>
      ) : null}

    </article>
  );
}

export function AdminReviewPage() {
  const maintenance = useDatasetMaintenance();
  const [filterStatus, setFilterStatus] = useState<ReviewStatus | "all">("pending");
  const [stories, setStories] = useState<ReviewStory[]>([]);
  const [items, setItems] = useState<ReviewItem[]>([]);
  const [selectedStoryId, setSelectedStoryId] = useState<string | null>(null);
  const [storyDetail, setStoryDetail] = useState<StoryDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [storyDetailLoading, setStoryDetailLoading] = useState(false);
  const [mutationBusy, setMutationBusy] = useState(false);
  const [notice, setNotice] = useState<PageNotice | null>(null);
  const storyRequestId = useRef(0);
  const queueRequestId = useRef(0);

  const selectedStory = stories.find((story) => story.story_id === selectedStoryId) || null;
  const currentStory = storyDetail?.id === selectedStoryId ? storyDetail : null;
  const currentItems = currentStory ? items : [];

  async function loadStories(preferredStoryId?: string | null) {
    const requestId = ++queueRequestId.current;
    try {
      setLoading(true);
      const nextStories = await getReviewStories(filterStatus);
      if (requestId !== queueRequestId.current) return null;
      setStories(nextStories);
      const nextStoryId = preferredStoryId && nextStories.some((story) => story.story_id === preferredStoryId)
        ? preferredStoryId : nextStories[0]?.story_id || null;
      setSelectedStoryId(nextStoryId);
      return nextStoryId;
    } catch (error) {
      if (requestId !== queueRequestId.current) return null;
      setNotice({ tone: "error", title: "Could not load review queue", body: getErrorMessage(error) });
      return null;
    } finally {
      if (requestId === queueRequestId.current) setLoading(false);
    }
  }

  async function loadStoryDetail(storyId: string, refreshReviews = true) {
    const requestId = ++storyRequestId.current;
    try {
      setStoryDetailLoading(true);
      const [detail, storyReviews] = await Promise.all([
        getStory(storyId),
        refreshReviews ? getStoryReviewItems(storyId, filterStatus) : Promise.resolve(null),
      ]);
      if (requestId !== storyRequestId.current) return null;
      setStoryDetail(detail);
      if (storyReviews) setItems(storyReviews.sort(compareStoryItems));
      setStories((current) => current.map((story) => story.story_id === storyId ? {
        ...story, title: detail.fields["Story title (Eng)"] || "Untitled story",
        ...(storyReviews ? {
          item_count: storyReviews.length,
          pending_count: storyReviews.filter((item) => item.status === "pending").length,
        } : {}),
      } : story));
      return detail;
    } catch (error) {
      if (requestId !== storyRequestId.current) return null;
      setNotice({ tone: "error", title: "Could not load story context", body: getErrorMessage(error) });
      setStoryDetail(null);
      return null;
    } finally {
      if (requestId === storyRequestId.current) setStoryDetailLoading(false);
    }
  }

  async function refresh() {
    setNotice(null);
    const nextStoryId = await loadStories(selectedStoryId);
    if (!nextStoryId) {
      setStoryDetail(null);
      setItems([]);
    } else if (nextStoryId === selectedStoryId) {
      await loadStoryDetail(nextStoryId);
    }
  }

  useEffect(() => {
    storyRequestId.current += 1;
    setStoryDetail(null);
    setItems([]);
    void refresh();
    return () => { queueRequestId.current += 1; storyRequestId.current += 1; };
  }, [filterStatus]);

  useEffect(() => {
    setStoryDetail(null);
    setItems([]);
    if (!selectedStoryId) {
      setStoryDetailLoading(false);
      return;
    }
    void loadStoryDetail(selectedStoryId);
    return () => { storyRequestId.current += 1; };
  }, [selectedStoryId]);

  async function refreshReviewItem(resolved: ReviewItem) {
    await refreshResolvedItems([resolved]);
  }

  async function refreshResolvedItems(resolved: ReviewItem[]) {
    if (!selectedStory) return;
    // Reload related decisions too: saving one term can resolve its catalogue review.
    const [detail, nextStories, allReviews] = await Promise.all([
      loadStoryDetail(selectedStory.story_id, false),
      getReviewStories(filterStatus),
      getStoryReviewItems(selectedStory.story_id, "all"),
    ]);
    const visibleIds = new Set([...items, ...resolved].map((item) => item.id));
    const nextItems = allReviews.filter((item) => filterStatus === "all" || item.status === filterStatus || visibleIds.has(item.id))
      .sort(compareStoryItems);
    setItems(nextItems);
    const updatedStory = {
      ...selectedStory,
      title: detail ? detail.fields["Story title (Eng)"] || "Untitled story" : selectedStory.title,
      item_count: nextItems.length,
      pending_count: nextItems.filter((item) => item.status === "pending").length,
    };
    // Keep the completed story visible until the admin chooses another story or refreshes.
    setStories(nextStories.some((story) => story.story_id === updatedStory.story_id)
      ? nextStories.map((story) => story.story_id === updatedStory.story_id ? updatedStory : story)
      : [updatedStory, ...nextStories]);
  }

  async function handleAddStoryItem(kind: StoryItemKind, input: StoryItemInput): Promise<boolean> {
    if (mutationBusy || loading || storyDetailLoading || maintenance.active || !storyDetail ||
        !selectedStory || storyDetail.id !== selectedStory.story_id) {
      return false;
    }
    setMutationBusy(true);
    setNotice(null);
    const storyId = storyDetail.id;
    try {
      const payload = { expected_story_version: storyDetail.version };
      if (kind === "theme") {
        await addStoryTheme(storyId, { ...payload, ...("id" in input ? { theme_id: input.id } : input) });
      } else if (kind === "trope") {
        await addStoryTrope(storyId, { ...payload, ...("id" in input ? { trope_id: input.id } : input) });
      } else {
        await addStoryKeyword(storyId, { ...payload, ...("id" in input ? { keyword_id: input.id } : input) });
      }
      const refreshed = await loadStoryDetail(storyId);
      setNotice({
        tone: refreshed ? "success" : "error",
        title: `${kind[0].toUpperCase()}${kind.slice(1)} added`,
        body: refreshed ? "The story now includes the item." : "The item was saved, but the story could not be reloaded. Refresh to see the latest version.",
      });
      return true;
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        const refreshed = await loadStoryDetail(storyId);
        setNotice({
          tone: "error",
          title: "Story updated elsewhere",
          body: refreshed
            ? "The latest story has been reloaded. Review its items and try again."
            : "The latest story could not be reloaded. Refresh before trying again.",
        });
      } else {
        setNotice({ tone: "error", title: `Could not add ${kind}`, body: getErrorMessage(error) });
      }
      return false;
    } finally {
      setMutationBusy(false);
    }
  }

  return (
    <div className="page-stack">
      <section className="panel">
        <div className="panel-header">
          <div>
            <h1>Review queue</h1>
            <p className="muted">Choose a story to review its data, themes, tropes and keywords. Approve or reject its data together; review its terms separately.</p>
          </div>
          <div className="button-row wrap-row">
            <label className="field">
              <span>Status</span>
              <select className="input" disabled={loading || mutationBusy} onChange={(event) => setFilterStatus(event.target.value as ReviewStatus | "all")} value={filterStatus}>
                <option value="pending">Pending</option>
                <option value="approved">Approved</option>
                <option value="rejected">Rejected</option>
                <option value="all">All</option>
              </select>
            </label>
            <button className="button button-ghost" disabled={loading || storyDetailLoading || mutationBusy} onClick={() => void refresh()} type="button">
              Refresh
            </button>
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
        <aside className="panel">
          <div className="panel-header">
            <h2>Stories</h2>
            <span className="pill">{stories.length}</span>
          </div>
          <div className="list">
            {loading ? <p className="muted">Loading stories...</p> : null}
            {!loading && stories.length === 0 ? <p className="muted">No stories match this filter.</p> : null}
            {stories.map((story) => (
              <button
                className={`list-row ${story.story_id === selectedStoryId ? "list-row-active" : ""}`}
                disabled={loading || mutationBusy}
                key={story.story_id}
                onClick={() => { setNotice(null); setSelectedStoryId(story.story_id); }}
                type="button"
              >
                <strong>{story.title}</strong>
                <span className="muted">{story.item_count} review{story.item_count === 1 ? "" : "s"} · Source row {story.source_row_number ?? "n/a"}</span>
                <span className="muted">{story.pending_count} pending · {story.item_count - story.pending_count} resolved</span>
              </button>
            ))}
          </div>
        </aside>

        <div className="page-stack">
          {!selectedStory ? (
            <section className="panel">
              <p className="muted">Choose a story to review all its items.</p>
            </section>
          ) : null}

          {selectedStory ? (
            <section className="panel">
              <div className="panel-header">
                <div>
                  <h2>{selectedStory.title}</h2>
                  <p className="muted">
                    Source row {selectedStory.source_row_number ?? "n/a"} · {currentItems.length} review{currentItems.length === 1 ? "" : "s"}
                  </p>
                </div>
                <span className="pill">{selectedStory.pending_count} pending</span>
              </div>

              {storyDetailLoading ? <p className="muted">Loading current story context...</p> : null}

              <div className="field-grid">
                <article className="card subdued">
                  <span className="stat-label">Story</span>
                  <strong>{selectedStory.title}</strong>
                  <p className="muted">{selectedStory.story_id}</p>
                </article>
                <article className="card subdued">
                  <span className="stat-label">Current story version</span>
                  <strong>{storyDetail?.id === selectedStory.story_id ? storyDetail.version : "Loading..."}</strong>
                  <p className="muted">Latest saved story state</p>
                </article>
              </div>

              <StoryReviewItemAdder
                key={selectedStory.story_id}
                story={currentStory}
                disabled={loading || storyDetailLoading || mutationBusy || maintenance.active}
                onAdd={handleAddStoryItem}
              />

              <StoryDataReviewCard
                key={`data:${selectedStory.story_id}`}
                items={currentItems}
                story={currentStory}
                disabled={loading || storyDetailLoading || mutationBusy || maintenance.active}
                onBusyChange={setMutationBusy}
                onResolved={refreshResolvedItems}
                onNotice={setNotice}
              />

              <div className="page-stack">
                {currentItems.filter((item) => item.status !== "pending" || !isStoryDataReview(item)).map((item) => (
                  <StoryReviewChangeCard
                    item={item}
                    key={item.id}
                    storyDetail={currentStory}
                    storyDetailLoading={storyDetailLoading}
                    busy={mutationBusy || loading}
                    onBusyChange={setMutationBusy}
                    onNotice={setNotice}
                    onRefresh={refreshReviewItem}
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
