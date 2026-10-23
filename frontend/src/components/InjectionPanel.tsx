import { useEffect, useState } from "react";
import { applyInjection, getErrorMessage, getInjection, previewInjection } from "../api/client";
import type { InjectionDecision, InjectionPreview, InjectionRow } from "../api/types";
import "./InjectionPanel.css";

const STORAGE_KEY = "marawa-injection-review";
const PAGE_SIZE = 10;
const title = (fields: Record<string, string>) => fields["Story title (Eng)"] || fields["Story title (French)"] || fields["Story title (other)"] || "Untitled story";
const fieldLabel = (column: string) => column === "Motifs (Eng)" ? "Tropes (CSV: Motifs (Eng))" : column;

function Comparison({ row, selected }: { row: InjectionRow; selected: string }) {
  const candidate = row.candidates.find((item) => item.key === selected);
  const fields = Object.keys(row.fields).filter((key) => row.fields[key] || candidate?.fields[key]);
  return <details className="injection-comparison">
    <summary>{candidate ? "Compare story fields and metadata" : "Inspect incoming story fields"}</summary>
    <div className="injection-table-scroll"><table className="injection-table">
      <thead><tr><th>Field</th>{candidate && <th>Original</th>}<th>Injected</th></tr></thead>
      <tbody>{fields.map((key) => <tr key={key} className={candidate && row.fields[key] !== candidate.fields[key] ? "injection-difference" : ""}>
        <th scope="row">{fieldLabel(key)}</th>{candidate && <td>{candidate.fields[key] || "—"}</td>}<td>{row.fields[key] || "—"}</td>
      </tr>)}</tbody>
    </table></div>
    {(row.metadata || candidate?.metadata) && <div className="injection-metadata">
      {candidate && <div><strong>Original metadata</strong><pre>{candidate.metadata ? JSON.stringify(candidate.metadata, null, 2) : "No metadata"}</pre></div>}
      <div><strong>Injected metadata</strong><pre>{row.metadata ? JSON.stringify(row.metadata, null, 2) : "Legacy CSV: incomplete story; CSV trope assignments are validated."}</pre></div>
    </div>}
  </details>;
}

