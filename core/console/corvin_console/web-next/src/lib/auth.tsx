import * as React from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, setOn401Handler, setOnCsrfErrorHandler, whoami, logout as apiLogout, type WhoamiResponse } from "@/lib/api";

interface AuthContextValue {
  status: "loading" | "anonymous" | "authenticated";
  session: WhoamiResponse | null;
  logout: () => Promise<void>;
  refresh: () => void;
}

const AuthContext = React.createContext<AuthContextValue | null>(null);

/** Minimum gap between two silent renewal attempts (loop guard). */
export const RENEW_COOLDOWN_MS = 30_000;

/**
 * Re-open the session in place, without navigating away.
 *
 * Sessions end after ABSOLUTE_TIMEOUT_S (8 h) or an hour idle. The login page
 * then does exactly one thing: navigate to /v1/console/auth/local-login, which
 * (on loopback, unless CORVIN_LOCAL_AUTOLOGIN=0) mints a new session. Doing the
 * same request with fetch() gets the same cookie WITHOUT tearing down the page,
 * so an open panel keeps its state and simply continues. The server applies the
 * very same gates, so this grants nothing the login page would not.
 * Returns true only when a follow-up whoami proves the session is back.
 */
export async function renewSessionSilently(): Promise<boolean> {
  try {
    await fetch("/v1/console/auth/local-login", { credentials: "include", redirect: "manual" });
    await whoami();
    return true;
  } catch {
    return false;
  }
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const qc = useQueryClient();
  const query = useQuery({
    queryKey: ["auth", "whoami"],
    queryFn: ({ signal }) => whoami(signal),
    retry: false,
    refetchInterval: 5 * 60_000,
    staleTime: 60_000,
  });

  // A 401 on whoami means the session is gone even though react-query still
  // holds the last good whoami in `data`. Reading `data` alone kept status
  // "authenticated" forever after the 8 h expiry (2026-09-23): no renewal, no
  // redirect, every panel frozen on its last numbers while polls 401-ed.
  const expired = query.error instanceof ApiError && query.error.status === 401;
  const [renewing, setRenewing] = React.useState(false);
  // Only a renewal that was TRIED and refused makes the session anonymous.
  // Deciding "anonymous" on the first 401 render (before the effect below had
  // started the renewal) let RequireAuth bounce the tab through the login page
  // — a full navigation that dropped the operator on /app/chat.
  const [renewFailed, setRenewFailed] = React.useState(false);
  const lastRenew = React.useRef(0);
  React.useEffect(() => {
    if (!expired) {
      if (renewFailed) setRenewFailed(false);
      return;
    }
    if (renewing || renewFailed) return;
    if (Date.now() - lastRenew.current < RENEW_COOLDOWN_MS) {
      // Expired again right after a renewal: renewing cannot fix it.
      setRenewFailed(true);
      return;
    }
    lastRenew.current = Date.now();
    setRenewing(true);
    void renewSessionSilently().then(async (ok) => {
      if (ok) {
        await qc.invalidateQueries({ queryKey: ["auth"] });
        // Everything that 401-ed meanwhile fetches again with the new session.
        void qc.invalidateQueries({ predicate: (q) => q.queryKey[0] !== "auth" });
      } else {
        setRenewFailed(true);
      }
      setRenewing(false);
    });
  }, [expired, renewing, renewFailed, qc]);

  const status: AuthContextValue["status"] = query.isLoading
    ? "loading"
    : expired
      // Keep the page mounted while the session is being re-opened; only a
      // refused renewal sends the operator to the login page.
      ? (renewFailed ? "anonymous" : query.data ? "authenticated" : "loading")
      : query.data
        ? "authenticated"
        : "anonymous";

  // A 401 anywhere (not just this hook's own whoami poll) means the session
  // is gone — react immediately instead of waiting up to 5 minutes for
  // refetchInterval to notice, which used to leave every other open page's
  // query 401-ing independently in the meantime.
  React.useEffect(() => {
    let last = 0;
    setOn401Handler((path) => {
      // whoami's own 401 is already the answer — re-asking loops forever.
      if (path.startsWith("/auth/")) return;
      // Many panels 401 at once after an expiry: one re-check is enough.
      const now = Date.now();
      if (now - last < 2_000) return;
      last = now;
      void qc.invalidateQueries({ queryKey: ["auth"] });
    });
    // A 403 "invalid CSRF token" from any mutation (most visibly the automatic
    // voice-note TTS) means the cached csrf_token no longer matches the session
    // — after a session rotation or a console restart's session-store rewrite.
    // Re-fetch whoami so the shared session (and every csrf-dependent callback,
    // e.g. useVoicePlayback) picks up the current token; the next mutation then
    // works without a manual page reload.
    setOnCsrfErrorHandler(() => {
      void qc.invalidateQueries({ queryKey: ["auth", "whoami"] });
    });
    return () => {
      setOn401Handler(null);
      setOnCsrfErrorHandler(null);
    };
  }, [qc]);

  // Treat 401 as anonymous, surface anything else.
  const session = React.useMemo<WhoamiResponse | null>(() => {
    if (expired && renewFailed) return null;
    return query.data ?? null;
  }, [query.data, expired, renewFailed]);

  const value: AuthContextValue = React.useMemo(
    () => ({
      status,
      session,
      async logout() {
        await apiLogout(session?.csrf_token ?? "");
        qc.setQueryData(["auth", "whoami"], null);
        await qc.invalidateQueries({ queryKey: ["auth"] });
      },
      refresh() {
        void qc.invalidateQueries({ queryKey: ["auth"] });
      },
    }),
    [status, session, qc],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = React.useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within <AuthProvider>");
  return ctx;
}
