import * as React from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, logout as apiLogout, setOn401Handler, setOnCsrfErrorHandler, whoami, type WhoamiResponse } from "@/lib/api";

interface AuthContextValue {
  status: "loading" | "anonymous" | "authenticated";
  session: WhoamiResponse | null;
  logout: () => Promise<void>;
  refresh: () => void;
}

const AuthContext = React.createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const qc = useQueryClient();
  const query = useQuery({
    queryKey: ["auth", "whoami"],
    queryFn: ({ signal }) => whoami(signal),
    retry: false,
    refetchInterval: 5 * 60_000,
    staleTime: 60_000,
  });

  const status: AuthContextValue["status"] = query.isLoading
    ? "loading"
    : query.data
      ? "authenticated"
      : "anonymous";

  // A 401 anywhere (not just this hook's own whoami poll) means the session
  // is gone — react immediately instead of waiting up to 5 minutes for
  // refetchInterval to notice, which used to leave every other open page's
  // query 401-ing independently in the meantime.
  React.useEffect(() => {
    setOn401Handler(() => {
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
    if (query.data) return query.data;
    if (query.error instanceof ApiError && query.error.status === 401) return null;
    return null;
  }, [query.data, query.error]);

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
