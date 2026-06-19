import { useEffect, useSyncExternalStore } from "react";

export type AppRoute =
  | "/login"
  | "/redeem"
  | "/dataset"
  | "/stories"
  | "/create"
  | "/review"
  | "/curation"
  | "/users"
  | "/exploration"
  | "/experimental/trope-force-3d";

const DEFAULT_ROUTE: AppRoute = "/exploration";
const KNOWN_ROUTES: AppRoute[] = [
  "/login",
  "/redeem",
  "/dataset",
  "/stories",
  "/create",
  "/review",
  "/curation",
  "/users",
  "/exploration",
  "/experimental/trope-force-3d",
];

function readHashPathAndSearch(): { path: string; search: string } {
  const raw = window.location.hash.replace(/^#/, "") || DEFAULT_ROUTE;
  const [path, search = ""] = raw.split("?");
  return {
    path: path || DEFAULT_ROUTE,
    search,
  };
}

function getRouteFromHash(): AppRoute {
  const { path } = readHashPathAndSearch();
  if (KNOWN_ROUTES.includes(path as AppRoute)) {
    return path as AppRoute;
  }
  return DEFAULT_ROUTE;
}

function getHashSearchFromHash(): string {
  return readHashPathAndSearch().search;
}

function subscribe(onStoreChange: () => void): () => void {
  window.addEventListener("hashchange", onStoreChange);
  return () => window.removeEventListener("hashchange", onStoreChange);
}

export function useHashRoute(): AppRoute {
  const route = useSyncExternalStore(subscribe, getRouteFromHash, () => DEFAULT_ROUTE);

  useEffect(() => {
    if (!window.location.hash) {
      window.location.replace(`#${DEFAULT_ROUTE}`);
    }
  }, []);

  return route;
}

export function useHashSearch(): string {
  return useSyncExternalStore(subscribe, getHashSearchFromHash, () => "");
}

export function routeHref(
  route: AppRoute,
  params?: Record<string, string | number | null | undefined>,
): string {
  const search = new URLSearchParams();
  Object.entries(params || {}).forEach(([key, value]) => {
    if (value === null || value === undefined || value === "") {
      return;
    }
    search.set(key, String(value));
  });
  const suffix = search.toString() ? `?${search.toString()}` : "";
  return `#${route}${suffix}`;
}
