/**
 * api/plugins — extracted from the former monolithic lib/api.ts.
 * Public surface is unchanged; re-exported via the ../api.ts barrel.
 */

import { api } from "./client";

// ── Plugin registry (ADR-0233) ─────────────────────────────────────────────
//
// Behind the `plugin_console_surface` feature flag: every endpoint here answers
// 404 while the flag is off, which the caller must treat as "feature absent",
// not as an error worth showing.

export interface PluginSummary {
  plugin_id: string;
  version: string;
  display_name: string;
  plugin_type: string;
  /** Provenance, NOT a capability tier: builtin | vetted | community. */
  origin: string;
  pii_risk: string;
  enabled: boolean;
  /** Registered in the server process right now — can differ from `enabled` when
   *  self-healing contained or unloaded the plugin without rewriting the config. */
  runtime_loaded: boolean;
  /** Why the runtime state differs: "healing_unloaded" | "breaker_open" | … */
  contained_by: string | null;
  requires_consent: boolean;
  settings: Record<string, unknown>;
  settings_schema: Record<string, unknown>;
  dependencies: string[];
  installed_at: string | null;
  last_error_type: string | null;
}

export interface PluginListResponse {
  plugins: PluginSummary[];
  total: number;
  /** False when plugin_runtime_lifecycle is off — the UI must render read-only. */
  lifecycle_enabled: boolean;
}

export interface PluginHealthResponse {
  monitoring_enabled: boolean;
  plugins?: Record<string, { ok: boolean; message: string; details: Record<string, unknown> }>;
  breakers: Record<string, Record<string, unknown>>;
}

export async function listPlugins(signal?: AbortSignal): Promise<PluginListResponse> {
  return api<PluginListResponse>("/plugins", { signal });
}

export async function getPluginHealth(signal?: AbortSignal): Promise<PluginHealthResponse> {
  return api<PluginHealthResponse>("/plugins/health", { signal });
}

export async function enablePlugin(
  pluginId: string,
  csrf: string,
  consentGranted = false,
): Promise<PluginSummary> {
  return api<PluginSummary>(`/plugins/${encodeURIComponent(pluginId)}/enable`, {
    method: "POST",
    csrf,
    body: { consent_granted: consentGranted },
  });
}

export async function disablePlugin(pluginId: string, csrf: string): Promise<PluginSummary> {
  return api<PluginSummary>(`/plugins/${encodeURIComponent(pluginId)}/disable`, {
    method: "POST",
    csrf,
    body: {},
  });
}

export async function updatePluginSettings(
  pluginId: string,
  settings: Record<string, unknown>,
  csrf: string,
): Promise<PluginSummary> {
  return api<PluginSummary>(`/plugins/${encodeURIComponent(pluginId)}/settings`, {
    method: "POST",
    csrf,
    body: { settings },
  });
}

export async function uninstallPlugin(
  pluginId: string,
  csrf: string,
): Promise<{ uninstalled: string; audit_retained: boolean }> {
  return api(`/plugins/${encodeURIComponent(pluginId)}`, { method: "DELETE", csrf });
}


// ── Plugin-Builder scaffolds (ADR-0253) ────────────────────────────────────
//
// NOT installed plugins — a scaffold the Plugin-Builder wrote to disk from a
// `/plugin-builder` interview. Gated by `plugin_builder_enabled`, independent
// of `plugin_console_surface` above.

export interface PluginScaffoldSummary {
  plugin_id: string;
  display_name: string;
  kind: string;
  tier: string;
  plugin_type: string | null;
  path: string;
  /** Unix seconds. */
  created_at: number;
}

export interface PluginScaffoldListResponse {
  scaffolds: PluginScaffoldSummary[];
  total: number;
}

export async function listScaffoldedPlugins(
  signal?: AbortSignal,
): Promise<PluginScaffoldListResponse> {
  return api<PluginScaffoldListResponse>("/plugins/scaffolded", { signal });
}


// ── Marketplace discovery (ADR-0385 Phase 1, CONCEPT-0023 Phase 2) ─────────
//
// Browse and install plugins from the marketplace index.

export interface MarketplaceExtension {
  plugin_id: string;
  name: string;
  version: string;
  category: string;
  description: string;
  author_id: string;
  rating_average: number;
  download_count: number;
  cached?: boolean;
}

export interface MarketplaceIndexResponse {
  version: string;
  extensions: MarketplaceExtension[];
  cached?: boolean;
}

export interface ExtensionDetailsResponse {
  id: string;
  metadata: MarketplaceExtension;
  readme_url?: string;
}

export interface InstallJobResponse {
  status: "queued" | "in_progress" | "completed" | "failed";
  job_id: string;
  progress_url: string;
}

export async function listMarketplace(
  signal?: AbortSignal,
): Promise<MarketplaceIndexResponse> {
  return api<MarketplaceIndexResponse>("/api/v2/marketplace/index", { signal });
}

export async function searchMarketplace(
  query: string,
  category?: string,
  origin?: string,
  signal?: AbortSignal,
): Promise<{ extensions: MarketplaceExtension[] }> {
  const params = new URLSearchParams();
  if (query) params.append("q", query);
  if (category) params.append("category", category);
  if (origin) params.append("origin", origin);
  const qs = params.toString() ? `?${params.toString()}` : "";
  return api<{ extensions: MarketplaceExtension[] }>(`/api/v2/marketplace/search${qs}`, {
    signal,
  });
}

export async function getExtensionDetails(
  extensionId: string,
  signal?: AbortSignal,
): Promise<ExtensionDetailsResponse> {
  return api<ExtensionDetailsResponse>(`/api/v2/marketplace/extension/${encodeURIComponent(extensionId)}`, {
    signal,
  });
}

export async function installMarketplacePlugin(
  extensionId: string,
  version?: string,
  tenantId?: string,
): Promise<InstallJobResponse> {
  return api<InstallJobResponse>("/api/v2/marketplace/install", {
    method: "POST",
    body: {
      extension_id: extensionId,
      version: version || "latest",
      tenant_id: tenantId || "default",
    },
  });
}

export async function uninstallMarketplacePlugin(
  extensionId: string,
  tenantId?: string,
): Promise<{ status: "queued"; job_id: string }> {
  return api<{ status: "queued"; job_id: string }>("/api/v2/marketplace/uninstall", {
    method: "POST",
    body: {
      extension_id: extensionId,
      tenant_id: tenantId || "default",
    },
  });
}

export async function enableMarketplacePlugin(
  extensionId: string,
  tenantId?: string,
): Promise<{ status: "enabled" }> {
  return api<{ status: "enabled" }>(
    `/api/v2/marketplace/extension/${encodeURIComponent(extensionId)}/enable`,
    {
      method: "PATCH",
      body: {
        tenant_id: tenantId || "default",
      },
    },
  );
}

export async function disableMarketplacePlugin(
  extensionId: string,
  tenantId?: string,
): Promise<{ status: "disabled" }> {
  return api<{ status: "disabled" }>(
    `/api/v2/marketplace/extension/${encodeURIComponent(extensionId)}/disable`,
    {
      method: "PATCH",
      body: {
        tenant_id: tenantId || "default",
      },
    },
  );
}
