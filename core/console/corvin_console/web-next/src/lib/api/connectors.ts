/**
 * api/connectors — extracted from the former monolithic lib/api.ts.
 * Public surface is unchanged; re-exported via the ../api.ts barrel.
 */

import { ApiError, BASE, api } from "./client";
import { GraphNode, WorkflowMeta } from "./workflows";

// ── Connectors (ADR-0039 Phase 8) ──────────────────────────────────

export interface ConnectorSummary {
  id: string;
  name: string;
  category: string;
  kind: "session_mcp" | "api_key_mcp";
  icon: string;
  description: string;
  capabilities: string[];
  example_instruction: string;
  enabled: boolean;
  status: "connected" | "disabled" | "needs_key";
  api_key_label: string | null;
  api_key_set: boolean;
  config_extra: Record<string, { label: string; default: string }>;
  extra_values: Record<string, string>;
}

export interface ConnectorListResponse {
  tenant_id: string;
  count: number;
  connectors: ConnectorSummary[];
  connected_ids: string[];
}

// ── Setup / Bridge guides / Engine keys ────────────────────────────

export interface EngineInfo {
  id: string;
  label: string;
  kind: "oauth" | "api_key" | "url";
  key: string | null;
  url: string;
  configured: boolean;
  value_masked: string | null;
}

export interface EnginesResponse {
  engines: EngineInfo[];
  env_path: string;
}

export async function listEngines(signal?: AbortSignal): Promise<EnginesResponse> {
  return api<EnginesResponse>("/setup/engines", { signal });
}

export async function updateEngineKey(
  engineId: string,
  value: string,
  csrf: string,
): Promise<{ ok: true; engine_id: string; key: string }> {
  return api(`/setup/engines/${encodeURIComponent(engineId)}`, {
    method: "PUT",
    csrf,
    body: { value },
  });
}


export async function listConnectors(signal?: AbortSignal): Promise<ConnectorListResponse> {
  return api<ConnectorListResponse>("/connectors", { signal });
}

export async function updateConnector(
  cid: string,
  body: { enabled: boolean; api_key?: string; extra?: Record<string, string> },
  csrf: string,
): Promise<{ ok: true; id: string; enabled: boolean }> {
  return api(`/connectors/${encodeURIComponent(cid)}`, { method: "PUT", csrf, body });
}

export interface MessengerChat {
  id: string;
  name: string;
  label: string;
  guild?: string;
  source: "api" | "inbox";
}

export async function listMessengerChats(
  messenger: string,
  signal?: AbortSignal,
): Promise<{ chats: MessengerChat[]; count: number; messenger: string }> {
  return api(`/connectors/${encodeURIComponent(messenger)}/chats`, { signal });
}

export async function importWorkflow(
  file: File,
  csrf: string,
): Promise<{ ok: true; id: string; workflow: WorkflowMeta; graph: GraphNode[] }> {
  const form = new FormData();
  form.append("file", file, file.name);
  const res = await fetch(`${BASE}/workflows/import`, {
    method: "POST",
    credentials: "include",
    headers: { "X-CSRF-Token": csrf },
    body: form,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new ApiError(res.status, text);
  }
  return res.json();
}
