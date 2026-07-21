import { FormEvent, useMemo, useState } from "react";

import { getErrorMessage } from "../api/client";
import { useAuth } from "../auth";
import { routeHref, useHashSearch, type AppRoute } from "../router";

interface PageNotice {
  tone: "error" | "warning";
  title: string;
  body?: string;
}

function normalizeNextRoute(value: string | null): AppRoute | null {
  if (!value) {
    return null;
  }
  const allowedRoutes: AppRoute[] = [
    "/dataset",
    "/stories",
    "/create",
    "/review",
    "/curation",
    "/users",
    "/exploration",
  ];
  if (allowedRoutes.includes(value as AppRoute)) {
    return value as AppRoute;
  }
  return null;
}

export function LoginPage({ accessNotice }: { accessNotice?: string | null }) {
  const { login } = useAuth();
  const hashSearch = useHashSearch();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<PageNotice | null>(null);

  const nextRoute = useMemo(() => {
    const params = new URLSearchParams(hashSearch);
    return normalizeNextRoute(params.get("next"));
  }, [hashSearch]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    try {
      setBusy(true);
      setNotice(null);
      await login({ email, password });
      window.location.hash = routeHref(nextRoute || "/dataset");
    } catch (error) {
      setNotice({
        tone: "error",
        title: "Could not sign in",
        body: getErrorMessage(error),
      });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="page-stack auth-page-shell">
      <section className="panel auth-panel">
        <div className="stack">
          <span className="eyebrow">Authentication</span>
          <h1>Sign in to Marawa</h1>
          <p className="muted">
            Guests get read-only access, contributors can edit stories and terms, and admins can manage datasets,
            reviews, and user accounts.
          </p>
        </div>

        {accessNotice ? (
          <section className="notice notice-warning">
            <strong className="notice-title">Authentication required</strong>
            <p>{accessNotice}</p>
          </section>
        ) : null}

        {notice ? (
          <section className={`notice ${notice.tone === "error" ? "notice-error" : "notice-warning"}`}>
            <strong className="notice-title">{notice.title}</strong>
            {notice.body ? <p>{notice.body}</p> : null}
          </section>
        ) : null}

        <form className="stack" onSubmit={(event) => void handleSubmit(event)}>
          <label className="field">
            <span>Email</span>
            <input
              autoComplete="email"
              className="input"
              disabled={busy}
              onChange={(event) => setEmail(event.target.value)}
              type="email"
              value={email}
            />
          </label>

          <label className="field">
            <span>Password</span>
            <input
              autoComplete="current-password"
              className="input"
              disabled={busy}
              onChange={(event) => setPassword(event.target.value)}
              type="password"
              value={password}
            />
          </label>

          <div className="button-row wrap-row">
            <button className="button" disabled={busy || !email.trim() || !password} type="submit">
              {busy ? "Signing in..." : "Sign in"}
            </button>
            <a className="button button-ghost" href={routeHref("/redeem", nextRoute ? { next: nextRoute } : undefined)}>
              Redeem invite or reset token
            </a>
          </div>
        </form>
      </section>
    </div>
  );
}
