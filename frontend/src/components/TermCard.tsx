import type { ReactNode } from "react";

interface TermReference {
  id: string;
  text: string;
  story_count: number;
}

interface TermCardProps {
  term: TermReference;
  subtitle?: string;
  meta?: string;
  actions?: ReactNode;
  className?: string;
  minimumStoryCount?: number;
  children?: ReactNode;
}

export function TermCard({
  term,
  subtitle,
  meta,
  actions,
  className = "",
  minimumStoryCount = 1,
  children,
}: TermCardProps) {
  const rawStoryCount = typeof term.story_count === "number" ? term.story_count : minimumStoryCount;
  const storyCount = Math.max(rawStoryCount, minimumStoryCount);
  const storyLabel = `${storyCount} stor${storyCount === 1 ? "y" : "ies"}`;
  const detailLabel = [storyLabel, subtitle].filter(Boolean).join(" · ");

  return (
    <article className={`card ${className}`.trim()}>
      <div className="card-row">
        <div>
          <h3>{term.text}</h3>
          <p className="muted">{detailLabel}</p>
        </div>
        {actions ? <div className="button-row">{actions}</div> : null}
      </div>
      {meta ? <p className="muted">{meta}</p> : null}
      {children}
    </article>
  );
}
