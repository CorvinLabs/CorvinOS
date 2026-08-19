/**
 * Panel registry (ADR-0353 P1) — the single list the shell renders routes from.
 * The ~35 simple top-level feature panels reuse the existing (proven) @/lazy-pages
 * lazy components via the `react-component` kind; vibe-engineering uses the `react`
 * load kind as the reference. Complex/param/redirect routes (personas/:name,
 * chat/:sid, workflows/:wid, engine-control redirect, index) stay hardcoded in
 * App.tsx — they are not simple panels. Nav metadata (label/icon/group) is minimal
 * for now; P3 matches requiredCapability/Flag against the backend capability
 * manifest and renders the nav from here.
 */
import { lazy, Suspense } from "react";
import { Loader2 } from "lucide-react";
import { Route } from "react-router-dom";
import type { ConsolePanel } from "./types";
import PanelHost from "./PanelHost";
import {
  DashboardPage, YourTalentPage, SettingsPage, EnginesPage, BrowserPage,
  ComputePage, BridgesPage, VoicePage, ForgePage, SkillsPage, PackagesPage,
  CoworkPage, LddPage, CompliancePage, FilesPage, SpacePage, MemoryPage,
  AgentHubPage, ConnectorsPage, ApiKeysPage, OrgsPage, PeoplePage, LicensePage,
  RAGPage, RAGHubPage, CustomProviderPage, DataSourcesPage, FlowsPage, AgentsPage,
  ExtensionsPage, McpPluginsPage, PluginsPage, ActivityFeedPage,
  LearningObjectivesPage, MultiInstancePage, VibeOverviewPage,
  GitHubPage, SyncMonitorPage, WebhooksPage, AuditPage, ReleasesPage,
} from "@/lazy-pages";
import TokenMetricsPage from "@/pages/token-metrics";
import type { ComponentType } from "react";

const rc = (route: string, label: string, component: ComponentType,
            extra?: Partial<ConsolePanel>): ConsolePanel => ({
  id: route, route, nav: { label, icon: "" },
  element: { kind: "react-component", component }, contractVersion: "1", ...extra,
});

export const PANELS: ConsolePanel[] = [
  // reference panel (react load kind)
  {
    id: "vibe-engineering", route: "vibe-engineering",
    nav: { label: "Vibe Engineering", icon: "Layers", group: "observability" },
    requiredFlag: "vibe_engineering",
    element: { kind: "react", load: () => import("@/pages/vibe-engineering") },
    contractVersion: "1",
  },
  // G2 (ADR-0370): Vibe Overview — replaces the removed Vibe Inspector, which was a
  // read-only subset of the Context Pipeline page (same /traces data) adding only
  // aggregate counters. Those counters + a CEL-flow explainer live here now, as a
  // first-party React page (no more sandboxed-iframe external panel to maintain).
  rc("vibe-overview", "Overview", VibeOverviewPage,
     { nav: { label: "Overview", icon: "" }, requiredFlag: "vibe_engineering" }),
  // Token Metrics Dashboard — Real-time token usage, cost savings, Vibe Engineering ROI
  rc("token-metrics", "Token Metrics", TokenMetricsPage,
     { nav: { label: "Token Metrics", icon: "Zap", group: "observability" }, requiredFlag: "vibe_engineering" }),
  // simple top-level feature panels (reuse proven lazy components)
  rc("dashboard", "Dashboard", DashboardPage),
  rc("talent", "Your Talent", YourTalentPage),
  rc("settings", "Settings", SettingsPage),
  rc("engines", "AI Engines", EnginesPage),
  rc("browser", "Browser", BrowserPage),
  rc("compute", "Compute", ComputePage),
  rc("bridges", "Bridges", BridgesPage),
  rc("voice", "Voice", VoicePage),
  rc("forge", "Forge", ForgePage),
  rc("skills", "Skills", SkillsPage),
  rc("packages", "Packages", PackagesPage),
  rc("cowork", "Cowork", CoworkPage),
  rc("ldd", "LDD", LddPage),
  rc("compliance", "Compliance", CompliancePage),
  rc("files", "Files", FilesPage),
  rc("space", "Space", SpacePage),
  rc("memory", "Memory", MemoryPage),
  rc("agent-hub", "Agent Hub", AgentHubPage),
  rc("connectors", "Connectors", ConnectorsPage),
  rc("api-keys", "API Keys", ApiKeysPage),
  rc("orgs", "Orgs", OrgsPage),
  rc("people", "People", PeoplePage),
  rc("license", "License", LicensePage),
  rc("rag", "RAG", RAGPage),
  rc("rag-hub", "RAG Hub", RAGHubPage),
  rc("custom-provider", "Custom Provider", CustomProviderPage),
  rc("data-sources", "Data Sources", DataSourcesPage),
  rc("flows", "Flows", FlowsPage),
  rc("agents", "Agents", AgentsPage),
  rc("extensions", "Extensions", ExtensionsPage),
  rc("mcp-plugins", "MCP Plugins", McpPluginsPage),
  rc("plugins", "Plugins", PluginsPage),
  rc("activity", "Activity", ActivityFeedPage),
  rc("learning-objectives", "Learning Objectives", LearningObjectivesPage),
  rc("multi-instance", "Multi-Instance", MultiInstancePage),
  // Cross-Device-Learning GitHub Integration (Iteration 1-5)
  rc("settings/github", "GitHub", GitHubPage,
     { nav: { label: "GitHub", icon: "Github", group: "settings" } }),
  rc("sync-monitor", "Sync Monitor", SyncMonitorPage,
     { nav: { label: "Sync Monitor", icon: "Activity" } }),
  rc("webhooks", "Webhooks", WebhooksPage,
     { nav: { label: "Webhooks", icon: "Zap" } }),
  rc("audit", "Audit", AuditPage,
     { nav: { label: "Audit", icon: "Shield" } }),
  rc("releases", "Releases", ReleasesPage,
     { nav: { label: "Releases", icon: "Package" } }),
];

export function getPanel(id: string): ConsolePanel | undefined {
  return PANELS.find((p) => p.id === id);
}

/** Render every react-kind panel as a <Route> under /app.
 *  Pass a gated subset (ADR-0357 P3: gatePanels(PANELS, manifest)) to render only
 *  the panels the backend capability manifest permits; defaults to all PANELS. */
export function panelRoutes(panels: readonly ConsolePanel[] = PANELS) {
  return panels.map((p) => {
    if (p.element.kind === "react") {
      const Lazy = lazy(p.element.load);
      return (
        <Route key={p.id} path={p.route} element={
          <Suspense fallback={<div className="flex justify-center py-12"><Loader2 className="h-6 w-6 animate-spin" /></div>}>
            <Lazy />
          </Suspense>
        } />
      );
    }
    if (p.element.kind === "react-component") {
      const C = p.element.component;
      return <Route key={p.id} path={p.route} element={<C />} />;
    }
    if (p.element.kind === "iframe") {
      // ADR-0362 P4: external panel, sandboxed, bridged via the postMessage
      // protocol. This is the safe path for community / FrontendForge panels.
      const el = p.element;
      return (
        <Route key={p.id} path={p.route}
          element={<PanelHost src={el.src} sandbox={el.sandbox} />} />
      );
    }
    return null; // web-component kind is vetted-only, lands with P7
  });
}
