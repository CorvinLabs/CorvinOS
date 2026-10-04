/**
 * api/chat-groups — ADR-2216 group chat (human/agent/a2a_peer participants).
 * Backend: core/console/corvin_console/routes/chat_groups.py
 */
import { api } from "./client";

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
