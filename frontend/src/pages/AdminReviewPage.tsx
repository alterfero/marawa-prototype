import { useEffect, useState } from "react";

import {
  approveReviewItem,
  getErrorMessage,
  getReviewItems,
  getStory,
  rejectReviewItem,
  replaceStoryKeyword,
  replaceStoryTrope,
  searchKeywords,
  searchTropes,
  updateStory,
} from "../api/client";
import type { ReviewItem, ReviewStatus, SearchItem, StoryDetail } from "../api/types";
import { TermCard } from "../components/TermCard";
import { LONG_TEXT_FIELDS } from "../constants/csv";

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

interface StoryReviewGroup {
  kind: "story";
  id: string;
  story_id: string;
  title: string;
  source_row_number: number | null;
  items: ReviewItem[];
  pending_count: number;
  latest_created_at: string;
}

interface TermReviewSelection {
  kind: "term";
  id: string;
  item: ReviewItem;
  latest_created_at: string;
}

type ReviewSelection = StoryReviewGroup | TermReviewSelection;

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

function storyReviewSourceRow(item: ReviewItem): number | null {
  return readNumber(item.metadata, "source_row_number") ?? item.subject_preview?.source_row_number ?? null;
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

function parseStoryTropeReview(item: ReviewItem | null): StoryTropeReviewMetadata | null {
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

function isPendingTerm(item: ReviewItem): boolean {
  return item.review_type === "trope_pending" || item.review_type === "keyword_pending";
}

function reviewStatusLabel(item: ReviewItem): string {
  return item.status.replace(/_/g, " ");
}

function reviewHeadline(item: ReviewItem): string {
  const fieldReview = parseStoryFieldReview(item);
  if (fieldReview) {
    return fieldReview.field_name;
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

  if (item.subject_table === "tropes") {
    return item.subject_preview?.text || readString(item.metadata, "text") || item.subject_id;
  }
  if (item.subject_table === "keywords") {
    return item.subject_preview?.text || readString(item.metadata, "text") || item.subject_id;
  }
  return storyReviewTitle(item);
}

function reviewSubhead(item: ReviewItem): string {
  if (item.subject_table === "stories") {
    return `${item.review_type === "story_created" ? "Story submission" : "Story update"} · ${storyReviewTitle(item)}`;
  }
  if (item.review_type === "trope_pending") {
    return "Pending canonical trope";
  }
  return "Pending canonical keyword";
}

function liveTropeText(detail: StoryDetail | null, review: StoryTropeReviewMetadata): string | null {
  if (!detail) {
    return null;
  }
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

function liveKeywordText(detail: StoryDetail | null, review: StoryKeywordReviewMetadata): string | null {
  if (!detail) {
    return null;
  }
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
    return fieldReview.field_name;
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

function buildReviewSelections(items: ReviewItem[]): ReviewSelection[] {
  const storyItemsById = new Map<string, ReviewItem[]>();
  const selections: ReviewSelection[] = [];

  items.forEach((item) => {
    if (item.subject_table === "stories") {
      const group = storyItemsById.get(item.subject_id);
      if (group) {
        group.push(item);
      } else {
        storyItemsById.set(item.subject_id, [item]);
      }
      return;
    }

    selections.push({
      kind: "term",
      id: `item:${item.id}`,
      item,
      latest_created_at: item.created_at,
    });
  });

  storyItemsById.forEach((storyItems, storyId) => {
    const sortedItems = [...storyItems].sort(compareStoryItems);
    selections.push({
      kind: "story",
      id: `story:${storyId}`,
      story_id: storyId,
      title: storyReviewTitle(sortedItems[0]),
      source_row_number: storyReviewSourceRow(sortedItems[0]),
      items: sortedItems,
      pending_count: sortedItems.filter((item) => item.status === "pending").length,
      latest_created_at: sortedItems.reduce((latest, item) => {
        return itemCreatedAtTimestamp(item) > Date.parse(latest) ? item.created_at : latest;
      }, sortedItems[0].created_at),
    });
  });

  return selections.sort((left, right) => {
    const leftPending = left.kind === "story" ? left.pending_count > 0 : left.item.status === "pending";
    const rightPending = right.kind === "story" ? right.pending_count > 0 : right.item.status === "pending";
    if (leftPending !== rightPending) {
      return leftPending ? -1 : 1;
    }
    const dateDelta = Date.parse(right.latest_created_at) - Date.parse(left.latest_created_at);
    if (dateDelta !== 0) {
      return dateDelta;
    }
    const leftTitle = left.kind === "story" ? left.title : reviewHeadline(left.item);
    const rightTitle = right.kind === "story" ? right.title : reviewHeadline(right.item);
    return leftTitle.localeCompare(rightTitle);
  });
}

function ReviewValueCard({ label, value }: { label: string; value: string }) {
  return (
    <article className="card subdued">
      <span className="stat-label">{label}</span>
      <p className="review-value">{formatValue(value)}</p>
    </article>
  );
}

function StoryReviewChangeCard({
  item,
  storyDetail,
  storyDetailLoading,
  onRefresh,
  onNotice,
}: {
  item: ReviewItem;
  storyDetail: StoryDetail | null;
  storyDetailLoading: boolean;
  onRefresh: () => Promise<void>;
  onNotice: (notice: PageNotice | null) => void;
}) {
  const fieldReview = parseStoryFieldReview(item);
  const tropeReview = parseStoryTropeReview(item);
  const keywordReview = parseStoryKeywordReview(item);
  const legacyStoryReview = isLegacyStoryReviewItem(item);
  const [decisionNote, setDecisionNote] = useState("");
  const [editableDraft, setEditableDraft] = useState("");
  const [busy, setBusy] = useState(false);

  const liveFieldValue = fieldReview && storyDetail ? storyDetail.fields[fieldReview.field_name] || "" : null;
  const liveTermValue = tropeReview
    ? liveTropeText(storyDetail, tropeReview)
    : keywordReview
      ? liveKeywordText(storyDetail, keywordReview)
      : null;

  useEffect(() => {
    setDecisionNote("");
  }, [item.id]);

  useEffect(() => {
    if (fieldReview) {
      setEditableDraft(storyDetail?.fields[fieldReview.field_name] ?? fieldReview.current_value);
      return;
    }
    if (tropeReview) {
      setEditableDraft(liveTropeText(storyDetail, tropeReview) ?? tropeReview.current_trope_text);
      return;
    }
    if (keywordReview) {
      setEditableDraft(liveKeywordText(storyDetail, keywordReview) ?? keywordReview.current_keyword_text);
      return;
    }
    setEditableDraft("");
  }, [item.id, storyDetail, fieldReview?.field_name, fieldReview?.current_value, tropeReview?.current_trope_text, keywordReview?.current_keyword_text]);

  async function handleApprove() {
    try {
      setBusy(true);
      onNotice(null);
      await approveReviewItem(item.id, decisionNote);
      await onRefresh();
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

  async function handleEditAndApprove() {
    if (!storyDetail || !canInlineEdit(item)) {
      return;
    }

    try {
      setBusy(true);
      onNotice(null);

      if (fieldReview) {
        const currentValue = storyDetail.fields[fieldReview.field_name] || "";
        if (editableDraft !== currentValue) {
          await updateStory({
            story_id: storyDetail.id,
            expected_story_version: storyDetail.version,
            fields: {
              [fieldReview.field_name]: editableDraft,
            },
          });
        }
      } else if (tropeReview) {
        const nextValue = editableDraft.trim();
        if (!nextValue) {
          onNotice({
            tone: "error",
            title: "Enter a trope before approving",
          });
          return;
        }
        if (!storyDetail.tropes.some((trope) => trope.id === tropeReview.current_trope_id)) {
          onNotice({
            tone: "error",
            title: "Current trope is no longer on the story",
          });
          return;
        }
        const currentValue = liveTropeText(storyDetail, tropeReview) ?? tropeReview.current_trope_text;
        if (nextValue !== currentValue) {
          await replaceStoryTrope(storyDetail.id, tropeReview.current_trope_id, {
            expected_story_version: storyDetail.version,
            text: nextValue,
          });
        }
      } else if (keywordReview) {
        const nextValue = editableDraft.trim();
        if (!nextValue) {
          onNotice({
            tone: "error",
            title: "Enter a keyword before approving",
          });
          return;
        }
        if (!storyDetail.keywords.some((keyword) => keyword.id === keywordReview.current_keyword_id)) {
          onNotice({
            tone: "error",
            title: "Current keyword is no longer on the story",
          });
          return;
        }
        const currentValue = liveKeywordText(storyDetail, keywordReview) ?? keywordReview.current_keyword_text;
        if (nextValue !== currentValue) {
          await replaceStoryKeyword(storyDetail.id, keywordReview.current_keyword_id, {
            expected_story_version: storyDetail.version,
            text: nextValue,
          });
        }
      }

      await approveReviewItem(item.id, decisionNote);
      await onRefresh();
      onNotice({
        tone: "success",
        title: "Change edited and approved",
        body: `${reviewHeadline(item)} was updated and approved.`,
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
      await rejectReviewItem({
        review_id: item.id,
        note: decisionNote,
      });
      await onRefresh();
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
    <article className="card review-change-card">
      <div className="panel-header">
        <div>
          <h3>{legacyStoryReview ? "Legacy story review item" : reviewHeadline(item)}</h3>
          <p className="muted">{item.created_at}</p>
        </div>
        <span className="pill">{reviewStatusLabel(item)}</span>
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

      {liveFieldMismatch ? (
        <section className="notice notice-warning">
          <strong className="notice-title">The live story field no longer matches the recorded contributor change</strong>
          <p>Approve as-is, edit the current story value, or reject if you want to revert only this change.</p>
        </section>
      ) : null}

      {liveTropeMismatch ? (
        <section className="notice notice-warning">
          <strong className="notice-title">The live story trope no longer matches the recorded contributor change</strong>
          <p>Edit and approve to keep the current story accurate, or reject if this change should be reverted.</p>
        </section>
      ) : null}

      {liveKeywordMismatch ? (
        <section className="notice notice-warning">
          <strong className="notice-title">The live story keyword no longer matches the recorded contributor change</strong>
          <p>Edit and approve to keep the current story accurate, or reject if this change should be reverted.</p>
        </section>
      ) : null}

      {item.status === "pending" ? (
        <div className="page-stack">
          {canInlineEdit(item) ? (
            <label className="field">
              <span>{editableDraftLabel(item)}</span>
              {fieldReview && LONG_TEXT_FIELDS.has(fieldReview.field_name) ? (
                <textarea className="input input-textarea" disabled={busy || storyDetailLoading} onChange={(event) => setEditableDraft(event.target.value)} value={editableDraft} />
              ) : (
                <input className="input" disabled={busy || storyDetailLoading} onChange={(event) => setEditableDraft(event.target.value)} value={editableDraft} />
              )}
            </label>
          ) : null}

          <label className="field">
            <span>Resolution note</span>
            <textarea className="input input-textarea" disabled={busy} onChange={(event) => setDecisionNote(event.target.value)} placeholder="Optional admin note" value={decisionNote} />
          </label>

          <div className="button-row wrap-row">
            <button className="button" disabled={busy} onClick={() => void handleApprove()} type="button">
              {busy ? "Working..." : "Approve"}
            </button>
            {canInlineEdit(item) ? (
              <button className="button button-ghost" disabled={busy || storyDetailLoading} onClick={() => void handleEditAndApprove()} type="button">
                {busy ? "Working..." : "Edit and approve"}
              </button>
            ) : null}
            <button className="button button-danger" disabled={busy} onClick={() => void handleReject()} type="button">
              {busy ? "Working..." : "Reject"}
            </button>
          </div>
        </div>
      ) : null}

      {item.status !== "pending" && resolutionMetadata(item) ? (
        <article className="card subdued">
          <span className="stat-label">Resolution</span>
          <pre className="json-block">{JSON.stringify(resolutionMetadata(item), null, 2)}</pre>
        </article>
      ) : null}
    </article>
  );
}

function TermReviewPanel({
  item,
  onRefresh,
  onNotice,
}: {
  item: ReviewItem;
  onRefresh: () => Promise<void>;
  onNotice: (notice: PageNotice | null) => void;
}) {
  const [decisionNote, setDecisionNote] = useState("");
  const [mergeQuery, setMergeQuery] = useState("");
  const [mergeResults, setMergeResults] = useState<SearchItem[]>([]);
  const [mergeSearchStatus, setMergeSearchStatus] = useState<"idle" | "loading" | "ready">("idle");
  const [mergeTargetId, setMergeTargetId] = useState<string | null>(null);
  const [removeFromAllStories, setRemoveFromAllStories] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setDecisionNote("");
    setMergeQuery("");
    setMergeResults([]);
    setMergeSearchStatus("idle");
    setMergeTargetId(null);
    setRemoveFromAllStories(false);
  }, [item.id]);

  useEffect(() => {
    if (!isPendingTerm(item)) {
      setMergeResults([]);
      setMergeSearchStatus("idle");
      return;
    }

    const trimmedQuery = mergeQuery.trim();
    if (!trimmedQuery) {
      setMergeResults([]);
      setMergeSearchStatus("idle");
      return;
    }

    let cancelled = false;
    const timeoutId = window.setTimeout(() => {
      void (async () => {
        try {
          setMergeSearchStatus("loading");
          const result =
            item.review_type === "trope_pending"
              ? await searchTropes({ query: trimmedQuery, limit: 8 })
              : await searchKeywords({ query: trimmedQuery, limit: 8 });
          if (cancelled) {
            return;
          }
          setMergeResults(result.items.filter((resultItem) => resultItem.id !== item.subject_id));
          setMergeSearchStatus("ready");
        } catch (error) {
          if (cancelled) {
            return;
          }
          setMergeResults([]);
          setMergeSearchStatus("ready");
          onNotice({
            tone: "error",
            title: "Could not search merge targets",
            body: getErrorMessage(error),
          });
        }
      })();
    }, 250);

    return () => {
      cancelled = true;
      window.clearTimeout(timeoutId);
    };
  }, [item.id, item.review_type, item.subject_id, mergeQuery, onNotice]);

  async function handleApprove() {
    try {
      setBusy(true);
      onNotice(null);
      await approveReviewItem(item.id, decisionNote);
      await onRefresh();
      onNotice({
        tone: "success",
        title: "Review approved",
        body: `${reviewHeadline(item)} was approved.`,
      });
    } catch (error) {
      onNotice({
        tone: "error",
        title: "Could not approve review",
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
      await rejectReviewItem({
        review_id: item.id,
        note: decisionNote,
        merge_target_id: mergeTargetId,
        remove_from_all_stories: removeFromAllStories,
      });
      await onRefresh();
      onNotice({
        tone: "success",
        title: "Review rejected",
        body: `${reviewHeadline(item)} was rejected.`,
      });
    } catch (error) {
      onNotice({
        tone: "error",
        title: "Could not reject review",
        body: getErrorMessage(error),
      });
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="panel">
      <div className="panel-header">
        <div>
          <h2>{reviewHeadline(item)}</h2>
          <p className="muted">{reviewSubhead(item)}</p>
        </div>
        <span className="pill">{reviewStatusLabel(item)}</span>
      </div>

      <div className="field-grid">
        <article className="card subdued">
          <span className="stat-label">Created</span>
          <strong>{item.created_at}</strong>
          <p className="muted">By {item.created_by_user_id || "unknown user"}</p>
        </article>
        <article className="card subdued">
          <span className="stat-label">Current subject</span>
          <strong>{item.subject_preview?.text || item.subject_preview?.title || item.subject_id}</strong>
          {item.subject_preview?.story_count !== undefined ? <p className="muted">{item.subject_preview.story_count} stories</p> : null}
        </article>
      </div>

      {item.status === "pending" ? (
        <div className="page-stack">
          <label className="field">
            <span>Resolution note</span>
            <textarea className="input input-textarea" disabled={busy} onChange={(event) => setDecisionNote(event.target.value)} placeholder="Optional admin note" value={decisionNote} />
          </label>

          {isPendingTerm(item) ? (
            <article className="card subdued">
              <div className="stack">
                <div className="panel-header">
                  <h3>Reject pending term</h3>
                </div>
                <label className="field">
                  <span>Merge into an approved existing term</span>
                  <input
                    className="input"
                    disabled={busy || removeFromAllStories}
                    onChange={(event) => {
                      setMergeQuery(event.target.value);
                      if (!event.target.value.trim()) {
                        setMergeTargetId(null);
                      }
                    }}
                    placeholder={item.review_type === "trope_pending" ? "Search canonical tropes" : "Search canonical keywords"}
                    value={mergeQuery}
                  />
                </label>

                {mergeQuery.trim() && mergeSearchStatus === "loading" ? <p className="muted">Searching existing terms...</p> : null}
                {mergeQuery.trim() && mergeSearchStatus === "ready" && mergeResults.length === 0 ? <p className="muted">No merge targets matched the current query.</p> : null}

                {mergeResults.length ? (
                  <div className="stack">
                    {mergeResults.map((resultItem) => (
                      <TermCard
                        key={resultItem.id}
                        meta={`${resultItem.story_count} stories`}
                        term={resultItem}
                        actions={
                          <button
                            className="button button-ghost"
                            disabled={busy}
                            onClick={() => {
                              setMergeTargetId(resultItem.id);
                              setMergeQuery(resultItem.text);
                              setRemoveFromAllStories(false);
                            }}
                            type="button"
                          >
                            {mergeTargetId === resultItem.id ? "Selected" : "Use merge target"}
                          </button>
                        }
                      />
                    ))}
                  </div>
                ) : null}

                <label className="checkbox-row">
                  <input
                    checked={removeFromAllStories}
                    disabled={busy}
                    onChange={(event) => {
                      setRemoveFromAllStories(event.target.checked);
                      if (event.target.checked) {
                        setMergeTargetId(null);
                      }
                    }}
                    type="checkbox"
                  />
                  <span>Remove this term from all stories and delete it instead of merging</span>
                </label>
              </div>
            </article>
          ) : null}

          <div className="button-row wrap-row">
            <button className="button" disabled={busy} onClick={() => void handleApprove()} type="button">
              {busy ? "Working..." : "Approve"}
            </button>
            <button className="button button-danger" disabled={busy || (isPendingTerm(item) && !mergeTargetId && !removeFromAllStories)} onClick={() => void handleReject()} type="button">
              {busy ? "Working..." : "Reject"}
            </button>
          </div>
        </div>
      ) : null}

      {item.status !== "pending" && resolutionMetadata(item) ? (
        <article className="card subdued">
          <span className="stat-label">Resolution</span>
          <pre className="json-block">{JSON.stringify(resolutionMetadata(item), null, 2)}</pre>
        </article>
      ) : null}
    </section>
  );
}

export function AdminReviewPage() {
  const [filterStatus, setFilterStatus] = useState<ReviewStatus | "all">("pending");
  const [items, setItems] = useState<ReviewItem[]>([]);
  const [selectedSelectionId, setSelectedSelectionId] = useState<string | null>(null);
  const [storyDetail, setStoryDetail] = useState<StoryDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [storyDetailLoading, setStoryDetailLoading] = useState(false);
  const [notice, setNotice] = useState<PageNotice | null>(null);

  const selections = buildReviewSelections(items);
  const selectedSelection = selections.find((selection) => selection.id === selectedSelectionId) || null;

  async function loadItems(preferredSelectionId?: string | null) {
    try {
      setLoading(true);
      const nextItems = await getReviewItems({ status: filterStatus, limit: 200 });
      setItems(nextItems);
      const nextSelections = buildReviewSelections(nextItems);
      const nextSelectionId =
        preferredSelectionId && nextSelections.some((selection) => selection.id === preferredSelectionId)
          ? preferredSelectionId
          : nextSelections[0]?.id || null;
      setSelectedSelectionId(nextSelectionId);
      return nextSelectionId;
    } catch (error) {
      setNotice({
        tone: "error",
        title: "Could not load review queue",
        body: getErrorMessage(error),
      });
      return null;
    } finally {
      setLoading(false);
    }
  }

  async function loadStoryDetail(storyId: string) {
    try {
      setStoryDetailLoading(true);
      const detail = await getStory(storyId);
      setStoryDetail(detail);
      return detail;
    } catch (error) {
      setNotice({
        tone: "error",
        title: "Could not load story context",
        body: getErrorMessage(error),
      });
      setStoryDetail(null);
      return null;
    } finally {
      setStoryDetailLoading(false);
    }
  }

  async function refresh(preferredSelectionId?: string | null) {
    const nextSelectionId = await loadItems(preferredSelectionId ?? selectedSelectionId);
    if (!nextSelectionId) {
      setStoryDetail(null);
    }
  }

  useEffect(() => {
    void refresh();
  }, [filterStatus]);

  useEffect(() => {
    if (!selectedSelection || selectedSelection.kind !== "story") {
      setStoryDetail(null);
      return;
    }
    void loadStoryDetail(selectedSelection.story_id);
  }, [selectedSelection?.id]);

  function selectionTitle(selection: ReviewSelection): string {
    if (selection.kind === "story") {
      return selection.title;
    }
    return reviewHeadline(selection.item);
  }

  function selectionSubhead(selection: ReviewSelection): string {
    if (selection.kind === "story") {
      return `${selection.items.length} change${selection.items.length === 1 ? "" : "s"} · Source row ${selection.source_row_number ?? "n/a"}`;
    }
    return reviewSubhead(selection.item);
  }

  async function refreshSelectedSelection() {
    await refresh(selectedSelection?.id || null);
  }

  return (
    <div className="page-stack">
      <section className="panel">
        <div className="panel-header">
          <div>
            <h1>Review queue</h1>
            <p className="muted">Choose a story on the left, then review each contributor change separately on the right.</p>
          </div>
          <div className="button-row wrap-row">
            <label className="field">
              <span>Status</span>
              <select className="input" onChange={(event) => setFilterStatus(event.target.value as ReviewStatus | "all")} value={filterStatus}>
                <option value="pending">Pending</option>
                <option value="approved">Approved</option>
                <option value="rejected">Rejected</option>
                <option value="all">All</option>
              </select>
            </label>
            <button className="button button-ghost" disabled={loading || storyDetailLoading} onClick={() => void refresh()} type="button">
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
            <span className="pill">{selections.length}</span>
          </div>
          <div className="list">
            {loading ? <p className="muted">Loading review targets...</p> : null}
            {!loading && selections.length === 0 ? <p className="muted">No review items match this filter.</p> : null}
            {selections.map((selection) => (
              <button
                className={`list-row ${selection.id === selectedSelectionId ? "list-row-active" : ""}`}
                key={selection.id}
                onClick={() => setSelectedSelectionId(selection.id)}
                type="button"
              >
                <strong>{selectionTitle(selection)}</strong>
                <span className="muted">{selectionSubhead(selection)}</span>
                {selection.kind === "story" ? (
                  <span className="muted">
                    {selection.pending_count} pending · {selection.items.length - selection.pending_count} resolved
                  </span>
                ) : (
                  <span className="muted">{reviewStatusLabel(selection.item)}</span>
                )}
              </button>
            ))}
          </div>
        </aside>

        <div className="page-stack">
          {!selectedSelection ? (
            <section className="panel">
              <p className="muted">Choose a story or pending canonical term to inspect it.</p>
            </section>
          ) : null}

          {selectedSelection?.kind === "story" ? (
            <section className="panel">
              <div className="panel-header">
                <div>
                  <h2>{selectedSelection.title}</h2>
                  <p className="muted">
                    Source row {selectedSelection.source_row_number ?? "n/a"} · {selectedSelection.items.length} change{selectedSelection.items.length === 1 ? "" : "s"}
                  </p>
                </div>
                <span className="pill">{selectedSelection.pending_count} pending</span>
              </div>

              {storyDetailLoading ? <p className="muted">Loading current story context...</p> : null}

              <div className="field-grid">
                <article className="card subdued">
                  <span className="stat-label">Story</span>
                  <strong>{selectedSelection.title}</strong>
                  <p className="muted">{selectedSelection.story_id}</p>
                </article>
                <article className="card subdued">
                  <span className="stat-label">Current story version</span>
                  <strong>{storyDetail?.version ?? "Loading..."}</strong>
                  <p className="muted">Latest saved story state</p>
                </article>
              </div>

              <div className="page-stack">
                {selectedSelection.items.map((item) => (
                  <StoryReviewChangeCard
                    item={item}
                    key={item.id}
                    storyDetail={storyDetail}
                    storyDetailLoading={storyDetailLoading}
                    onNotice={setNotice}
                    onRefresh={refreshSelectedSelection}
                  />
                ))}
              </div>
            </section>
          ) : null}

          {selectedSelection?.kind === "term" ? (
            <TermReviewPanel item={selectedSelection.item} onNotice={setNotice} onRefresh={refreshSelectedSelection} />
          ) : null}
        </div>
      </section>
    </div>
  );
}
