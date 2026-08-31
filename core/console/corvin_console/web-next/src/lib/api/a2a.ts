/**
 * api/a2a — extracted from the former monolithic lib/api.ts.
 * Public surface is unchanged; re-exported via the ../api.ts barrel.
 */

import { api } from "./client";

// ── Agent Hub — A2A remote-trigger (Layer 38) ──────────────────────

export interface A2AOrigin {
  origin_id: string;
  enabled: boolean;
  spawn_worker: boolean;
  max_ttl_s: number | null;
  allowed_personas: string[];
  state?: "PENDING" | "ACTIVE";
  label?: string | null;
  _friendship?: boolean;
  // M2 tool policy — deny-by-default opt-ins (ADR-0144)
  allow_bash?: boolean;
  allow_network?: boolean;
  allow_read_files?: boolean;
  allow_write_files?: boolean;
  allow_subagents?: boolean;
}

export interface A2AOriginsResponse {
  ts: number;
  count: number;
  origins: A2AOrigin[];
}

export async function getA2AOrigins(signal?: AbortSignal): Promise<A2AOriginsResponse> {
  return api<A2AOriginsResponse>("/remote-trigger/origins", { signal });
}

export interface A2AEndpoint {
  endpoint_id: string;
  url: string | null;
  instance_id_pin: string;
  enabled: boolean;
  default_ttl_s: number | null;
  state?: "PENDING" | "ACTIVE";
  label?: string | null;
  _friendship?: boolean;
}

export interface A2AEndpointsResponse {
  ts: number;
  count: number;
  endpoints: A2AEndpoint[];
}

export async function getA2AEndpoints(signal?: AbortSignal): Promise<A2AEndpointsResponse> {
  return api<A2AEndpointsResponse>("/remote-trigger/endpoints", { signal });
}

export interface A2AEvent {
  ts: number | null;
  event_type: string;
  severity: string;
  task_id: string | null;
  origin_id: string | null;
  endpoint_id: string | null;
  persona: string | null;
  engine_id: string | null;
  status: string | null;
  reason: string | null;
  duration_ms: number | null;
  nonce_prefix: string | null;
  ttl_s: number | null;
  sender_instance_id: string | null;
  instance_id_match: boolean | null;
  filter_pass_count: number | null;
  filter_reject_count: number | null;
}

export interface A2ALogResponse {
  tenant_id: string;
  ts: number;
  count: number;
  chain_size_b?: number;
  events: A2AEvent[];
  by_peer: Record<string, A2AEvent[]>;
}

export async function getA2ALog(
  params: { limit?: number; origin_id?: string; endpoint_id?: string; severity?: string } = {},
  signal?: AbortSignal,
): Promise<A2ALogResponse> {
  const q = new URLSearchParams();
  if (params.limit) q.set("limit", String(params.limit));
  if (params.origin_id) q.set("origin_id", params.origin_id);
  if (params.endpoint_id) q.set("endpoint_id", params.endpoint_id);
  if (params.severity) q.set("severity", params.severity);
  const qs = q.toString();
  return api<A2ALogResponse>(`/remote-trigger/log${qs ? `?${qs}` : ""}`, { signal });
}

// ── A2A Pairing (invite-code flow) ────────────────────────────────────

export interface A2APairMyInfo {
  instance_id: string;
  label: string;
  tenant_id: string;
}

export interface A2AGenerateRequest {
  label: string;
  url: string;
  console_url: string;
  peer_origin_id: string;
  max_ttl_s?: number;
  ttl_minutes?: number;
}

export interface A2AGenerateResponse {
  invite_code: string;
  accept_id: string;
  expires_at: number;
  accept_url: string;
}

export interface A2ARedeemRequest {
  invite_code: string;
  our_url: string;
  our_console_url: string;
  our_label: string;
  our_origin_id: string;
  spawn_worker?: boolean;
}

export interface A2ARedeemResponse {
  ok: boolean;
  paired_with: string;
  issuer_label: string;
  issuer_instance_id: string;
  our_origin_id: string;
  bidirectional: boolean;
}

export async function getA2APairMyInfo(signal?: AbortSignal): Promise<A2APairMyInfo> {
  return api<A2APairMyInfo>("/remote-trigger/pair/my-info", { signal });
}

export async function generateA2AInvite(
  body: A2AGenerateRequest,
  csrf: string,
): Promise<A2AGenerateResponse> {
  return api<A2AGenerateResponse>("/remote-trigger/pair/generate", {
    method: "POST",
    body,
    csrf,
  });
}

export async function redeemA2AInvite(
  body: A2ARedeemRequest,
  csrf: string,
): Promise<A2ARedeemResponse> {
  return api<A2ARedeemResponse>("/remote-trigger/pair/redeem", {
    method: "POST",
    body,
    csrf,
  });
}

