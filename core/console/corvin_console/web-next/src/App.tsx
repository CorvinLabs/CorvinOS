import * as React from "react";
import { panelRoutes } from "@/panels/registry";
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
            {/* ADR-0353 P1: panels render from the registry, not hardcoded routes */}
            {panelRoutes()}
            <Route path="workflows" element={<WorkflowsListPage />} />
            <Route path="workflows/:wid" element={<WorkflowEditorPage />} />
            <Route path="workflows/:wid/runs" element={<WorkflowRunsPage />} />
            <Route path="workflows/:wid/runs/:rid" element={<WorkflowRunDetailPage />} />
            {/* Engine Control merged into the AI Engine page (Control tab). */}
            <Route path="engine-control" element={<Navigate to="/app/engines" replace />} />
            {/* ADR-0275/0277 — Multi-Instance Cross-Device Learning */}
          </Route>
          <Route path="*" element={<NotFoundPage />} />
          </Routes>
        </React.Suspense>
      </ChunkErrorBoundary>
    </AuthProvider>
  );
}
