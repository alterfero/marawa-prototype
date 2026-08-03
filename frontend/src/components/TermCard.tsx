import type { KeyboardEvent, ReactNode } from "react";

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
  onOpen?: (term: TermReference) => void;
}

export function TermCard({
  term,
  meta,
  actions,
  className = "",
  children,
  onOpen,
}: TermCardProps) {
  function handleKeyDown(event: KeyboardEvent<HTMLElement>) {
    if (!onOpen || (event.key !== "Enter" && event.key !== " ")) {
      return;
    }
    event.preventDefault();
    onOpen(term);
  }

  return (
    <article
      aria-label={onOpen ? `Open details for ${term.text}` : undefined}
      className={`card ${onOpen ? "term-card-clickable" : ""} ${className}`.trim()}
      onClick={onOpen ? () => onOpen(term) : undefined}
      onKeyDown={onOpen ? handleKeyDown : undefined}
      role={onOpen ? "button" : undefined}
      tabIndex={onOpen ? 0 : undefined}
    >
      <div className="card-row">
        <div>
          <h3>{term.text}</h3>
        </div>
        {actions ? (
          <div
            className="button-row term-card-actions"
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
