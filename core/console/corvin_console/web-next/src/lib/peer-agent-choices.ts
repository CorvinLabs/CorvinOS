/**
 * Pure view logic of the peer agent picker (workflow node `peer_agent`): which agents can be
 * chosen, which peers have a stale catalog, and whether the node's current target is still
 * offered. Nothing here invents data: an empty input is an empty result.
 */
import type { FederationPeer, PeerAgent } from "@/lib/api/federation";

export interface PeerChoice {
  /** Select option value: `<endpoint_id>\n<agent_id>` (a newline cannot occur in either id). */
  value: string;
  endpoint_id: string;
  agent_id: string;
  label: string;
}

export function choiceValue(endpointId: string, agentId: string): string {
  return `${endpointId}\n${agentId}`;
}

export function splitChoice(value: string): { endpoint_id: string; agent_id: string } | null {
  const i = value.indexOf("\n");
  if (i <= 0 || i === value.length - 1) return null;
  return { endpoint_id: value.slice(0, i), agent_id: value.slice(i + 1) };
}

/** Agents of fresh catalogs, stable order (peer, then agent). */
export function peerChoices(agents: readonly PeerAgent[]): PeerChoice[] {
  return [...agents]
    .sort((a, b) => a.endpoint_id.localeCompare(b.endpoint_id) || a.agent_id.localeCompare(b.agent_id))
    .map((a) => ({
      value: choiceValue(a.endpoint_id, a.agent_id),
      endpoint_id: a.endpoint_id,
      agent_id: a.agent_id,
      label: `${a.agent_id} @ ${a.endpoint_id}${a.model ? ` (${a.model})` : ""}`,
    }));
}

/** Paired peers whose catalog is older than its freshness window: they offer no agents until refreshed. */
export function stalePeers(peers: readonly FederationPeer[]): FederationPeer[] {
  return peers.filter((p) => !p.fresh);
}

/** Is the node's current target (if any) among the offered choices? */
export function currentIsOffered(choices: readonly PeerChoice[], endpointId: unknown, agentId: unknown): boolean {
  if (typeof endpointId !== "string" || typeof agentId !== "string" || !endpointId || !agentId) return true;
  return choices.some((c) => c.endpoint_id === endpointId && c.agent_id === agentId);
}

/** First offered agent: what the palette pre-fills (the user can change it in the inspector). */
export function firstChoice(choices: readonly PeerChoice[]): PeerChoice | null {
  return choices[0] ?? null;
}
