/**
 * api/datasources — extracted from the former monolithic lib/api.ts.
 * Public surface is unchanged; re-exported via the ../api.ts barrel.
 */

import { api } from "./client";

// ── ADR-0106 DSI v1 — Data Sources ────────────────────────────────────────

export interface DSIAdapterMeta {
  adapter_name: string;
  display_name: string;
  description: string;
  supported_formats: string[];
  locality: string;
  network_egress: string;
  config_schema: Record<string, unknown>;
  dsi_version: string;
  tier: string;
}

export interface DSIConnection {
  dsi_version: string;
  name: string;
  adapter: string;
  config: Record<string, unknown>;
  data_classification: "PUBLIC" | "INTERNAL" | "CONFIDENTIAL" | "SECRET";
  secrets?: string[];
  data_residency: string;
  tags?: string[];
  pii_scan?: boolean;
  read_only?: boolean;
  auto_refresh_schema?: boolean;
  description?: string;
  adapter_meta?: DSIAdapterMeta | null;
}

export interface DSIPingResult {
  ok: boolean;
  latency_ms: number;
  detail: string;
}

export async function listDataSources(signal?: AbortSignal): Promise<DSIConnection[]> {
  return api<DSIConnection[]>("/data-sources", { signal });
}

export async function listDataSourceAdapters(signal?: AbortSignal): Promise<DSIAdapterMeta[]> {
  return api<DSIAdapterMeta[]>("/data-sources/adapters", { signal });
}

export async function getDataSource(name: string, signal?: AbortSignal): Promise<DSIConnection> {
  return api<DSIConnection>(`/data-sources/${encodeURIComponent(name)}`, { signal });
}

export async function registerDataSource(
  manifest: Record<string, unknown>,
  csrf: string,
): Promise<DSIConnection> {
  return api<DSIConnection>("/data-sources", {
    method: "POST",
    body: { manifest },
    csrf,
  });
}

export async function testDataSource(
  name: string,
  csrf: string,
): Promise<DSIPingResult> {
  return api<DSIPingResult>(`/data-sources/${encodeURIComponent(name)}/test`, {
    method: "POST",
    body: {},
    csrf,
  });
}

export async function unregisterDataSource(
  name: string,
  csrf: string,
): Promise<void> {
  return api<void>(`/data-sources/${encodeURIComponent(name)}`, {
    method: "DELETE",
    csrf,
  });
}

export interface DSIAuditEvent {
  event_type: string;
  severity: string;
  ts: number | null;
  details: Record<string, unknown>;
}

export async function getDataSourceAudit(
  name: string,
  limit = 20,
  signal?: AbortSignal,
): Promise<DSIAuditEvent[]> {
  return api<DSIAuditEvent[]>(
    `/data-sources/${encodeURIComponent(name)}/audit?limit=${limit}`,
    { signal },
  );
}


// ── ADR-0124 M4: DSI v2 HTTP Adapter ─────────────────────────────────────────

export interface HttpAdapter {
  adapter_id: string;
  display_name: string;
  base_url_hash: string;
  auth_type: "none" | "bearer" | "api_key";
  auth_env: string | null;
  locality: string;
  network_egress: string;
  description: string;
  protocol: string;
  created_at: number;
  updated_at: number;
}

export interface HttpAdapterListResponse {
  tenant_id: string;
  count: number;
  adapters: HttpAdapter[];
}

export async function listHttpAdapters(signal?: AbortSignal): Promise<HttpAdapterListResponse> {
  return api<HttpAdapterListResponse>("/data-sources/adapters/http", { signal });
}

export interface HttpAdapterRegisterRequest {
  display_name: string;
  base_url: string;
  auth_type?: string;
  auth_env?: string | null;
  auth_header?: string | null;
  locality?: string;
  network_egress?: string;
  description?: string;
}

export async function registerHttpAdapter(
  adapter_id: string,
  body: HttpAdapterRegisterRequest,
  csrf: string,
): Promise<{ ok: boolean; adapter_id: string; updated: boolean }> {
  return api(`/data-sources/adapters/http/${encodeURIComponent(adapter_id)}`, {
    method: "PUT",
    body,
    csrf,
  });
}

export async function removeHttpAdapter(
  adapter_id: string,
  csrf: string,
): Promise<{ ok: boolean }> {
  return api(`/data-sources/adapters/http/${encodeURIComponent(adapter_id)}`, {
    method: "DELETE",
    csrf,
  });
}

export async function pingHttpAdapter(
  adapter_id: string,
  csrf: string,
): Promise<{ ok: boolean; reachable: boolean; name?: string; version?: string; error?: string }> {
  return api(`/data-sources/adapters/http/${encodeURIComponent(adapter_id)}/ping`, {
    method: "POST",
    csrf,
  });
}
