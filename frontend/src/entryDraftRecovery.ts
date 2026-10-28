export interface EntryDraftRecovery {
  version: 1;
  storyId: string | null;
  fields: Record<string, string>;
  tropes: string[];
  keywords: string[];
}

const ENTRY_DRAFT_RECOVERY_KEY = "marawa:create-entry-draft:v1";

function isStringRecord(value: unknown): value is Record<string, string> {
  return (
    typeof value === "object" &&
    value !== null &&
    !Array.isArray(value) &&
    Object.values(value).every((item) => typeof item === "string")
  );
}

function isEntryDraftRecovery(value: unknown): value is EntryDraftRecovery {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return false;
  }

  const draft = value as Record<string, unknown>;
  return (
    draft.version === 1 &&
    (typeof draft.storyId === "string" || draft.storyId === null) &&
    isStringRecord(draft.fields) &&
    Array.isArray(draft.tropes) &&
    draft.tropes.every((term) => typeof term === "string") &&
    Array.isArray(draft.keywords) &&
    draft.keywords.every((term) => typeof term === "string")
  );
}

export function readEntryDraftRecovery(): EntryDraftRecovery | null {
  try {
    const storedValue: unknown = JSON.parse(window.sessionStorage.getItem(ENTRY_DRAFT_RECOVERY_KEY) || "null");
    return isEntryDraftRecovery(storedValue) ? storedValue : null;
  } catch {
    return null;
  }
}

export function writeEntryDraftRecovery(draft: EntryDraftRecovery): void {
  try {
    window.sessionStorage.setItem(ENTRY_DRAFT_RECOVERY_KEY, JSON.stringify(draft));
  } catch {
    // Draft recovery is optional and must never prevent data entry.
  }
}

export function clearEntryDraftRecovery(): void {
  try {
    window.sessionStorage.removeItem(ENTRY_DRAFT_RECOVERY_KEY);
  } catch {
    // Draft recovery is optional and must never prevent data entry.
  }
}
