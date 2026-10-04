/**
 * Pending A2A-send / friendship-token confirmations — sidebar section.
 * Extracted from pages/chat-groups.tsx PendingCard. Polls the two pending-list
 * endpoints (the chat-staged MCP tools return pending_id in their tool RESULT,
 * but chat_runtime.py only streams "tool_use" — no live push signal exists yet,
 * see chat-groups.tsx header comment).
 */
import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { Bell, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  listPendingSends, confirmSend, listPendingTokenRequests, confirmTokenRequest,
  type FriendshipTokenResult,
} from "@/lib/api/pending-actions";
import { TokenResultCard } from "./TokensSection";

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
        <Bell className="h-3.5 w-3.5" /> AUSSTEHEND ({total})
      </div>
      <div className="space-y-1.5 px-3 pb-3">
        <p className="text-[10px] text-muted-foreground">
          Nur vorbereitet — nichts gesendet, bis du bestätigst.
        </p>
        {error && <p className="text-[11px] text-destructive">{error}</p>}
        {lastToken && <TokenResultCard result={lastToken} />}
        {(sends.data ?? []).map((p) => (
          <div key={p.pending_id} className="rounded border border-amber-500/30 bg-background p-2 text-[11px] space-y-1">
            <span className="block truncate">
              A2A an <strong>{p.peer_id}</strong>: &ldquo;{p.text.slice(0, 60)}&rdquo;
            </span>
            <Button size="sm" className="h-6 w-full px-2 text-[10px]" disabled={busyId === p.pending_id}
              onClick={() => handleConfirmSend(p.pending_id)}>
              {busyId === p.pending_id ? <Loader2 className="h-3 w-3 animate-spin" /> : "Senden bestätigen"}
            </Button>
          </div>
        ))}
        {(tokens.data ?? []).map((p) => (
          <div key={p.pending_id} className="rounded border border-amber-500/30 bg-background p-2 text-[11px] space-y-1">
            <span className="block truncate">
              Token{p.label ? ` "${p.label}"` : ""} erzeugen
            </span>
            <Button size="sm" className="h-6 w-full px-2 text-[10px]" disabled={busyId === p.pending_id}
              onClick={() => handleConfirmToken(p.pending_id)}>
              {busyId === p.pending_id ? <Loader2 className="h-3 w-3 animate-spin" /> : "Erzeugen bestätigen"}
            </Button>
          </div>
        ))}
      </div>
    </div>
  );
}
