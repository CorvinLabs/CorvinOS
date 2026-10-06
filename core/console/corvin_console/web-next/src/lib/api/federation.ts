/**
 * Federation API — backend: core/console/corvin_console/routes/federation_routes.py
 * (mounted under "/v1/console/federation"). Agent-to-agent conversations: a
 * local agent and a peer agent take turns; this installation moderates.
 */
import { api } from "./client";

const P = "/federation";

export interface LocalAgent {
  agent_id: string;
  engine_type: string;
  model: string;
  capabilities: string[];
  federable: boolean;
}

export interface PeerAgent {
  agent_id: string;
  address: string;
  endpoint_id: string;
  peer_instance_id: string;
  model: string;
  capabilities: string[];
}

export interface FederationPeer {
  endpoint_id: string;
  peer_instance_id: string;
  agent_count: number;
  fresh: boolean;
}

export interface Participant {
  agent_id: string;
  model: string;
  address: string;
  endpoint_id?: string;
}

export type ConversationStatus = "running" | "completed" | "stopped" | "failed" | "interrupted";

export interface ConversationMessage {
  seq: number;
  ts: number;
  speaker: "operator" | "local" | "peer";
  agent_id: string;
  address: string | null;
  task_id: string | null;
  status: string;
  text: string;
  duration_ms: number;
  error?: string | null;
}

export interface ConversationSummary {
  conversation_id: string;
  status: ConversationStatus;
  reason: string | null;
  local: Participant | null;
  peer: Participant | null;
  max_turns: number | null;
  started_at: number | null;
  ended_at: number | null;
  turns: number;
  topic: string;
}

export interface Conversation extends ConversationSummary {
  messages: ConversationMessage[];
  last_seq: number;
}

export interface StartConversation {
  local_agent_id: string;
  endpoint_id: string;
  peer_agent_id: string;
  opener: string;
  max_turns: number;
  first_speaker: "local" | "peer";
}

export const listLocalAgents = () => api<{ agents: LocalAgent[] }>(`${P}/agents`);
export const listPeerAgents = () => api<{ agents: PeerAgent[] }>(`${P}/peer-agents`);
export const listFederationPeers = () => api<{ peers: FederationPeer[] }>(`${P}/peers`);
export const refreshPeerCatalog = (endpointId: string, csrf: string) =>
  api<{ agents: PeerAgent[] }>(`${P}/peers/${encodeURIComponent(endpointId)}/refresh`, {
    method: "POST", csrf,
  });

export const listConversations = () =>
  api<{ conversations: ConversationSummary[] }>(`${P}/conversations`);
export const getConversation = (id: string, afterSeq = -1) =>
  api<Conversation>(`${P}/conversations/${encodeURIComponent(id)}?after_seq=${afterSeq}`);
export const startConversation = (body: StartConversation, csrf: string) =>
  api<{ conversation_id: string }>(`${P}/conversations`, { method: "POST", body, csrf });
export const stopConversation = (id: string, csrf: string) =>
  api<{ stopping: boolean }>(`${P}/conversations/${encodeURIComponent(id)}/stop`, {
    method: "POST", csrf,
  });
export const deleteConversation = (id: string, csrf: string) =>
  api<{ deleted: string }>(`${P}/conversations/${encodeURIComponent(id)}`, {
    method: "DELETE", csrf,
  });
