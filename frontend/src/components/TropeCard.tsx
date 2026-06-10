import type { KeyboardEvent, ReactNode } from "react";

import type { TropeReference } from "../api/types";
import { useTropeBrowser } from "./TropeBrowser";

interface TropeCardProps {
  trope: TropeReference;
  subtitle?: string;
  meta?: string;
  actions?: ReactNode;
  compact?: boolean;
  className?: string;
  minimumStoryCount?: number;
  children?: ReactNode;
}

export function TropeCard({
  trope,
  subtitle,
  meta,
  actions,
  compact = false,
  className = "",
  minimumStoryCount = 1,
  children,
}: TropeCardProps) {
  const { openTrope } = useTropeBrowser();
  const rawStoryCount = typeof trope.story_count === "number" ? trope.story_count : minimumStoryCount;
  const storyCount = Math.max(rawStoryCount, minimumStoryCount);
  const storyLabel = `${storyCount} stor${storyCount === 1 ? "y" : "ies"}`;
  const detailLabel = [storyLabel, subtitle].filter(Boolean).join(" · ");
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
          <p className="muted">{detailLabel}</p>
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
