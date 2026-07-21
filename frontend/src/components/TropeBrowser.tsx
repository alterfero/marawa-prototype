import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { ApiError, getTropeDetail } from "../api/client";
import type { TropeDetail, TropeReference } from "../api/types";
import { routeHref } from "../router";

interface TropeBrowserContextValue {
  openTrope: (trope: TropeReference) => void;
}

const TropeBrowserContext = createContext<TropeBrowserContextValue | null>(null);

function tropeBrowserErrorMessage(error: unknown): string {
  if (error instanceof ApiError && error.status === 404) {
    const detail = error.detail;
    const code =
      detail && typeof detail === "object" && "code" in detail && typeof (detail as { code?: unknown }).code === "string"
        ? (detail as { code: string }).code
        : null;

    if (code === "trope_not_found") {
      return "This trope could not be found on the current backend data anymore.";
    }

    return "Could not load story titles for this trope. The backend may need a restart to expose the trope detail endpoint.";
  }

  if (error instanceof ApiError) {
    return `Could not load story titles for this trope: ${error.message}`;
  }

  if (error instanceof Error) {
    return `Could not load story titles for this trope: ${error.message}`;
  }

  return "Could not load story titles for this trope.";
}

export function TropeBrowserProvider({ children }: { children: ReactNode }) {
  const [selectedTrope, setSelectedTrope] = useState<TropeReference | null>(null);
  const [detail, setDetail] = useState<TropeDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const requestIdRef = useRef(0);

  const closeTrope = useCallback(() => {
    requestIdRef.current += 1;
    setSelectedTrope(null);
    setDetail(null);
    setLoading(false);
    setError(null);
  }, []);

  const openTrope = useCallback((trope: TropeReference) => {
    const requestId = requestIdRef.current + 1;
    requestIdRef.current = requestId;
    setSelectedTrope(trope);
    setDetail(null);
    setError(null);
    setLoading(true);

    void (async () => {
      try {
        const result = await getTropeDetail(trope.id);
        if (requestIdRef.current !== requestId) {
          return;
        }
        setDetail(result);
      } catch (caughtError) {
        if (requestIdRef.current !== requestId) {
          return;
        }
        setError(tropeBrowserErrorMessage(caughtError));
      } finally {
        if (requestIdRef.current === requestId) {
          setLoading(false);
        }
      }
    })();
  }, []);

  useEffect(() => {
    if (!selectedTrope) {
      return;
    }

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        closeTrope();
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [closeTrope, selectedTrope]);

  const contextValue = useMemo<TropeBrowserContextValue>(
    () => ({
      openTrope,
    }),
    [openTrope],
  );

  const effectiveDetail = detail ?? selectedTrope;
  const mapHref = selectedTrope ? routeHref("/exploration", { selected_trope_id: selectedTrope.id }) : routeHref("/exploration");

  return (
    <TropeBrowserContext.Provider value={contextValue}>
      {children}

      {selectedTrope ? (
        <div className="modal-backdrop" onClick={closeTrope} role="presentation">
          <section
            aria-labelledby="trope-browser-title"
            aria-modal="true"
            className="modal-shell"
            onClick={(event) => event.stopPropagation()}
            role="dialog"
          >
            <div className="panel-header">
              <h2 id="trope-browser-title">{effectiveDetail?.text || selectedTrope.text}</h2>
              <button className="button button-ghost" onClick={closeTrope} type="button">
                Close
              </button>
            </div>

            {loading ? <p className="muted">Loading story titles...</p> : null}
            {error ? <p className="notice-inline">{error}</p> : null}

            {!loading && !error ? (
              <div className="modal-story-list">
                {detail?.stories.length ? (
                  detail.stories.map((story) => (
                    <button
                      className="list-row"
                      key={story.id}
                      onClick={() => {
                        window.location.hash = routeHref("/stories", { selected_story_id: story.id });
                        closeTrope();
                      }}
                      type="button"
                    >
                      <strong>{story.title}</strong>
                    </button>
                  ))
                ) : (
                  <p className="muted">No stories currently use this trope in the active dataset.</p>
                )}
              </div>
            ) : null}

            <div className="button-row">
              <button
                className="button"
                onClick={() => {
                  window.location.hash = mapHref;
                  closeTrope();
                }}
                type="button"
              >
                See on map
              </button>
            </div>
          </section>
        </div>
      ) : null}
    </TropeBrowserContext.Provider>
  );
}

export function useTropeBrowser(): TropeBrowserContextValue {
  const context = useContext(TropeBrowserContext);
  if (!context) {
    throw new Error("useTropeBrowser must be used within a TropeBrowserProvider.");
  }
  return context;
}
