import { useEffect } from "react";

import { recordUsageActivity } from "./api/client";

const IDLE_MS = 5 * 60_000;
const PING_MS = 60_000;

/** Only visible browsing with recent interaction counts as activity. */
export function useUsageActivity(ready: boolean, userId?: string) {
  useEffect(() => {
    if (!ready) return;
    let lastInteraction = Date.now();
    let lastSent = 0;
    let pending = false;

    const ping = () => {
      const now = Date.now();
      const newHour = Math.floor(now / 3_600_000) !== Math.floor(lastSent / 3_600_000);
      if (document.visibilityState !== "visible" || now - lastInteraction >= IDLE_MS || pending || (!newHour && now - lastSent < PING_MS)) return;
      pending = true;
      lastSent = now;
      void recordUsageActivity().catch(() => {
        // Retry on the next minute; analytics failures must not interrupt research work.
      }).finally(() => { pending = false; });
    };
    const interaction = () => {
      lastInteraction = Date.now();
      ping();
    };
    const visible = () => {
      if (document.visibilityState === "visible") interaction();
    };

    ping();
    // Align checks with minute boundaries, including the beginning of UTC hours.
    let interval: number | undefined;
    const timeout = window.setTimeout(() => {
      ping();
      interval = window.setInterval(ping, PING_MS);
    }, PING_MS - Date.now() % PING_MS);
    const events = ["pointerdown", "pointermove", "keydown", "scroll"];
    events.forEach((name) => window.addEventListener(name, interaction, { passive: true, capture: true }));
    document.addEventListener("visibilitychange", visible);
    return () => {
      window.clearTimeout(timeout);
      window.clearInterval(interval);
      events.forEach((name) => window.removeEventListener(name, interaction, { capture: true }));
      document.removeEventListener("visibilitychange", visible);
    };
  }, [ready, userId]);
}
