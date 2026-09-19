/**
 * Marketplace API surface (ADR-0892). Every call goes through
 * `lib/api/client.ts::api()` — base `/v1/console`, JSON body, `X-CSRF-Token`
 * on writes, `ApiError` on non-2xx. The one exception is the package upload,
 * which is multipart: it uses `fetch` directly with the SAME base and header,
 * because `api()` JSON-encodes every body.
 *
 * Four real backends, one module:
 *  - plugin index + install:  /api/v1/marketplace/*   (marketplace.py + marketplace_install.py)
 *  - installed plugins:       /plugins/*              (plugins.py, lib/api/plugins.ts)
 *  - skill packages:          /packages/*             (packages.py, ADR-0268)
 *  - MCP tools:               /mcp-plugins/*          (mcp_plugins.py, ADR-0096)
 */
import { BASE, api, ApiError } from "@/lib/api/client";
export {
  listPlugins as listInstalledPlugins,
  getPluginHealth,
  enablePlugin,
  disablePlugin,
  uninstallPlugin,
  updatePluginSettings,
  type PluginSummary,
  type PluginListResponse,
  type PluginHealthResponse,
} from "@/lib/api/plugins";

// ── Marketplace index (ADR-0511) with local state (ADR-0892 D2) ──────────

export interface IndexPlugin {
  id: string;
  type: string;
  name: string;
  version: string;
  author: string;
  license: string;
  license_url?: string;
  tier: "buildin" | "contributor" | string;
  category: string;
  description: string;
  readme_url?: string;
  distribution?: { supports_source?: boolean; supports_wheel?: boolean; source_url?: string };
  dependencies?: string[];
  requires_version?: string;
  boot_layer?: string;
  sla_level?: string;
  tags?: string[];
  /** Local state, computed by the same resolution the install route runs. */
  registry_id: string | null;
  installable: boolean;
  install_blocker: string | null;
  installed: boolean;
  enabled: boolean;
  runtime_loaded: boolean;
}

export interface IndexListResponse {
  plugins: IndexPlugin[];
  count: number;
  filtered_by: { category: string | null; tier: string | null };
}

export interface IndexStats {
  total_plugins: number;
  by_category: Record<string, number>;
  by_tier: Record<string, number>;
  schema_version?: string;
  generated_at?: string;
}

export function listIndex(signal?: AbortSignal): Promise<IndexListResponse> {
  return api<IndexListResponse>("/api/v1/marketplace/plugins?limit=1000", { signal });
}

export function getIndexStats(signal?: AbortSignal): Promise<IndexStats> {
  return api<IndexStats>("/api/v1/marketplace/stats", { signal });
}

/** The install job is SYNCHRONOUS: the response already carries the final
 *  status. `failed` is a 200 with `error` — an operator-readable reason
 *  (index unknown, no local source, manifest gate, lifecycle off). A licence
 *  denial is a 403 `ApiError` with `detail.error === "license_required"`. */
export interface InstallResult {
  status: "completed" | "failed";
  job_id: string;
  plugin_id: string;
  registry_id?: string;
  version?: string;
  already_installed?: boolean;
  error?: string;
  /** Provenance of the installed record (`community` needs consent on enable). */
  origin?: string;
  requires_consent?: boolean;
}

export function installIndexPlugin(indexId: string, version: string, csrf: string): Promise<InstallResult> {
  return api<InstallResult>(`/api/v1/marketplace/plugins/${encodeURIComponent(indexId)}/install`, {
    method: "POST",
    csrf,
    body: { version },
  });
}

/** The install as a JOB (ADR-0892 amendment): the backend advances it phase by
 *  phase on a worker thread — index check, source resolution, manifest gate,
 *  licence, record, registration — and `getInstallProgress` reads the phase
 *  it has REACHED. The bar the SPA draws is those phases, never a timer. */
export interface InstallJob {
  job_id: string;
  plugin_id: string;
  tenant_id: string;
  status: "pending" | "downloading" | "installing" | "completed" | "failed";
  progress: number;
  message: string;
  created_at: string;
  updated_at: string;
  error: string | null;
}

