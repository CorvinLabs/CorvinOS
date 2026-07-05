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

export const EnginesPage = React.lazy(() =>
  import("@/pages/engines").then((m) => ({ default: m.EnginesPage }))
);

export const BrowserPage = React.lazy(() =>
  import("@/pages/browser").then((m) => ({ default: m.BrowserPage }))
);

export const EngineControlPage = React.lazy(() =>
  import("@/pages/engine-control").then((m) => ({ default: m.EngineControlPage }))
);

export const ComputePage = React.lazy(() =>
  import("@/pages/compute").then((m) => ({ default: m.ComputePage }))
);

export const ActivityFeedPage = React.lazy(() =>
  import("@/pages/activity").then((m) => ({ default: m.ActivityFeedPage }))
);

export const PersonasListPage = React.lazy(() =>
  import("@/pages/personas").then((m) => ({ default: m.PersonasListPage }))
);

export const PersonaDetailPage = React.lazy(() =>
  import("@/pages/personas").then((m) => ({ default: m.PersonaDetailPage }))
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

export const CoworkPage = React.lazy(() =>
  import("@/pages/cowork").then((m) => ({ default: m.CoworkPage }))
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

export const SpacePage = React.lazy(() =>
  import("@/pages/space").then((m) => ({ default: m.SpacePage }))
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

export const AgentsPage = React.lazy(() =>
  import("@/pages/agents").then((m) => ({ default: m.AgentsPage }))
);

export const ExtensionsPage = React.lazy(() =>
  import("@/pages/extensions").then((m) => ({ default: m.ExtensionsPage }))
);

export const McpPluginsPage = React.lazy(() =>
  import("@/pages/mcp-plugins").then((m) => ({ default: m.default }))
);

export const LearningObjectivesPage = React.lazy(() =>
  import("@/pages/learning-objectives").then((m) => ({ default: m.LearningObjectivesPage }))
);
