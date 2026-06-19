import { useEffect, useMemo, useState } from "react";

import { getDatasetStatus } from "./api/client";
import type { DatasetStatus, UserRole } from "./api/types";
import { roleAtLeast, roleLabel, useAuth } from "./auth";
import { TropeBrowserProvider } from "./components/TropeBrowser";
import { routeHref, useHashRoute, useHashSearch, type AppRoute } from "./router";
import { AdminReviewPage } from "./pages/AdminReviewPage";
import { AdminUsersPage } from "./pages/AdminUsersPage";
import { CreateEntryPage } from "./pages/CreateEntryPage";
import { CurationPage } from "./pages/CurationPage";
import { DatasetPage } from "./pages/DatasetPage";
import { ExperimentalTropeForce3DPage } from "./pages/ExperimentalTropeForce3DPage";
import { ExplorationPage } from "./pages/ExplorationPage";
import { LoginPage } from "./pages/LoginPage";
import { RedeemTokenPage } from "./pages/RedeemTokenPage";
import { StoriesPage } from "./pages/StoriesPage";

const SIDEBAR_STATUS_POLL_INTERVAL_MS = 5000;

type SidebarStatusTone = "ready" | "rebuilding" | "unavailable";

interface NavItem {
  route: AppRoute;
  label: string;
  minimumRole?: UserRole;
}

const NAV_ITEMS: NavItem[] = [
  { route: "/exploration", label: "Exploration" },
  { route: "/dataset", label: "Dataset", minimumRole: "guest" },
  { route: "/stories", label: "Stories", minimumRole: "guest" },
  { route: "/create", label: "Create new entry", minimumRole: "contributor" },
  { route: "/review", label: "Review queue", minimumRole: "admin" },
  { route: "/curation", label: "Curation", minimumRole: "admin" },
  { route: "/users", label: "Users", minimumRole: "admin" },
  { route: "/experimental/trope-force-3d", label: "Experimental visualization", minimumRole: "guest" },
];

function minimumRoleForRoute(route: AppRoute): UserRole | null {
  switch (route) {
    case "/dataset":
    case "/stories":
    case "/experimental/trope-force-3d":
      return "guest";
    case "/create":
      return "contributor";
    case "/review":
    case "/curation":
    case "/users":
      return "admin";
    default:
      return null;
  }
}

function sidebarEmbeddingState(status: DatasetStatus | null): {
  label: string;
  tone: SidebarStatusTone;
} {
  if (!status) {
    return {
      label: "Checking",
      tone: "rebuilding",
    };
  }

  if (status.embedding_status.current) {
    return {
      label: "Ready",
      tone: "ready",
    };
  }

  return {
    label: "Rebuilding",
    tone: "rebuilding",
  };
}

function CurrentPage({
  route,
  canEditStories,
  canManageDataset,
  loginAccessNotice,
}: {
  route: AppRoute;
  canEditStories: boolean;
  canManageDataset: boolean;
  loginAccessNotice: string | null;
}) {
  switch (route) {
    case "/login":
      return <LoginPage accessNotice={loginAccessNotice} />;
    case "/redeem":
      return <RedeemTokenPage />;
    case "/dataset":
      return <DatasetPage canManageDataset={canManageDataset} />;
    case "/stories":
      return <StoriesPage canEdit={canEditStories} />;
    case "/create":
      return <CreateEntryPage />;
    case "/review":
      return <AdminReviewPage />;
    case "/curation":
      return <CurationPage />;
    case "/users":
      return <AdminUsersPage />;
    case "/exploration":
      return <ExplorationPage />;
    case "/experimental/trope-force-3d":
      return <ExperimentalTropeForce3DPage />;
    default:
      return <ExplorationPage />;
  }
}

