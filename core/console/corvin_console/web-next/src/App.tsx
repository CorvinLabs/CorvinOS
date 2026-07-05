import * as React from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { AuthProvider, useAuth } from "@/lib/auth";
import { AppLayout } from "@/components/layout";
import { SetupGate } from "@/components/setup/SetupGate";
import { ChunkErrorBoundary } from "@/components/error-boundary";
import {
  LandingPage,
  LoginPage,
  DashboardPage,
  SettingsPage,
  EnginesPage,
  BrowserPage,
  ComputePage,
  PersonaDetailPage,
  PersonasListPage,
  BridgesPage,
  VoicePage,
  ForgePage,
  SkillsPage,
  CoworkPage,
  LddPage,
  CompliancePage,
  ChatPage,
  WorkflowsListPage,
  WorkflowEditorPage,
  WorkflowRunsPage,
  WorkflowRunDetailPage,
  FilesPage,
  SpacePage,
  MemoryPage,
  AgentHubPage,
  ConnectorsPage,
  ApiKeysPage,
  OrgsPage,
  PeoplePage,
  NotFoundPage,
  LicensePage,
  RAGPage,
  RAGHubPage,
  CustomProviderPage,
  DataSourcesPage,
  FlowsPage,
  AgentsPage,
  ExtensionsPage,
  McpPluginsPage,
  ActivityFeedPage,
  LearningObjectivesPage,
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
            <Route path="dashboard" element={<DashboardPage />} />
            <Route path="personas" element={<PersonasListPage />} />
            <Route path="personas/:name" element={<PersonaDetailPage />} />
            <Route path="bridges" element={<BridgesPage />} />
            <Route path="voice" element={<VoicePage />} />
            <Route path="forge" element={<ForgePage />} />
            <Route path="skills" element={<SkillsPage />} />
            <Route path="cowork" element={<CoworkPage />} />
            <Route path="ldd" element={<LddPage />} />
            <Route path="compliance" element={<CompliancePage />} />
            <Route path="chat" element={<ChatPage />} />
            <Route path="chat/:sid" element={<ChatPage />} />
            <Route path="agent-hub" element={<AgentHubPage />} />
            <Route path="connectors" element={<ConnectorsPage />} />
            <Route path="workflows" element={<WorkflowsListPage />} />
            <Route path="workflows/:wid" element={<WorkflowEditorPage />} />
            <Route path="workflows/:wid/runs" element={<WorkflowRunsPage />} />
            <Route path="workflows/:wid/runs/:rid" element={<WorkflowRunDetailPage />} />
            <Route path="compute" element={<ComputePage />} />
            <Route path="data-sources" element={<DataSourcesPage />} />
            <Route path="engines" element={<EnginesPage />} />
            <Route path="browser" element={<BrowserPage />} />
            {/* Engine Control merged into the AI Engine page (Control tab). */}
            <Route path="engine-control" element={<Navigate to="/app/engines" replace />} />
            <Route path="api-keys" element={<ApiKeysPage />} />
            <Route path="files" element={<FilesPage />} />
            <Route path="memory" element={<MemoryPage />} />
            <Route path="space" element={<SpacePage />} />
            <Route path="orgs" element={<OrgsPage />} />
            <Route path="people" element={<PeoplePage />} />
            <Route path="settings" element={<SettingsPage />} />
            <Route path="license" element={<LicensePage />} />
            <Route path="rag" element={<RAGPage />} />
            <Route path="rag-hub" element={<RAGHubPage />} />
            <Route path="custom-provider" element={<CustomProviderPage />} />
            <Route path="flows" element={<FlowsPage />} />
            <Route path="agents" element={<AgentsPage />} />
            <Route path="extensions" element={<ExtensionsPage />} />
            <Route path="mcp-plugins" element={<McpPluginsPage />} />
            <Route path="activity" element={<ActivityFeedPage />} />
            <Route path="learning-objectives" element={<LearningObjectivesPage />} />
          </Route>
          <Route path="*" element={<NotFoundPage />} />
          </Routes>
        </React.Suspense>
      </ChunkErrorBoundary>
    </AuthProvider>
  );
}
