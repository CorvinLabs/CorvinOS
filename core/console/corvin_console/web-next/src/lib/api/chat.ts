/**
 * api/chat — extracted from the former monolithic lib/api.ts.
 * Public surface is unchanged; re-exported via the ../api.ts barrel.
 */

import { ApiError, BASE, api } from "./client";
import { ChatSessionSummary } from "./sessions";

export async function createChatSession(
  csrf: string,
  title = "",
): Promise<{ ok: true; session: ChatSessionSummary }> {
  return api("/chat/sessions", {
    method: "POST",
    csrf,
    body: { title },
  });
}

export async function deleteChatSession(
  sid: string,
  csrf: string,
): Promise<{ ok: true; sid: string }> {
  return api(`/chat/sessions/${encodeURIComponent(sid)}`, {
    method: "DELETE",
    csrf,
  });
}

export async function updateChatSessionTitle(
  sid: string,
  title: string,
  csrf: string,
): Promise<{ ok: true; session: ChatSessionSummary }> {
  return api(`/chat/sessions/${encodeURIComponent(sid)}`, {
    method: "PATCH",
    csrf,
    body: { title },
  });
}

export interface ChatTurnPart {
  kind: "text" | "tool" | "artifact";
  text?: string;
  name?: string;
  input?: Record<string, unknown>;
  path?: string;
  mime?: string;
  size?: number;
  label?: string;  // M5 (ADR-0170): provenance badge
}

export interface ChatTurn {
  role: "user" | "assistant" | "system";
  ts: number;
  parts: ChatTurnPart[];
  /** ADR-0214 k=8: TDE delegation metrics persisted by the backend
   *  (chat_runtime.py::_append_turn, snake_case wrapper key). Present only
   *  on assistant turns that ran through the Tiered Delegation Engine.
   *  Inner field names match chat-registry's TdeProgress 1:1, including the
   *  ADR-0216 badge fields quota_used_today/quota_limit (limit `null` =
   *  unlimited tier) and the classification fields task_type/complexity. */
  tde_progress?: Record<string, unknown> | null;
}

export interface ChatTurnsResponse {
  sid: string;
  count: number;
  turns: ChatTurn[];
}

export async function getChatTurns(
  sid: string,
  limit = 200,
  signal?: AbortSignal,
): Promise<ChatTurnsResponse> {
  return api<ChatTurnsResponse>(
    `/chat/sessions/${encodeURIComponent(sid)}/turns?limit=${limit}`,
    { signal },
  );
}


// ── Voice (Iter 3b) ────────────────────────────────────────────────

export interface TranscribeResponse {
  ok: true;
  text: string;
  lang: string | null;
  provider: string;
  elapsed_ms: number;
  bytes: number;
}

