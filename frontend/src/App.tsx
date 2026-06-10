import { useEffect, useState } from "react";

import { getDatasetStatus } from "./api/client";
import { TropeBrowserProvider } from "./components/TropeBrowser";
import type { DatasetStatus } from "./api/types";
import { routeHref, useHashRoute, type AppRoute } from "./router";
import { CreateEntryPage } from "./pages/CreateEntryPage";
import { CurationPage } from "./pages/CurationPage";
import { DatasetPage } from "./pages/DatasetPage";
import { ExperimentalTropeForce3DPage } from "./pages/ExperimentalTropeForce3DPage";
import { ExplorationPage } from "./pages/ExplorationPage";
import { ReviewPage } from "./pages/ReviewPage";

const SIDEBAR_STATUS_POLL_INTERVAL_MS = 5000;

const NAV_ITEMS: Array<{ route: AppRoute; label: string; caption: string }> = [
  { route: "/dataset", label: "Dataset", caption: "Import, export, and status" },
  { route: "/create", label: "Create new entry", caption: "Add story metadata and tropes" },
  { route: "/review", label: "Review", caption: "Stories and trope assignments" },
  { route: "/curation", label: "Curation", caption: "Merge or remove tropes" },
  { route: "/exploration", label: "Exploration", caption: "Search and network preview" },
  {
    route: "/experimental/trope-force-3d",
    label: "Experimental visualization",
    caption: "3D trope-sequence force prototype",
  },
];

type SidebarStatusTone = "ready" | "rebuilding" | "unavailable";

function sidebarEmbeddingState(status: DatasetStatus | null): {
  label: string;
  tone: SidebarStatusTone;
  detail: string;
} {
  if (!status) {
    return {
      label: "Checking",
      tone: "rebuilding",
      detail: "Loading embedding readiness.",
    };
  }

  if (status.embedding_status.current) {
    return {
      label: "Ready",
      tone: "ready",
      detail: `Dataset v${status.active_dataset_version ?? "n/a"} is current.`,
    };
  }

  if (status.active_dataset_version == null) {
    return {
      label: "Rebuilding",
      tone: "rebuilding",
      detail: "No active dataset yet.",
    };
  }

  return {
    label: "Rebuilding",
    tone: "rebuilding",
    detail: `Dataset v${status.active_dataset_version} is waiting for current embeddings.`,
  };
}

function CurrentPage({ route }: { route: AppRoute }) {
  switch (route) {
    case "/dataset":
      return <DatasetPage />;
    case "/create":
      return <CreateEntryPage />;
    case "/review":
      return <ReviewPage />;
    case "/curation":
      return <CurationPage />;
    case "/exploration":
      return <ExplorationPage />;
    case "/experimental/trope-force-3d":
      return <ExperimentalTropeForce3DPage />;
    default:
      return <DatasetPage />;
  }
}

export default function App() {
  const route = useHashRoute();
  const [datasetStatus, setDatasetStatus] = useState<DatasetStatus | null>(null);
  const [statusUnavailable, setStatusUnavailable] = useState(false);

  useEffect(() => {
    let cancelled = false;
    let timerId: number | undefined;

    const loadStatus = async () => {
      try {
        const nextStatus = await getDatasetStatus();
        if (cancelled) {
          return;
        }
        setDatasetStatus(nextStatus);
        setStatusUnavailable(false);
      } catch {
        if (cancelled) {
          return;
        }
        setStatusUnavailable(true);
      } finally {
        if (!cancelled) {
          timerId = window.setTimeout(() => {
            void loadStatus();
          }, SIDEBAR_STATUS_POLL_INTERVAL_MS);
        }
      }
    };

    void loadStatus();

    return () => {
      cancelled = true;
      if (timerId) {
        window.clearTimeout(timerId);
      }
    };
  }, []);

  const sidebarStatus = statusUnavailable
    ? {
        label: "Unavailable",
        tone: "unavailable" as SidebarStatusTone,
        detail: "Backend status could not be loaded.",
      }
    : sidebarEmbeddingState(datasetStatus);

  return (
    <div className="app-shell">
      <aside className="app-sidebar">
        <div className="brand-block">
          <p className="eyebrow">Marawa</p>
          <h1>Oral mythology workbench</h1>
          <div className="sidebar-status-card">
            <div className="sidebar-status-row">
              <strong>Embeddings</strong>
              <span className={`sidebar-status-pill sidebar-status-${sidebarStatus.tone}`}>{sidebarStatus.label}</span>
            </div>
            <p className="muted">{sidebarStatus.detail}</p>
          </div>
          <p className="muted">
            Minimal frontend scaffold for the FastAPI rewrite. Each page calls the live backend, but the UI is still intentionally light.
          </p>
        </div>

        <nav className="nav-list" aria-label="Primary">
          {NAV_ITEMS.map((item) => (
            <a
              className={`nav-link ${route === item.route ? "nav-link-active" : ""}`}
              href={routeHref(item.route)}
              key={item.route}
            >
              <strong>{item.label}</strong>
              <span>{item.caption}</span>
            </a>
          ))}
        </nav>
      </aside>

      <main className="app-content">
        <TropeBrowserProvider>
          <CurrentPage route={route} />
        </TropeBrowserProvider>
      </main>
    </div>
  );
}
