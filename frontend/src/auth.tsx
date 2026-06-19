import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

import {
  clearCsrfToken,
  getCurrentUser,
  getErrorMessage,
  login as loginRequest,
  logout as logoutRequest,
  redeemAuthToken,
} from "./api/client";
import type { CurrentUser, UserRole } from "./api/types";

type AuthStatus = "loading" | "anonymous" | "authenticated";

interface AuthContextValue {
  status: AuthStatus;
  user: CurrentUser | null;
  errorMessage: string | null;
  refreshAuth: () => Promise<void>;
  login: (payload: { email: string; password: string }) => Promise<CurrentUser>;
  logout: () => Promise<void>;
  redeemToken: (payload: { token: string; new_password: string; display_name?: string }) => Promise<CurrentUser>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

const ROLE_LEVELS: Record<UserRole, number> = {
  guest: 1,
  contributor: 2,
  admin: 3,
};

export function roleAtLeast(role: UserRole | null | undefined, minimumRole: UserRole): boolean {
  if (!role) {
    return false;
  }
  return ROLE_LEVELS[role] >= ROLE_LEVELS[minimumRole];
}

export function roleLabel(role: UserRole | null | undefined): string {
  if (!role) {
    return "Anonymous";
  }
  return role.charAt(0).toUpperCase() + role.slice(1);
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthStatus>("loading");
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  async function refreshAuth() {
    try {
      const nextUser = await getCurrentUser();
      setUser(nextUser);
      setStatus("authenticated");
      setErrorMessage(null);
    } catch (error) {
      setUser(null);
      setStatus("anonymous");
      setErrorMessage(null);
      clearCsrfToken();
      if (error instanceof Error && "status" in error && (error as { status?: unknown }).status !== 401) {
        setErrorMessage(getErrorMessage(error));
      }
    }
  }

  useEffect(() => {
    void refreshAuth();
  }, []);

  async function login(payload: { email: string; password: string }): Promise<CurrentUser> {
    const session = await loginRequest(payload);
    setUser(session.user);
    setStatus("authenticated");
    setErrorMessage(null);
    return session.user;
  }

  async function logout(): Promise<void> {
    try {
      await logoutRequest();
    } finally {
      clearCsrfToken();
      setUser(null);
      setStatus("anonymous");
    }
  }

  async function redeemToken(payload: {
    token: string;
    new_password: string;
    display_name?: string;
  }): Promise<CurrentUser> {
    const session = await redeemAuthToken(payload);
    setUser(session.user);
    setStatus("authenticated");
    setErrorMessage(null);
    return session.user;
  }

  return (
    <AuthContext.Provider
      value={{
        status,
        user,
        errorMessage,
        refreshAuth,
        login,
        logout,
        redeemToken,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error("useAuth must be used inside AuthProvider.");
  }
  return context;
}
