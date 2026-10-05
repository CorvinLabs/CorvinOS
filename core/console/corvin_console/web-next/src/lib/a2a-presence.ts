/**
 * How a peer's presence is shown. The status itself is decided server-side
 * by a2a_connectivity.presence (one rule for every reader); this only maps
 * it to a dot colour and words. A missing field (older backend) reads as
 * "unknown" — never as online.
 */
import type { A2AFeedPeer, A2APresence } from "@/lib/api/a2a";

export interface PresenceView {
  presence: A2APresence;
  label: string;
  dotClass: string;
  title: string;
}

export function fmtAgo(ts: number | null | undefined, now: number = Date.now() / 1000): string | null {
  if (ts == null) return null;
  const s = Math.max(0, Math.round(now - ts));
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return `${Math.floor(s / 86400)} d ago`;
}

const DOT: Record<A2APresence, string> = {
  online: "bg-emerald-500",
  offline: "bg-destructive",
  pending: "bg-amber-500",
  unknown: "bg-muted-foreground/40",
  disabled: "bg-muted-foreground/20",
};

const LABEL: Record<A2APresence, string> = {
  online: "online",
  offline: "offline",
  pending: "pairing pending",
  unknown: "status unknown",
  disabled: "disabled",
};

export function presenceView(
  peer: Pick<A2AFeedPeer, "presence" | "last_check_at" | "last_ok_at">,
  now: number = Date.now() / 1000,
): PresenceView {
  const presence: A2APresence = peer.presence ?? "unknown";
  const checked = fmtAgo(peer.last_check_at, now);
  const seen = fmtAgo(peer.last_ok_at, now);
  let title: string;
  if (presence === "online") title = `Reachable — checked ${checked}`;
  else if (presence === "offline") title = seen ? `Not reachable — last seen ${seen}` : "Not reachable — never seen";
  else if (presence === "unknown") title = checked ? `No recent check (last ${checked})` : "Not checked yet";
  else if (presence === "pending") title = "Waiting for the peer to complete pairing";
  else title = "Connection disabled";
  const label = presence === "offline" && seen ? `offline · seen ${seen}` : LABEL[presence];
  return { presence, label, dotClass: DOT[presence], title };
}
