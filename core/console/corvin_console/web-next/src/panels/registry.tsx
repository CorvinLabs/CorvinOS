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
import { MarketplacePanel } from "./marketplace";
import { SkillsOverviewPanel } from "@/components/SkillsOverviewPanel";
import { ApprovalControlPanel } from "./ApprovalControlPanel";
import L5MetricsMonitor from "./L5MetricsMonitor";
import { VibeDashboard } from "@/pages/vibe-engineering";
import {
  DashboardPage, SettingsPage, EnginesPage, BrowserPage,
  ComputePage, BridgesPage, VoicePage, ForgePage, SkillsPage, PackagesPage,
  CoworkPage, LddPage, CompliancePage, FilesPage, SpacePage, MemoryPage,
  AgentHubPage, ConnectorsPage, ApiKeysPage, OrgsPage, PeoplePage, LicensePage,
  RAGPage, RAGHubPage, CustomProviderPage, DataSourcesPage, FlowsPage, AgentsPage,
  ExtensionsPage, McpPluginsPage, PluginsPage, PluginCenterPage, ActivityFeedPage,
  GitHubPage, SyncMonitorPage, WebhooksPage, AuditPage, ReleasesPage,
  BrainMonitorPage, ContextIntelligencePage, LearningHubPage, SessionExplorerPage,
} from "@/lazy-pages";
import type { ComponentType } from "react";
import type { PanelDescriptor } from "@/adapters/capabilities";
import { GenericPluginInspector } from "@/components/GenericPluginInspector";
import { SkillInspector } from "@/components/SkillInspector";

// ─ Manifest rendering support (ADR-0561) ────────────────────────────────────

/** Map of component names (from manifest) to actual React components (lazy-loaded).
 *  Used by manifestPanelRoutes() to resolve "component": "DashboardPage" strings
 *  to actual components. Add new pages here + to lazy-pages.ts when adding panels.
 */
const COMPONENTS_BY_NAME: Record<string, ComponentType> = {
  DashboardPage,
  SettingsPage,
  EnginesPage,
  BrowserPage,
  ComputePage,
  BridgesPage,
  VoicePage,
  ForgePage,
  SkillsPage,
  SkillsOverviewPanel: SkillsOverviewPanel as unknown as ComponentType,
  ApprovalControlPanel: ApprovalControlPanel as unknown as ComponentType,
  L5MetricsMonitor: L5MetricsMonitor as unknown as ComponentType,
  PackagesPage,
  CoworkPage,
  LddPage,
  CompliancePage,
  FilesPage,
  SpacePage,
  MemoryPage,
  AgentHubPage,
  ConnectorsPage,
  ApiKeysPage,
  OrgsPage,
  PeoplePage,
  LicensePage,
  RAGPage,
  RAGHubPage,
  CustomProviderPage,
  DataSourcesPage,
  FlowsPage,
  AgentsPage,
  ExtensionsPage,
  McpPluginsPage,
  PluginsPage,
  PluginCenterPage,
  ActivityFeedPage,
  GitHubPage,
  SyncMonitorPage,
  WebhooksPage,
  AuditPage,
  ReleasesPage,
  BrainMonitorPage,
  ContextIntelligencePage,
  LearningHubPage,
  SessionExplorerPage,
};

const rc = (route: string, label: string, component: ComponentType,
            extra?: Partial<ConsolePanel>): ConsolePanel => ({
  id: route, route, nav: { label, icon: "" },
  element: { kind: "react-component", component }, contractVersion: "1", ...extra,
});

