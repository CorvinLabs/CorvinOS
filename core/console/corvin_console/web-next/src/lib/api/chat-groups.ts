/**
 * api/chat-groups — ADR-2216 group chat (human/agent/a2a_peer participants).
 * Backend: core/console/corvin_console/routes/chat_groups.py
 */
import { ApiError, BASE, api } from "./client";
import type { AttachmentMeta } from "./chat";

export type ParticipantKind = "human" | "agent" | "a2a_peer";

export interface Participant {
  participant_id: string;
  kind: ParticipantKind;
  display_name: string;
  peer_endpoint_id: string | null;
  added_at: number;
  added_by: string;
}

export interface ChatGroup {
  group_id: string;
  title: string;
  created_at: number;
  created_by: string;
  participants: Participant[];
}

export interface GroupMessage {
  id: string;
  group_id: string;
  sender_participant_id: string;
  ts: number;
  text: string;
  delivery: string;
}

export async function listGroups(signal?: AbortSignal): Promise<ChatGroup[]> {
  return api<ChatGroup[]>("/chat/groups", { signal });
}

export async function createGroup(title: string, csrf: string): Promise<ChatGroup> {
  return api<ChatGroup>("/chat/groups", { method: "POST", body: { title }, csrf });
}

export async function getGroup(groupId: string, signal?: AbortSignal): Promise<ChatGroup> {
  return api<ChatGroup>(`/chat/groups/${encodeURIComponent(groupId)}`, { signal });
}

export async function deleteGroup(groupId: string, csrf: string): Promise<{ deleted: boolean }> {
  return api(`/chat/groups/${encodeURIComponent(groupId)}`, { method: "DELETE", csrf });
}

export async function addParticipant(
  groupId: string,
  body: { participant_id: string; kind: ParticipantKind; display_name?: string; peer_endpoint_id?: string },
  csrf: string,
): Promise<ChatGroup> {
  return api<ChatGroup>(`/chat/groups/${encodeURIComponent(groupId)}/participants`, {
    method: "POST", body, csrf,
  });
}

export async function removeParticipant(
  groupId: string, participantId: string, csrf: string,
): Promise<ChatGroup> {
  return api<ChatGroup>(
    `/chat/groups/${encodeURIComponent(groupId)}/participants/${encodeURIComponent(participantId)}`,
    { method: "DELETE", csrf },
  );
}

export async function listMessages(groupId: string, signal?: AbortSignal): Promise<GroupMessage[]> {
  return api<GroupMessage[]>(`/chat/groups/${encodeURIComponent(groupId)}/messages`, { signal });
}

export async function sendMessage(
  groupId: string, text: string, senderParticipantId: string, csrf: string,
): Promise<GroupMessage> {
  return api<GroupMessage>(`/chat/groups/${encodeURIComponent(groupId)}/messages`, {
    method: "POST",
    body: { text, sender_participant_id: senderParticipantId },
    csrf,
  });
}

// ── Attachments (mirrors api/chat.ts::uploadAttachments — same 50 MB/file
// limit, no extension whitelist; stored under the group's own
// attachments/ directory, see routes/chat_groups.py::upload_group_attachments) ──

/** URL the console serves a stored group attachment from (inline when safe). */
export function groupAttachmentUrl(groupId: string, name: string): string {
  return `${BASE}/chat/groups/${encodeURIComponent(groupId)}/attachments/${encodeURIComponent(name)}`;
}

export async function uploadGroupAttachments(
  groupId: string,
  files: File[],
  csrf: string,
): Promise<AttachmentMeta[]> {
  const form = new FormData();
  for (const f of files) {
    form.append("files", f, f.name);
  }
  const res = await fetch(`${BASE}/chat/groups/${encodeURIComponent(groupId)}/attachments`, {
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
