import { useEffect, useLayoutEffect, useRef, useState, type FormEvent } from "react";
import { createPortal } from "react-dom";

import { getErrorMessage, getUsage, getUsageExportUrl } from "../api/client";
import type { UsageHour, UsageReport } from "../api/types";

const HOUR_MS = 3_600_000;
const WIDTHS = [12, 20, 32, 48, 72];
type Range = { start: string; end: string };

function latestRange(hours: number): Range {
  const end = (Math.floor(Date.now() / HOUR_MS) + 1) * HOUR_MS;
  return { start: new Date(end - hours * HOUR_MS).toISOString(), end: new Date(end).toISOString() };
}

function utcLabel(value: string): string {
  return new Date(value).toISOString().slice(0, 16).replace("T", " ");
}

function HourDetails({ hour }: { hour: UsageHour }) {
  return <>
    <h3>{utcLabel(hour.hour_start)}–{new Date(Date.parse(hour.hour_start) + HOUR_MS).toISOString().slice(11, 16)} UTC</h3>
    <p><strong>{hour.total_visitors}</strong> unique visitors · <strong>{hour.unregistered_guests}</strong> unregistered guests</p>
    <p className="muted">Registered users active during this hour ({hour.registered_users.length})</p>
    {hour.registered_users.length ? <ul className="usage-user-list">
      {hour.registered_users.map((user) => <li key={user.id}><strong>{user.display_name}</strong><span>{user.email}</span></li>)}
    </ul> : <p className="muted">No registered users active.</p>}
  </>;
}

