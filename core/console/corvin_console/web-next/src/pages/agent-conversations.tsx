/**
 * Agent conversations — one of this installation's agents talks to an agent
 * on a paired peer installation; the operator opens the topic and watches.
 * The transcript polls while the conversation runs (same transport as the
 * peer chat feed). Backend: routes/federation_routes.py "/conversations".
 */
import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bot, Loader2, RefreshCw, Square, Trash2, Users } from "lucide-react";
import { useAuth } from "@/lib/auth";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";
import { getA2AEndpoints } from "@/lib/api/a2a";
import {
  deleteConversation, getConversation, listConversations, listLocalAgents, listPeerAgents,
  refreshPeerCatalog, startConversation, stopConversation,
  type ConversationMessage, type ConversationStatus,
} from "@/lib/api/federation";

const LIVE_POLL_MS = 1000;
const LIST_POLL_MS = 5000;

const STATUS_VARIANT: Record<ConversationStatus, string> = {
  running: "bg-amber-500/15 text-amber-700 dark:text-amber-300",
  completed: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300",
  stopped: "bg-muted text-muted-foreground",
  failed: "bg-red-500/15 text-red-700 dark:text-red-300",
  interrupted: "bg-red-500/15 text-red-700 dark:text-red-300",
};

const REASON_TEXT: Record<string, string> = {
  max_turns: "Reached the turn limit",
  operator_stop: "Stopped by you",
  local_turn_failed: "Your agent's turn failed",
  local_turn_refused: "Your agent's turn was refused by a safety gate",
  peer_turn_failed: "The peer agent did not answer",
  empty_reply: "An agent returned an empty reply",
  audit_failed: "A turn could not be recorded in the audit log",
  interrupted: "The console restarted while it was running",
  internal_error: "Internal error",
};

function StatusBadge({ status }: { status: ConversationStatus }) {
  return <Badge className={cn("border-0 font-normal", STATUS_VARIANT[status])}>{status}</Badge>;
}

