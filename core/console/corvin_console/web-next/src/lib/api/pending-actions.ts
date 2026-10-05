/**
 * api/pending-actions — chat-staged A2A actions awaiting operator
 * confirmation (ADR-2099 Phase 2 a2a_send, ADR-2216 friendship tokens).
 *
 * No tool_result event is streamed to the console today (verified before
 * writing this: chat_runtime.py only emits "tool_use", never the result),
 * so a chat turn that stages a pending action gives the operator no other
 * signal than its own prose. These list endpoints are the discovery
 * surface that makes the two gated MCP tools actually reachable from a UI.
 */
import { api } from "./client";

export interface PendingSend {
  pending_id: string;
  peer_id: string;
  text: string;
  created_at: number;
}

export interface PendingTokenRequest {
  pending_id: string;
  label: string | null;
  ttl_hours: number;
  personas: string[];
  created_at: number;
}

export async function listPendingSends(signal?: AbortSignal): Promise<PendingSend[]> {
  const res = await api<{ pending: PendingSend[] }>("/a2a/feed/send/pending", { signal });
  return res.pending;
}

export async function confirmSend(pendingId: string, csrf: string): Promise<{ accepted: boolean; peer_id: string }> {
  return api(`/a2a/feed/send/confirm/${encodeURIComponent(pendingId)}`, { method: "POST", csrf });
}

export async function discardSend(pendingId: string, csrf: string): Promise<{ discarded: boolean }> {
  return api(`/a2a/feed/send/discard/${encodeURIComponent(pendingId)}`, { method: "POST", csrf });
}

export async function discardTokenRequest(pendingId: string, csrf: string): Promise<{ discarded: boolean }> {
  return api(`/remote-trigger/pair/friendship-token/discard/${encodeURIComponent(pendingId)}`, { method: "POST", csrf });
}

export async function listPendingTokenRequests(signal?: AbortSignal): Promise<PendingTokenRequest[]> {
  return api<PendingTokenRequest[]>("/remote-trigger/pair/friendship-token/pending", { signal });
}

export interface FriendshipTokenResult {
  token: string;
  kid: string;
  expires: number | null;
  label: string | null;
}

export async function confirmTokenRequest(pendingId: string, csrf: string): Promise<FriendshipTokenResult> {
  return api<FriendshipTokenResult>(
    `/remote-trigger/pair/friendship-token/confirm/${encodeURIComponent(pendingId)}`,
    { method: "POST", csrf },
  );
}

// ── Direct (non-chat) friendship actions — pre-existing routes, reused ────

export async function createFriendshipTokenDirect(
  body: { label?: string; ttl_hours?: number }, csrf: string,
): Promise<FriendshipTokenResult> {
  return api<FriendshipTokenResult>("/remote-trigger/pair/friendship/create", {
    method: "POST",
    body: { url: "", label: body.label ?? "", ttl_hours: body.ttl_hours ?? 720 },
    csrf,
  });
}

export interface FriendshipImportResult {
  ok: boolean;
  kid: string;
  state: string;
  label: string | null;
}

export async function importFriendshipToken(
  token: string, csrf: string,
): Promise<FriendshipImportResult> {
  return api<FriendshipImportResult>("/remote-trigger/pair/friendship/import", {
    method: "POST",
    body: { token },
    csrf,
  });
}
