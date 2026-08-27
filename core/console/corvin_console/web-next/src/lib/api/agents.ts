/**
 * api/agents — extracted from the former monolithic lib/api.ts.
 * Public surface is unchanged; re-exported via the ../api.ts barrel.
 */

import { api } from "./client";

// ── ADR-0131: Agent Lifecycle Governance ─────────────────────────────────────

export type AgentStatus =
  | "active"
  | "review_pending"
  | "review_overdue"
  | "pending_sunset"
  | "disabled"
  | "orphan";

export interface AgentSignOff {
  role: "it" | "business" | "compliance";
  signer: string;
  signed_at: string;
}

export interface AgentCharter {
  agent_id: string;
  name: string;
  kind: "forge_tool" | "skill";
  scope: "project" | "user" | "tenant_wide";
  status: AgentStatus;
  it_owner: string;
  business_owner: string;
  compliance_owner: string;
  problem: string;
  success_metric: string;
  baseline: number;
  target: number;
  unit: string;
  created_at: string;
  review_date: string;
  sunset_date: string;
  data_class: string;
  egress_zone: string;
  engine_allowlist: string[];
  sign_offs: AgentSignOff[];
  signed_scope: string | null;
  required_roles: string[];
  days_to_review: number;
  days_to_sunset: number;
  disabled: boolean;
  version: number;
}

export interface CreateAgentCharterRequest {
  agent_id: string;
  name: string;
  kind: "forge_tool" | "skill";
  scope: "project" | "user" | "tenant_wide";
  problem: string;
  success_metric: string;
  baseline: number;
  target: number;
  unit: string;
  it_owner: string;
  business_owner: string;
  compliance_owner: string;
  review_date: string;
  sunset_date: string;
  data_class: string;
  egress_zone: string;
  engine_allowlist?: string[];
}

export interface SignOffRequest {
  scope_target: "project" | "user" | "tenant_wide";
  role: "it" | "business" | "compliance";
}

export async function listAgents(signal?: AbortSignal): Promise<AgentCharter[]> {
  return api<AgentCharter[]>("/agents", { signal });
}

export async function getAgent(agentId: string, signal?: AbortSignal): Promise<AgentCharter> {
  return api<AgentCharter>(`/agents/${encodeURIComponent(agentId)}`, { signal });
}

export async function createAgentCharter(
  body: CreateAgentCharterRequest,
  csrf: string,
): Promise<AgentCharter> {
  return api<AgentCharter>("/agents", { method: "POST", body, csrf });
}

export async function addAgentSignOff(
  agentId: string,
  body: SignOffRequest,
  csrf: string,
): Promise<AgentCharter> {
  return api<AgentCharter>(`/agents/${encodeURIComponent(agentId)}/sign`, {
    method: "PUT",
    body,
    csrf,
  });
}

export async function revokeAgentSignOff(
  agentId: string,
  role: string,
  csrf: string,
): Promise<AgentCharter> {
  return api<AgentCharter>(`/agents/${encodeURIComponent(agentId)}/sign/${encodeURIComponent(role)}`, {
    method: "DELETE",
    csrf,
  });
}

export async function disableAgent(agentId: string, csrf: string): Promise<AgentCharter> {
  return api<AgentCharter>(`/agents/${encodeURIComponent(agentId)}/disable`, {
    method: "POST",
    csrf,
  });
}


// ── Universal Activity Hub (UAH) ─────────────────────────────────────────────

export interface ActivityEntry {
  ts: number;
  action: string;
  panel: string;
  entity_id: string;
  chat_key: string;
  summary: string;
  extra?: Record<string, string>;
  panel_label: string;
  action_label: string;
}

export interface ActivityFeedResponse {
  items: ActivityEntry[];
  returned: number;
}

export async function getActivityFeed(
  opts: { limit?: number; panel?: string; chat_key?: string } = {},
  signal?: AbortSignal,
): Promise<ActivityFeedResponse> {
  const params = new URLSearchParams();
  if (opts.limit !== undefined) params.set("limit", String(opts.limit));
  if (opts.panel) params.set("panel", opts.panel);
  if (opts.chat_key) params.set("chat_key", opts.chat_key);
  const qs = params.toString();
  return api<ActivityFeedResponse>(`/activity/feed${qs ? "?" + qs : ""}`, { signal });
}