function StartForm({ csrf, onStarted }: { csrf: string; onStarted: (id: string) => void }) {
  const qc = useQueryClient();
  const local = useQuery({ queryKey: ["federation", "agents"], queryFn: listLocalAgents });
  const peers = useQuery({ queryKey: ["federation", "peer-agents"], queryFn: listPeerAgents });
  const endpoints = useQuery({ queryKey: ["a2a", "endpoints"], queryFn: () => getA2AEndpoints() });
  const [localId, setLocalId] = React.useState("");
  const [peerKey, setPeerKey] = React.useState("");
  const [opener, setOpener] = React.useState("");
  const [maxTurns, setMaxTurns] = React.useState(6);
  const [first, setFirst] = React.useState<"local" | "peer">("local");

  const localAgents = (local.data?.agents ?? []).filter((a) => a.engine_type === "claude_code");
  const peerAgents = peers.data?.agents ?? [];

  const refresh = useMutation({
    mutationFn: async () => {
      const eps = (endpoints.data?.endpoints ?? []).filter((e) => e.enabled);
      await Promise.allSettled(eps.map((e) => refreshPeerCatalog(e.endpoint_id, csrf)));
    },
    onSettled: () => qc.invalidateQueries({ queryKey: ["federation", "peer-agents"] }),
  });

  const start = useMutation({
    mutationFn: () => {
      const [endpoint_id, peer_agent_id] = peerKey.split("\u0000");
      return startConversation({ local_agent_id: localId, endpoint_id, peer_agent_id,
                                 opener: opener.trim(), max_turns: maxTurns,
                                 first_speaker: first }, csrf);
    },
    onSuccess: (r) => {
      setOpener("");
      qc.invalidateQueries({ queryKey: ["federation", "conversations"] });
      onStarted(r.conversation_id);
    },
  });

  const selectCls = "h-9 w-full rounded-md border bg-background px-2 text-sm";
  const ready = localId && peerKey && opener.trim() && !start.isPending;

  return (
    <Card>
      <CardContent className="space-y-3 p-4">
        <div className="text-sm font-medium">New conversation</div>
        <label className="block space-y-1 text-xs text-muted-foreground">
          <span>Your agent</span>
          <select className={selectCls} value={localId} onChange={(e) => setLocalId(e.target.value)}
                  aria-label="Your agent">
            <option value="">Select…</option>
            {localAgents.map((a) => (
              <option key={a.agent_id} value={a.agent_id}>{a.agent_id} · {a.model}</option>
            ))}
          </select>
          {local.isSuccess && localAgents.length === 0 && (
            <span>No Claude Code agent is registered on this installation.</span>
          )}
        </label>
        <label className="block space-y-1 text-xs text-muted-foreground">
          <span className="flex items-center justify-between">
            Peer agent
            <button type="button" className="inline-flex items-center gap-1 hover:text-foreground"
                    onClick={() => refresh.mutate()} disabled={refresh.isPending}>
              <RefreshCw className={cn("h-3 w-3", refresh.isPending && "animate-spin")} />
              Refresh peers
            </button>
          </span>
          <select className={selectCls} value={peerKey} onChange={(e) => setPeerKey(e.target.value)}
                  aria-label="Peer agent">
            <option value="">Select…</option>
            {peerAgents.map((a) => (
              <option key={a.address} value={`${a.endpoint_id}\u0000${a.agent_id}`}>
                {a.agent_id} · {a.endpoint_id} · {a.model}
              </option>
            ))}
          </select>
          {peers.isSuccess && peerAgents.length === 0 && (
            <span>No peer agents known yet — refresh the paired peers.</span>
          )}
        </label>
        <label className="block space-y-1 text-xs text-muted-foreground">
          <span>Topic</span>
          <Textarea value={opener} onChange={(e) => setOpener(e.target.value)} rows={3}
                    maxLength={2000} placeholder="What should the two agents discuss?"
                    aria-label="Topic" />
        </label>
        <div className="grid grid-cols-2 gap-3">
          <label className="block space-y-1 text-xs text-muted-foreground">
            <span>Turns</span>
            <input type="number" min={1} max={12} value={maxTurns} className={selectCls}
                   onChange={(e) => setMaxTurns(Math.min(12, Math.max(1, Number(e.target.value) || 1)))} />
          </label>
          <label className="block space-y-1 text-xs text-muted-foreground">
            <span>Speaks first</span>
            <select className={selectCls} value={first}
                    onChange={(e) => setFirst(e.target.value as "local" | "peer")}>
              <option value="local">Your agent</option>
              <option value="peer">Peer agent</option>
            </select>
          </label>
        </div>
        {start.isError && <div className="text-xs text-red-600">{(start.error as Error).message}</div>}
        <Button className="w-full" disabled={!ready} onClick={() => start.mutate()}>
          {start.isPending ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
          Start conversation
        </Button>
      </CardContent>
    </Card>
  );
}

function Message({ m, localId }: { m: ConversationMessage; localId?: string }) {
  if (m.speaker === "operator") {
    return (
      <div className="rounded-md border border-dashed px-3 py-2 text-sm">
        <div className="mb-1 text-xs text-muted-foreground">Topic</div>
        <div className="whitespace-pre-wrap">{m.text}</div>
      </div>
    );
  }
  const mine = m.speaker === "local";
  return (
    <div className={cn("flex", mine ? "justify-start" : "justify-end")}>
      <div className={cn("max-w-[80%] rounded-lg px-3 py-2 text-sm",
                         mine ? "bg-muted" : "bg-primary/10")}>
        <div className="mb-1 flex items-center gap-2 text-xs text-muted-foreground">
          <Bot className="h-3 w-3" />
          <span className="font-medium">{m.agent_id}</span>
          <span>{mine && localId ? "this installation" : m.address}</span>
          <span>#{m.seq}</span>
          {m.duration_ms ? <span>{(m.duration_ms / 1000).toFixed(1)} s</span> : null}
        </div>
        {m.status === "error"
          ? <div className="italic text-red-600">No reply — {REASON_TEXT[m.error ?? ""] ?? m.error}</div>
          : <div className="whitespace-pre-wrap">{m.text}</div>}
      </div>
    </div>
  );
}

function Transcript({ id, csrf, onDeleted }: { id: string; csrf: string; onDeleted: () => void }) {
  const qc = useQueryClient();
  const conv = useQuery({
    queryKey: ["federation", "conversation", id],
    queryFn: () => getConversation(id),
    refetchInterval: (q) => (q.state.data?.status === "running" ? LIVE_POLL_MS : false),
  });
  const stop = useMutation({ mutationFn: () => stopConversation(id, csrf),
                             onSettled: () => conv.refetch() });
  const del = useMutation({
    mutationFn: () => deleteConversation(id, csrf),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["federation", "conversations"] }); onDeleted(); },
  });
  const endRef = React.useRef<HTMLDivElement>(null);
  const count = conv.data?.messages.length ?? 0;
  React.useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth" }); }, [count]);

  if (conv.isLoading) return <Loader2 className="m-6 h-5 w-5 animate-spin text-muted-foreground" />;
  if (conv.isError || !conv.data) return <div className="p-6 text-sm text-red-600">Could not load this conversation.</div>;
  const c = conv.data;
  const running = c.status === "running";

  return (
    <div className="flex h-full flex-col">
      <div className="flex flex-wrap items-center gap-2 border-b px-4 py-3">
        <span className="font-medium">{c.local?.agent_id}</span>
        <span className="text-muted-foreground">↔</span>
        <span className="font-medium">{c.peer?.agent_id}</span>
        <span className="text-xs text-muted-foreground">{c.peer?.address}</span>
        <StatusBadge status={c.status} />
        <span className="text-xs text-muted-foreground">{c.turns}/{c.max_turns} turns</span>
        <div className="ml-auto flex gap-2">
          {running ? (
            <Button size="sm" variant="outline" onClick={() => stop.mutate()} disabled={stop.isPending}>
              <Square className="mr-1 h-3 w-3" /> Stop
            </Button>
          ) : (
            <Button size="sm" variant="outline" onClick={() => del.mutate()} disabled={del.isPending}>
              <Trash2 className="mr-1 h-3 w-3" /> Delete
            </Button>
          )}
        </div>
      </div>
      <div className="flex-1 space-y-3 overflow-y-auto p-4">
        {c.messages.map((m) => <Message key={m.seq} m={m} localId={c.local?.agent_id} />)}
        {running && (
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <Loader2 className="h-3 w-3 animate-spin" /> Waiting for the next turn…
          </div>
        )}
        {!running && c.reason && (
          <div className="text-center text-xs text-muted-foreground">
            Ended: {REASON_TEXT[c.reason] ?? c.reason}
          </div>
        )}
        <div ref={endRef} />
      </div>
    </div>
  );
}

