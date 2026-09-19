import * as React from "react";
import { mergePanelRoutes } from "@/panels/registry";
import PanelHost from "@/panels/PanelHost";
import { useAiPanels, aiPanelSrc } from "@/adapters/ai-panels";
import { useConsoleManifest } from "@/adapters/capabilities";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { AuthProvider, useAuth } from "@/lib/auth";
import { AppLayout } from "@/components/layout";
import { ChunkErrorBoundary } from "@/components/error-boundary";
import {
  LandingPage,
  LoginPage,
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
    // Keep the WHOLE deep link (path + query): a bounce through local-login
    // used to land on /console/ and every old bookmark lost its ?tab= (ADR-0885).
    return <Navigate to="/login" state={{ from: location.pathname + location.search }} replace />;
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

// The first-run onboarding gate (engine/bridge wizard + spoken welcome) was
// removed so a fresh install lands directly on the chat console — the
// installer now marks onboarding complete itself (corvinOS/installer/core.py
// step_18_finalise) instead of waiting for a "Finish" click in a modal.
// ConsoleAssistant is now embedded in AppLayout (header button + panel).
function AuthenticatedShell() {
  return <AppLayout />;
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
            {/* ADR-0885 (2026-09-18): Engine Config, Model Cost Optimizer and Model
                Selection were folded into the Models console. The three old paths
                (and the two older /app/engines aliases) redirect to its tabs. These
                sit AFTER the panel routes on purpose: react-router matches the
                earlier sibling, so a redirect for a path that a mounted panel still
                owns would be dead — the panels were removed in the same commit. */}
            <Route path="engine-config" element={<Navigate to="/app/models?tab=routing" replace />} />
            <Route path="model-cost-optimizer" element={<Navigate to="/app/models?tab=usage-cost" replace />} />
            <Route path="model-selection" element={<Navigate to="/app/models?tab=catalog" replace />} />
            <Route path="engine-control" element={<Navigate to="/app/models?tab=routing" replace />} />
            <Route path="engines" element={<Navigate to="/app/models?tab=routing" replace />} />
            {/* ADR-0892 — ONE marketplace. The old hub, the manifest's plugin-center
                (a deleted component that rendered the 404 page), the packages page
                and the three panels folded on 2026-09-16 all land on its tabs. */}
            <Route path="marketplace-hub" element={<Navigate to="/app/marketplace?tab=browse" replace />} />
            <Route path="plugin-center" element={<Navigate to="/app/marketplace?tab=installed" replace />} />
            <Route path="plugins" element={<Navigate to="/app/marketplace?tab=installed" replace />} />
            <Route path="extensions" element={<Navigate to="/app/marketplace?tab=installed" replace />} />
            <Route path="mcp-plugins" element={<Navigate to="/app/marketplace?tab=tools" replace />} />
            <Route path="packages" element={<Navigate to="/app/marketplace?tab=packages" replace />} />
          </Route>
          <Route path="*" element={<NotFoundPage />} />
          </Routes>
        </React.Suspense>
      </ChunkErrorBoundary>
    </AuthProvider>
  );
}