function UsageHistogram({ hours }: { hours: UsageHour[] }) {
  const [zoom, setZoom] = useState(2);
  const [tooltip, setTooltip] = useState<{ hour: UsageHour; left: number; top: number } | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const hideTimer = useRef<number>();
  const previousRange = useRef("");
  const previousWidth = useRef(WIDTHS[zoom]);
  const width = WIDTHS[zoom];
  const maximum = Math.max(1, ...hours.map((hour) => hour.total_visitors));
  const step = Math.max(1, Math.ceil(maximum / 4));
  const ceiling = step * 4;
  const ticks = [ceiling, step * 3, step * 2, step, 0];
  const rangeKey = `${hours[0]?.hour_start}/${hours[hours.length - 1]?.hour_start}`;
  const selectedHour = hours.find((hour) => hour.hour_start === selected);

  useLayoutEffect(() => {
    const element = scrollRef.current;
    if (!element) return;
    if (previousRange.current !== rangeKey) {
      element.scrollLeft = element.scrollWidth;
      previousRange.current = rangeKey;
    } else if (previousWidth.current !== width) {
      const centerHour = (element.scrollLeft + element.clientWidth / 2) / previousWidth.current;
      element.scrollLeft = centerHour * width - element.clientWidth / 2;
    }
    previousWidth.current = width;
  }, [rangeKey, width]);

  useEffect(() => {
    const dismiss = (event: KeyboardEvent) => { if (event.key === "Escape") setTooltip(null); };
    const dismissOnResize = () => setTooltip(null);
    window.addEventListener("keydown", dismiss);
    window.addEventListener("resize", dismissOnResize);
    return () => {
      window.removeEventListener("keydown", dismiss);
      window.removeEventListener("resize", dismissOnResize);
      window.clearTimeout(hideTimer.current);
    };
  }, []);

  function showHour(hour: UsageHour, target: HTMLElement) {
    window.clearTimeout(hideTimer.current);
    const box = target.getBoundingClientRect();
    setTooltip({
      hour,
      left: Math.max(12, Math.min(box.left + width / 2, window.innerWidth - 352)),
      top: Math.max(12, Math.min(box.top + 32, window.innerHeight - 380)),
    });
  }

  function hideSoon() {
    hideTimer.current = window.setTimeout(() => setTooltip(null), 180);
  }

  return <>
    <div className="usage-chart-tools">
      <div className="usage-legend"><span><i className="usage-registered" />Registered users</span><span><i className="usage-guests" />Unregistered guests</span></div>
      <div className="usage-zoom">
        <button className="button button-ghost" aria-label="Zoom out" disabled={zoom === 0} onClick={() => { setTooltip(null); setZoom(zoom - 1); }}>−</button>
        <label>Zoom <input aria-label="Histogram zoom" type="range" min={0} max={WIDTHS.length - 1} value={zoom} onChange={(event) => { setTooltip(null); setZoom(Number(event.target.value)); }} /></label>
        <button className="button button-ghost" aria-label="Zoom in" disabled={zoom === WIDTHS.length - 1} onClick={() => { setTooltip(null); setZoom(zoom + 1); }}>+</button>
      </div>
    </div>
    <p id="usage-chart-help" className="muted">Scroll horizontally to explore hours. Hover, focus, or tap a bar for details. Use the arrow keys to move between hours.</p>
    <div className="usage-chart" aria-label="Unique visitors per UTC hour">
      <div className="usage-y-axis" aria-hidden="true">{ticks.map((tick) => <span key={tick}>{tick}</span>)}</div>
      <div ref={scrollRef} className="usage-chart-scroll" tabIndex={0} aria-label="Scrollable hourly histogram" aria-describedby="usage-chart-help" onScroll={() => setTooltip(null)}>
        <div className="usage-plot" style={{ width: hours.length * width }}>
          <div className="usage-grid" aria-hidden="true">{ticks.map((tick) => <div key={tick} />)}</div>
          {hours.map((hour, index) => {
            const label = utcLabel(hour.hour_start);
            const labelVisible = index % Math.ceil(56 / width) === 0;
            return <button
              type="button"
              key={hour.hour_start}
              className={`usage-column${selected === hour.hour_start ? " usage-column-selected" : ""}`}
              style={{ width }}
              aria-label={`${label} UTC: ${hour.total_visitors} visitors, ${hour.registered_users.length} registered users, ${hour.unregistered_guests} unregistered guests`}
              aria-describedby={tooltip?.hour.hour_start === hour.hour_start ? "usage-hour-tooltip" : undefined}
              onMouseEnter={(event) => showHour(hour, event.currentTarget)}
              onMouseLeave={hideSoon}
              onFocus={(event) => showHour(hour, event.currentTarget)}
              onBlur={hideSoon}
              onClick={() => { setSelected(hour.hour_start); setTooltip(null); }}
              onKeyDown={(event) => {
                const offset = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
                if (!offset) return;
                event.preventDefault();
                const sibling = offset > 0 ? event.currentTarget.nextElementSibling : event.currentTarget.previousElementSibling;
                if (sibling instanceof HTMLButtonElement) sibling.focus();
              }}
            >
              <span className="usage-bar" style={{ height: `${hour.total_visitors / ceiling * 100}%` }}>
                {width >= 32 && hour.total_visitors > 0 && <span className="usage-bar-count">{hour.total_visitors}</span>}
                <span className="usage-guests" style={{ flex: hour.unregistered_guests }} />
                <span className="usage-registered" style={{ flex: hour.registered_users.length }} />
              </span>
              {labelVisible && <span className="usage-x-label" aria-hidden="true"><span>{label.slice(11)}</span><small>{label.slice(5, 10)}</small></span>}
            </button>;
          })}
        </div>
      </div>
    </div>
    <p className="usage-axis-label muted">UTC hour · one bar per hour</p>
    {selectedHour && <section className="usage-hour-details" aria-live="polite"><HourDetails hour={selectedHour} /></section>}
    {tooltip && createPortal(<div
      id="usage-hour-tooltip" role="tooltip" className="usage-tooltip"
      style={{ left: tooltip.left, top: tooltip.top }}
      onMouseEnter={() => window.clearTimeout(hideTimer.current)} onMouseLeave={hideSoon}
    ><HourDetails hour={tooltip.hour} /></div>, document.body)}
  </>;
}

