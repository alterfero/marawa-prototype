import { useEffect, useMemo, useState } from "react";

import {
  canonicalizeThemes,
  createCanonicalTheme,
  deleteTheme,
  deleteUnusedThemes,
  getCanonicalThemes,
  getErrorMessage,
  getNearDuplicateThemes,
  getThemeDetail,
  mergeThemes,
  searchThemes,
  updateThemeConfirmationStatus,
  validateThemeMerges,
} from "../api/client";
import { ConfirmationStatusSwitch } from "../components/ConfirmationStatusSwitch";
import { TermCard } from "../components/TermCard";
import type {
  CanonicalThemeListItem,
  NearDuplicateThemeListResponse,
  ThemeConfirmationStatus,
  ThemeSummary,
} from "../api/types";
import { useDatasetMaintenance } from "../maintenance";

interface Notice {
  tone: "error" | "success";
  title: string;
  body?: string;
}

interface PendingMerge {
  pairId: string;
  source: ThemeSummary;
  target: ThemeSummary;
}

function pairKey(pair: { source_theme: ThemeSummary; target_theme: ThemeSummary }): string {
  return `${pair.source_theme.id}:${pair.target_theme.id}`;
}

function selectedTerms(
  pair: NearDuplicateThemeListResponse["items"][number],
  overrides: Record<string, ThemeSummary>,
): { source: ThemeSummary; target: ThemeSummary } {
  return {
    source: pair.source_theme,
    target: overrides[pairKey(pair)] ?? pair.target_theme,
  };
}