export const PANELS: ConsolePanel[] = [
  // The Vibe Engineering group's primary view (ADR-0400): the unified 3-column
  // Dashboard in src/pages/vibe-engineering/. Until 2026-08-27 a sibling FILE,
  // pages/vibe-engineering.tsx (the retired Context Pipeline page), shadowed the
  // directory — file beats directory in module resolution — so this import
  // silently loaded the old page and the Dashboard was unreachable.
  rc("vibe-engineering", "Dashboard", VibeDashboard as unknown as typeof DashboardPage, { nav: { label: "Dashboard", icon: "Layers", group: "vibe" } }),
  rc("dashboard", "Dashboard", DashboardPage),
  rc("settings", "Settings", SettingsPage),
  rc("engines", "AI Engines", EnginesPage),
  rc("browser", "Browser", BrowserPage),
  rc("compute", "Compute", ComputePage),
  rc("bridges", "Bridges", BridgesPage),
  rc("voice", "Voice", VoicePage),
  rc("forge", "Forge", ForgePage),
  rc("skills", "Skills", SkillsPage),
  rc("os-skills", "OS-Skills", SkillsOverviewPanel as unknown as ComponentType),
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
  // Unified "Plugins & Extensions" hub — the ONE sidebar entry for the three
  // extend-CorvinOS subsystems (roadmap de-dup of the plugin triple). It renders
  // the three components below as tabs. The three standalone routes stay mounted
  // for deep-link stability but are dropped from the sidebar — see NAV_EXEMPT in
  // tests/unit/panel-nav-wiring.test.ts.
  rc("plugin-center", "Plugins & Extensions", PluginCenterPage),
  rc("extensions", "Extensions", ExtensionsPage),
  rc("mcp-plugins", "MCP Plugins", McpPluginsPage),
  rc("plugins", "Plugins", PluginsPage),
  rc("marketplace", "Marketplace", MarketplacePanel, { requiredFlag: "console_marketplace_panel" }),
  rc("activity", "Activity", ActivityFeedPage),
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
  // Vibe Engineering secondary views (CONSOLE_REDESIGN_UNIFIED_CONCEPT):
  // Dashboard (above) · Brain Monitor · Context Intelligence · Learning Hub ·
  // Session Explorer. Brain Status and Debug Panel were folded into these two
  // and the Dashboard respectively; they are no longer standalone panels.
  rc("brain-monitor", "Brain Monitor", BrainMonitorPage,
     { nav: { label: "Brain Monitor", icon: "Cpu", group: "vibe" }, requiredFlag: "vibe_engineering" }),
  rc("context-intelligence", "Context Intelligence", ContextIntelligencePage,
     { nav: { label: "Context Intelligence", icon: "GitBranch", group: "vibe" }, requiredFlag: "vibe_engineering" }),
  rc("learning-hub", "Learning Hub", LearningHubPage,
     { nav: { label: "Learning Hub", icon: "Lightbulb", group: "vibe" }, requiredFlag: "vibe_engineering" }),
  rc("session-explorer", "Session Explorer", SessionExplorerPage,
     { nav: { label: "Session Explorer", icon: "History", group: "vibe" }, requiredFlag: "vibe_engineering" }),
  // L5 Phase 3: Approval Control Panel (ADR-0584)
  rc("approval-control", "L5 Approvals", ApprovalControlPanel as unknown as typeof DashboardPage,
     { nav: { label: "L5 Approvals", icon: "CheckCircle", group: "learning" }, requiredFlag: "l5_approval_panel" }),
  // L5 Phase 5: Metrics Monitor (ADR-0588)
  rc("l5-metrics-monitor", "L5 Metrics", L5MetricsMonitor as unknown as typeof DashboardPage,
     { nav: { label: "L5 Metrics", icon: "BarChart3", group: "learning" }, requiredFlag: "l5_metrics_monitor" }),
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

/**
 * Render manifest-driven panels as <Route> elements (ADR-0561, Phase 2-3).
 *
 * Converts PanelDescriptor[] (from backend manifest) to React Routes.
 * Resolves component names to actual components via COMPONENTS_BY_NAME registry.
 *
 * Supports:
 * - "react-component": lookup from COMPONENTS_BY_NAME
 * - "react": async import (deferred; TODO P3)
 * - "iframe": sandboxed PanelHost
 * - "plugin-inspector": GenericPluginInspector (ADR-0561 P3)
 * - "skill-inspector": SkillInspector (ADR-0561 P3)
 */
export function manifestPanelRoutes(panels: readonly PanelDescriptor[]) {
  return panels.map((p) => {
    if (p.element.kind === "react-component") {
      // Resolve component name from manifest to actual React component
      const componentName = p.element.component;
      const C = COMPONENTS_BY_NAME[componentName];
      if (!C) {
        console.warn(`manifestPanelRoutes: unknown component "${componentName}" for panel "${p.id}"`);
        return null;
      }
      return <Route key={p.id} path={p.route} element={<C />} />;
    }
    if (p.element.kind === "react") {
      // Async import path from manifest — deferred for P3 (dynamic import resolution)
      console.warn(`manifestPanelRoutes: "react" kind not yet supported for panel "${p.id}"`);
      return null;
    }
    if (p.element.kind === "iframe") {
      return (
        <Route key={p.id} path={p.route}
          element={<PanelHost src={p.element.src} sandbox="allow-scripts allow-same-origin" />} />
      );
    }
    if (p.element.kind === "plugin-inspector") {
      // ADR-0561 P3: Generic plugin panel (config + audit + enable/disable)
      const pluginElement = p.element as any;
      return (
        <Route key={p.id} path={p.route}
          element={
            <GenericPluginInspector
              pluginId={pluginElement.plugin_id}
              title={p.title}
              version={p.version}
              enabled={true}
              onToggleEnabled={() => {}}
            />
          } />
      );
    }
    if (p.element.kind === "skill-inspector") {
      // ADR-0561 P3: Generic skill panel (learning + audit + config)
      const skillElement = p.element as any;
      return (
        <Route key={p.id} path={p.route}
          element={
            <SkillInspector
              skillId={skillElement.skill_id}
              title={p.title}
              version={p.version}
            />
          } />
      );
    }
    return null;
  });
}

/**
 * ADR-0561 Phase 2 — the route set the console actually mounts: manifest routes
 * first, then every registry panel the manifest did NOT produce a route for.
 *
 * Dedupe is by ROUTE PATH of the routes actually PRODUCED, never by manifest panel
 * id. The manifest may list a panel this bundle cannot render yet (`react` kind is
 * unsupported, a `component` name may be unknown) — manifestPanelRoutes() then
 * returns null for it, and the registry route MUST take over. Keying the dedupe on
 * the manifest's ids instead dropped `/app/vibe-engineering` the moment the
 * manifest loaded (2026-09-03: "The address /app/vibe-engineering doesn't exist").
 * Pure; unit-tested in tests/unit/merge-panel-routes.test.tsx.
 */
export function mergePanelRoutes(
  manifestPanels: readonly PanelDescriptor[] | undefined,
  registryPanels: readonly ConsolePanel[] = PANELS,
) {
  const manifestRoutes = manifestPanels ? manifestPanelRoutes(manifestPanels) : [];
  const produced = new Set(
    manifestRoutes.filter((r) => r != null).map((r) => String(r!.props.path)),
  );
  const fallbackRoutes = panelRoutes(registryPanels).filter(
    (r) => r != null && !produced.has(String(r.props.path)),
  );
  return [...manifestRoutes, ...fallbackRoutes];
}