export function startInstallJob(indexId: string, version: string, csrf: string): Promise<InstallJob> {
  return api<InstallJob>(`/api/v1/marketplace/plugins/${encodeURIComponent(indexId)}/install`, {
    method: "POST",
    csrf,
    body: { version, wait: false },
  });
}

export function getInstallProgress(jobId: string, signal?: AbortSignal): Promise<InstallJob> {
  return api<InstallJob>(`/api/v1/marketplace/install/${encodeURIComponent(jobId)}/progress`, { signal });
}

// ── Skill packages (ADR-0268) ────────────────────────────────────────────

export interface PackageInfo {
  package_id: string;
  version: string;
  display_name: string;
  description: string;
  author: string;
  installed_at: string;
  tenant_id: string;
}

export interface PackageDetails extends PackageInfo {
  license: string;
  manifest: Record<string, unknown>;
  dependencies: string[];
  permissions: Array<{ permission: string; required: boolean; description: string }>;
}

export interface PackageUploadResponse {
  status: string;
  package_id: string;
  version: string;
  display_name: string;
}

export function listPackages(signal?: AbortSignal): Promise<{ packages: PackageInfo[]; total: number }> {
  return api("/packages", { signal });
}

export function getPackageDetails(id: string, signal?: AbortSignal): Promise<PackageDetails> {
  return api<PackageDetails>(`/packages/${encodeURIComponent(id)}/details`, { signal });
}

export function deletePackage(id: string, csrf: string): Promise<unknown> {
  return api(`/packages/${encodeURIComponent(id)}`, { method: "DELETE", csrf });
}

/** Multipart, so not `api()`: same base, same cookie, same CSRF header. */
export async function uploadPackage(file: File, csrf: string): Promise<PackageUploadResponse> {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(`${BASE}/packages/upload`, {
    method: "POST",
    headers: { Accept: "application/json", "X-CSRF-Token": csrf },
    credentials: "include",
    body: form,
  });
  let payload: unknown = null;
  try { payload = await res.json(); } catch { /* non-JSON error body */ }
  if (!res.ok) throw new ApiError(res.status, payload);
  return payload as PackageUploadResponse;
}

// ── MCP tools (ADR-0096) ─────────────────────────────────────────────────

export interface McpTool {
  id: string;
  source: string;
  installed_at: string | null;
  runtime: unknown;
  compliance: Record<string, unknown>;
  secrets: Array<{ name: string; required: boolean }>;
  active: boolean;
  active_scopes: string[];
  sha256: string | null;
}

export interface McpToolList {
  tenant_id: string;
  count: number;
  tools: McpTool[];
  active: Record<string, string[]>;
}

export type McpScope = "user" | "tenant";

export function listTools(signal?: AbortSignal): Promise<McpToolList> {
  return api<McpToolList>("/mcp-plugins", { signal });
}

export function installTool(source: string, allowUnpin: boolean, csrf: string): Promise<{ ok: boolean; tool: McpTool }> {
  return api("/mcp-plugins/install", { method: "POST", csrf, body: { source, allow_unpin: allowUnpin } });
}

export function activateTool(id: string, scope: McpScope, csrf: string): Promise<{ ok: boolean; tool: McpTool }> {
  return api(`/mcp-plugins/${encodeURIComponent(id)}/activate`, { method: "POST", csrf, body: { scope } });
}

export function deactivateTool(id: string, scope: McpScope, csrf: string): Promise<{ ok: boolean; tool: McpTool }> {
  return api(`/mcp-plugins/${encodeURIComponent(id)}/deactivate`, { method: "POST", csrf, body: { scope } });
}

export function removeTool(id: string, csrf: string): Promise<unknown> {
  return api(`/mcp-plugins/${encodeURIComponent(id)}`, { method: "DELETE", csrf });
}

/** 503 = the manager is genuinely absent on this build; 404 = the surface is
 *  switched off by a flag. Both are "not available here", never an error toast. */
export const isUnavailable = (e: unknown): e is ApiError =>
  e instanceof ApiError && (e.status === 503 || e.status === 404);
