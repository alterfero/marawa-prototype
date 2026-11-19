import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { readEntryDraftRecovery, writeEntryDraftRecovery, type EntryDraftRecovery } from "../src/entryDraftRecovery.js";

beforeEach(() => {
  const storage = new Map<string, string>();
  Object.defineProperty(globalThis, "window", {
    configurable: true,
    value: { sessionStorage: {
      getItem: (key: string) => storage.get(key) ?? null,
      setItem: (key: string, value: string) => storage.set(key, value),
    } },
  });
});
afterEach(() => { Reflect.deleteProperty(globalThis, "window"); });

const draft: EntryDraftRecovery = {
  version: 1, storyId: null, fields: { territory: "Tahiti" },
  tropes: ["Someone leaves", "A boy crosses the sea"], keywords: [],
};

test("draft recovery preserves individual specificity choices and automatic ratings", () => {
  const ratedDraft = { ...draft, tropeSpecificityLevels: { "A boy crosses the sea": 3 } };
  writeEntryDraftRecovery(ratedDraft);
  assert.deepEqual(readEntryDraftRecovery(), ratedDraft);
});

test("drafts saved before specificity selection remain recoverable", () => {
  writeEntryDraftRecovery(draft);
  assert.deepEqual(readEntryDraftRecovery(), draft);
});

test("draft recovery rejects invalid specificity metadata", () => {
  for (const levels of [null, [], { trope: 0 }, { trope: 5 }, { trope: 2.5 }, { trope: "2" }]) {
    window.sessionStorage.setItem("marawa:create-entry-draft:v1", JSON.stringify({ ...draft, tropeSpecificityLevels: levels }));
    assert.equal(readEntryDraftRecovery(), null);
  }
});
