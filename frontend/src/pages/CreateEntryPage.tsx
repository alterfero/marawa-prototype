import { FormEvent, useEffect, useRef, useState } from "react";

import {
  addStoryKeyword,
  addStoryTrope,
  ApiError,
  createStory,
  deleteStoryKeyword,
  deleteStoryTrope,
  getDatasetStatus,
  getErrorMessage,
  getStory,
  getStoryTerritories,
  searchKeywords,
  searchTropes,
  updateStory,
} from "../api/client";
import { useAuth } from "../auth";
import {
  applyLocationDraftToFields,
  buildLocationDraft,
  isValidRecordingYearInterval,
  type LocationDraft,
  StoryFieldInput,
  StoryLocationPickerModal,
} from "../components/StoryFieldWidgets";
import { TermCard } from "../components/TermCard";
import { TropeCard } from "../components/TropeCard";
import { buildBlankStoryFields, DATE_OF_RECORDING_FIELD, LEGACY_METADATA_SECTIONS, normalizeDraftText, splitThemeText, THEME_FIELD, TROPE_FIELD } from "../constants/csv";
import type { CreateStoryResponse, DatasetStatus, SearchItem, StoryDetail, TropeReference } from "../api/types";
import { clearEntryDraftRecovery, readEntryDraftRecovery, writeEntryDraftRecovery, type EntryDraftRecovery } from "../entryDraftRecovery";
import { useDatasetMaintenance } from "../maintenance";
import { routeHref, useHashSearch } from "../router";

interface PageNotice {
  tone: "error" | "success" | "warning";
  title: string;
  body?: string;
}

type DraftTerm = TropeReference;

function sameFields(left: Record<string, string>, right: Record<string, string>): boolean {
  const fields = new Set([...Object.keys(left), ...Object.keys(right)]);
  return [...fields].every((field) => field === THEME_FIELD
    ? splitThemeText(left[field] || "").map(normalizeDraftText).join("\n") === splitThemeText(right[field] || "").map(normalizeDraftText).join("\n")
    : (left[field] || "") === (right[field] || ""));
}

function sameTerms(left: readonly { text: string }[], right: readonly { text: string }[]): boolean {
  const normalizeTerms = (terms: readonly { text: string }[]) => terms.map((term) => normalizeDraftText(term.text));
  const leftTerms = normalizeTerms(left);
  const rightTerms = normalizeTerms(right);
  return leftTerms.length === rightTerms.length && leftTerms.every((term, index) => term === rightTerms[index]);
}

function recoveryTerms(terms: readonly string[], kind: "trope" | "keyword"): DraftTerm[] {
  const seen = new Set<string>();
  return terms.flatMap((text, index) => {
    const trimmedText = text.trim();
    const marker = normalizeDraftText(trimmedText);
    if (!marker || seen.has(marker)) {
      return [];
    }
    seen.add(marker);
    return [{ id: `draft:recovery:${kind}:${index}`, text: trimmedText, story_count: 0 }];
  });
}

function normalizeTerritoryOptions(territories: readonly string[]): string[] {
  const labelsByKey = new Map<string, string>();
  for (const territory of territories) {
    const label = territory.replace(/\ufeff/g, "").trim();
    const key = label.replace(/\s+/g, " ").toLowerCase();
    if (key && !labelsByKey.has(key)) {
      labelsByKey.set(key, label);
    }
  }
  return [...labelsByKey.entries()]
    .sort(([leftKey, leftLabel], [rightKey, rightLabel]) => leftKey.localeCompare(rightKey) || leftLabel.localeCompare(rightLabel))
    .map(([, label]) => label);
}

function TerritoryField({
  contributor,
  disabled,
  loading,
  onChange,
  options,
  value,
}: {
  contributor: boolean;
  disabled: boolean;
  loading: boolean;
  onChange: (value: string) => void;
  options: readonly string[];
  value: string;
}) {
  const inputId = "create-entry-territory";
  const datalistId = "create-entry-territory-options";

  if (contributor) {
    const hasSelectedOption = options.includes(value);
    return (
      <label className="field">
        <strong>Territory (required)</strong>
        <select
          aria-describedby={`${inputId}-help`}
          aria-invalid={!loading && Boolean(value) && !hasSelectedOption}
          className="input"
          disabled={disabled || loading || !options.length}
          id={inputId}
          onChange={(event) => onChange(event.target.value)}
          required
          value={value}
        >
          <option value="">
            {loading ? "Loading territories..." : options.length ? "Select an authorized territory" : "No authorized territories are available"}
          </option>
          {options.map((territory) => (
            <option key={territory} value={territory}>
              {territory}
            </option>
          ))}
        </select>
        <p className="muted" id={`${inputId}-help`}>
          {loading
            ? "Loading the territories you are allowed to contribute to."
            : options.length
              ? "Required before saving. Choose one of the territories you are authorized to add stories in."
              : "You cannot create a story yet. Ask an administrator to assign you an existing territory."}
        </p>
      </label>
    );
  }

  return (
    <label className="field">
      <strong>Territory (required)</strong>
      <input
        aria-describedby={`${inputId}-help`}
        className="input"
        disabled={disabled}
        id={inputId}
        list={datalistId}
        onChange={(event) => onChange(event.target.value)}
        required
        value={value}
      />
      <datalist id={datalistId}>
        {options.map((territory) => (
          <option key={territory} value={territory} />
        ))}
      </datalist>
      <p className="muted" id={`${inputId}-help`}>
        {loading
          ? "Loading existing territories..."
          : "Required before saving. As an administrator, choose an existing territory or enter a new territory label."}
      </p>
    </label>
  );
}