export function ThemeCurationPage() {
  const maintenance = useDatasetMaintenance();
  const [pairs, setPairs] = useState<NearDuplicateThemeListResponse | null>(null);
  const [themes, setThemes] = useState<CanonicalThemeListItem[]>([]);
  const [pendingMerges, setPendingMerges] = useState<PendingMerge[]>([]);
  const [targetOverrides, setTargetOverrides] = useState<Record<string, ThemeSummary>>({});
  const [editingPairId, setEditingPairId] = useState<string | null>(null);
  const [targetQuery, setTargetQuery] = useState("");
  const [targetResults, setTargetResults] = useState<ThemeSummary[]>([]);
  const [targetSearchStatus, setTargetSearchStatus] = useState<"idle" | "loading" | "ready">("idle");
  const [unusedQuery, setUnusedQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [editorNotice, setEditorNotice] = useState<Notice | null>(null);
  const mutationDisabled = busy || maintenance.active;

  async function refresh(options?: { clearNotice?: boolean }) {
    try {
      setLoading(true);
      if (options?.clearNotice !== false) {
        setNotice(null);
      }
      const [pairResponse, themeResponse] = await Promise.all([
        getNearDuplicateThemes(),
        getCanonicalThemes({ limit: 5000 }),
      ]);
      setPairs(pairResponse);
      setThemes(themeResponse);
      setPendingMerges((current) =>
        current.filter((merge) =>
          themeResponse.some((theme) => theme.id === merge.source.id) &&
          themeResponse.some((theme) => theme.id === merge.target.id),
        ),
      );
    } catch (caughtError) {
      setNotice({ tone: "error", title: "Could not load theme curation data", body: getErrorMessage(caughtError) });
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void refresh();
  }, []);

  const editingPair = useMemo(
    () => (editingPairId ? pairs?.items.find((pair) => pairKey(pair) === editingPairId) ?? null : null),
    [editingPairId, pairs],
  );

  useEffect(() => {
    const query = targetQuery.trim();
    if (!editingPair || !query) {
      setTargetResults([]);
      setTargetSearchStatus("idle");
      return;
    }
    let cancelled = false;
    const timeoutId = window.setTimeout(() => {
      void (async () => {
        try {
          setTargetSearchStatus("loading");
          const response = await searchThemes({ query, limit: 8 });
          if (!cancelled) {
            setTargetResults(response.items);
            setTargetSearchStatus("ready");
          }
        } catch (caughtError) {
          if (!cancelled) {
            setTargetResults([]);
            setTargetSearchStatus("ready");
            setEditorNotice({ tone: "error", title: "Could not search target themes", body: getErrorMessage(caughtError) });
          }
        }
      })();
    }, 250);
    return () => {
      cancelled = true;
      window.clearTimeout(timeoutId);
    };
  }, [editingPair, targetQuery]);

  const unusedThemes = useMemo(
    () =>
      themes.filter(
        (theme) =>
          theme.story_count === 0 && theme.text.toLocaleLowerCase().includes(unusedQuery.trim().toLocaleLowerCase()),
      ),
    [themes, unusedQuery],
  );
  const pendingSourceIds = useMemo(() => new Set(pendingMerges.map((merge) => merge.source.id)), [pendingMerges]);

  function resetTargetEditor() {
    setEditingPairId(null);
    setTargetQuery("");
    setTargetResults([]);
    setTargetSearchStatus("idle");
    setEditorNotice(null);
  }

  function stageMerge(pair: NearDuplicateThemeListResponse["items"][number]) {
    const { source, target } = selectedTerms(pair, targetOverrides);
    const id = pairKey(pair);
    setPendingMerges((current) =>
      current.some((merge) => merge.pairId === id || merge.source.id === source.id)
        ? current
        : [...current, { pairId: id, source, target }],
    );
  }

  function setPairTarget(pair: NearDuplicateThemeListResponse["items"][number], target: ThemeSummary): boolean {
    if (target.id === pair.source_theme.id) {
      setEditorNotice({ tone: "error", title: "Target must differ from source" });
      return false;
    }
    const id = pairKey(pair);
    setTargetOverrides((current) => ({ ...current, [id]: target }));
    setPendingMerges((current) => current.map((merge) => (merge.pairId === id ? { ...merge, target } : merge)));
    return true;
  }

  async function useExistingTarget(pair: NearDuplicateThemeListResponse["items"][number], themeId: string) {
    try {
      setBusy(true);
      const target = await getThemeDetail(themeId);
      if (setPairTarget(pair, target)) {
        resetTargetEditor();
        setNotice({ tone: "success", title: "Merge target updated", body: `Set ${target.text} as the merge target.` });
      }
    } catch (caughtError) {
      setEditorNotice({ tone: "error", title: "Could not set merge target", body: getErrorMessage(caughtError) });
    } finally {
      setBusy(false);
    }
  }

  async function createTarget(pair: NearDuplicateThemeListResponse["items"][number]) {
    if (!targetQuery.trim()) {
      return;
    }
    try {
      setBusy(true);
      const result = await createCanonicalTheme(targetQuery.trim());
      if (setPairTarget(pair, result.theme)) {
        resetTargetEditor();
        setNotice({
          tone: "success",
          title: result.created ? "Target theme created" : "Existing theme reused",
          body: `Set ${result.theme.text} as the merge target.`,
        });
        await refresh({ clearNotice: false });
      }
    } catch (caughtError) {
      setEditorNotice({ tone: "error", title: "Could not set merge target", body: getErrorMessage(caughtError) });
    } finally {
      setBusy(false);
    }
  }

  async function keepBoth(pair: NearDuplicateThemeListResponse["items"][number]) {
    const { source, target } = selectedTerms(pair, targetOverrides);
    if (!source.version || !target.version) {
      return;
    }
    try {
      setBusy(true);
      await canonicalizeThemes({
        themes: [
          { theme_id: source.id, expected_theme_version: source.version },
          { theme_id: target.id, expected_theme_version: target.version },
        ],
      });
      setPendingMerges((current) => current.filter((merge) => merge.pairId !== pairKey(pair)));
      setNotice({ tone: "success", title: "Both themes are canonical", body: `Marked ${source.text} and ${target.text} as canonical.` });
      await refresh({ clearNotice: false });
    } catch (caughtError) {
      setNotice({ tone: "error", title: "Could not mark both themes as canonical", body: getErrorMessage(caughtError) });
    } finally {
      setBusy(false);
    }
  }

  async function updateTargetStatus(target: ThemeSummary, status: ThemeConfirmationStatus) {
    if (!target.version) {
      return;
    }
    try {
      setBusy(true);
      await updateThemeConfirmationStatus(target.id, {
        expected_theme_version: target.version,
        confirmation_status: status,
      });
      await refresh({ clearNotice: false });
      setNotice({ tone: "success", title: "Theme updated", body: `Confirmation status set to ${status}.` });
    } catch (caughtError) {
      setNotice({ tone: "error", title: "Could not update theme confirmation", body: getErrorMessage(caughtError) });
    } finally {
      setBusy(false);
    }
  }

  async function validateMerges() {
    if (!pendingMerges.length || !window.confirm(`Validate ${pendingMerges.length} theme merge decision${pendingMerges.length === 1 ? "" : "s"}?`)) {
      return;
    }
    try {
      setBusy(true);
      const result = await validateThemeMerges({
        merges: pendingMerges.map((merge) => ({ source_theme_id: merge.source.id, target_theme_id: merge.target.id })),
      });
      setPendingMerges([]);
      setTargetOverrides({});
      setNotice({
        tone: "success",
        title: "Theme merge batch applied",
        body: `Validated ${result.merge_count} merge decision${result.merge_count === 1 ? "" : "s"} affecting ${result.affected_story_count} stor${result.affected_story_count === 1 ? "y" : "ies"}.`,
      });
      await refresh({ clearNotice: false });
    } catch (caughtError) {
      setNotice({ tone: "error", title: "Theme merge validation failed", body: getErrorMessage(caughtError) });
    } finally {
      setBusy(false);
    }
  }

  async function deleteUnusedTheme(theme: CanonicalThemeListItem) {
    if (!window.confirm(`Delete unused theme "${theme.text}"?`)) {
      return;
    }
    try {
      setBusy(true);
      await deleteTheme({ theme_id: theme.id, expected_theme_version: theme.version, remove_from_all_stories: false });
      setNotice({ tone: "success", title: "Unused theme deleted", body: `Deleted ${theme.text}.` });
      await refresh({ clearNotice: false });
    } catch (caughtError) {
      setNotice({ tone: "error", title: "Could not delete theme", body: getErrorMessage(caughtError) });
    } finally {
      setBusy(false);
    }
  }

  async function deleteAllUnusedThemes() {
    if (!window.confirm("Delete all unused themes? This permanently removes every theme without story assignments.")) {
      return;
    }
    try {
      setBusy(true);
      const result = await deleteUnusedThemes();
      setNotice({
        tone: "success",
        title: "Unused themes deleted",
        body: result.deleted_theme_count ? `Deleted ${result.deleted_theme_count} unused themes.` : "There were no unused themes to delete.",
      });
      await refresh({ clearNotice: false });
    } catch (caughtError) {
      setNotice({ tone: "error", title: "Could not delete unused themes", body: getErrorMessage(caughtError) });
    } finally {
      setBusy(false);
    }
  }

  async function mergeSingle(pair: NearDuplicateThemeListResponse["items"][number]) {
    const { source, target } = selectedTerms(pair, targetOverrides);
    if (!window.confirm(`Merge ${source.text} into ${target.text}?`)) {
      return;
    }
    try {
      setBusy(true);
      const result = await mergeThemes({ source_theme_id: source.id, target_theme_id: target.id });
      setNotice({ tone: "success", title: "Themes merged", body: `Merged ${source.text} into ${target.text} across ${result.affected_story_count} stories.` });
      await refresh({ clearNotice: false });
    } catch (caughtError) {
      setNotice({ tone: "error", title: "Could not merge themes", body: getErrorMessage(caughtError) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="page-stack">
      <section className="panel">
        <div className="panel-header">
          <div><h1>Theme curation</h1><p className="muted">Merge near-duplicate themes, confirm themes to keep, and delete unused themes.</p></div>
          <button className="button button-ghost" disabled={loading || busy} onClick={() => void refresh()} type="button">{loading ? "Loading..." : "Refresh"}</button>
        </div>
      </section>

      {notice ? <section className={`notice ${notice.tone === "error" ? "notice-error" : "notice-success"}`}><strong className="notice-title">{notice.title}</strong>{notice.body ? <p>{notice.body}</p> : null}</section> : null}

      <section className="panel">
        <div className="panel-header"><h2>Pending merge batch</h2><span className="pill">{pendingMerges.length} pending</span></div>
        {pendingMerges.length ? <>
          <div className="stack">{pendingMerges.map((merge) => <article className="card" key={merge.pairId}><div className="panel-header"><div><h3>Merge {merge.source.text} into {merge.target.text}</h3><p className="muted">{merge.source.story_count} stor{merge.source.story_count === 1 ? "y" : "ies"} affected</p></div><button className="button button-ghost" disabled={mutationDisabled} onClick={() => setPendingMerges((current) => current.filter((item) => item.pairId !== merge.pairId))} type="button">Remove</button></div></article>)}</div>
          <div className="button-row wrap-row"><button className="button button-ghost" disabled={mutationDisabled} onClick={() => setPendingMerges([])} type="button">Clear batch</button><button className="button" disabled={mutationDisabled} onClick={() => void validateMerges()} type="button">Validate all merges</button></div>
        </> : <p className="muted">No merge decisions are staged yet.</p>}
      </section>

      <section className="two-column-layout">
        <div className="panel">
          <div className="panel-header"><h2>Near-duplicate theme pairs</h2><span className="pill">{pairs?.total ?? 0} pairs</span></div>
          <div className="stack">
            {pairs?.items.length ? pairs.items.map((pair) => {
              const id = pairKey(pair);
              const { source, target } = selectedTerms(pair, targetOverrides);
              const pending = pendingMerges.some((merge) => merge.pairId === id);
              const sourcePending = pendingSourceIds.has(source.id);
              return <article className="card" key={id}>
                <div className="panel-header"><h3>Similarity {pair.similarity_score.toFixed(2)}</h3></div>
                <div className="field-grid"><div className="stack"><strong>Source</strong><TermCard className="subdued" term={source} /></div><div className="stack"><strong>Target</strong><TermCard className="subdued" term={target}><div className="theme-curation-target-confirmation"><ConfirmationStatusSwitch ariaLabel={`Confirmation status for ${target.text}`} disabled={mutationDisabled || !target.version} onChange={(status) => void updateTargetStatus(target, status)} value={target.confirmation_status ?? "unconfirmed"} /></div></TermCard></div></div>
                <p className="muted">{source.story_count} stor{source.story_count === 1 ? "y" : "ies"} affected</p>
                <div className="button-row wrap-row"><button className="button" disabled={mutationDisabled || pending || sourcePending} onClick={() => stageMerge(pair)} type="button">{pending ? "In merge batch" : sourcePending ? "Source already in batch" : "Add merge to batch"}</button><button className="button button-ghost" disabled={mutationDisabled || !source.version || !target.version} onClick={() => void keepBoth(pair)} type="button">Keep both</button><button className="button button-ghost" disabled={mutationDisabled} onClick={() => { setEditingPairId(id); setTargetQuery(target.text); setEditorNotice(null); }} type="button">Change target</button><button className="button button-ghost" disabled={mutationDisabled} onClick={() => void mergeSingle(pair)} type="button">Merge now</button></div>
              </article>;
            }) : <p className="muted">{pairs?.artifact_version === null ? "No current theme embeddings are available. Run Rebuild, then refresh this view." : "No unresolved near-duplicate theme pairs were found."}</p>}
          </div>
        </div>

        <aside className="panel">
          <div className="panel-header"><h2>Unused themes</h2><button className="button button-danger" disabled={mutationDisabled} onClick={() => void deleteAllUnusedThemes()} type="button">Delete all unused</button></div>
          <label className="field"><span>Search unused themes</span><input className="input" onChange={(event) => setUnusedQuery(event.target.value)} placeholder="Filter unused themes" value={unusedQuery} /></label>
          <div className="stack">{unusedThemes.length ? unusedThemes.map((theme) => <TermCard key={theme.id} meta="0 stories" term={theme} actions={<button className="button button-danger" disabled={mutationDisabled} onClick={() => void deleteUnusedTheme(theme)} type="button">Delete</button>} />) : <p className="muted">No unused themes match the current filter.</p>}</div>
        </aside>
      </section>

      {editingPair ? <div className="modal-backdrop" onClick={resetTargetEditor} role="presentation"><section aria-modal="true" className="modal-shell" onClick={(event) => event.stopPropagation()} role="dialog"><div className="panel-header"><h2>Edit merge target</h2><button className="button button-ghost" disabled={mutationDisabled} onClick={resetTargetEditor} type="button">Close</button></div>{editorNotice ? <section className={`notice ${editorNotice.tone === "error" ? "notice-error" : "notice-success"}`}><strong className="notice-title">{editorNotice.title}</strong>{editorNotice.body ? <p>{editorNotice.body}</p> : null}</section> : null}<label className="field"><span>Target theme query</span><input className="input" disabled={mutationDisabled} onChange={(event) => setTargetQuery(event.target.value)} placeholder="Type a target theme to search or create" value={targetQuery} /></label><div className="button-row wrap-row"><button className="button" disabled={mutationDisabled || !targetQuery.trim()} onClick={() => void createTarget(editingPair)} type="button">Keep typed theme</button><button className="button button-ghost" disabled={mutationDisabled} onClick={() => { setTargetOverrides((current) => { const next = { ...current }; delete next[pairKey(editingPair)]; return next; }); resetTargetEditor(); }} type="button">Reset target</button></div><div className="stack"><div className="panel-header"><h3>Similar existing themes</h3><span className="pill">{targetSearchStatus === "loading" ? "searching" : `${targetResults.length} results`}</span></div>{targetSearchStatus === "loading" ? <p className="muted">Searching themes...</p> : null}{targetQuery.trim() && targetSearchStatus === "ready" && !targetResults.length ? <p className="muted">No similar themes were returned for this query.</p> : null}<div className="modal-story-list">{targetResults.map((theme) => <TermCard key={theme.id} meta={`Similarity ${("score" in theme && typeof theme.score === "number" ? theme.score : 0).toFixed(2)}`} term={theme} actions={<button className="button button-ghost" disabled={mutationDisabled || theme.id === editingPair.source_theme.id} onClick={() => void useExistingTarget(editingPair, theme.id)} type="button">{theme.id === editingPair.source_theme.id ? "Source theme" : "Use existing theme"}</button>} />)}</div></div></section></div> : null}
    </div>
  );
}