export function UsagePage() {
  const [range, setRange] = useState<Range>(() => latestRange(24));
  const [draft, setDraft] = useState(range);
  const [liveHours, setLiveHours] = useState<number | null>(24);
  const [report, setReport] = useState<UsageReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [refresh, setRefresh] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    void getUsage(range.start, range.end, controller.signal).then((next) => {
      if (controller.signal.aborted) return;
      setReport(next);
      setError(null);
    }).catch((failure: unknown) => {
      if (!controller.signal.aborted) setError(getErrorMessage(failure));
    }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [range, refresh]);

  useEffect(() => {
    const timer = window.setInterval(() => {
      if (document.visibilityState !== "visible") return;
      if (liveHours !== null) {
        const next = latestRange(liveHours);
        setRange(next);
        setDraft(next);
      } else setRefresh((value) => value + 1);
    }, 60_000);
    return () => window.clearInterval(timer);
  }, [liveHours]);

  function changeRange(next: Range, live: number | null = null) {
    setLiveHours(live);
    setRange(next);
    setDraft(next);
    setReport(null);
  }

  function submitRange(event: FormEvent) {
    event.preventDefault();
    const duration = Date.parse(draft.end) - Date.parse(draft.start);
    if (!Number.isFinite(duration) || duration <= 0 || duration > 31 * 24 * HOUR_MS) {
      setError("Choose a UTC date range longer than zero and no longer than 31 days.");
      return;
    }
    changeRange(draft);
  }

  function moveRange(direction: number) {
    const duration = Date.parse(range.end) - Date.parse(range.start);
    changeRange({ start: new Date(Date.parse(range.start) + direction * duration).toISOString(), end: new Date(Date.parse(range.end) + direction * duration).toISOString() });
  }

  const totalActiveHours = report?.hours.filter((hour) => hour.total_visitors > 0).length ?? 0;
  const peak = Math.max(0, ...(report?.hours.map((hour) => hour.total_visitors) ?? []));

  return <div className="page-stack usage-page">
    <section className="panel">
      <div className="panel-header"><div><p className="eyebrow">Administration</p><h2>Usage</h2></div><span className="pill">UTC</span></div>
      <p>Unique visitors active in each hour, with registered users and unregistered guests shown separately.</p>
      <p className="muted">Activity is recorded while the app is visible and there has been interaction in the last five minutes. Guests are estimated by browser cookie. Signing in combines that browser’s guest activity with the account for the same hour.</p>
    </section>
    <section className="panel">
      <div className="usage-range-shortcuts button-row wrap-row">
        {[24, 168, 744].map((hours) => <button type="button" className={`button ${liveHours === hours ? "" : "button-ghost"}`} key={hours} onClick={() => changeRange(latestRange(hours), hours)}>{hours === 24 ? "Last 24 hours" : hours === 168 ? "Last 7 days" : "Last 31 days"}</button>)}
        <button className="button button-ghost" onClick={() => setRefresh((value) => value + 1)} disabled={loading}>Refresh</button>
      </div>
      <form className="usage-range-form" onSubmit={submitRange}>
        <label className="field"><span>From (UTC)</span><input required className="input" type="datetime-local" step={3600} value={draft.start.slice(0, 16)} onChange={(event) => { setLiveHours(null); setDraft({ ...draft, start: event.target.value ? `${event.target.value}:00.000Z` : "" }); }} /></label>
        <label className="field"><span>Until (UTC, exclusive)</span><input required className="input" type="datetime-local" step={3600} value={draft.end.slice(0, 16)} onChange={(event) => { setLiveHours(null); setDraft({ ...draft, end: event.target.value ? `${event.target.value}:00.000Z` : "" }); }} /></label>
        <button className="button" type="submit">Apply dates</button>
      </form>
      <div className="usage-chart-tools">
        <div className="button-row"><button className="button button-ghost" onClick={() => moveRange(-1)}>← Earlier</button><button className="button button-ghost" onClick={() => moveRange(1)} disabled={Date.parse(range.end) >= Date.now()}>Later →</button></div>
        <div className="button-row wrap-row"><a className="button button-ghost" href={getUsageExportUrl(range)}>Download range CSV</a><a className="button button-ghost" href={getUsageExportUrl()}>Download all history</a></div>
      </div>
      {error && <div className="notice notice-error" role="alert">{error}</div>}
      {loading && !report && <p role="status">Loading usage history…</p>}
      {report && <>
        <div className="usage-summary"><span><strong>{peak}</strong> most visitors in one hour</span><span><strong>{totalActiveHours}</strong> hours with activity</span><span className="muted">Updated {utcLabel(report.generated_at)} UTC</span></div>
        {!totalActiveHours && <p className="notice" role="status">No activity recorded in this range.</p>}
        <UsageHistogram hours={report.hours} />
        <p className="muted">{report.first_recorded_at ? `First recorded activity: ${utcLabel(report.first_recorded_at)} UTC. Earlier activity is unavailable.` : "History starts when visitors use this version of the app."} History is retained in the database. All-history CSV includes hours with recorded activity; range CSV includes empty hours too.</p>
      </>}
    </section>
  </div>;
}