function extractConflictVersion(error: ApiError): number | null {
  const detail = error.detail;
  if (!detail || typeof detail !== "object") {
    return null;
  }
  const nestedDetails = (detail as { details?: unknown }).details;
  if (!nestedDetails || typeof nestedDetails !== "object") {
    return null;
  }
  const currentDatasetVersion = (nestedDetails as { current_dataset_version?: unknown }).current_dataset_version;
  return typeof currentDatasetVersion === "number" ? currentDatasetVersion : null;
}

function extractStoryConflictVersion(error: ApiError): number | null {
  const detail = error.detail;
  if (!detail || typeof detail !== "object") {
    return null;
  }
  const nestedDetails = (detail as { details?: unknown }).details;
  if (!nestedDetails || typeof nestedDetails !== "object") {
    return null;
  }
  const currentStoryVersion = (nestedDetails as { current_story_version?: unknown }).current_story_version;
  return typeof currentStoryVersion === "number" ? currentStoryVersion : null;
}

function isDatasetMaintenanceError(error: ApiError): boolean {
  return Boolean(error.detail && typeof error.detail === "object" && (error.detail as { code?: unknown }).code === "dataset_maintenance_in_progress");
}

function buildErrorNotice(title: string, error: unknown): PageNotice {
  return {
    tone: "error",
    title,
    body: getErrorMessage(error),
  };
}

