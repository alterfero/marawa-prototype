import { FormEvent, useMemo, useState } from "react";

import { getErrorMessage } from "../api/client";
import { useAuth } from "../auth";
import { routeHref, useHashSearch, type AppRoute } from "../router";

interface PageNotice {
  tone: "error" | "success";
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

export function RedeemTokenPage() {
  const { redeemToken } = useAuth();
  const hashSearch = useHashSearch();
  const params = useMemo(() => new URLSearchParams(hashSearch), [hashSearch]);
  const nextRoute = normalizeNextRoute(params.get("next"));
  const [token, setToken] = useState(params.get("token") || "");
  const [displayName, setDisplayName] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<PageNotice | null>(null);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (password !== confirmPassword) {
      setNotice({
        tone: "error",
        title: "Passwords do not match",
        body: "Use the same new password in both fields.",
      });
      return;
    }

    try {
      setBusy(true);
      setNotice(null);
      await redeemToken({
        token,
        new_password: password,
        display_name: displayName.trim() || undefined,
      });
      setNotice({
        tone: "success",
        title: "Account ready",
        body: "Your password has been set and your session is active.",
      });
      window.location.hash = routeHref(nextRoute || "/dataset");
    } catch (error) {
      setNotice({
        tone: "error",
        title: "Could not redeem token",
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
          <span className="eyebrow">Account Setup</span>
          <h1>Redeem invite or reset token</h1>
          <p className="muted">
            Use the token provided by an admin to activate your account or set a new password.
          </p>
        </div>

        {notice ? (
          <section className={`notice ${notice.tone === "error" ? "notice-error" : "notice-success"}`}>
            <strong className="notice-title">{notice.title}</strong>
            {notice.body ? <p>{notice.body}</p> : null}
          </section>
        ) : null}

        <form className="stack" onSubmit={(event) => void handleSubmit(event)}>
          <label className="field">
            <span>Token</span>
            <input
              className="input mono"
              disabled={busy}
              onChange={(event) => setToken(event.target.value)}
              value={token}
            />
          </label>

          <label className="field">
            <span>Display name</span>
            <input
              className="input"
              disabled={busy}
              onChange={(event) => setDisplayName(event.target.value)}
              placeholder="Optional for reset tokens"
              value={displayName}
            />
          </label>

          <label className="field">
            <span>New password</span>
            <input
              autoComplete="new-password"
              className="input"
              disabled={busy}
              minLength={8}
              onChange={(event) => setPassword(event.target.value)}
              type="password"
              value={password}
            />
          </label>

          <label className="field">
            <span>Confirm password</span>
            <input
              autoComplete="new-password"
              className="input"
              disabled={busy}
              minLength={8}
              onChange={(event) => setConfirmPassword(event.target.value)}
              type="password"
              value={confirmPassword}
            />
          </label>

          <div className="button-row wrap-row">
            <button className="button" disabled={busy || !token.trim() || password.length < 8 || confirmPassword.length < 8} type="submit">
              {busy ? "Redeeming..." : "Redeem token"}
            </button>
            <a className="button button-ghost" href={routeHref("/login", nextRoute ? { next: nextRoute } : undefined)}>
              Back to sign in
            </a>
          </div>
        </form>
      </section>
    </div>
  );
}
