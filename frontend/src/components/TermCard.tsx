import type { ReactNode } from "react";

interface TermReference {
  id: string;
  text: string;
  story_count: number;
}

interface TermCardProps {
  term: TermReference;
  meta?: string;
  actions?: ReactNode;
  className?: string;
  children?: ReactNode;
}

export function TermCard({
  term,
  meta,
  actions,
  className = "",
  children,
}: TermCardProps) {
  return (
    <article className={`card ${className}`.trim()}>
      <div className="card-row">
        <div>
          <h3>{term.text}</h3>
        </div>
        {actions ? <div className="button-row">{actions}</div> : null}
      </div>
      {meta ? <p className="muted">{meta}</p> : null}
      {children}
    </article>
  );
}
