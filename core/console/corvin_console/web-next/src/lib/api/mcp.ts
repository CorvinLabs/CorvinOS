/**
 * api/mcp — extracted from the former monolithic lib/api.ts.
 * Public surface is unchanged; re-exported via the ../api.ts barrel.
 */

import { api } from "./client";

// ── ADR-0096 M3 — MCP Plugin Manager ──────────────────────────────

export interface McpToolSecret {
  name: string;
  required: boolean;
}

export interface McpToolSummary {
  id: string;
  source: string;
  installed_at: string | null;
  runtime: { command: string; args: string[] } | null;
  compliance: { locality?: string; network_egress?: string };
  secrets: McpToolSecret[];
  active: boolean;
  active_scopes: string[];
  sha256?: string | null;
}

export interface McpToolListResponse {
  tenant_id: string;
  count: number;
  tools: McpToolSummary[];
  active: Record<string, string[]>;
}

export interface McpToolResponse {
  ok: boolean;
  tool: McpToolSummary;
}

export async function listMcpPlugins(signal?: AbortSignal): Promise<McpToolListResponse> {
  return api<McpToolListResponse>("/mcp-plugins", { signal });
}

export async function installMcpPlugin(
  source: string,
  csrf: string,
  allow_unpin = false,
): Promise<McpToolResponse> {
  return api<McpToolResponse>("/mcp-plugins/install", {
    method: "POST",
    csrf,
    body: { source, allow_unpin },
  });
}

export async function activateMcpPlugin(
  toolId: string,
  scope: string,
  csrf: string,
): Promise<McpToolResponse> {
  return api<McpToolResponse>(`/mcp-plugins/${encodeURIComponent(toolId)}/activate`, {
    method: "POST",
    csrf,
    body: { scope },
  });
}

export async function deactivateMcpPlugin(
  toolId: string,
  scope: string,
  csrf: string,
): Promise<McpToolResponse> {
  return api<McpToolResponse>(`/mcp-plugins/${encodeURIComponent(toolId)}/deactivate`, {
    method: "POST",
    csrf,
    body: { scope },
  });
}

export async function removeMcpPlugin(
  toolId: string,
  csrf: string,
): Promise<{ ok: boolean; tool_id: string }> {
  return api(`/mcp-plugins/${encodeURIComponent(toolId)}`, {
    method: "DELETE",
    csrf,
  });
}
