import { useCallback, useEffect, useState } from "react";

import type { StorySummary } from "./api/types";

const PINNED_STORIES_STORAGE_KEY = "marawa.pinnedStoryIdsByDataset";

function normalizePinnedStoryIds(value: unknown): string[] {
  const pinnedStoryIds = typeof value === "string" ? [value] : Array.isArray(value) ? value : [];
  return [...new Set(pinnedStoryIds.filter((storyId): storyId is string => typeof storyId === "string" && Boolean(storyId)))];
}

function getPinnedStoryIds(datasetId: string | undefined): string[] {
  if (!datasetId) {
    return [];
  }

  try {
    const storedValue: unknown = JSON.parse(window.localStorage.getItem(PINNED_STORIES_STORAGE_KEY) || "{}");
    if (!storedValue || typeof storedValue !== "object" || Array.isArray(storedValue)) {
      return [];
    }
    return normalizePinnedStoryIds((storedValue as Record<string, unknown>)[datasetId]);
  } catch {
    return [];
  }
}

function savePinnedStoryIds(datasetId: string, storyIds: string[]): void {
  try {
    const storedValue: unknown = JSON.parse(window.localStorage.getItem(PINNED_STORIES_STORAGE_KEY) || "{}");
    const pinnedStoryIds =
      storedValue && typeof storedValue === "object" && !Array.isArray(storedValue)
        ? { ...(storedValue as Record<string, unknown>) }
        : {};

    const normalizedStoryIds = normalizePinnedStoryIds(storyIds);
    if (normalizedStoryIds.length > 0) {
      pinnedStoryIds[datasetId] = normalizedStoryIds;
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
  const [pinnedStoryIds, setPinnedStoryIds] = useState<string[]>([]);

  useEffect(() => {
    const availableStoryIds = new Set(stories.map((story) => story.id));
    setPinnedStoryIds(getPinnedStoryIds(datasetId).filter((storyId) => availableStoryIds.has(storyId)));
  }, [datasetId, stories]);

  const togglePinnedStory = useCallback(
    (story: StorySummary) => {
      const nextPinnedStoryIds = pinnedStoryIds.includes(story.id)
        ? pinnedStoryIds.filter((storyId) => storyId !== story.id)
        : [...pinnedStoryIds, story.id];
      setPinnedStoryIds(nextPinnedStoryIds);
      savePinnedStoryIds(story.dataset_id, nextPinnedStoryIds);
    },
    [pinnedStoryIds],
  );

  return { pinnedStoryIds, togglePinnedStory };
}
