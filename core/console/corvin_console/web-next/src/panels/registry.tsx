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
  DashboardPage, SettingsPage,
  ComputePage, BridgesPage, VoicePage, ForgePage, SkillsPage, PackagesPage,
  LddPage, CompliancePage, FilesPage, MemoryPage,
  AgentHubPage, ConnectorsPage, ApiKeysPage, OrgsPage, PeoplePage, LicensePage,
  RAGPage, RAGHubPage, CustomProviderPage, DataSourcesPage, FlowsPage,
  MarketplaceHubPage,
  GitHubPage, SyncMonitorPage,
  QualityGatesPage, VideoProducerPage, VideoQualityMetricsPage,
  DataHubUnifiedPage, SkillForgeGeneratorPage,
  LicensingAuditPage, OTELTelemetryPage, VibeEngineeringPage, ModelsPage,
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
  ComputePage,
  BridgesPage,
  VoicePage,
  ForgePage,
  SkillsPage,
  PackagesPage,
  LddPage,
  CompliancePage,
  FilesPage,
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
  MarketplaceHubPage,
  GitHubPage,
  SyncMonitorPage,
  QualityGatesPage,
  VideoProducerPage,
  VideoQualityMetricsPage,
  DataHubUnifiedPage,
  SkillForgeGeneratorPage,
  LicensingAuditPage,
  OTELTelemetryPage,
  ModelsPage,
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
  rc("vibe-engineering", "Learnings", VibeEngineeringPage as unknown as typeof DashboardPage, { nav: { label: "Learnings", icon: "Brain", group: "primary" } }),
  rc("dashboard", "Dashboard", DashboardPage, { nav: { label: "Dashboard", icon: "LayoutDashboard", group: "primary" } }),
  rc("settings", "Settings", SettingsPage, { nav: { label: "Settings", icon: "Settings", group: "system" } }),
  rc("compute", "Compute", ComputePage, { nav: { label: "Compute", icon: "Gauge", group: "build" } }),
  rc("bridges", "Bridges", BridgesPage, { nav: { label: "Channels", icon: "Network", group: "messaging" } }),
  rc("voice", "Voice", VoicePage, { nav: { label: "Profile", icon: "AudioLines", group: "messaging" } }),
  rc("forge", "Forge", ForgePage, { nav: { label: "Forge", icon: "Hammer", group: "build" } }),
  rc("skills", "Skills", SkillsPage, { nav: { label: "Skills", icon: "BookOpen", group: "build" } }),
  rc("packages", "Packages", PackagesPage, { nav: { label: "Packages", icon: "Package", group: "build" } }),
  rc("ldd", "LDD", LddPage, { nav: { label: "Quality", icon: "Boxes", group: "system" } }),
  rc("compliance", "Compliance", CompliancePage, { nav: { label: "Audit & Compliance", icon: "ShieldCheck", group: "system" } }),
  rc("quality", "Quality Gates", QualityGatesPage, { nav: { label: "Quality Gates", icon: "CheckCircle", group: "observability" } }),
  // ADR-0695 Phase 2 — Video Quality Metrics Dashboard
  rc("video-quality-metrics", "Video Quality", VideoQualityMetricsPage, { nav: { label: "Video Quality", icon: "Gauge", group: "observability" }, requiredFlag: "video_producer_enabled" }),
  rc("files", "Files", FilesPage, { nav: { label: "Files", icon: "FolderOpen", group: "intelligence" } }),
  // REMOVED 2026-09-15: "space" panel (superseded by modern UI, no nav entry)
  rc("memory", "Memory", MemoryPage, { nav: { label: "Memory", icon: "BookOpen", group: "intelligence" } }),
  rc("agent-hub", "Agent Hub", AgentHubPage, { nav: { label: "Agent Hub", icon: "Globe2", group: "network" } }),
  rc("connectors", "Connectors", ConnectorsPage, { nav: { label: "Connectors", icon: "Plug", group: "network" } }),
  rc("api-keys", "API Keys", ApiKeysPage, { nav: { label: "API Keys", icon: "KeyRound", group: "system" } }),
  rc("orgs", "Orgs", OrgsPage, { nav: { label: "Orgs", icon: "" } }), // hidden from nav (for future)
  rc("people", "People", PeoplePage, { nav: { label: "People", icon: "" } }), // hidden from nav (for future)
  rc("license", "License", LicensePage, { nav: { label: "License", icon: "Lock", group: "system" } }),
  rc("rag", "RAG", RAGPage, { nav: { label: "RAG", icon: "Database", group: "knowledge" } }),
  rc("rag-hub", "RAG Hub", RAGHubPage, { nav: { label: "RAG Hub", icon: "Globe2", group: "knowledge" } }),
  rc("custom-provider", "Custom Provider", CustomProviderPage, { nav: { label: "Custom Provider", icon: "Plug", group: "knowledge" } }),
  rc("data-sources", "Data Sources", DataSourcesPage, { nav: { label: "Data Sources", icon: "Server", group: "knowledge" } }),
  rc("flows", "Flows", FlowsPage, { nav: { label: "Flows", icon: "Workflow", group: "knowledge" } }),
  // REMOVED 2026-09-15: "agents" panel (superseded by agent-hub, duplication)
  // Unified "Marketplace" hub (renamed from "Plugins & Extensions", 2026-09-12) —
  // the ONE sidebar entry for the three extend-CorvinOS subsystems (roadmap
  // de-dup of the plugin triple). New MarketplaceHubPage (2026-09-16) replaces
  // the old PluginCenterPage with improved UX, trending/newest, and search.
  // REMOVED (2026-09-19): extensions, mcp-plugins, plugins panels — consolidated
  // into marketplace-hub. Unused page files deleted; this is now the only entry.
  rc("marketplace-hub", "Marketplace", MarketplaceHubPage, { nav: { label: "Marketplace", icon: "Blocks", group: "marketplace" } }),
  // Cross-Device-Learning GitHub Integration (Iteration 1-5)
  rc("settings/github", "GitHub", GitHubPage,
     { nav: { label: "GitHub", icon: "Github", group: "system" } }),
  rc("sync-monitor", "Sync Monitor", SyncMonitorPage,
     { nav: { label: "Sync Monitor", icon: "Activity", group: "observability" } }),
  // REMOVED: webhooks, audit, releases — backend routes 404 (not implemented)
  // Use compliance.tsx for audit needs; GitHub integration works via settings/github
  // REMOVED 2026-09-15 (operator request): learning-dashboard panel.
  rc("datahub-unified", "DataHub", DataHubUnifiedPage,
     { nav: { label: "DataHub", icon: "Database", group: "knowledge" } }),
  rc("skill-forge-generator", "Skill Forge", SkillForgeGeneratorPage,
     { nav: { label: "Skill Forge", icon: "Sparkles", group: "build" } }),
  rc("licensing-audit", "Licensing Audit", LicensingAuditPage,
     { nav: { label: "Licensing Audit", icon: "Lock", group: "system" } }),
  rc("otel-telemetry", "OTEL Telemetry", OTELTelemetryPage,
     { nav: { label: "OTEL Telemetry", icon: "Gauge", group: "observability" } }),
  // ADR-0885 — ONE panel for routing, usage & cost, learning and the catalogue.
  // Replaced engine-config, model-cost-optimizer and model-selection on
  // 2026-09-18; App.tsx redirects the three old paths to its tabs.
  rc("models", "Models", ModelsPage, { nav: { label: "Models", icon: "Brain", group: "intelligence" } }),
  // Vibe Engineering is ONE panel: the tabbed dashboard registered above.
  // Brain Monitor · Context Intelligence · Learning Hub · Session Explorer were
  // retired on 2026-09-05 (their content is reachable as dashboard tabs);
  // Brain Status and Debug Panel had already been folded in before that.
];

