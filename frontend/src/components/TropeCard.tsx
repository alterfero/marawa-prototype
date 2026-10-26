import { useState, type KeyboardEvent, type ReactNode } from "react";

import { getErrorMessage, getTropeDetail, updateCanonicalTrope } from "../api/client";
import { useAuth } from "../auth";
import { useDatasetMaintenance } from "../maintenance";

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
  active?: boolean;
  children?: ReactNode;
  editDisabled?: boolean;
  onEditDraft?: () => void;
  onUpdated?: (trope: TropeReference, previousText: string) => Promise<void>;
  onOpen?: (trope: TropeReference) => void;
}

export function TropeCard({
  trope: originalTrope,
  meta,
  badge,
  actions,
  compact = false,
  className = "",
  minimumStoryCount = 1,
  active,
  children,
  onOpen,
  editDisabled = false,
  onEditDraft,
  onUpdated,
}: TropeCardProps) {
  const { openTrope, updatedTropes, rememberTrope } = useTropeBrowser();
  const { user } = useAuth();
  const maintenance = useDatasetMaintenance();
  const [editing, setEditing] = useState(false);
  const [editError, setEditError] = useState<string | null>(null);
  const remembered = updatedTropes[originalTrope.id];
  const trope = remembered && (remembered.version ?? 0) >= (originalTrope.version ?? 0) ? remembered : originalTrope;
  const canEdit = user?.role === "contributor" && trope.confirmation_status !== "canonical"
    && (!trope.id.startsWith("draft:") || Boolean(onEditDraft));

  async function editTrope() {
    if (onEditDraft && trope.id.startsWith("draft:")) {
      onEditDraft();
      return;
    }
    setEditing(true);
    setEditError(null);
    try {
      const current = await getTropeDetail(trope.id);
      rememberTrope(current);
      if (current.confirmation_status === "canonical") {
        setEditError("This trope is now canonical and can only be edited by an administrator.");
        return;
      }
      const text = window.prompt("Edit trope", current.text)?.trim();
      if (!text || text === current.text) return;
      const result = await updateCanonicalTrope({
        trope_id: current.id, expected_trope_version: current.version, text,
      });
      rememberTrope(result.trope);
      await onUpdated?.(result.trope, current.text);
    } catch (error) {
      setEditError(getErrorMessage(error));
    } finally {
      setEditing(false);
    }
  }
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
      aria-pressed={active}
      className={`card trope-card ${compact ? "trope-card-compact" : ""} ${active ? "trope-card-active" : ""} ${className}`.trim()}
      onClick={() => handleOpen(normalizedTrope)}
      onKeyDown={handleKeyDown}
      role="button"
      tabIndex={0}
    >
      <div className="card-row">
        <div>
          <h3>{trope.text}</h3>
          <p className="muted trope-card-story-count">
            {storyCount} stor{storyCount === 1 ? "y" : "ies"}
          </p>
        </div>
        {badge || actions || canEdit ? (
          <div
            className="button-row trope-card-header-right term-card-actions"
            onClick={(event) => event.stopPropagation()}
            onKeyDown={(event) => event.stopPropagation()}
          >
            {badge}
            {canEdit ? (
              <button className="button button-ghost" disabled={editDisabled || editing || maintenance.active}
                onClick={() => void editTrope()} type="button">
                {editing ? "Saving..." : "Edit trope"}
              </button>
            ) : null}
            {actions}
          </div>
        ) : null}
      </div>
      {editError ? <p className="notice-inline" role="alert">{editError}</p> : null}
      {meta ? <p className="muted">{meta}</p> : null}
      {children}
    </article>
  );
}
