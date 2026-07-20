import { useMemo, type ReactNode } from "react";

import type { StorySummary } from "../api/types";
import { getStoryFieldLabel, KEYWORD_FIELD, LEGACY_METADATA_SECTIONS, TROPE_FIELD } from "../constants/csv";

export interface StoryFieldFilter {
  id: number;
  field: string;
  selectedValues: string[];
}

interface FilterFieldOption {
  key: string;
  label: string;
}

const FILTER_COMPLETENESS_FIELD = "completeness";
const ALL_COMPLETENESS_OPTIONS = ["incomplete", "pending review", "complete"] as const;
const TROPE_SPLIT_RE = /[;\n]+/;
const FILTERABLE_STORY_FIELDS: FilterFieldOption[] = [
  { key: FILTER_COMPLETENESS_FIELD, label: "Completeness" },
  ...LEGACY_METADATA_SECTIONS[0].fields.map((field) => ({ key: field, label: getStoryFieldLabel(field) })),
  ...LEGACY_METADATA_SECTIONS[1].fields.map((field) => ({ key: field, label: getStoryFieldLabel(field) })),
  { key: KEYWORD_FIELD, label: getStoryFieldLabel(KEYWORD_FIELD) },
  { key: TROPE_FIELD, label: getStoryFieldLabel(TROPE_FIELD) },
  ...LEGACY_METADATA_SECTIONS[2].fields.map((field) => ({ key: field, label: getStoryFieldLabel(field) })),
];

export function createEmptyStoryFieldFilter(nextId: number): StoryFieldFilter {
  return {
    id: nextId,
    field: "",
    selectedValues: [],
  };
}

export function normalizeFilterValue(value: string): string {
  return value.normalize("NFC").replace(/\ufeff/g, "").replace(/\s+/g, " ").trim();
}

function normalizeTropeFilterValue(value: string): string {
  return normalizeFilterValue(value).toLowerCase();
}

export function getStoryFilterValue(story: StorySummary, field: string): string {
  if (field === FILTER_COMPLETENESS_FIELD) {
    return story.completeness;
  }
  return story.fields[field] ?? "";
}

function splitStoryTropeValues(value: string): string[] {
  const normalized = normalizeFilterValue(value).replace(/\r\n/g, "\n");
  if (!normalized) {
    return [];
  }

  const cleanPiece = (piece: string) => piece.replace(/^[\s;]+|[\s;]+$/g, "");

  const pieces = normalized.includes("§§")
    ? normalized.split("§§").map(cleanPiece)
    : normalized.split(TROPE_SPLIT_RE).map(cleanPiece);
  const storyTropes: string[] = [];
  const seen = new Set<string>();
  pieces.forEach((piece) => {
    const marker = normalizeTropeFilterValue(piece);
    if (!marker || seen.has(marker)) {
      return;
    }
    seen.add(marker);
    storyTropes.push(marker);
  });
  return storyTropes;
}

export function getNormalizedStoryFilterValue(story: StorySummary, field: string): string {
  return normalizeFilterValue(getStoryFilterValue(story, field));
}

export function collectStoryFilterValues(stories: StorySummary[], field: string): string[] {
  if (field === FILTER_COMPLETENESS_FIELD) {
    return ALL_COMPLETENESS_OPTIONS.filter((value) => stories.some((story) => story.completeness === value));
  }

  const seen = new Set<string>();
  const values: string[] = [];
  stories.forEach((story) => {
    const value = getNormalizedStoryFilterValue(story, field);
    if (!value || seen.has(value)) {
      return;
    }
    seen.add(value);
    values.push(value);
  });

  values.sort((left, right) => left.localeCompare(right, undefined, { sensitivity: "base", numeric: true }));
  return values;
}

export function storyMatchesFieldFilters(story: StorySummary, filters: StoryFieldFilter[]): boolean {
  return filters.every((filter) => {
    if (!filter.field || filter.selectedValues.length === 0) {
      return true;
    }
    return filter.selectedValues.includes(getNormalizedStoryFilterValue(story, filter.field));
  });
}

