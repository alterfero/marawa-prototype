import type { KeyboardEvent, ReactNode } from "react";

import type { TropeReference } from "../api/types";
import { useTropeBrowser } from "./TropeBrowser";

interface TropeCardProps {
  trope: TropeReference;
  meta?: string;
  actions?: ReactNode;
  compact?: boolean;
  className?: string;
  minimumStoryCount?: number;
  children?: ReactNode;
}

export function TropeCard({
  trope,
  meta,
  actions,
  compact = false,
  className = "",
  minimumStoryCount = 1,
  children,
}: TropeCardProps) {
  const { openTrope } = useTropeBrowser();
  const storyCount = Math.max(typeof trope.story_count === "number" ? trope.story_count : minimumStoryCount, minimumStoryCount);
  const normalizedTrope = {
    ...trope,
    story_count: storyCount,
  };

  function handleKeyDown(event: KeyboardEvent<HTMLElement>) {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      openTrope(normalizedTrope);
    }
  }

  return (
    <article
      aria-label={`Open trope details for ${trope.text}`}
      className={`card trope-card ${compact ? "trope-card-compact" : ""} ${className}`.trim()}
      onClick={() => openTrope(normalizedTrope)}
      onKeyDown={handleKeyDown}
      role="button"
      tabIndex={0}
    >
      <div className="card-row">
        <div>
          <h3>{trope.text}</h3>
        </div>
        {actions ? (
          <div
            className="button-row"
            onClick={(event) => event.stopPropagation()}
            onKeyDown={(event) => event.stopPropagation()}
          >
            {actions}
          </div>
        ) : null}
      </div>
      {meta ? <p className="muted">{meta}</p> : null}
      {children}
    </article>
  );
}