export function AgentConversationsPage() {
  const { session } = useAuth();
  const csrf = session?.csrf_token ?? "";
  const [selected, setSelected] = React.useState<string | null>(null);
  const list = useQuery({ queryKey: ["federation", "conversations"], queryFn: listConversations,
                          refetchInterval: LIST_POLL_MS });
  const items = list.data?.conversations ?? [];

  return (
    <div className="grid h-[calc(100vh-4rem)] grid-cols-1 gap-4 p-4 lg:grid-cols-[22rem_1fr]">
      <div className="space-y-4 overflow-y-auto">
        <div className="flex items-center gap-2">
          <Users className="h-5 w-5" />
          <h1 className="text-lg font-semibold">Agent conversations</h1>
        </div>
        <p className="text-xs text-muted-foreground">
          One of your agents talks to an agent on a paired installation. You set the topic;
          every turn is shown here as it happens and recorded in the audit log.
        </p>
        <StartForm csrf={csrf} onStarted={setSelected} />
        <div className="space-y-1">
          {items.map((s) => (
            <button key={s.conversation_id} type="button" onClick={() => setSelected(s.conversation_id)}
                    className={cn("w-full rounded-md border px-3 py-2 text-left text-sm hover:bg-muted",
                                  selected === s.conversation_id && "bg-muted")}>
              <div className="flex items-center gap-2">
                <span className="truncate font-medium">{s.local?.agent_id} ↔ {s.peer?.agent_id}</span>
                <span className="ml-auto"><StatusBadge status={s.status} /></span>
              </div>
              <div className="truncate text-xs text-muted-foreground">{s.topic}</div>
            </button>
          ))}
          {list.isSuccess && items.length === 0 && (
            <div className="text-xs text-muted-foreground">No conversations yet.</div>
          )}
        </div>
      </div>
      <Card className="min-h-0 overflow-hidden">
        {selected
          ? <Transcript id={selected} csrf={csrf} onDeleted={() => setSelected(null)} />
          : <div className="p-6 text-sm text-muted-foreground">Select or start a conversation.</div>}
      </Card>
    </div>
  );
}
