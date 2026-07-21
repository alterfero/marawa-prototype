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
  onClick,
}: {
  story: StorySummary;
  active?: boolean;
  disabled?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      className={`list-row story-browser-row ${active ? "list-row-active" : ""}`.trim()}
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
  );
}
