import type { KeyboardEvent, ReactNode } from "react";

import type { TropeReference } from "../api/types";
import { useTropeBrowser } from "./TropeBrowser";

interface TropeCardProps {
  trope: TropeReference;
  meta?: string;
  badge?: ReactNode;
  actions?: ReactNode;
  compact?: boolean;
  className?: string;
  minimumStoryCount?: number;
  children?: ReactNode;
  onOpen?: (trope: TropeReference) => void;
}

export function TropeCard({
  trope,
  meta,
  badge,
  actions,
  compact = false,
  className = "",
  minimumStoryCount = 1,
  children,
  onOpen,
}: TropeCardProps) {
  const { openTrope } = useTropeBrowser();
  const storyCount = Math.max(typeof trope.story_count === "number" ? trope.story_count : minimumStoryCount, minimumStoryCount);
  const normalizedTrope = {
    ...trope,
    story_count: storyCount,
  };
  const handleOpen = onOpen ?? openTrope;

  function handleKeyDown(event: KeyboardEvent<HTMLElement>) {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      handleOpen(normalizedTrope);
    }
  }

  return (
    <article
      aria-label={`Open trope details for ${trope.text}`}
      className={`card trope-card ${compact ? "trope-card-compact" : ""} ${className}`.trim()}
      onClick={() => handleOpen(normalizedTrope)}
      onKeyDown={handleKeyDown}
      role="button"
      tabIndex={0}
    >
      <div className="card-row">
        <div>
          <h3>{trope.text}</h3>
        </div>
        {badge || actions ? (
          <div
            className="button-row trope-card-header-right"
            onClick={(event) => event.stopPropagation()}
            onKeyDown={(event) => event.stopPropagation()}
          >
            {badge}
            {actions}
          </div>
        ) : null}
      </div>
      {meta ? <p className="muted">{meta}</p> : null}
      {children}
    </article>
  );
}
