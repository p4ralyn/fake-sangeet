/**
 * Bootstraps GET /api/v1/me and exposes the current user, roles and CSRF
 * token via a React context. signIn() is a full-page navigate (never a
 * fetch — the server owns the OIDC/dev-picker dance). signOut() posts with
 * the CSRF header and follows the server's redirect.
 */
import {
  createContext,
  createElement,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import type { ReactNode } from "react";
import { ApiError, SignedOutError, api, setCsrfToken } from "./api";
import type { MeResponse, Role } from "./types";

export interface SessionState {
  /** undefined while the initial /me call is in flight. */
  user: MeResponse | undefined;
  roles: Role[];
  csrfToken: string | null;
  loading: boolean;
  /** Non-null when the initial /me call failed for a reason other than
   * "signed out" (e.g. the event isn't seeded). */
  error: ApiError | null;
  signIn: (next?: string) => void;
  signOut: () => Promise<void>;
  refresh: () => Promise<void>;
}

const SessionContext = createContext<SessionState | null>(null);

async function loadMe(): Promise<MeResponse | undefined> {
  try {
    const me = await api.get<MeResponse>("/api/v1/me");
    setCsrfToken(me.csrf_token);
    return me;
  } catch (err) {
    if (err instanceof SignedOutError) {
      setCsrfToken(null);
      return undefined;
    }
    throw err;
  }
}

export function SessionProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<MeResponse | undefined>(undefined);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ApiError | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const me = await loadMe();
      setUser(me);
    } catch (err) {
      setUser(undefined);
      setError(err instanceof ApiError ? err : new ApiError(0, "INTERNAL", "Could not reach the server.", null, null));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const signIn = useCallback((next?: string) => {
    const target = next && next.startsWith("/") ? next : window.location.pathname;
    window.location.assign(`/auth/start?next=${encodeURIComponent(target)}`);
  }, []);

  const signOut = useCallback(async () => {
    const response = await fetch("/auth/logout", {
      method: "POST",
      credentials: "same-origin",
      headers: user?.csrf_token ? { "X-CSRF-Token": user.csrf_token } : undefined,
    });
    setCsrfToken(null);
    setUser(undefined);
    // The server redirects to "/" (its own login page) — follow it with a
    // full navigation so cookies clear cleanly.
    window.location.assign(response.redirected ? response.url : "/");
  }, [user]);

  const value = useMemo<SessionState>(
    () => ({
      user,
      roles: user?.roles ?? [],
      csrfToken: user?.csrf_token ?? null,
      loading,
      error,
      signIn,
      signOut,
      refresh,
    }),
    [user, loading, error, signIn, signOut, refresh],
  );

  return createElement(SessionContext.Provider, { value }, children);
}

export function useSession(): SessionState {
  const ctx = useContext(SessionContext);
  if (!ctx) {
    throw new Error("useSession must be used within a SessionProvider");
  }
  return ctx;
}
