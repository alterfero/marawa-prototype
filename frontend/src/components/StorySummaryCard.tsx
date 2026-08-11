import type { StorySummary } from "../api/types";

function completenessBadgeClassName(completeness: StorySummary["completeness"]): string {
  return `story-completeness-${completeness.replace(/\s+/g, "-")}`;
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

export function StorySummaryCard({
  story,
  active = false,
  disabled = false,
  pinned = false,
  onClick,
  onPinToggle,
}: {
  story: StorySummary;
  active?: boolean;
  disabled?: boolean;
  pinned?: boolean;
  onClick: () => void;
  onPinToggle: () => void;
}) {
  return (
    <div
      className={`list-row story-browser-row ${active ? "list-row-active" : ""}`.trim()}
    >
      <button
        className="story-browser-card-select"
        disabled={disabled}
        onClick={onClick}
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
      <button
        aria-label={pinned ? "Unpin story" : "Pin story"}
        aria-pressed={pinned}
        className={`story-pin-button ${pinned ? "story-pin-button-active" : ""}`.trim()}
        disabled={disabled}
        onClick={onPinToggle}
        title={pinned ? "Unpin story" : "Pin story"}
        type="button"
      >
        <svg aria-hidden="true" fill={pinned ? "currentColor" : "none"} viewBox="0 0 24 24">
          <path d="M15 3 9 3l1.2 6.2-3.7 3.7v1.6h10.9v-1.6l-3.7-3.7L15 3Z" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.8" />
          <path d="M12 14.5V21" stroke="currentColor" strokeLinecap="round" strokeWidth="1.8" />
        </svg>
      </button>
    </div>
  );
}