export function CreateEntryPage() {
  const maintenance = useDatasetMaintenance();
  const { user } = useAuth();
  const hashSearch = useHashSearch();
  const [datasetStatus, setDatasetStatus] = useState<DatasetStatus | null>(null);
  const [statusLoading, setStatusLoading] = useState(true);
  const [territoryOptions, setTerritoryOptions] = useState<string[]>([]);
  const [territoryOptionsLoading, setTerritoryOptionsLoading] = useState(true);
  const [fields, setFields] = useState<Record<string, string>>(() => buildBlankStoryFields());
  const [locationDraft, setLocationDraft] = useState<LocationDraft | null>(null);
  const [draftKeywords, setDraftKeywords] = useState<DraftTerm[]>([]);
  const [keywordQuery, setKeywordQuery] = useState("");
  const [keywordResults, setKeywordResults] = useState<SearchItem[]>([]);
  const [keywordSearchStatus, setKeywordSearchStatus] = useState<"idle" | "loading" | "ready">("idle");
  const [draftTropes, setDraftTropes] = useState<DraftTerm[]>([]);
  const [tropeQuery, setTropeQuery] = useState("");
  const [tropeResults, setTropeResults] = useState<SearchItem[]>([]);
  const [tropeSearchStatus, setTropeSearchStatus] = useState<"idle" | "loading" | "ready">("idle");
  const [savedDraftSnapshot, setSavedDraftSnapshot] = useState<string | null>(null);
  const draftSnapshot = JSON.stringify([fields, draftTropes.map((trope) => trope.text), draftKeywords.map((keyword) => keyword.text)]);
  useEffect(() => {
    if (savedDraftSnapshot !== null && draftSnapshot !== savedDraftSnapshot) setSavedDraftSnapshot(null);
  }, [draftSnapshot, savedDraftSnapshot]);
  const [savedStory, setSavedStory] = useState<StoryDetail | null>(null);
  const [entryLoading, setEntryLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<PageNotice | null>(null);
  const [recoveryReady, setRecoveryReady] = useState(false);
  const [initialRecovery] = useState<EntryDraftRecovery | null>(() => readEntryDraftRecovery());
  const storyIdParam = new URLSearchParams(hashSearch).get("story_id");
  const recoveredDraftRef = useRef<EntryDraftRecovery | null>(initialRecovery);
  const recoveryRestoredRef = useRef(false);
  const allowNavigationRef = useRef(false);
  const lastCreateHashRef = useRef(window.location.hash);

  const datasetVersion = datasetStatus?.active_dataset_version ?? null;
  const isContributor = user?.role === "contributor";
  const draftKeywordMarkers = new Set(draftKeywords.map((keyword) => normalizeDraftText(keyword.text)));
  const draftTropeMarkers = new Set(draftTropes.map((trope) => normalizeDraftText(trope.text)));
  const recordingYearInterval = fields[DATE_OF_RECORDING_FIELD] || "";
  const hasInvalidRecordingYearInterval =
    Boolean(recordingYearInterval) && !isValidRecordingYearInterval(recordingYearInterval);
  const hasValidTerritory = isContributor
    ? territoryOptions.includes(fields["territory"] || "")
    : Boolean((fields["territory"] || "").replace(/\ufeff/g, "").trim());
  const territorySelectionRequired = !hasValidTerritory || (isContributor && territoryOptionsLoading);
  const territorySelectionHelp = isContributor
    ? territoryOptionsLoading
      ? "Wait for your authorized territories to finish loading."
      : territoryOptions.length
        ? "Select a territory you are authorized to add stories in under Source and provenance. A territory is mandatory before saving any section."
        : "You cannot create a story yet. Ask an administrator to assign you an existing territory."
    : "Choose an existing territory or enter a new territory under Source and provenance. A territory is mandatory before saving any section.";
  const isDirty =
    !sameFields(fields, savedStory?.fields || buildBlankStoryFields()) ||
    !sameTerms(draftTropes, savedStory?.tropes || []) ||
    !sameTerms(draftKeywords, savedStory?.keywords || []);
  const cannotCreateStory =
    savedStory === null &&
    (statusLoading || datasetVersion == null);
  const readOnlyStory = savedStory !== null && !savedStory.can_edit;
  const interactionDisabled = busy || entryLoading || readOnlyStory;
  const isSaveDisabled =
    interactionDisabled ||
    maintenance.active ||
    cannotCreateStory ||
    territorySelectionRequired ||
    hasInvalidRecordingYearInterval ||
    (savedStory !== null && !isDirty);

  function discardDraftRecovery() {
    recoveredDraftRef.current = null;
    clearEntryDraftRecovery();
  }

  function restoreDraft(recovery: EntryDraftRecovery, story?: StoryDetail) {
    setFields({ ...buildBlankStoryFields(), ...recovery.fields });
    setDraftTropes(recoveryTerms(recovery.tropes, "trope").map((trope) =>
      story?.tropes.find((item) => normalizeDraftText(item.text) === normalizeDraftText(trope.text)) ?? trope));
    setDraftKeywords(recoveryTerms(recovery.keywords, "keyword"));
    setKeywordQuery("");
    setKeywordResults([]);
    setKeywordSearchStatus("idle");
    setTropeQuery("");
    setTropeResults([]);
    setTropeSearchStatus("idle");
  }

  async function loadStatus() {
    try {
      setStatusLoading(true);
      setTerritoryOptionsLoading(true);
      const [nextStatus, territoryResponse] = await Promise.all([getDatasetStatus(), getStoryTerritories()]);
      setDatasetStatus(nextStatus);
      setTerritoryOptions(normalizeTerritoryOptions(territoryResponse.items));
      return nextStatus;
    } finally {
      setStatusLoading(false);
      setTerritoryOptionsLoading(false);
    }
  }

  function resetDraftState() {
    setFields(buildBlankStoryFields());
    setLocationDraft(null);
    setDraftKeywords([]);
    setKeywordQuery("");
    setKeywordResults([]);
    setKeywordSearchStatus("idle");
    setDraftTropes([]);
    setTropeQuery("");
    setTropeResults([]);
    setTropeSearchStatus("idle");
    setSavedStory(null);
  }

  function confirmDiscardDraft(): boolean {
    return window.confirm("Discard your unsaved changes? They can no longer be saved after you leave this entry.");
  }

  function handleResetDraft() {
    if (isDirty && !confirmDiscardDraft()) {
      return;
    }

    discardDraftRecovery();
    resetDraftState();
    if (storyIdParam) {
      allowNavigationRef.current = true;
      window.location.hash = routeHref("/create");
    }
  }

  useEffect(() => {
    void loadStatus().catch((caughtError) => {
      setNotice(buildErrorNotice("Could not load dataset status or territory options", caughtError));
    });
  }, []);

  useEffect(() => {
    const recovery = recoveredDraftRef.current;
    const storyIdToLoad = storyIdParam || recovery?.storyId;
    setRecoveryReady(false);

    if (!storyIdToLoad) {
      setEntryLoading(false);
      if (recovery && !recoveryRestoredRef.current) {
        restoreDraft(recovery);
        recoveryRestoredRef.current = true;
        setNotice({
          tone: "success",
          title: "Recovered unsaved draft",
          body: "Your entry has been restored from this browser. Save it when you are ready.",
        });
      } else {
        resetDraftState();
      }
      setRecoveryReady(true);
      return;
    }

    let cancelled = false;
    setEntryLoading(true);
    void getStory(storyIdToLoad)
      .then((story) => {
        if (!cancelled) {
          loadSavedStoryIntoDraft(story);
          if (recovery?.storyId === story.id && !recoveryRestoredRef.current) {
            restoreDraft(recovery, story);
            recoveryRestoredRef.current = true;
            setNotice({
              tone: "success",
              title: "Recovered unsaved changes",
              body: "Your changes have been restored from this browser. Save them when you are ready.",
            });
          }
        }
      })
      .catch((caughtError) => {
        if (!cancelled) {
          setNotice(buildErrorNotice("Could not load the new entry", caughtError));
        }
      })
      .finally(() => {
        if (!cancelled) {
          setEntryLoading(false);
          setRecoveryReady(true);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [storyIdParam]);

  useEffect(() => {
    if (!recoveryReady) {
      return;
    }

    if (!isDirty) {
      discardDraftRecovery();
      return;
    }

    const recovery: EntryDraftRecovery = {
      version: 1,
      storyId: savedStory?.id || null,
      fields,
      tropes: draftTropes.map((trope) => trope.text),
      keywords: draftKeywords.map((keyword) => keyword.text),
    };
    recoveredDraftRef.current = recovery;
    writeEntryDraftRecovery(recovery);
  }, [draftKeywords, draftTropes, fields, isDirty, recoveryReady, savedStory?.id]);

  useEffect(() => {
    if (!isDirty) {
      return;
    }

    const handleBeforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };

    window.addEventListener("beforeunload", handleBeforeUnload);
    return () => window.removeEventListener("beforeunload", handleBeforeUnload);
  }, [isDirty]);

  useEffect(() => {
    const handleDocumentClick = (event: MouseEvent) => {
      if (
        !isDirty ||
        event.defaultPrevented ||
        event.button !== 0 ||
        event.metaKey ||
        event.ctrlKey ||
        event.shiftKey ||
        event.altKey
      ) {
        return;
      }

      const element = event.target instanceof Element ? event.target : null;
      const link = element?.closest<HTMLAnchorElement>("a[href]");
      const href = link?.getAttribute("href");
      if (!link || !href?.startsWith("#") || href === window.location.hash || link.target === "_blank") {
        return;
      }

      if (!confirmDiscardDraft()) {
        event.preventDefault();
        return;
      }

      allowNavigationRef.current = true;
      discardDraftRecovery();
    };

    const handleHashChange = () => {
      const nextHash = window.location.hash;
      if (nextHash === lastCreateHashRef.current) {
        return;
      }

      if (!isDirty) {
        discardDraftRecovery();
        if (nextHash.startsWith("#/create")) {
          lastCreateHashRef.current = nextHash;
        }
        return;
      }

      if (allowNavigationRef.current) {
        allowNavigationRef.current = false;
        if (nextHash.startsWith("#/create")) {
          lastCreateHashRef.current = nextHash;
        }
        return;
      }

      if (confirmDiscardDraft()) {
        discardDraftRecovery();
        if (nextHash.startsWith("#/create")) {
          lastCreateHashRef.current = nextHash;
        }
        return;
      }

      window.location.hash = lastCreateHashRef.current;
    };

    document.addEventListener("click", handleDocumentClick, true);
    window.addEventListener("hashchange", handleHashChange);
    return () => {
      document.removeEventListener("click", handleDocumentClick, true);
      window.removeEventListener("hashchange", handleHashChange);
    };
  }, [isDirty]);

  useEffect(() => {
    const trimmedQuery = keywordQuery.trim();
    if (!trimmedQuery) {
      setKeywordResults([]);
      setKeywordSearchStatus("idle");
      return;
    }

    let cancelled = false;
    const timeoutId = window.setTimeout(() => {
      void (async () => {
        try {
          setKeywordSearchStatus("loading");
          const result = await searchKeywords({ query: trimmedQuery, limit: 8 });
          if (cancelled) {
            return;
          }
          setKeywordResults(result.items);
          setKeywordSearchStatus("ready");
        } catch (caughtError) {
          if (cancelled) {
            return;
          }
          setKeywordResults([]);
          setKeywordSearchStatus("ready");
          setNotice(buildErrorNotice("Could not search keywords", caughtError));
        }
      })();
    }, 250);

    return () => {
      cancelled = true;
      window.clearTimeout(timeoutId);
    };
  }, [keywordQuery]);

  useEffect(() => {
    const trimmedQuery = tropeQuery.trim();
    if (!trimmedQuery) {
      setTropeResults([]);
      setTropeSearchStatus("idle");
      return;
    }

    let cancelled = false;
    const timeoutId = window.setTimeout(() => {
      void (async () => {
        try {
          setTropeSearchStatus("loading");
          const result = await searchTropes({ query: trimmedQuery, limit: 8 });
          if (cancelled) {
            return;
          }
          setTropeResults(result.items);
          setTropeSearchStatus("ready");
        } catch (caughtError) {
          if (cancelled) {
            return;
          }
          setTropeResults([]);
          setTropeSearchStatus("ready");
          setNotice(buildErrorNotice("Could not search tropes", caughtError));
        }
      })();
    }, 250);

    return () => {
      cancelled = true;
      window.clearTimeout(timeoutId);
    };
  }, [tropeQuery]);

  function updateField(field: string, value: string) {
    setFields((current) => ({
      ...current,
      [field]: value,
    }));
  }

  function openLocationPicker() {
    setLocationDraft(buildLocationDraft(fields));
  }

  function closeLocationPicker() {
    setLocationDraft(null);
  }

  function applyLocationPicker(nextLocationDraft: LocationDraft) {
    setFields((current) => applyLocationDraftToFields(current, nextLocationDraft));
    setLocationDraft(null);
  }

  function renderMetadataField(field: string) {
    if (isContributor && field === "proposition de nouveaux motifs") return null;
    if (field === "territory") {
      return (
        <TerritoryField
          contributor={isContributor}
          disabled={interactionDisabled}
          loading={territoryOptionsLoading}
          onChange={(value) => updateField(field, value)}
          options={territoryOptions}
          key={field}
          value={fields[field] || ""}
        />
      );
    }

    return (
      <StoryFieldInput
        disabled={interactionDisabled}
        field={field}
        inputIdPrefix="create-entry"
        key={field}
        onChange={(value) => updateField(field, value)}
        onOpenLocationPicker={openLocationPicker}
        value={fields[field] || ""}
      />
    );
  }

  function addDraftKeyword(nextKeyword: DraftTerm) {
    const marker = normalizeDraftText(nextKeyword.text);
    if (draftKeywordMarkers.has(marker)) {
      setNotice({
        tone: "warning",
        title: "Keyword already added",
        body: "This keyword is already in the draft entry.",
      });
      return;
    }

    setDraftKeywords((current) => [...current, nextKeyword]);
    setKeywordQuery("");
    setKeywordResults([]);
    setKeywordSearchStatus("idle");
    setNotice(null);
  }

  function addDraftTrope(nextTrope: DraftTerm) {
    const marker = normalizeDraftText(nextTrope.text);
    if (draftTropeMarkers.has(marker)) {
      setNotice({
        tone: "warning",
        title: "Trope already added",
        body: "This trope is already in the draft entry.",
      });
      return;
    }

    setDraftTropes((current) => [...current, nextTrope]);
    setTropeQuery("");
    setTropeResults([]);
    setTropeSearchStatus("idle");
    setNotice(null);
  }

  function handleUseExistingKeyword(item: SearchItem) {
    addDraftKeyword({
      id: item.id,
      text: item.text,
      story_count: item.story_count,
    });
  }

  function handleUseExistingTrope(item: SearchItem) {
    addDraftTrope({ ...item });
  }

  function handleKeepTypedKeyword() {
    const trimmedQuery = keywordQuery.trim();
    if (!trimmedQuery) {
      return;
    }

    addDraftKeyword({
      id: `draft:${normalizeDraftText(trimmedQuery)}`,
      text: trimmedQuery,
      story_count: 0,
    });
  }

  function handleKeepTypedTrope() {
    const trimmedQuery = tropeQuery.trim();
    if (!trimmedQuery) {
      return;
    }

    addDraftTrope({
      id: `draft:${normalizeDraftText(trimmedQuery)}`,
      text: trimmedQuery,
      story_count: 0,
    });
  }

  function handleDeleteDraftKeyword(keywordText: string) {
    const marker = normalizeDraftText(keywordText);
    setDraftKeywords((current) => current.filter((keyword) => normalizeDraftText(keyword.text) !== marker));
  }

  function handleEditDraftTrope(trope: DraftTerm) {
    const text = window.prompt("Edit trope", trope.text)?.trim();
    if (!text || text === trope.text) return;
    if (draftTropes.some((other) => other.id !== trope.id && normalizeDraftText(other.text) === normalizeDraftText(text))) {
      setNotice({ tone: "warning", title: "Trope already added", body: "This trope is already in the draft entry." });
      return;
    }
    setDraftTropes((current) => current.map((item) => item.id === trope.id ? { ...item, text } : item));
  }

  async function handleTropeUpdated(trope: TropeReference, previousText: string) {
    setTropeResults((current) => current.map((item) => item.id === trope.id ? { ...item, ...trope } : item));
    setDraftTropes((current) => current.map((item) =>
      item.id === trope.id || normalizeDraftText(item.text) === normalizeDraftText(previousText) ? trope : item));
    if (savedStory?.tropes.some((item) => item.id === trope.id)) {
      const story = await getStory(savedStory.id);
      // Preserve conflict detection if another edit happened alongside the rename.
      if (story.version === savedStory.version + 1) {
        setSavedStory((current) => current ? {
          ...current, version: story.version, tropes: story.tropes,
          fields: { ...current.fields, [TROPE_FIELD]: story.fields[TROPE_FIELD] || "" },
        } : current);
        setFields((current) => ({ ...current, [TROPE_FIELD]: story.fields[TROPE_FIELD] || "" }));
      }
    }
  }

  function handleDeleteDraftTrope(tropeText: string) {
    const marker = normalizeDraftText(tropeText);
    setDraftTropes((current) => current.filter((trope) => normalizeDraftText(trope.text) !== marker));
  }

  function fieldsForSection(section: (typeof LEGACY_METADATA_SECTIONS)[number]): Record<string, string> {
    return Object.fromEntries(section.fields.map((field) => [field, fields[field] || ""]));
  }

  function isSectionSaveDisabled(section: (typeof LEGACY_METADATA_SECTIONS)[number]): boolean {
    const includesRecordingYearInterval = section.fields.includes(DATE_OF_RECORDING_FIELD);
    return (
      interactionDisabled ||
      maintenance.active ||
      cannotCreateStory ||
      territorySelectionRequired ||
      (includesRecordingYearInterval && hasInvalidRecordingYearInterval)
    );
  }

  async function syncStoryTerms(initialStory: StoryDetail): Promise<StoryDetail> {
    let story = initialStory;

    for (const trope of story.tropes) {
      if (draftTropeMarkers.has(normalizeDraftText(trope.text))) {
        continue;
      }
      const result = await deleteStoryTrope(story.id, trope.id, story.version);
      story = {
        ...story,
        version: result.story_version,
        tropes: story.tropes.filter((currentTrope) => currentTrope.id !== trope.id),
      };
      setSavedStory(story);
    }

    for (const draftTrope of draftTropes) {
      if (story.tropes.some((trope) => normalizeDraftText(trope.text) === normalizeDraftText(draftTrope.text))) {
        continue;
      }
      const result = await addStoryTrope(story.id, {
        expected_story_version: story.version,
        text: draftTrope.text,
      });
      story = {
        ...story,
        version: result.story_version,
        tropes: [...story.tropes, result.trope],
      };
      setSavedStory(story);
    }

    for (const keyword of story.keywords) {
      if (draftKeywordMarkers.has(normalizeDraftText(keyword.text))) {
        continue;
      }
      const result = await deleteStoryKeyword(story.id, keyword.id, story.version);
      story = {
        ...story,
        version: result.story_version,
        keywords: story.keywords.filter((currentKeyword) => currentKeyword.id !== keyword.id),
      };
      setSavedStory(story);
    }

    for (const draftKeyword of draftKeywords) {
      if (story.keywords.some((keyword) => normalizeDraftText(keyword.text) === normalizeDraftText(draftKeyword.text))) {
        continue;
      }
      const result = await addStoryKeyword(story.id, {
        expected_story_version: story.version,
        text: draftKeyword.text,
      });
      story = {
        ...story,
        version: result.story_version,
        keywords: [...story.keywords, result.keyword],
      };
      setSavedStory(story);
    }

    return story;
  }

  function loadSavedStoryIntoDraft(story: StoryDetail) {
    setSavedStory(story);
    setFields(story.fields);
    setDraftTropes(
      story.tropes.map((trope) => ({
        ...trope,
      })),
    );
    setDraftKeywords(
      story.keywords.map((keyword) => ({
        id: keyword.id,
        text: keyword.text,
        story_count: 0,
      })),
    );
  }

  async function saveFields({
    fieldsToSave,
    successTitle,
    successBody,
    includeTerms,
  }: {
    fieldsToSave: Record<string, string>;
    successTitle: string;
    successBody: string;
    includeTerms: boolean;
  }) {
    if (readOnlyStory) {
      return;
    }
    if (DATE_OF_RECORDING_FIELD in fieldsToSave && hasInvalidRecordingYearInterval) {
      setNotice({
        tone: "error",
        title: "Invalid recording date",
        body: "Enter a whole year from 1800 to 2050, or a period with both years in that range and the end year no earlier than the begin year.",
      });
      return;
    }
    if (savedStory === null && datasetVersion == null) {
      setNotice({
        tone: "warning",
        title: "No active dataset",
        body: "Upload a dataset before creating a new entry.",
      });
      return;
    }
    if (territorySelectionRequired) {
      setNotice({
        tone: "warning",
        title: "Territory is required",
        body: territorySelectionHelp,
      });
      return;
    }

    try {
      setBusy(true);
      setNotice(null);

      let result: CreateStoryResponse;
      if (savedStory) {
        result = await updateStory({
          story_id: savedStory.id,
          expected_story_version: savedStory.version,
          fields: fieldsToSave,
        });
      } else {
        if (datasetVersion === null) {
          return;
        }
        result = await createStory({
          expected_dataset_version: datasetVersion,
          fields: { ...fieldsToSave, territory: fields["territory"] || "" },
          tropes: includeTerms ? draftTropes.map((trope) => trope.text) : [],
          keywords: includeTerms ? draftKeywords.map((keyword) => keyword.text) : [],
        });
      }

      setSavedStory(result.story);
      const finalStory = includeTerms && savedStory ? await syncStoryTerms(result.story) : result.story;
      if (includeTerms) setDraftTropes(finalStory.tropes.map((trope) => ({ ...trope })));
      setSavedDraftSnapshot(JSON.stringify([fields, (includeTerms ? finalStory.tropes : draftTropes).map((trope) => trope.text), draftKeywords.map((keyword) => keyword.text)]));

      let refreshError: string | null = null;
      try {
        await loadStatus();
      } catch (caughtError) {
        refreshError = getErrorMessage(caughtError);
      }

      setNotice({
        tone: refreshError ? "warning" : "success",
        title: refreshError ? `${successTitle}, but status did not refresh` : successTitle,
        body: refreshError ? `${successBody} Status refresh failed: ${refreshError}` : successBody,
      });
    } catch (caughtError) {
      if (caughtError instanceof ApiError && caughtError.status === 409) {
        if (isDatasetMaintenanceError(caughtError)) {
          setNotice({
            tone: "warning",
            title: "Dataset changes are paused",
            body: getErrorMessage(caughtError),
          });
          return;
        }
        if (savedStory) {
          const currentStoryVersion = extractStoryConflictVersion(caughtError);
          try {
            const currentStory = await getStory(savedStory.id);
            loadSavedStoryIntoDraft(currentStory);
          } catch {
            // Keep the original conflict visible even if the refresh fails.
          }
          setNotice({
            tone: "error",
            title: "Entry updated elsewhere",
            body:
              currentStoryVersion === null
                ? "This entry changed in another browser session. Its latest saved values were loaded; review them and try again."
                : `This entry changed in another browser session. Version ${currentStoryVersion} was loaded; review it and try again.`,
          });
          return;
        }
        const currentVersion = extractConflictVersion(caughtError);
        try {
          await loadStatus();
        } catch {
          // Keep the original conflict visible even if the refresh fails.
        }
        setNotice({
          tone: "error",
          title: "Dataset updated elsewhere",
          body:
            currentVersion === null
              ? "This dataset changed in another browser session. The latest dataset version has been reloaded."
              : `This dataset changed in another browser session. Reloaded dataset version ${currentVersion}. Review the draft and try again.`,
        });
        return;
      }

      setNotice(buildErrorNotice("Could not save the entry", caughtError));
    } finally {
      setBusy(false);
    }
  }

  async function handleSaveSection(section: (typeof LEGACY_METADATA_SECTIONS)[number]) {
    await saveFields({
      fieldsToSave: fieldsForSection(section),
      successTitle: `${section.title} saved`,
      successBody: savedStory
        ? "Your changes were saved. You can continue entering the rest of this story."
        : "An incomplete entry was created and saved. You can continue entering the rest of this story.",
      includeTerms: false,
    });
  }

  async function handleSaveAll(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await saveFields({
      fieldsToSave: fields,
      successTitle: "Entry saved",
      successBody: "All story fields, tropes, and keywords were saved. You can continue editing this entry or reset the draft to create another one.",
      includeTerms: true,
    });
  }

  return (
    <form className="page-stack create-entry-page" onSubmit={(event) => void handleSaveAll(event)}>
      <section className="panel">
        <div className="panel-header">
          <div>
            <h1>Create new entry</h1>
          </div>
          <div className="button-row wrap-row">
            <button className="button button-ghost" disabled={busy || entryLoading} onClick={handleResetDraft} type="button">
              {savedStory ? "Start another entry" : "Reset draft"}
            </button>
            <button className="button button-ghost" disabled={interactionDisabled || statusLoading} onClick={() => void loadStatus()} type="button">
              {statusLoading ? "Refreshing..." : "Refresh dataset"}
            </button>
            <button className="button" disabled={isSaveDisabled} type="submit">
              {busy ? "Saving..." : savedStory ? (isDirty ? "Save changes" : "All changes saved") : "Save new entry"}
            </button>
          </div>
        </div>
      </section>

      {!readOnlyStory && territorySelectionRequired ? (
        <section className="notice notice-warning" role="status" aria-label="Territory required">
          <strong className="notice-title">Territory is required before saving</strong>
          <p>{territorySelectionHelp}</p>
          <button
            className="button button-ghost"
            disabled={interactionDisabled || (isContributor && (territoryOptionsLoading || !territoryOptions.length))}
            onClick={() => {
              const territoryInput = document.getElementById("create-entry-territory");
              territoryInput?.scrollIntoView({ behavior: "smooth", block: "center" });
              territoryInput?.focus({ preventScroll: true });
            }}
            type="button"
          >
            Choose territory
          </button>
        </section>
      ) : null}

      {isDirty && draftSnapshot !== savedDraftSnapshot && (
        <section aria-label="Entry save status" className="entry-save-bar entry-save-bar-dirty">
          <div aria-live="polite">
            <strong>Unsaved changes</strong>
            <p>Your latest entry changes are only in this browser until you save.</p>
          </div>
          <button className="button" disabled={isSaveDisabled} type="submit">
            {busy ? "Saving..." : savedStory ? "Save changes" : "Save entry"}
          </button>
        </section>
      )}

      {notice && (
        <section
          className={`notice ${
            notice.tone === "error" ? "notice-error" : notice.tone === "warning" ? "notice-warning" : "notice-success"
          }`}
        >
          <strong className="notice-title">{notice.title}</strong>
          {notice.body ? <p>{notice.body}</p> : null}
        </section>
      )}

      {datasetVersion == null ? (
        <section className="notice notice-warning">
          <strong className="notice-title">No active dataset loaded</strong>
          <p>Upload a CSV from the Dataset page before creating manual story entries.</p>
        </section>
      ) : null}

      {readOnlyStory ? (
        <section className="notice" role="status">
          This story is read-only. You can edit stories only in your assigned territories, or start another entry.
        </section>
      ) : null}

      <section className="two-column-layout create-entry-layout">
        <div className="page-stack">
          <section className="panel">
            <div className="panel-header">
              <h2>Active dataset</h2>
              {savedStory ? <span className="pill">Entry saved · incomplete</span> : null}
            </div>
            <div className="stats-grid">
              <article className="stat-card">
                <span className="stat-label">Dataset Version</span>
                <strong>{datasetVersion ?? "none"}</strong>
              </article>
              <article className="stat-card">
                <span className="stat-label">Stories</span>
                <strong>{datasetStatus?.story_count ?? 0}</strong>
              </article>
              <article className="stat-card">
                <span className="stat-label">Tropes</span>
                <strong>{datasetStatus?.trope_count ?? 0}</strong>
              </article>
              <article className="stat-card">
                <span className="stat-label">Keywords</span>
                <strong>{datasetStatus?.keyword_count ?? 0}</strong>
              </article>
            </div>
          </section>

          {LEGACY_METADATA_SECTIONS.map((section) => (
            <section className="panel" key={section.title}>
              <div className="panel-header">
                <h2>{section.title}</h2>
              </div>
              <div className="create-field-grid">
                {section.fields.map((field) => renderMetadataField(field))}
              </div>
              <div className="button-row">
                <span className="muted">Save this section before moving on.</span>
                <button
                  className="button"
                  disabled={isSectionSaveDisabled(section)}
                  onClick={() => void handleSaveSection(section)}
                  type="button"
                >
                  {busy ? "Saving..." : `Save ${section.title}`}
                </button>
              </div>
            </section>
          ))}
        </div>

        <div className="page-stack">
          <section className="panel">
            <div className="panel-header">
              <h2>Current keywords</h2>
              <span className="pill">{draftKeywords.length} selected</span>
            </div>
            <div className="stack">
              {draftKeywords.length ? (
                draftKeywords.map((keyword) => (
                  <TermCard
                    key={keyword.id}
                    term={keyword}
                    actions={
                      <button
                        className="button button-danger"
                        disabled={interactionDisabled}
                        onClick={() => handleDeleteDraftKeyword(keyword.text)}
                        type="button"
                      >
                        Delete
                      </button>
                    }
                  />
                ))
              ) : (
                <p className="muted">No keywords on this draft yet.</p>
              )}
            </div>
          </section>

          <section className="panel">
            <div className="panel-header">
              <h2>Add keyword</h2>
            </div>

            <label className="field">
              <span>Keyword query</span>
              <input
                className="input"
                disabled={interactionDisabled}
                onChange={(event) => setKeywordQuery(event.target.value)}
                placeholder="Type a keyword to search for similar existing keywords"
                value={keywordQuery}
              />
            </label>

            <div className="card subdued">
              <div className="card-row">
                <h3>Keep typed keyword</h3>
                <button className="button" disabled={interactionDisabled || !keywordQuery.trim()} onClick={() => handleKeepTypedKeyword()} type="button">
                  Keep typed keyword
                </button>
              </div>
            </div>

            <div className="stack">
              <div className="panel-header">
                <h3>Similar existing keywords</h3>
                <span className="pill">
                  {keywordSearchStatus === "loading" ? "searching" : `${keywordResults.length} results`}
                </span>
              </div>
              {keywordQuery.trim() && keywordSearchStatus === "loading" ? <p className="muted">Searching keywords...</p> : null}
              {keywordQuery.trim() && keywordSearchStatus === "ready" && keywordResults.length === 0 ? (
                <p className="muted">No similar keywords were returned for this query.</p>
              ) : null}
              {keywordResults.map((item) => {
                const alreadyAssigned = draftKeywordMarkers.has(normalizeDraftText(item.text));
                return (
                  <TermCard
                    key={item.id}
                    term={item}
                    actions={
                      <button
                        className="button button-ghost"
                        disabled={interactionDisabled || alreadyAssigned}
                        onClick={() => handleUseExistingKeyword(item)}
                        type="button"
                      >
                        {alreadyAssigned ? "Already added" : "Use existing keyword"}
                      </button>
                    }
                  />
                );
              })}
            </div>
          </section>

          <section className="panel">
            <div className="panel-header">
              <h2>Current tropes</h2>
              <span className="pill">{draftTropes.length} selected</span>
            </div>
            <div className="stack">
              {draftTropes.length ? (
                draftTropes.map((trope) => (
                  <TropeCard
                    key={trope.id}
                    minimumStoryCount={0}
                    trope={trope}
                    editDisabled={interactionDisabled}
                    onEditDraft={() => handleEditDraftTrope(trope)}
                    onUpdated={handleTropeUpdated}
                    actions={
                      <button
                        className="button button-danger"
                        disabled={interactionDisabled}
                        onClick={() => handleDeleteDraftTrope(trope.text)}
                        type="button"
                      >
                        Delete
                      </button>
                    }
                  />
                ))
              ) : (
                <p className="muted">No tropes on this draft yet.</p>
              )}
            </div>
          </section>

          <section className="panel">
            <div className="panel-header">
              <h2>Add trope</h2>
            </div>

            <label className="field">
              <span>Trope query</span>
              <input
                className="input"
                disabled={interactionDisabled}
                onChange={(event) => setTropeQuery(event.target.value)}
                placeholder="Type a trope to search for similar existing tropes"
                value={tropeQuery}
              />
            </label>

            <div className="card subdued">
              <div className="card-row">
                <h3>Keep typed trope</h3>
                <button className="button" disabled={interactionDisabled || !tropeQuery.trim()} onClick={() => handleKeepTypedTrope()} type="button">
                  Keep typed trope
                </button>
              </div>
            </div>

            <div className="stack">
              <div className="panel-header">
                <h3>Similar existing tropes</h3>
                <span className="pill">
                  {tropeSearchStatus === "loading" ? "searching" : `${tropeResults.length} results`}
                </span>
              </div>
              {tropeQuery.trim() && tropeSearchStatus === "loading" ? <p className="muted">Searching tropes...</p> : null}
              {tropeQuery.trim() && tropeSearchStatus === "ready" && tropeResults.length === 0 ? (
                <p className="muted">No similar tropes were returned for this query.</p>
              ) : null}
              {tropeResults.map((item) => {
                const alreadyAssigned = draftTropeMarkers.has(normalizeDraftText(item.text));
                return (
                  <TropeCard
                    key={item.id}
                    trope={item}
                    editDisabled={interactionDisabled}
                    onUpdated={handleTropeUpdated}
                    actions={
                      <button
                        className="button button-ghost"
                        disabled={interactionDisabled || alreadyAssigned}
                        onClick={() => handleUseExistingTrope(item)}
                        type="button"
                      >
                        {alreadyAssigned ? "Already added" : "Use existing trope"}
                      </button>
                    }
                  />
                );
              })}
            </div>
          </section>
        </div>
      </section>

      {locationDraft ? (
        <StoryLocationPickerModal
          busy={interactionDisabled}
          locationDraft={locationDraft}
          onApply={applyLocationPicker}
          onCancel={closeLocationPicker}
          onChange={setLocationDraft}
        />
      ) : null}
    </form>
  );
}
