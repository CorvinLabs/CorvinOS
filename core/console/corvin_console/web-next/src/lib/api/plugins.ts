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

// Marketplace browsing/installing goes through the ADR-0511 v1 API directly from
// panels/marketplace.tsx (`/api/v1/marketplace/plugins`, `/plugins/{id}/install`,
// `/install/{job}/progress`). The former `/api/v2/marketplace/*` client that lived
// here targeted an API that was never mounted (every call 404ed) and had no
// callers — removed 2026-09-06.