export function getPanel(id: string): ConsolePanel | undefined {
  return PANELS.find((p) => p.id === id);
}

/** Generate navigation groups from panel registry (ADR-0353 P1 dynamic nav).
 *  Each panel's nav.group field determines its sidebar section. Panels without
 *  a group are hidden from the sidebar (they're still mounted as routes, but
 *  reachable only via deep-link or programmatic navigation).
 *  @param panels — list of ConsolePanel to generate nav from
 *  @returns NavGroup[] ready to render in layout.tsx
 */
export interface NavGroup {
  id: string;
  label?: string;
  collapsible?: boolean;
  defaultOpen?: boolean;
  items: Array<{ to: string; label: string; icon?: any; end?: boolean }>;
}

export function generateNavGroupsFromRegistry(panels: readonly ConsolePanel[]): NavGroup[] {
  // Map panels by their nav.group (or "hidden" if no group)
  const grouped = new Map<string, ConsolePanel[]>();
  for (const p of panels) {
    const group = p.nav.group || "hidden";
    if (!grouped.has(group)) grouped.set(group, []);
    grouped.get(group)!.push(p);
  }

  // Define group metadata (order, label, collapsible, defaultOpen)
  const groupMeta: Record<string, { label?: string; order: number; collapsible?: boolean; defaultOpen?: boolean }> = {
    primary: { order: 0 },
    marketplace: { label: "Marketplace", order: 1, collapsible: true, defaultOpen: true },
    observability: { label: "Observability", order: 2, collapsible: true, defaultOpen: true },
    messaging: { label: "Messaging", order: 3 },
    intelligence: { label: "Assistant", order: 4 },
    build: { label: "Build", order: 5, collapsible: true, defaultOpen: true },
    network: { label: "Network", order: 6, collapsible: true, defaultOpen: true },
    knowledge: { label: "Data", order: 7, collapsible: true, defaultOpen: true },
    system: { label: "System", order: 8, collapsible: true, defaultOpen: false },
  };

  // Build NavGroup[] in order
  return Array.from(grouped.entries())
    .filter(([groupId]) => groupId !== "hidden") // skip hidden group
    .map(([groupId, groupPanels]) => {
      const meta = groupMeta[groupId] || { order: 99 };
      return {
        id: groupId,
        label: meta.label,
        collapsible: meta.collapsible,
        defaultOpen: meta.defaultOpen,
        items: groupPanels
          .sort((a, b) => (a.nav.order || 0) - (b.nav.order || 0))
          .map((p) => ({
            to: `/app/${p.route}`,
            label: p.nav.label,
            icon: p.nav.icon,
          })),
      };
    })
    .sort((a, b) => (groupMeta[a.id]?.order || 99) - (groupMeta[b.id]?.order || 99));
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
      const pluginElement = p.element as { kind: "plugin-inspector"; plugin_id: string };
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
      const skillElement = p.element as { kind: "skill-inspector"; skill_id: string };
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