export default function App() {
  const route = useHashRoute();
  const hashSearch = useHashSearch();
  const { status: authStatus, user, logout } = useAuth();
  const [datasetStatus, setDatasetStatus] = useState<DatasetStatus | null>(null);
  const [statusUnavailable, setStatusUnavailable] = useState(false);

  const authenticatedRole = user?.role || null;
  const requiredRole = minimumRoleForRoute(route);
  const isAuthenticated = authStatus === "authenticated";
  const canEditStories = roleAtLeast(authenticatedRole, "contributor");
  const canManageDataset = roleAtLeast(authenticatedRole, "admin");

  const effectiveRoute = useMemo<AppRoute>(() => {
    if (authStatus === "loading") {
      return route;
    }

    if (route === "/login" || route === "/redeem") {
      return isAuthenticated ? "/dataset" : route;
    }

    if (!requiredRole) {
      return route;
    }

    if (!isAuthenticated) {
      return "/login";
    }

    if (!roleAtLeast(authenticatedRole, requiredRole)) {
      return "/dataset";
    }

    return route;
  }, [authStatus, authenticatedRole, isAuthenticated, requiredRole, route]);

  const loginAccessNotice = useMemo(() => {
    const next = new URLSearchParams(hashSearch).get("next");
    if (route !== "/login" || !next) {
      return null;
    }
    return "Sign in to open dataset, stories, contributor tools, or admin screens.";
  }, [hashSearch, route]);

  useEffect(() => {
    if (authStatus === "loading" || route === effectiveRoute) {
      return;
    }

    if (effectiveRoute === "/login" && !isAuthenticated && requiredRole) {
      window.location.replace(routeHref("/login", { next: route }));
      return;
    }

    window.location.replace(routeHref(effectiveRoute));
  }, [authStatus, effectiveRoute, isAuthenticated, requiredRole, route]);

  useEffect(() => {
    if (!isAuthenticated) {
      setDatasetStatus(null);
      setStatusUnavailable(false);
      return;
    }

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
  }, [isAuthenticated]);

  const sidebarStatus = statusUnavailable
    ? {
        label: "Unavailable",
        tone: "unavailable" as SidebarStatusTone,
      }
    : sidebarEmbeddingState(datasetStatus);

  const visibleNavItems = NAV_ITEMS.filter((item) => !item.minimumRole || roleAtLeast(authenticatedRole, item.minimumRole));

  async function handleLogout() {
    await logout();
    window.location.hash = routeHref("/exploration");
  }

  return (
    <div className="app-shell">
      <aside className="app-sidebar">
        <div className="brand-block">
          <h1>Marawa</h1>

          {isAuthenticated ? (
            <>
              <div className="sidebar-status-card">
                <div className="sidebar-status-row">
                  <strong>Embeddings</strong>
                  <span className={`sidebar-status-pill sidebar-status-${sidebarStatus.tone}`}>{sidebarStatus.label}</span>
                </div>
              </div>

              <div className="sidebar-status-card">
                <div className="stack">
                  <strong>{user?.display_name}</strong>
                  <p className="muted">{user?.email}</p>
                  <div className="button-row wrap-row">
                    <span className="pill">{roleLabel(user?.role)}</span>
                    <button className="button button-ghost" onClick={() => void handleLogout()} type="button">
                      Sign out
                    </button>
                  </div>
                </div>
              </div>
            </>
          ) : (
            <div className="sidebar-status-card">
              <div className="stack">
                <strong>Public exploration is open</strong>
                <p className="muted">Sign in for read-only dataset access, contributor editing, or admin controls.</p>
                <div className="button-row wrap-row">
                  <a className="button" href={routeHref("/login")}>
                    Sign in
                  </a>
                  <a className="button button-ghost" href={routeHref("/redeem")}>
                    Redeem token
                  </a>
                </div>
              </div>
            </div>
          )}
        </div>

        <nav className="nav-list" aria-label="Primary">
          {visibleNavItems.map((item) => (
            <a
              className={`nav-link ${effectiveRoute === item.route ? "nav-link-active" : ""}`}
              href={routeHref(item.route)}
              key={item.route}
            >
              <strong>{item.label}</strong>
            </a>
          ))}
        </nav>
      </aside>

      <main className="app-content">
        {authStatus === "loading" ? (
          <section className="panel">
            <h2>Checking session</h2>
            <p className="muted">Loading your access level and the right workspace.</p>
          </section>
        ) : (
          <TropeBrowserProvider>
            <CurrentPage
              canEditStories={canEditStories}
              canManageDataset={canManageDataset}
              loginAccessNotice={loginAccessNotice}
              route={effectiveRoute}
            />
          </TropeBrowserProvider>
        )}
      </main>
    </div>
  );
}
