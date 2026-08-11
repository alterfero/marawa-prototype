import { useCallback, useEffect, useState } from "react";

import type { StorySummary } from "./api/types";

const PINNED_STORIES_STORAGE_KEY = "marawa.pinnedStoryIdsByDataset";

function getPinnedStoryId(datasetId: string | undefined): string | null {
  if (!datasetId) {
    return null;
  }

  try {
    const storedValue: unknown = JSON.parse(window.localStorage.getItem(PINNED_STORIES_STORAGE_KEY) || "{}");
    if (!storedValue || typeof storedValue !== "object" || Array.isArray(storedValue)) {
      return null;
    }
    const pinnedStoryId = (storedValue as Record<string, unknown>)[datasetId];
    return typeof pinnedStoryId === "string" && pinnedStoryId ? pinnedStoryId : null;
  } catch {
    return null;
  }
}

function savePinnedStoryId(datasetId: string, storyId: string | null): void {
  try {
    const storedValue: unknown = JSON.parse(window.localStorage.getItem(PINNED_STORIES_STORAGE_KEY) || "{}");
    const pinnedStoryIds =
      storedValue && typeof storedValue === "object" && !Array.isArray(storedValue)
        ? { ...(storedValue as Record<string, unknown>) }
        : {};

    if (storyId) {
      pinnedStoryIds[datasetId] = storyId;
    } else {
      delete pinnedStoryIds[datasetId];
    }
    window.localStorage.setItem(PINNED_STORIES_STORAGE_KEY, JSON.stringify(pinnedStoryIds));
  } catch {
    // Pinning remains available for the current page even if browser storage is unavailable.
  }
}

export function useStoryPin(stories: StorySummary[]) {
  const datasetId = stories[0]?.dataset_id;
  const [pinnedStoryId, setPinnedStoryId] = useState<string | null>(null);

  useEffect(() => {
    const storedPinnedStoryId = getPinnedStoryId(datasetId);
    setPinnedStoryId(
      storedPinnedStoryId && stories.some((story) => story.id === storedPinnedStoryId) ? storedPinnedStoryId : null,
    );
  }, [datasetId, stories]);

  const togglePinnedStory = useCallback(
    (story: StorySummary) => {
      const nextPinnedStoryId = pinnedStoryId === story.id ? null : story.id;
      setPinnedStoryId(nextPinnedStoryId);
      savePinnedStoryId(story.dataset_id, nextPinnedStoryId);
    },
    [pinnedStoryId],
  );

  return { pinnedStoryId, togglePinnedStory };
}