// ── A2A CLI-Token flow (ADR-0063) ──────────────────────────────────────

export interface CLIInviteRequest {
  url: string;
  origin_id: string;
  label?: string;
  scope?: string;
  ttl_hours?: number;
  single_use?: boolean;
  spawn_worker?: boolean;
  max_call_ttl_s?: number;
}

export interface CLIInviteResponse {
  token: string;
  ikey: string;
  oid: string;
  exp: number | null;
}

export interface CLIAcceptRequest {
  token: string;
  overwrite?: boolean;
}

export interface CLIAcceptResponse {
  ok: boolean;
  oid: string;
  url: string;
  personas: string[];
  spawn_worker: boolean;
  exp: number | null;
}

export interface InviteListEntry {
  ikey: string;
  oid: string;
  lbl: string;
  iat: number;
  exp: number | null;
  su: boolean;
  status: "pending" | "accepted" | "revoked" | "expired";
}

export interface InviteListResponse {
  invites: InviteListEntry[];
}

export async function generateCLIInvite(
  body: CLIInviteRequest,
  csrf: string,
): Promise<CLIInviteResponse> {
  return api<CLIInviteResponse>("/remote-trigger/pair/cli-invite", {
    method: "POST",
    body,
    csrf,
  });
}

export async function acceptCLIInvite(
  body: CLIAcceptRequest,
  csrf: string,
): Promise<CLIAcceptResponse> {
  return api<CLIAcceptResponse>("/remote-trigger/pair/cli-accept", {
    method: "POST",
    body,
    csrf,
  });
}

export async function listA2AInvites(signal?: AbortSignal): Promise<InviteListResponse> {
  return api<InviteListResponse>("/remote-trigger/pair/invites", { signal });
}

export async function revokeA2AInvite(ikey: string, csrf: string): Promise<void> {
  return api<void>(`/remote-trigger/pair/invites/${encodeURIComponent(ikey)}`, {
    method: "DELETE",
    csrf,
  });
}

// ── A2A Friendship Token (ADR-0070) ───────────────────────────────────────

export interface FriendshipCreateRequest {
  url?: string;
  label?: string;
  ttl_hours?: number;
  personas?: string;
  max_call_ttl_s?: number;
  remember_url?: boolean;
}

export interface FriendshipCreateResponse {
  token: string;
  kid: string;
  expires: number | null;
}

export interface FriendshipImportRequest {
  token: string;
  peer_url?: string;
  overwrite?: boolean;
  spawn_worker?: boolean;
}

export interface FriendshipImportResponse {
  ok: boolean;
  kid: string;
  state: "PENDING" | "ACTIVE" | "UNREACHABLE";
  url: string | null;
  label: string | null;
  personas: string[];
  expires: number | null;
  // Reciprocal-handshake outcome (2026-07-29): whether the issuer verified
  // our callback and now has its OWN record for us (true bidirectionality),
  // and whether the issuer itself reports being able to reach us back.
  peer_knows_us: boolean;
  peer_reports_reachable: boolean;
}

export interface FriendshipConnection {
  kid: string;
  state: "PENDING" | "ACTIVE" | "UNREACHABLE";
  label: string | null;
  personas: string[];
  url: string | null;
  expires: number | null;
  peer_knows_us: boolean;
  peer_reports_reachable: boolean;
  // ADR-0258 Stage 3 — which transport last answered a successful ping
  // ("direct" | "relay"), or null if never successfully reached yet.
  // Sticky across a failed recheck (last known-good path).
  via: "direct" | "relay" | null;
}

export interface FriendshipConnectionsResponse {
  connections: FriendshipConnection[];
  count: number;
}

export interface MyUrlResponse {
  url: string | null;
  suggested: string | null;
}

export async function getMyA2AUrl(signal?: AbortSignal): Promise<MyUrlResponse> {
  return api<MyUrlResponse>("/remote-trigger/pair/my-url", { signal });
}

export async function setMyA2AUrl(url: string, csrf: string): Promise<MyUrlResponse> {
  return api<MyUrlResponse>("/remote-trigger/pair/my-url", {
    method: "POST",
    body: { url },
    csrf,
  });
}

export async function createFriendshipToken(
  body: FriendshipCreateRequest,
  csrf: string,
): Promise<FriendshipCreateResponse> {
  return api<FriendshipCreateResponse>("/remote-trigger/pair/friendship/create", {
    method: "POST",
    body,
    csrf,
  });
}

export async function importFriendshipToken(
  body: FriendshipImportRequest,
  csrf: string,
): Promise<FriendshipImportResponse> {
  return api<FriendshipImportResponse>("/remote-trigger/pair/friendship/import", {
    method: "POST",
    body,
    csrf,
  });
}

