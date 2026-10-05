/**
 * Pending A2A-send / friendship-token confirmations — sidebar section.
 * Extracted from pages/chat-groups.tsx PendingCard. Polls the two pending-list
 * endpoints (the chat-staged MCP tools return pending_id in their tool RESULT,
 * but chat_runtime.py only streams "tool_use" — no live push signal exists yet,
 * see GroupConversation.tsx).
 */
import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { Bell, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  listPendingSends, confirmSend, discardSend, listPendingTokenRequests, confirmTokenRequest,
  discardTokenRequest, type FriendshipTokenResult,
} from "@/lib/api/pending-actions";
import { getA2AFeed } from "@/lib/api/a2a";
import { TokenResultCard } from "./TokensSection";

function fmtHours(h: number): string {
  return h >= 48 ? `${Math.round(h / 24)} days` : `${Math.round(h)} hours`;
}

// 10s — matches the pre-refactor chat-groups.tsx polling cadence.
const PENDING_REFETCH_MS = 10_000;

export function PendingConfirmations({ csrf, onChanged }: { csrf: string; onChanged: () => void }) {
  const sends = useQuery({
    queryKey: ["pending", "sends"],
    queryFn: ({ signal }) => listPendingSends(signal),
    refetchInterval: PENDING_REFETCH_MS,
  });
  const tokens = useQuery({
    queryKey: ["pending", "tokens"],
    queryFn: ({ signal }) => listPendingTokenRequests(signal),
    refetchInterval: PENDING_REFETCH_MS,
  });
  // Peer labels for the confirm card — an id alone says nothing about who
  // the message goes to (same query key as the sidebar, so no extra request).
  const peers = useQuery({
    queryKey: ["a2a", "peers"],
    queryFn: ({ signal }) => getA2AFeed({ limit: 1 }, signal),
    select: (r) => r.peers,
  });
  const peerLabel = (id: string) => peers.data?.find((p) => p.peer_id === id)?.label || id;
  const [busyId, setBusyId] = React.useState<string | null>(null);
  const [error, setError] = React.useState("");
  const [lastToken, setLastToken] = React.useState<FriendshipTokenResult | null>(null);

  const total = (sends.data?.length ?? 0) + (tokens.data?.length ?? 0);

  async function handleConfirmSend(id: string) {
    setBusyId(id); setError("");
    try {
      await confirmSend(id, csrf);
      sends.refetch(); onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyId(null);
    }
  }
  async function handleDiscard(id: string, kind: "send" | "token") {
    setBusyId(id); setError("");
    try {
      if (kind === "send") { await discardSend(id, csrf); sends.refetch(); }
      else { await discardTokenRequest(id, csrf); tokens.refetch(); }
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyId(null);
    }
  }
  async function handleConfirmToken(id: string) {
    setBusyId(id); setError("");
    try {
      const res = await confirmTokenRequest(id, csrf);
      setLastToken(res);
      tokens.refetch(); onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyId(null);
    }
  }

  // Collapses away entirely when nothing is pending — no empty section
  // taking up sidebar space (see wireframe "empty state" spec).
  if (total === 0 && !lastToken) return null;

  return (
    <div className="border-t border-border/40 bg-amber-500/5">
      <div className="flex items-center gap-1.5 px-3 py-2 text-xs font-semibold text-amber-700 dark:text-amber-400">
        <Bell className="h-3.5 w-3.5" /> PENDING ({total})
      </div>
      <div className="space-y-1.5 px-3 pb-3">
        <p className="text-[10px] text-muted-foreground">
          Staged only — nothing is sent until you confirm.
        </p>
        {error && <p className="text-[11px] text-destructive">{error}</p>}
        {lastToken && <TokenResultCard result={lastToken} />}
        {(sends.data ?? []).map((p) => (
          <div key={p.pending_id} data-testid="pending-send"
            className="rounded border border-amber-500/30 bg-background p-2 text-[11px] space-y-1.5">
            <span className="block">
              A2A message to <strong>{peerLabel(p.peer_id)}</strong>
              {peerLabel(p.peer_id) !== p.peer_id && <span className="text-muted-foreground"> ({p.peer_id.slice(0, 8)}…)</span>}:
            </span>
            {/* The whole text the operator approves — never a preview. */}
            <pre data-testid="pending-send-text"
              className="max-h-48 overflow-auto whitespace-pre-wrap break-words rounded bg-muted/40 p-1.5 font-sans text-[11px]">{p.text}</pre>
            <div className="flex gap-1.5">
              <Button size="sm" className="h-6 flex-1 px-2 text-[10px]" disabled={busyId === p.pending_id}
                onClick={() => handleConfirmSend(p.pending_id)}>
                {busyId === p.pending_id ? <Loader2 className="h-3 w-3 animate-spin" /> : "Confirm send"}
              </Button>
              <Button size="sm" variant="outline" className="h-6 px-2 text-[10px]" disabled={busyId === p.pending_id}
                onClick={() => handleDiscard(p.pending_id, "send")}>
                Discard
              </Button>
            </div>
          </div>
        ))}
        {(tokens.data ?? []).map((p) => (
          <div key={p.pending_id} data-testid="pending-token"
            className="rounded border border-amber-500/30 bg-background p-2 text-[11px] space-y-1.5">
            <span className="block">Create friendship token{p.label ? ` "${p.label}"` : ""}</span>
            <dl className="grid grid-cols-[auto_1fr] gap-x-2 text-[10px] text-muted-foreground">
              <dt>Valid for</dt>
              <dd data-testid="pending-token-ttl">{p.ttl_hours > 0 ? fmtHours(p.ttl_hours) : "no expiry (refused)"}</dd>
              <dt>Personas</dt>
              <dd>{p.personas.length ? p.personas.join(", ") : "default"}</dd>
            </dl>
            <div className="flex gap-1.5">
              <Button size="sm" className="h-6 flex-1 px-2 text-[10px]" disabled={busyId === p.pending_id || !(p.ttl_hours > 0)}
                onClick={() => handleConfirmToken(p.pending_id)}>
                {busyId === p.pending_id ? <Loader2 className="h-3 w-3 animate-spin" /> : "Confirm token"}
              </Button>
              <Button size="sm" variant="outline" className="h-6 px-2 text-[10px]" disabled={busyId === p.pending_id}
                onClick={() => handleDiscard(p.pending_id, "token")}>
                Discard
              </Button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