export async function transcribeAudio(
  blob: Blob,
  csrf: string,
  lang?: string,
): Promise<TranscribeResponse> {
  const form = new FormData();
  form.append("audio", blob, "recording.webm");
  if (lang) form.append("lang", lang);
  const res = await fetch("/v1/console/voice/transcribe", {
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


// ── Chat settings (cowork per-chat persona pinning) ─────────────────

export interface ChatSettingsSummary {
  channel: string;
  chat_key: string;
  persona: string | null;
  ldd_enabled: boolean | null;
  dialectic_enabled: boolean | null;
}

export interface ChatSettingsListResponse {
  tenant_id: string;
  count: number;
  chats: ChatSettingsSummary[];
  known_channels: string[];
}

export async function listChatSettings(
  signal?: AbortSignal,
): Promise<ChatSettingsListResponse> {
  return api<ChatSettingsListResponse>("/chat-settings", { signal });
}

export async function patchChatSettings(
  channel: string,
  chatKey: string,
  patch: { persona?: string | null; ldd_enabled?: boolean; dialectic_enabled?: boolean },
  csrf: string,
): Promise<{ ok: true }> {
  return api(`/chat-settings/${encodeURIComponent(channel)}/${encodeURIComponent(chatKey)}`, {
    method: "PATCH",
    csrf,
    body: { ...patch },
  });
}


// ── Chat Attachments ───────────────────────────────────────────────

export interface AttachmentMeta {
  name: string;
  size: number;
  mime: string;
  path: string; // relative to workdir, e.g. "attachments/report.csv"
}

export async function uploadAttachments(
  sid: string,
  files: File[],
  csrf: string,
): Promise<AttachmentMeta[]> {
  const form = new FormData();
  for (const f of files) {
    form.append("files", f, f.name);
  }
  const res = await fetch(`${BASE}/chat/sessions/${encodeURIComponent(sid)}/attachments`, {
    method: "POST",
    headers: { "X-CSRF-Token": csrf },
    credentials: "include",
    body: form,
  });
  if (!res.ok) {
    const text = await res.text().catch(() => `HTTP ${res.status}`);
    let detail = text;
    try {
      const json = JSON.parse(text);
      if (json?.detail) detail = String(json.detail);
    } catch { /* keep text */ }
    throw new ApiError(res.status, detail);
  }
  const data = await res.json() as { attachments: AttachmentMeta[] };
  return data.attachments;
}

export async function openSessionWorkdir(
  sid: string,
  csrf: string,
  reveal = true,
): Promise<{ ok: boolean; path: string; opened: boolean }> {
  const params = new URLSearchParams({ reveal: reveal ? "true" : "false" });
  const res = await fetch(
    `${BASE}/chat/sessions/${encodeURIComponent(sid)}/workdir-path?${params}`,
    { headers: { "X-CSRF-Token": csrf }, credentials: "include" },
  );
  if (!res.ok) {
    const text = await res.text().catch(() => `HTTP ${res.status}`);
    throw new ApiError(res.status, text);
  }
  return res.json() as Promise<{ ok: boolean; path: string; opened: boolean }>;
}


// ── ULO (ADR-0163 M4) — User-Defined Learning Objectives ─────────────────

export interface UloObjective {
  id:                      string;
  text:                    string;
  priority:                "low" | "medium" | "high";
  scope:                   "session" | "chat" | "all";
  active:                  boolean;
  created_at:              number;
  updated_at:              number;
  compliance_window:       number;
  compliance_rate:         number | null;
  reinforcement_threshold: number;
  turns_checked:           number;
  consecutive_failures:    number;
  check_trigger:           "always" | "code" | "review" | "commit";
}

export interface UloListResponse {
  objectives:   UloObjective[];
  count:        number;
  active_count: number;
}

export function getUloObjectives(
  channel: string,
  chat: string,
  signal?: AbortSignal,
): Promise<UloListResponse> {
  return api(`/ulo/objectives?channel=${encodeURIComponent(channel)}&chat=${encodeURIComponent(chat)}`, { signal });
}

export function addUloObjective(
  channel: string,
  chat_key: string,
  text: string,
  priority: "low" | "medium" | "high",
  csrf: string,
): Promise<{ objective: UloObjective }> {
  return api("/ulo/objectives", {
    method: "POST",
    body: { channel, chat_key, text, priority },
    csrf,
  });
}

export function pauseUloObjective(
  id: string,
  channel: string,
  chat_key: string,
  csrf: string,
): Promise<{ id: string; active: boolean }> {
  return api(`/ulo/objectives/${encodeURIComponent(id)}`, {
    method: "PUT",
    body: { action: "pause", channel, chat_key },
    csrf,
  });
}

export function resumeUloObjective(
  id: string,
  channel: string,
  chat_key: string,
  csrf: string,
): Promise<{ id: string; active: boolean }> {
  return api(`/ulo/objectives/${encodeURIComponent(id)}`, {
    method: "PUT",
    body: { action: "resume", channel, chat_key },
    csrf,
  });
}

export function deleteUloObjective(
  id: string,
  channel: string,
  chat_key: string,
  csrf: string,
): Promise<{ id: string; deleted: boolean }> {
  const params = new URLSearchParams({ channel, chat: chat_key });
  return api(`/ulo/objectives/${encodeURIComponent(id)}?${params}`, {
    method: "DELETE",
    csrf,
  });
}