export function storyMatchesSelectedTropes(
  story: StorySummary,
  selectedTropes: Array<{ text: string }>,
): boolean {
  if (selectedTropes.length === 0) {
    return true;
  }

  const storyTropes = new Set(splitStoryTropeValues(getStoryFilterValue(story, TROPE_FIELD)));
  return selectedTropes.some((trope) => storyTropes.has(normalizeTropeFilterValue(trope.text)));
}

export function filterStoriesBySelectedTropes(
  stories: StorySummary[],
  selectedTropes: Array<{ text: string }>,
): StorySummary[] {
  if (selectedTropes.length === 0) {
    return stories;
  }
  return stories.filter((story) => storyMatchesSelectedTropes(story, selectedTropes));
}

export function summarizeFilterValue(value: string): string {
  const normalized = normalizeFilterValue(value);
  if (normalized.length <= 120) {
    return normalized;
  }
  return `${normalized.slice(0, 117)}...`;
}

export function toggleFilterValue(selectedValues: string[], value: string): string[] {
  if (selectedValues.includes(value)) {
    return selectedValues.filter((item) => item !== value);
  }
  return [...selectedValues, value];
}

export function serializeStoryFieldFilters(filters: StoryFieldFilter[]): string {
  return JSON.stringify(
    filters.map((filter) => ({
      field: filter.field,
      selectedValues: filter.selectedValues,
    })),
  );
}

export function storyFieldFiltersAreComplete(filters: StoryFieldFilter[]): boolean {
  return filters.every((filter) => filter.field && filter.selectedValues.length > 0);
}

export function normalizeStoryFieldFilters(filters: StoryFieldFilter[], stories: StorySummary[]): StoryFieldFilter[] {
  const normalized: StoryFieldFilter[] = [];

  filters.forEach((filter) => {
    const storiesMatchingPreviousFilters = stories.filter((story) => storyMatchesFieldFilters(story, normalized));
    const availableValues = filter.field ? collectStoryFilterValues(storiesMatchingPreviousFilters, filter.field) : [];

    normalized.push({
      ...filter,
      selectedValues: filter.selectedValues.filter((value) => availableValues.includes(value)),
    });
  });

  return normalized;
}

function buildDraftFilterOptions(stories: StorySummary[], draftFilters: StoryFieldFilter[]) {
  return draftFilters.map((filter, index) => {
    const storiesMatchingPreviousFilters = stories.filter((story) =>
      storyMatchesFieldFilters(story, draftFilters.slice(0, index)),
    );
    const usedFields = new Set(
      draftFilters
        .filter((item) => item.id !== filter.id)
        .map((item) => item.field)
        .filter(Boolean),
    );
    const availableFieldChoices = FILTERABLE_STORY_FIELDS.filter((option) => {
      if (option.key === filter.field) {
        return true;
      }
      if (usedFields.has(option.key)) {
        return false;
      }
      return collectStoryFilterValues(storiesMatchingPreviousFilters, option.key).length > 0;
    });

    return {
      filterId: filter.id,
      availableFieldChoices,
      availableValues: filter.field ? collectStoryFilterValues(storiesMatchingPreviousFilters, filter.field) : [],
    };
  });
}