export async function setFriendshipUrl(
  kid: string,
  peer_url: string,
  csrf: string,
): Promise<{ ok: boolean; kid: string; state: string }> {
  return api("/remote-trigger/pair/friendship/set-url", {
    method: "POST",
    body: { kid, peer_url },
    csrf,
  });
}

export async function revokeFriendshipToken(
  kid: string,
  csrf: string,
): Promise<{ ok: boolean; kid: string }> {
  return api(`/remote-trigger/pair/friendship/${encodeURIComponent(kid)}`, {
    method: "DELETE",
    csrf,
  });
}

export async function listFriendshipConnections(
  signal?: AbortSignal,
): Promise<FriendshipConnectionsResponse> {
  return api<FriendshipConnectionsResponse>("/remote-trigger/pair/friendship/connections", {
    signal,
  });
}

// Manual re-verify (ADR-0199 ping) for an existing connection — pings the
// peer and updates state (ACTIVE / UNREACHABLE) without redoing the token
// exchange. Does not touch peer_knows_us (only a completed ack sets that).
export async function recheckFriendshipConnection(
  kid: string,
  csrf: string,
): Promise<{ ok: boolean; kid: string; state: string; reachable: boolean; via: string | null }> {
  return api(`/remote-trigger/pair/friendship/${encodeURIComponent(kid)}/recheck`, {
    method: "POST",
    csrf,
  });
}

// ── ADR-0258 Stage 3 — relay config (2026-08-03) ──────────────────────────

export interface RelayUrlResponse {
  url: string | null;
  flag_enabled: boolean;
}

export async function getA2ARelayUrl(signal?: AbortSignal): Promise<RelayUrlResponse> {
  return api<RelayUrlResponse>("/remote-trigger/pair/relay-url", { signal });
}

export async function setA2ARelayUrl(url: string, csrf: string): Promise<{ ok: boolean; url: string }> {
  return api("/remote-trigger/pair/relay-url", {
    method: "POST",
    body: { url },
    csrf,
  });
}

// One-click, contextual opt-in surfaced when a direct connection to a peer
// fails: sets the relay URL (if given) and flips the a2a_relay_fallback
// flag (same tenant overlay Settings -> Features uses), then re-verifies
// reachability through the same path a manual Recheck would use.
export async function enableRelayForPeer(
  kid: string,
  relayUrl: string,
  csrf: string,
): Promise<{ ok: boolean; kid: string; state: string; reachable: boolean; via: string | null; relay_enabled: boolean }> {
  return api(`/remote-trigger/pair/friendship/${encodeURIComponent(kid)}/enable-relay`, {
    method: "POST",
    body: { relay_url: relayUrl },
    csrf,
  });
}

// ── A2A Origin permission editing ──────────────────────────────────────

export interface OriginPatchRequest {
  spawn_worker?: boolean;
  enabled?: boolean;
  allowed_personas?: string[];
  max_ttl_s?: number | null;
  label?: string;
  allow_bash?: boolean;
  allow_network?: boolean;
  allow_read_files?: boolean;
  allow_write_files?: boolean;
  allow_subagents?: boolean;
}

export async function patchA2AOrigin(
  originId: string,
  body: OriginPatchRequest,
  csrf: string,
): Promise<{
  ok: boolean;
  origin_id: string;
  spawn_worker: boolean;
  enabled: boolean;
  allowed_personas: string[];
  max_ttl_s: number | null;
  label: string | null;
  allow_bash: boolean;
  allow_network: boolean;
  allow_read_files: boolean;
  allow_write_files: boolean;
  allow_subagents: boolean;
}> {
  return api(`/remote-trigger/origins/${encodeURIComponent(originId)}`, {
    method: "PATCH",
    body,
    csrf,
  });
}

export async function deleteA2AOrigin(
  originId: string,
  csrf: string,
): Promise<{ ok: boolean }> {
  return api(`/remote-trigger/origins/${encodeURIComponent(originId)}`, {
    method: "DELETE",
    csrf,
  });
}

export interface EndpointPatchRequest {
  label?: string | null;
  url?: string | null;
  enabled?: boolean;
  default_ttl_s?: number | null;
}

export async function patchA2AEndpoint(
  endpointId: string,
  body: EndpointPatchRequest,
  csrf: string,
): Promise<{ ok: boolean; endpoint_id: string; label: string | null; url: string | null; enabled: boolean; default_ttl_s: number | null }> {
  return api(`/remote-trigger/endpoints/${encodeURIComponent(endpointId)}`, {
    method: "PATCH",
    body,
    csrf,
  });
}

export async function deleteA2AEndpoint(
  endpointId: string,
  csrf: string,
): Promise<{ ok: boolean }> {
  return api(`/remote-trigger/endpoints/${encodeURIComponent(endpointId)}`, {
    method: "DELETE",
    csrf,
  });
}
