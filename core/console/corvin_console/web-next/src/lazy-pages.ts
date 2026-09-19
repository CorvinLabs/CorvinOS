import * as React from "react";

// Lazy load all page components to enable route-based code splitting.
// Only the page(s) on the current route load into memory.

export const LandingPage = React.lazy(() =>
  import("@/pages/landing").then((m) => ({ default: m.LandingPage }))
);

export const LoginPage = React.lazy(() =>
  import("@/pages/login").then((m) => ({ default: m.LoginPage }))
);

export const DashboardPage = React.lazy(() =>
  import("@/pages/dashboard").then((m) => ({ default: m.DashboardPage }))
);


export const SettingsPage = React.lazy(() =>
  import("@/pages/settings").then((m) => ({ default: m.SettingsPage }))
);


export const ComputePage = React.lazy(() =>
  import("@/pages/compute").then((m) => ({ default: m.ComputePage }))
);



export const BridgesPage = React.lazy(() =>
  import("@/pages/bridges").then((m) => ({ default: m.BridgesPage }))
);

export const VoicePage = React.lazy(() =>
  import("@/pages/voice").then((m) => ({ default: m.VoicePage }))
);

export const ForgePage = React.lazy(() =>
  import("@/pages/forge").then((m) => ({ default: m.ForgePage }))
);

export const SkillsPage = React.lazy(() =>
  import("@/pages/skills").then((m) => ({ default: m.SkillsPage }))
);

export const PackagesPage = React.lazy(() =>
  import("@/pages/packages").then((m) => ({ default: m.PackagesPage }))
);

export const LddPage = React.lazy(() =>
  import("@/pages/ldd").then((m) => ({ default: m.LddPage }))
);

export const CompliancePage = React.lazy(() =>
  import("@/pages/compliance").then((m) => ({ default: m.CompliancePage }))
);

export const ChatPage = React.lazy(() =>
  import("@/pages/chat").then((m) => ({ default: m.ChatPage }))
);

export const WorkflowsListPage = React.lazy(() =>
  import("@/pages/workflows").then((m) => ({ default: m.WorkflowsListPage }))
);

export const WorkflowEditorPage = React.lazy(() =>
  import("@/pages/workflows").then((m) => ({ default: m.WorkflowEditorPage }))
);

export const WorkflowRunsPage = React.lazy(() =>
  import("@/pages/workflows").then((m) => ({ default: m.WorkflowRunsPage }))
);

export const WorkflowRunDetailPage = React.lazy(() =>
  import("@/pages/workflows").then((m) => ({ default: m.WorkflowRunDetailPage }))
);

export const FilesPage = React.lazy(() =>
  import("@/pages/files").then((m) => ({ default: m.FilesPage }))
);

export const AgentHubPage = React.lazy(() =>
  import("@/pages/agent-hub").then((m) => ({ default: m.AgentHubPage }))
);

export const ConnectorsPage = React.lazy(() =>
  import("@/pages/connectors").then((m) => ({ default: m.ConnectorsPage }))
);

export const ApiKeysPage = React.lazy(() =>
  import("@/pages/api-keys").then((m) => ({ default: m.ApiKeysPage }))
);

export const OrgsPage = React.lazy(() =>
  import("@/pages/orgs").then((m) => ({ default: m.OrgsPage }))
);

export const PeoplePage = React.lazy(() =>
  import("@/pages/people").then((m) => ({ default: m.PeoplePage }))
);

export const NotFoundPage = React.lazy(() =>
  import("@/pages/not-found").then((m) => ({ default: m.NotFoundPage }))
);

export const LicensePage = React.lazy(() =>
  import("@/pages/license").then((m) => ({ default: m.LicensePage }))
);

export const RAGPage = React.lazy(() =>
  import("@/pages/rag").then((m) => ({ default: m.default }))
);

export const FlowsPage = React.lazy(() =>
  import("@/app/console/flows/page").then((m) => ({ default: m.default }))
);

export const RAGHubPage = React.lazy(() =>
  import("@/pages/rag-hub").then((m) => ({ default: m.default }))
);

export const CustomProviderPage = React.lazy(() =>
  import("@/pages/custom-provider").then((m) => ({ default: m.default }))
);

export const DataSourcesPage = React.lazy(() =>
  import("@/pages/data-sources").then((m) => ({ default: m.DataSourcesPage }))
);

export const MemoryPage = React.lazy(() =>
  import("@/pages/memory").then((m) => ({ default: m.MemoryPage }))
);

// REMOVED (2026-09-19): ExtensionsPage, McpPluginsPage, PluginsPage
// These three panels were consolidated into MarketplaceHubPage (ADR-0561 P2, 2026-09-16).
// The corresponding files /src/pages/{extensions,mcp-plugins,plugins}.tsx have been deleted.

// Marketplace Hub — the unified discovery + install experience for plugins, extensions, and MCP servers
export const MarketplaceHubPage = React.lazy(() =>
  import("@/pages/marketplace-hub").then((m) => ({ default: m.MarketplaceHub }))
);


// Resolves to src/pages/vibe-engineering/index.tsx (VibeDashboard, ADR-0400/0728).
// A sibling FILE pages/vibe-engineering.tsx wins over the directory silently —
// it shadowed this dashboard twice (2026-08-27 ADR-0431, 2026-09-17 95ecc2b6);
// tests/unit/page-dir-shadow.test.ts fails on the next one.
export const VibeEngineeringPage = React.lazy(() =>
  import("@/pages/vibe-engineering").then((m) => ({ default: m.default }))
);




// Cross-Device-Learning GitHub Integration
export const GitHubPage = React.lazy(() =>
  import("@/pages/github").then((m) => ({ default: m.default }))
);

export const SyncMonitorPage = React.lazy(() =>
  import("@/pages/sync-monitor").then((m) => ({ default: m.default }))
);

// REMOVED: WebhooksPage, AuditPage, ReleasesPage — backend routes return 404
// These are not implemented and should not be referenced

export const QualityGatesPage = React.lazy(() =>
  import("@/pages/quality").then((m) => ({ default: m.default }))
);

export const VideoProducerPage = React.lazy(() =>
  import("@/pages/video-producer")
);

export const VideoQualityMetricsPage = React.lazy(() =>
  import("@/panels/video-quality-metrics").then((m) => ({ default: m.VideoQualityMetricsPanel }))
);

export const DataHubUnifiedPage = React.lazy(() =>
  import("@/pages/datahub-unified").then((m) => ({ default: m.default }))
);

export const SkillForgeGeneratorPage = React.lazy(() =>
  import("@/pages/skill-forge-generator").then((m) => ({ default: m.default }))
);

export const LicensingAuditPage = React.lazy(() =>
  import("@/pages/licensing-audit").then((m) => ({ default: m.LicensingAuditPage }))
);

export const OTELTelemetryPage = React.lazy(() =>
  import("@/pages/otel-telemetry").then((m) => ({ default: m.OTELTelemetryPage }))
);

/** ADR-0885 — the Models console (routing · usage & cost · learning · catalog). */
export const ModelsPage = React.lazy(() =>
  import("@/pages/models").then((m) => ({ default: m.ModelsPage }))
);

// Vibe Engineering panels (ADR-0400 dashboard + secondary views)