export function StoryFieldFilterBuilder({
  stories,
  draftFilters,
  appliedFilters,
  loading,
  onAddFilter,
  onApplyFilters,
  onClearFilters,
  onRemoveFilter,
  onUpdateFilterField,
  onUpdateFilterValues,
  children,
  hasPendingChanges,
  clearDisabled,
  activeCount,
}: {
  stories: StorySummary[];
  draftFilters: StoryFieldFilter[];
  appliedFilters: StoryFieldFilter[];
  loading: boolean;
  onAddFilter: () => void;
  onApplyFilters: () => void;
  onClearFilters: () => void;
  onRemoveFilter: (filterId: number) => void;
  onUpdateFilterField: (filterId: number, field: string) => void;
  onUpdateFilterValues: (filterId: number, selectedValues: string[]) => void;
  children?: ReactNode;
  hasPendingChanges?: boolean;
  clearDisabled?: boolean;
  activeCount?: number;
}) {
  const appliedCount = activeCount ?? appliedFilters.length;
  const hasAppliedFilters = appliedCount > 0;
  const hasPendingFilterChanges =
    hasPendingChanges ?? serializeStoryFieldFilters(draftFilters) !== serializeStoryFieldFilters(appliedFilters);
  const draftFiltersAreComplete = storyFieldFiltersAreComplete(draftFilters);
  const draftFilterOptions = useMemo(
    () => buildDraftFilterOptions(stories, draftFilters),
    [draftFilters, stories],
  );
  const filtersClearDisabled = clearDisabled ?? (draftFilters.length === 0 && appliedFilters.length === 0);

  return (
    <article className="card subdued story-filter-builder">
      <div className="stack">
        {hasAppliedFilters ? (
          <div className="story-filter-status-row">
            <span className="pill">{appliedCount} active</span>
          </div>
        ) : null}

        {children}

        <div className="story-filter-list">
          {draftFilters.map((filter, index) => {
            const optionState = draftFilterOptions[index];
            const availableFieldChoices = optionState?.availableFieldChoices ?? [];
            const availableValues = optionState?.availableValues ?? [];

            return (
              <div className="story-filter-row" key={filter.id}>
                <label className="field">
                  <span>Field</span>
                  <select
                    className="input"
                    disabled={loading}
                    onChange={(event) => onUpdateFilterField(filter.id, event.target.value)}
                    value={filter.field}
                  >
                    <option value="">Choose a field</option>
                    {availableFieldChoices.map((option) => (
                      <option key={option.key} value={option.key}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                </label>

                <label className="field field-span-full">
                  <span>Values</span>
                  <div className="story-filter-value-panel">
                    <div className="story-filter-value-summary">
                      {filter.selectedValues.length > 0
                        ? `${filter.selectedValues.length} value${filter.selectedValues.length === 1 ? "" : "s"} selected`
                        : "No values selected yet"}
                    </div>
                    <div className="story-filter-value-list" role="group" aria-label="Filter values">
                      {availableValues.map((value) => {
                        const checked = filter.selectedValues.includes(value);
                        return (
                          <label className={`story-filter-value-option ${checked ? "story-filter-value-option-selected" : ""}`} key={value}>
                            <input
                              checked={checked}
                              className="story-filter-value-checkbox"
                              disabled={loading || !filter.field}
                              onChange={() =>
                                onUpdateFilterValues(filter.id, toggleFilterValue(filter.selectedValues, value))
                              }
                              type="checkbox"
                            />
                            <span>{summarizeFilterValue(value)}</span>
                          </label>
                        );
                      })}
                    </div>
                  </div>
                  {!filter.field ? <p className="muted">Pick a field first, then select one or more existing values.</p> : null}
                  {filter.field && availableValues.length === 0 ? <p className="muted">No existing values are available for this field in the active dataset.</p> : null}
                </label>

                <div className="story-filter-actions">
                  <button className="button button-ghost" disabled={loading} onClick={() => onRemoveFilter(filter.id)} type="button">
                    Remove
                  </button>
                </div>
              </div>
            );
          })}
        </div>

        <div className="button-row wrap-row">
          <button className="button button-ghost" disabled={loading} onClick={onAddFilter} type="button">
            Add filter
          </button>
          <button
            className="button"
            disabled={loading || !draftFiltersAreComplete || !hasPendingFilterChanges}
            onClick={onApplyFilters}
            type="button"
          >
            Filter
          </button>
          <button
            className="button button-ghost"
            disabled={loading || filtersClearDisabled}
            onClick={onClearFilters}
            type="button"
          >
            Clear filters
          </button>
        </div>

        {draftFilters.length > 0 && !draftFiltersAreComplete ? (
          <p className="muted">Complete each filter row before applying it.</p>
        ) : null}
        {hasPendingFilterChanges && draftFiltersAreComplete ? (
          <p className="muted">Filter changes are staged and will apply when you press Filter.</p>
        ) : null}
      </div>
    </article>
  );
}
