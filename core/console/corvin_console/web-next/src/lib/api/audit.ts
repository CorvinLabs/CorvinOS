/**
 * api/audit — extracted from the former monolithic lib/api.ts.
 * Public surface is unchanged; re-exported via the ../api.ts barrel.
 */

import { api } from "./client";

// ── Audit tail + members (for Compliance page) ────────────────────

export interface AuditEvent {
  ts: number | null;
  event_type: string;
  severity: string;
  hash_prefix: string | null;
  run_id: string | null;
  tool: string | null;
  details: Record<string, unknown>;
}

export interface AuditTailResponse {
  tenant_id: string;
  ts: number;
  count: number;
  chain_size_b?: number;
  events: AuditEvent[];
}

export async function auditTail(
  params: { limit?: number; severity?: string; eventPrefix?: string } = {},
  signal?: AbortSignal,
): Promise<AuditTailResponse> {
  const q = new URLSearchParams();
  if (params.limit) q.set("limit", String(params.limit));
  if (params.severity) q.set("severity", params.severity);
  if (params.eventPrefix) q.set("event_prefix", params.eventPrefix);
  const qs = q.toString();
  return api<AuditTailResponse>(`/audit/tail${qs ? `?${qs}` : ""}`, { signal });
}

/**
 * One row per chat. Mirrors the actual `/v1/console/members` route in
 * `routes/members.py::members_list` — NOT a per-uid grant list (that
 * lives behind `/members/{chat_key}` and is not surfaced yet).
 */
export interface MembersChatSummary {
  chat_key: string;
  channel: string;
  chat: string;
  members: number;
  bundles: Record<string, number>;
  quota_entries: number;
  consent_entries: number;
  disclosure_entries: number;
}

export interface MembersListResponse {
  tenant_id: string;
  ts: number;
  count: number;
  chats: MembersChatSummary[];
}

export async function listMembers(signal?: AbortSignal): Promise<MembersListResponse> {
  return api<MembersListResponse>("/members", { signal });
}

export interface MemberUidRecord {
  uid: string;
  role: Record<string, unknown> | null;
  quota: Record<string, unknown> | null;
  consent: Record<string, unknown> | null;
  disclosure: Record<string, unknown> | null;
}

export interface MembersDetailResponse {
  tenant_id: string;
  ts: number;
  chat_key: string;
  channel: string;
  chat: string;
  uid_count: number;
  uids: MemberUidRecord[];
}

export async function getMembersDetail(
  chatKey: string,
  signal?: AbortSignal,
): Promise<MembersDetailResponse> {
  return api<MembersDetailResponse>(`/members/${encodeURIComponent(chatKey)}`, { signal });
}

// ── ADR-0062 M7: Workflow explanation ────────────────────────────────────

export async function explainWorkflow(
  wid: string,
  csrf: string,
): Promise<{ ok: boolean; explanation: string; cached: boolean }> {
  return api(`/workflows/${encodeURIComponent(wid)}/explain`, { method: "POST", csrf });
}


// ── ADR-0124 M6: Custom Audit Layers ─────────────────────────────────────────

export interface AuditLayer {
  layer_id: string;
  display_name: string;
  event_types: string[];
  allowed_fields: string[];
  description: string;
  created_at: number;
  updated_at: number;
}

export interface AuditLayerListResponse {
  tenant_id: string;
  count: number;
  layers: AuditLayer[];
}

export async function listAuditLayers(signal?: AbortSignal): Promise<AuditLayerListResponse> {
  return api<AuditLayerListResponse>("/audit/layers", { signal });
}

export interface AuditLayerRegisterRequest {
  display_name: string;
  event_types: string[];
  allowed_fields?: string[];
  description?: string;
}

export async function registerAuditLayer(
  layer_id: string,
  body: AuditLayerRegisterRequest,
  csrf: string,
): Promise<{ ok: boolean; layer_id: string; updated: boolean }> {
  return api(`/audit/layers/${encodeURIComponent(layer_id)}`, {
    method: "PUT",
    body,
    csrf,
  });
}

export async function removeAuditLayer(
  layer_id: string,
  csrf: string,
): Promise<{ ok: boolean }> {
  return api(`/audit/layers/${encodeURIComponent(layer_id)}`, { method: "DELETE", csrf });
}

export async function emitCustomAuditEvent(
  layer_id: string,
  event_type: string,
  details: Record<string, unknown>,
  csrf: string,
): Promise<{ ok: boolean; ts: number }> {
  return api("/audit/emit", {
    method: "POST",
    body: { layer_id, event_type, details },
    csrf,
  });
}
