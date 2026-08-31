import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { QueryCache, MutationCache, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "./App";
// Self-hosted web fonts (zero-egress / air-gapped deployment requirement):
// the woff2 files ship in the bundle via @fontsource — no request ever leaves
// the host to a Google / rsms.me CDN on console load. See index.html note.
import "@fontsource-variable/inter";
import "@fontsource-variable/fraunces";
import "./index.css";
// Initialize console API routing (adds /console prefix to /api/* requests)
import "./lib/api-utils";
import { startTaskCleanupSchedule } from "./lib/task-lifecycle";
import { ApiError } from "./lib/api";
import {
  installPreloadErrorRecovery,
  clearPreloadErrorGuardAfterSuccessfulRender,
} from "./lib/preload-error-recovery";

// ADR-0082: Initialize task persistence
startTaskCleanupSchedule();

// Stale-deployment recovery (see lib/preload-error-recovery.ts for the full
// rationale -- live-reported "Unable to preload CSS for /console/assets/
// highlight-BEHUn5zE.css" after a console restart left a tab holding
// asset references from the OLD build).
installPreloadErrorRecovery();

const queryClient = new QueryClient({
  // Re-check session on any 401 so RequireAuth can redirect to /login immediately.
  // Guard: skip the whoami query itself to avoid a feedback loop where
  // whoami 401 → invalidate whoami → refetch → 401 → invalidate → ∞ loading.
  queryCache: new QueryCache({
    onError: (error, query) => {
      if (query.queryKey[0] === "auth" && query.queryKey[1] === "whoami") return;
      if (error instanceof ApiError && error.status === 401) {
        void queryClient.invalidateQueries({ queryKey: ["auth", "whoami"] });
      }
    },
  }),
  mutationCache: new MutationCache({
    onError: (error) => {
      if (error instanceof ApiError && error.status === 401) {
        void queryClient.invalidateQueries({ queryKey: ["auth", "whoami"] });
      }
    },
  }),
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      gcTime: 5 * 60_000,
      refetchOnWindowFocus: false,
      // Retry up to 2× on transient errors, but never on 401/403/404.
      retry: (failureCount, error) => {
        if (error instanceof ApiError && [401, 403, 404].includes(error.status)) return false;
        return failureCount < 2;
      },
    },
  },
});

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter basename="/console">
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>,
);

clearPreloadErrorGuardAfterSuccessfulRender();
