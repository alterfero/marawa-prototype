import { FormEvent, useEffect, useState } from "react";

import {
  activateUser,
  createUser,
  deactivateUser,
  getErrorMessage,
  getUsers,
  issuePasswordReset,
  updateUser,
} from "../api/client";
import { roleLabel, useAuth } from "../auth";
import type { CreateUserResponse, CurrentUser, PasswordResetResponse, UserRole } from "../api/types";

interface PageNotice {
  tone: "error" | "success";
  title: string;
  body?: string;
}

interface UserDraft {
  display_name: string;
  role: UserRole;
}

type IssuedToken =
  | {
      kind: "invite";
      response: CreateUserResponse;
    }
  | {
      kind: "reset";
      response: PasswordResetResponse;
    };

function statusLabel(user: CurrentUser): string {
  return user.status.replace(/_/g, " ");
}

export function AdminUsersPage() {
  const { user: currentUser } = useAuth();
  const [users, setUsers] = useState<CurrentUser[]>([]);
  const [drafts, setDrafts] = useState<Record<string, UserDraft>>({});
  const [loading, setLoading] = useState(true);
  const [busyUserId, setBusyUserId] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [notice, setNotice] = useState<PageNotice | null>(null);
  const [issuedToken, setIssuedToken] = useState<IssuedToken | null>(null);
  const [newEmail, setNewEmail] = useState("");
  const [newDisplayName, setNewDisplayName] = useState("");
  const [newRole, setNewRole] = useState<UserRole>("guest");

  async function loadUsers() {
    try {
      setLoading(true);
      const nextUsers = await getUsers();
      setUsers(nextUsers);
      setDrafts(
        Object.fromEntries(
          nextUsers.map((user) => [
            user.id,
            {
              display_name: user.display_name,
              role: user.role,
            },
          ]),
        ),
      );
    } catch (error) {
      setNotice({
        tone: "error",
        title: "Could not load users",
        body: getErrorMessage(error),
      });
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadUsers();
  }, []);

  async function handleCreateUser(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    try {
      setCreating(true);
      setNotice(null);
      const response = await createUser({
        email: newEmail,
        display_name: newDisplayName,
        role: newRole,
      });
      setIssuedToken({ kind: "invite", response });
      setNewEmail("");
      setNewDisplayName("");
      setNewRole("guest");
      await loadUsers();
      setNotice({
        tone: "success",
        title: "User created",
        body: `Invite token issued for ${response.user.email}. Share it out-of-band so they can activate the account.`,
      });
    } catch (error) {
      setNotice({
        tone: "error",
        title: "Could not create user",
        body: getErrorMessage(error),
      });
    } finally {
      setCreating(false);
    }
  }

  async function runUserAction(userId: string, action: () => Promise<void>, successNotice: PageNotice) {
    try {
      setBusyUserId(userId);
      setNotice(null);
      await action();
      await loadUsers();
      setNotice(successNotice);
    } catch (error) {
      setNotice({
        tone: "error",
        title: "User update failed",
        body: getErrorMessage(error),
      });
    } finally {
      setBusyUserId(null);
    }
  }

  return (
    <div className="page-stack">
      <section className="panel">
        <div className="panel-header">
          <div>
            <h1>User management</h1>
            <p className="muted">Create invited accounts, change roles, deactivate access, and issue admin reset tokens.</p>
          </div>
          <button className="button button-ghost" disabled={loading} onClick={() => void loadUsers()} type="button">
            {loading ? "Loading..." : "Refresh"}
          </button>
        </div>
      </section>

      {notice ? (
        <section className={`notice ${notice.tone === "error" ? "notice-error" : "notice-success"}`}>
          <strong className="notice-title">{notice.title}</strong>
          {notice.body ? <p>{notice.body}</p> : null}
        </section>
      ) : null}

      {issuedToken ? (
        <section className="panel">
          <div className="panel-header">
            <h2>{issuedToken.kind === "invite" ? "Latest invite token" : "Latest password reset token"}</h2>
          </div>
          <div className="stack">
            <p className="muted">
              {issuedToken.kind === "invite"
                ? `Share this invite token with ${issuedToken.response.user.email}.`
                : `Share this reset token with ${issuedToken.response.user.email}.`}
            </p>
            <pre className="json-block mono">
              {issuedToken.kind === "invite"
                ? issuedToken.response.invite_token
                : issuedToken.response.reset_token}
            </pre>
            <p className="muted">
              Expires at{" "}
              {issuedToken.kind === "invite"
                ? issuedToken.response.expires_at
                : issuedToken.response.expires_at}
            </p>
          </div>
        </section>
      ) : null}

      <section className="panel">
        <div className="panel-header">
          <h2>Create user</h2>
        </div>
        <form className="field-grid" onSubmit={(event) => void handleCreateUser(event)}>
          <label className="field">
            <span>Email</span>
            <input
              className="input"
              disabled={creating}
              onChange={(event) => setNewEmail(event.target.value)}
              type="email"
              value={newEmail}
            />
          </label>
          <label className="field">
            <span>Display name</span>
            <input
              className="input"
              disabled={creating}
              onChange={(event) => setNewDisplayName(event.target.value)}
              value={newDisplayName}
            />
          </label>
          <label className="field">
            <span>Role</span>
            <select className="input" disabled={creating} onChange={(event) => setNewRole(event.target.value as UserRole)} value={newRole}>
              <option value="guest">Guest</option>
              <option value="contributor">Contributor</option>
              <option value="admin">Admin</option>
            </select>
          </label>
          <div className="field field-span-full">
            <div className="button-row wrap-row">
              <button className="button" disabled={creating || !newEmail.trim() || !newDisplayName.trim()} type="submit">
                {creating ? "Creating..." : "Create invited user"}
              </button>
            </div>
          </div>
        </form>
      </section>

      <section className="panel">
        <div className="panel-header">
          <h2>Users</h2>
          <span className="pill">{users.length}</span>
        </div>

        <div className="list">
          {users.map((user) => {
            const draft = drafts[user.id] || { display_name: user.display_name, role: user.role };
            const isBusy = busyUserId === user.id;
            const isSelf = currentUser?.id === user.id;
            const hasChanges = draft.display_name !== user.display_name || draft.role !== user.role;

            return (
              <article className="card" key={user.id}>
                <div className="panel-header">
                  <div className="stack">
                    <div className="button-row wrap-row">
                      <strong>{user.email}</strong>
                      <span className="pill">{roleLabel(user.role)}</span>
                      <span className="pill">{statusLabel(user)}</span>
                    </div>
                    <p className="muted">Created {user.created_at}</p>
                  </div>
                </div>

                <div className="field-grid">
                  <label className="field">
                    <span>Display name</span>
                    <input
                      className="input"
                      disabled={isBusy}
                      onChange={(event) =>
                        setDrafts((current) => ({
                          ...current,
                          [user.id]: {
                            ...draft,
                            display_name: event.target.value,
                          },
                        }))
                      }
                      value={draft.display_name}
                    />
                  </label>

                  <label className="field">
                    <span>Role</span>
                    <select
                      className="input"
                      disabled={isBusy}
                      onChange={(event) =>
                        setDrafts((current) => ({
                          ...current,
                          [user.id]: {
                            ...draft,
                            role: event.target.value as UserRole,
                          },
                        }))
                      }
                      value={draft.role}
                    >
                      <option value="guest">Guest</option>
                      <option value="contributor">Contributor</option>
                      <option value="admin">Admin</option>
                    </select>
                  </label>
                </div>

                <div className="button-row wrap-row">
                  <button
                    className="button"
                    disabled={isBusy || !hasChanges}
                    onClick={() =>
                      void runUserAction(
                        user.id,
                        async () => {
                          await updateUser({
                            user_id: user.id,
                            display_name: draft.display_name,
                            role: draft.role,
                          });
                        },
                        {
                          tone: "success",
                          title: "User updated",
                          body: `${user.email} now has the ${roleLabel(draft.role)} role.`,
                        },
                      )
                    }
                    type="button"
                  >
                    Save changes
                  </button>

                  <button
                    className="button button-ghost"
                    disabled={isBusy}
                    onClick={() =>
                      void runUserAction(
                        user.id,
                        async () => {
                          const response = await issuePasswordReset(user.id);
                          setIssuedToken({ kind: "reset", response });
                        },
                        {
                          tone: "success",
                          title: "Reset token issued",
                          body: `A new password reset token is ready for ${user.email}.`,
                        },
                      )
                    }
                    type="button"
                  >
                    Issue reset token
                  </button>

                  {user.status === "active" ? (
                    <button
                      className="button button-danger"
                      disabled={isBusy || isSelf}
                      onClick={() =>
                        void runUserAction(
                          user.id,
                          async () => {
                            await deactivateUser(user.id);
                          },
                          {
                            tone: "success",
                            title: "User deactivated",
                            body: `${user.email} can no longer sign in until reactivated.`,
                          },
                        )
                      }
                      type="button"
                    >
                      {isSelf ? "Current admin" : "Deactivate"}
                    </button>
                  ) : (
                    <button
                      className="button button-ghost"
                      disabled={isBusy}
                      onClick={() =>
                        void runUserAction(
                          user.id,
                          async () => {
                            await activateUser(user.id);
                          },
                          {
                            tone: "success",
                            title: "User activated",
                            body: `${user.email} can sign in again.`,
                          },
                        )
                      }
                      type="button"
                    >
                      Activate
                    </button>
                  )}
                </div>
              </article>
            );
          })}
        </div>
      </section>
    </div>
  );
}