export function InjectionPanel({ disabled, onApplied }: { disabled: boolean; onApplied: () => Promise<void> }) {
  const [file, setFile] = useState<File | null>(null);
  const [report, setReport] = useState<InjectionPreview | null>(null);
  const [decisions, setDecisions] = useState<Record<number, InjectionDecision>>({});
  const [targets, setTargets] = useState<Record<number, string>>({});
  const [acknowledged, setAcknowledged] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [onlyDuplicates, setOnlyDuplicates] = useState(true);
  const [page, setPage] = useState(0);
  const [issuePage, setIssuePage] = useState(0);
  const [severity, setSeverity] = useState("all");

  useEffect(() => {
    let cancelled = false;
    const saved = sessionStorage.getItem(STORAGE_KEY);
    if (saved) {
      setBusy(true);
      void (async () => {
        try {
          const state = JSON.parse(saved) as { id: string; decisions: Record<number, InjectionDecision> };
          const restored = await getInjection(state.id);
          if (!cancelled) {
            setReport(restored);
            setDecisions(state.decisions);
            setTargets(Object.fromEntries(Object.values(state.decisions).map((d) => [d.row_number, d.target_key || ""])));
          }
        } catch (caught) {
          if (!cancelled) {
            sessionStorage.removeItem(STORAGE_KEY);
            setError(getErrorMessage(caught));
          }
        } finally {
          if (!cancelled) setBusy(false);
        }
      })();
    }
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    if (report) sessionStorage.setItem(STORAGE_KEY, JSON.stringify({ id: report.id, decisions }));
  }, [report, decisions]);

  async function checkFile() {
    if (!file) return;
    setBusy(true); setError(null);
    try {
      const next = await previewInjection(file);
      setReport(next); setDecisions({}); setTargets({}); setAcknowledged(false); setPage(0); setIssuePage(0); setSeverity("all");
    } catch (caught) { setError(getErrorMessage(caught)); }
    finally { setBusy(false); }
  }

  async function inject() {
    if (!report) return;
    setBusy(true); setError(null);
    try {
      const result = await applyInjection(report.id, Object.values(decisions), acknowledged);
      setReport({ ...report, result });
      await onApplied();
    } catch (caught) { setError(getErrorMessage(caught)); }
    finally { setBusy(false); }
  }

  function downloadReport() {
    if (!report) return;
    const content = report.issues.map((i) => `${i.severity.toUpperCase()} | ${i.row_number ? `Data row ${i.row_number}, lines ${i.line_start}–${i.line_end}` : "Header / file"} | ${i.column || "Row"}${i.column_number ? ` (column ${i.column_number})` : ""}\n${i.message}\n${i.value ? `Value: ${i.value}\n` : ""}Fix: ${i.suggestion}\n`).join("\n");
    const url = URL.createObjectURL(new Blob([`${report.source_filename}\n${report.row_count} entries checked\n\n${content}`], { type: "text/plain;charset=utf-8" }));
    const a = document.createElement("a"); a.href = url; a.download = "injection-validation.txt"; a.click(); URL.revokeObjectURL(url);
  }

  const rows = report?.rows.filter((row) => !onlyDuplicates || row.candidates.length > 0) || [];
  const issues = report?.issues.filter((issue) => severity === "all" || issue.severity === severity) || [];
  const unresolved = report?.rows.filter((r) => r.candidates.length && !decisions[r.row_number]).length || 0;
  const locked = disabled || busy || Boolean(report?.result);

  return <section className="panel stack injection-panel" aria-labelledby="injection-title">
    <div><h2 id="injection-title">Inject CSV into this dataset</h2>
      <p>Add stories from a full Marawa or legacy export. Check every entry, resolve duplicates, then inject into the current database.</p>
      <p>Every added or replaced story starts as <strong>Incomplete</strong> and enters the <strong>Review queue</strong> for admin review, like a contributor submission. Full-export completeness is reset.</p>
      <p className="muted">Recording dates accept a year or [year1, year2]; other values are left blank. New stories receive fresh row numbers automatically.</p></div>
    <ol className="injection-steps"><li>Check CSV</li><li>Correct issues and review duplicates</li><li>Inject</li></ol>
    <label className="field"><span>CSV to inject</span><input className="input" type="file" accept=".csv,text/csv" disabled={disabled || busy} onChange={(e) => {
      setFile(e.target.files?.[0] || null); setReport(null); setDecisions({}); setAcknowledged(false); setError(null); sessionStorage.removeItem(STORAGE_KEY);
    }} /></label>
    <button type="button" className="button button-ghost" disabled={disabled || busy || !file} onClick={() => void checkFile()}>{busy ? "Working…" : "Check CSV / recheck corrected file"}</button>
    {disabled && <p className="muted">Injection is available when there is an active dataset and maintenance has finished.</p>}
    {error && <p className="notice notice-error" role="alert">{error}</p>}
    {report && <>
      <div className="notice" aria-live="polite"><strong>{report.source_filename} · {report.format === "full" ? "Full Marawa export" : "Legacy export"}</strong>
        <p>{report.row_count} entries · {report.error_count} errors · {report.warning_count} warnings · {report.duplicate_count} entries with duplicate candidates</p>
        <p>Reviewed against dataset version {report.dataset_version}. Data rows count from 1 after the header; line numbers include quoted multiline cells.</p>
      </div>
      {report.issues.length > 0 && <div className="stack">
        <div className="injection-toolbar"><h3>Validation messages</h3><button type="button" className="button button-ghost" onClick={downloadReport}>Download issue report</button>
          <label>Show <select className="input" value={severity} onChange={(e) => { setSeverity(e.target.value); setIssuePage(0); }}><option value="all">All issues</option><option value="error">Errors</option><option value="warning">Warnings</option></select></label></div>
        <p>Correct errors in your CSV, select the corrected file, and check it again. Warnings describe values to inspect or conversions to acknowledge.</p>
        <ul className="injection-issues">{issues.slice(issuePage * 20, (issuePage + 1) * 20).map((issue, i) => <li key={i} className={`notice notice-${issue.severity === "error" ? "error" : "warning"}`}>
          <strong>{issue.severity === "error" ? "Error" : "Warning"} · {issue.row_number ? `Data row ${issue.row_number} · lines ${issue.line_start}–${issue.line_end}` : "Header / file"}{issue.column ? ` · ${fieldLabel(issue.column)}` : ""}{issue.column_number ? ` · column ${issue.column_number}` : ""}</strong>
          <p>{issue.message}</p>{issue.value && <pre>{issue.value}</pre>}<p><strong>How to fix:</strong> {issue.suggestion}</p>
        </li>)}</ul>
        {issues.length > 20 && <div className="injection-toolbar"><button type="button" className="button button-ghost" disabled={issuePage === 0} onClick={() => setIssuePage(issuePage - 1)}>Previous issues</button><span>Page {issuePage + 1} of {Math.ceil(issues.length / 20)}</span><button type="button" className="button button-ghost" disabled={(issuePage + 1) * 20 >= issues.length} onClick={() => setIssuePage(issuePage + 1)}>Next issues</button></div>}
      </div>}
      <div className="injection-toolbar"><h3>Entry review</h3><label><input type="checkbox" checked={onlyDuplicates} onChange={(e) => { setOnlyDuplicates(e.target.checked); setPage(0); }} /> Only duplicate candidates</label><span>{unresolved} choices remaining</span></div>
      <p className="muted">Duplicates have both a matching non-empty title and abstract, ignoring capitalization and extra whitespace. When only a title matches, the new entry receives the next available number, such as “The legend of Titua 1”, without a warning. Choose for every duplicate entry. Entries are applied in CSV order; a later row can replace an earlier row’s resolved story. With multiple candidates, choose the original to keep or update; the other stories remain.</p>
      {rows.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE).map((row) => {
        const target = targets[row.row_number] ?? (row.candidates.length === 1 ? row.candidates[0].key : "");
        const candidate = row.candidates.find((c) => c.key === target);
        return <article className="injection-entry stack" key={row.row_number}>
          <h4>Data row {row.row_number} · {title(row.fields)}</h4>
          <p className="muted">Lines {row.line_start}–{row.line_end} · {row.candidates.length ? `${row.candidates.length} duplicate candidate(s)` : "New entry"}</p>
          {row.candidates.length > 0 && <>
            <label className="field"><span>Original to compare and resolve</span><select className="input" disabled={locked} value={target} onChange={(e) => {
              setTargets({ ...targets, [row.row_number]: e.target.value });
              setDecisions((current) => { const next = { ...current }; delete next[row.row_number]; return next; });
            }}><option value="" disabled>Select an original</option>{row.candidates.map((c) => <option key={c.key} value={c.key}>{c.kind === "file" ? `Earlier CSV row ${c.row_number}` : `Database story ${c.story_id?.slice(0, 8)}`} · {title(c.fields)}</option>)}</select></label>
            {candidate && <p>{[...new Set(candidate.reasons)].join(" · ")}</p>}
            <fieldset className="injection-choices" disabled={locked}><legend>Resolve data row {row.row_number}</legend>
              {([ ["keep_both", "Keep both", "Add this entry as another story."], ["keep_original", "Keep original", "Skip this entry and retain the selected original."], ["keep_injected", "Keep injected", "Replace the selected original’s fields and assignments in place."] ] as const).map(([action, label, explanation]) => <label key={action}>
                <input type="radio" name={`injection-${row.row_number}`} disabled={action !== "keep_both" && !target} checked={decisions[row.row_number]?.action === action} onChange={() => setDecisions({ ...decisions, [row.row_number]: { row_number: row.row_number, action, target_key: target || null } })} /> <strong>{label}</strong><span>{explanation}</span>
              </label>)}
            </fieldset>
          </>}
          <Comparison row={row} selected={target} />
        </article>;
      })}
      {rows.length === 0 && <p className="muted">{onlyDuplicates ? "No duplicate candidates to review. Clear the filter to inspect all entries." : "No readable entries."}</p>}
      {rows.length > PAGE_SIZE && <div className="injection-toolbar"><button type="button" className="button button-ghost" disabled={page === 0} onClick={() => setPage(page - 1)}>Previous entries</button><span>Page {page + 1} of {Math.ceil(rows.length / PAGE_SIZE)}</span><button type="button" className="button button-ghost" disabled={(page + 1) * PAGE_SIZE >= rows.length} onClick={() => setPage(page + 1)}>Next entries</button></div>}
      {report.result ? <div className="notice notice-success" role="status"><strong>Injection completed</strong><p>{report.result.added} added · {report.result.updated} updated · {report.result.skipped} skipped · {report.result.new_terms} new terms.</p>{report.result.added + report.result.updated > 0 && <p>Injected stories are Incomplete and awaiting admin review in the Review queue.</p>}<p>{report.result.job_id ? "A durable rebuild has been queued to refresh search and computed data." : "No content changes required a rebuild."}</p></div> : <div className="stack">
        {report.warning_count > 0 && <label><input type="checkbox" checked={acknowledged} disabled={locked} onChange={(e) => setAcknowledged(e.target.checked)} /> I reviewed the warnings and accept the described conversions and retained values.</label>}
        <p>Ready when all errors are corrected and every duplicate has a choice. Applying updates the active dataset in one transaction and queues a rebuild. Existing shared term statuses are retained; full-export catalogs also add unused terms.</p>
        <button type="button" className="button" disabled={locked || report.error_count > 0 || unresolved > 0 || (report.warning_count > 0 && !acknowledged)} onClick={() => void inject()}>{busy ? "Applying injection…" : "Apply reviewed injection"}</button>
      </div>}
    </>}
  </section>;
}
