import * as React from "react";
import { mergePanelRoutes } from "@/panels/registry";
import PanelHost from "@/panels/PanelHost";
import { useAiPanels, aiPanelSrc } from "@/adapters/ai-panels";
import { useConsoleManifest } from "@/adapters/capabilities";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { AuthProvider, useAuth } from "@/lib/auth";
import { AppLayout } from "@/components/layout";
import { SetupGate } from "@/components/setup/SetupGate";
import { ChunkErrorBoundary } from "@/components/error-boundary";
import {
  LandingPage,
  LoginPage,
  PersonaDetailPage,
  PersonasListPage,
  ChatPage,
  WorkflowsListPage,
  WorkflowEditorPage,
  WorkflowRunsPage,
  WorkflowRunDetailPage,
  NotFoundPage,
} from "@/lazy-pages";

function RequireAuth({ children }: { children: React.ReactNode }) {
  const { status } = useAuth();
  const location = useLocation();
  if (status === "loading") {
    return (
      <div className="grid min-h-screen place-items-center text-sm text-muted-foreground">
        Loading session…
      </div>
    );
  }
  if (status !== "authenticated") {
    return <Navigate to="/login" state={{ from: location.pathname }} replace />;
  }
  return <>{children}</>;
}

function RedirectIfAuthed({ children }: { children: React.ReactNode }) {
  const { status } = useAuth();
  if (status === "authenticated") {
    return <Navigate to="/app" replace />;
  }
  return <>{children}</>;
}

function PageLoadingFallback() {
  return (
    <div className="grid min-h-screen place-items-center text-sm text-muted-foreground">
      <div className="flex flex-col items-center gap-3">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-muted-foreground border-t-foreground" />
        <p>Loading…</p>
      </div>
    </div>
  );
}

// Scroll to top whenever the pathname changes (nav clicks, back/forward).
function ScrollToTop() {
  const { pathname } = useLocation();
  React.useEffect(() => {
    window.scrollTo({ top: 0, behavior: "instant" });
  }, [pathname]);
  return null;
}

/**
 * useManifestPanelRoutes — ADR-0561 Phase 2 (manifest-driven routing)
 *
 * Returns the panel <Route> elements from two sources:
 * 1. Backend manifest (if available) — panels created, installed, or AI-generated
 * 2. Fallback registry (always) — core builtin panels for robustness
 *
 * Deduplicates routes (manifest takes precedence if a panel_id appears in both).
 * If manifest fetch fails (timeout/error), falls back to registry-only routes.
 *
 * This is a HOOK returning an array, NOT a component, on purpose: react-router's
 * <Routes> walks its children statically (createRoutesFromChildren) and throws an
 * invariant for any child element whose type is not <Route> / <Fragment>. A
 * `<ManifestPanelRoutes />` component element in that position crashed the whole
 * console at boot (blank page, "Uncaught Error" in the prod bundle — the invariant
 * message is stripped in production). See tests/unit/app-routes-static.test.tsx.
 */
function useManifestPanelRoutes(): React.ReactNode[] {
  const { data: manifest } = useConsoleManifest();
  // Manifest routes first, registry routes for everything the manifest did not
  // actually produce a route for — dedupe by produced path, see mergePanelRoutes().
  return mergePanelRoutes(manifest?.panels);
}

// SetupGate is rendered inside RequireAuth so it has access to the auth
// context and only appears for authenticated operators.
// ConsoleAssistant is now embedded in AppLayout (header button + panel).
function AuthenticatedShell() {
  return (
    <>
      <SetupGate />
      <AppLayout />
    </>
  );
}

function RootRedirect() {
  const { status } = useAuth();
  if (status === "loading") {
    return (
      <div className="grid min-h-screen place-items-center text-sm text-muted-foreground">
        Loading…
      </div>
    );
  }
  if (status === "authenticated") {
    return <Navigate to="/app" replace />;
  }
  return <Navigate to="/login" replace />;
}

export default function App() {
  // AI-generated panels (ADR-0366): mounted dynamically as sandboxed iframes, so a
  // panel the KI just created is reachable without a rebuild. Trusted (first-party,
  // same-origin API) → allow-same-origin for its credentialed fetches.
  const { data: aiPanels } = useAiPanels();
  const manifestPanelRouteElements = useManifestPanelRoutes();
  // Routes are rendered UNGATED: every panel route is mounted, always. Access is
  // enforced by the backend (auth + flags), and the capability manifest can 401
  // pre-auth — gating the ROUTES here made the vibe-engineering page (and others)
  // disappear whenever the manifest had not loaded yet (a regression). The
  // capability/flag GATING lives on the NAV instead (layout.tsx, in the authed
  // shell where the manifest loads reliably) — that is the UX surface it belongs on.
  return (
    <AuthProvider>
      <ChunkErrorBoundary>
        <ScrollToTop />
        <React.Suspense fallback={<PageLoadingFallback />}>
          <Routes>
          <Route path="/" element={<RootRedirect />} />
          <Route path="/landing" element={<LandingPage />} />
          <Route
            path="/login"
            element={
              <RedirectIfAuthed>
                <LoginPage />
              </RedirectIfAuthed>
            }
          />
          <Route
            path="/app"
            element={
              <RequireAuth>
                <AuthenticatedShell />
              </RequireAuth>
            }
          >
            <Route index element={<Navigate to="/app/chat" replace />} />
            <Route path="personas" element={<PersonasListPage />} />
            <Route path="personas/:name" element={<PersonaDetailPage />} />
            <Route path="chat" element={<ChatPage />} />
            <Route path="chat/:sid" element={<ChatPage />} />
            {/* ADR-0561 Phase 2: panels render from backend manifest + fallback registry.
                Manifest provides dynamic panels (plugin, skill, ai-generated); registry
                provides fallback core panels if manifest unavailable.
                Must be a plain array of <Route> elements — see useManifestPanelRoutes. */}
            {manifestPanelRouteElements}
            {/* ADR-0366: AI-generated panels, mounted dynamically. */}
            {(aiPanels ?? []).map((p) => (
              <Route
                key={p.id}
                path={p.id}
                element={<PanelHost src={aiPanelSrc(p.id)} sandbox="allow-scripts allow-same-origin" />}
              />
            ))}
            <Route path="workflows" element={<WorkflowsListPage />} />
            <Route path="workflows/:wid" element={<WorkflowEditorPage />} />
            <Route path="workflows/:wid/runs" element={<WorkflowRunsPage />} />
            <Route path="workflows/:wid/runs/:rid" element={<WorkflowRunDetailPage />} />
            {/* Engine Control merged into the AI Engine page (Control tab). */}
            <Route path="engine-control" element={<Navigate to="/app/engines" replace />} />
          </Route>
          <Route path="*" element={<NotFoundPage />} />
          </Routes>
        </React.Suspense>
      </ChunkErrorBoundary>
    </AuthProvider>
  );
}
