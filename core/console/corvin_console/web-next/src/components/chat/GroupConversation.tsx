/**
 * Group conversation in the chat's main area (ADR-2216, ADR-2218).
 *
 * A message posted here is stored locally AND delivered by the backend to
 * every A2A peer in the group (routes/chat_groups.py::send_message fan-out);
 * the peer's instance files it under the same group_id. Peer replies arrive
 * through the A2A receiver and show up on the next poll — there is no push
 * channel for group messages, so the list polls.
 */
import * as React from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Globe2, Loader2, Send, Trash2, UserPlus, Users } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";
import {
  addParticipant, deleteGroup, getGroup, listMessages, sendMessage,
  type ChatGroup, type GroupMessage,
} from "@/lib/api/chat-groups";
import { getA2AFeed } from "@/lib/api/a2a";
import { MembersSection } from "./MembersSection";

const MESSAGES_REFETCH_MS = 4_000;

export function plural(n: number, word: string): string {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

function fmtTime(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

/** Friended peers that can receive and are not yet members — one click to add. */
function AddPeerQuick({ group, csrf, onAdded }: { group: ChatGroup; csrf: string; onAdded: () => void }) {
  const peers = useQuery({
    queryKey: ["a2a", "peers"],
    queryFn: ({ signal }) => getA2AFeed({ limit: 1 }, signal),
    select: (r) => r.peers,
    staleTime: 15_000,
  });
  const [busy, setBusy] = React.useState<string | null>(null);
  const [error, setError] = React.useState("");
  const memberEndpoints = new Set(group.participants.map((p) => p.peer_endpoint_id).filter(Boolean));
  const candidates = (peers.data ?? []).filter((p) => p.can_send && !memberEndpoints.has(p.peer_id));
  if (candidates.length === 0) return null;

  async function add(peerId: string, label: string) {
    setBusy(peerId); setError("");
    try {
      await addParticipant(group.group_id, {
        participant_id: peerId, kind: "a2a_peer", display_name: label, peer_endpoint_id: peerId,
      }, csrf);
      onAdded();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="border-t border-border/40 px-3 py-2">
      <p className="mb-1.5 text-[11px] font-semibold text-muted-foreground">ADD A CONNECTED AGENT</p>
      <div className="flex flex-wrap gap-1.5">
        {candidates.map((p) => (
          <Button key={p.peer_id} size="sm" variant="outline" className="h-6 gap-1 px-2 text-[11px]"
            disabled={busy !== null} onClick={() => add(p.peer_id, p.label || p.peer_id)}>
            {busy === p.peer_id ? <Loader2 className="h-3 w-3 animate-spin" /> : <UserPlus className="h-3 w-3" />}
            {p.label || p.peer_id}
          </Button>
        ))}
      </div>
      {error && <p className="mt-1 text-[11px] text-destructive">{error}</p>}
    </div>
  );
}

function GroupMessageRow({ m, group, selfId }: { m: GroupMessage; group: ChatGroup; selfId: string }) {
  const sender = group.participants.find((p) => p.participant_id === m.sender_participant_id);
  const mine = m.sender_participant_id === selfId;
  const isPeer = sender?.kind === "a2a_peer";
  return (
    <div className={cn("flex", mine ? "justify-end" : "justify-start")}>
      <div className={cn(
        "max-w-[75%] rounded-2xl px-4 py-2 text-sm",
        mine ? "bg-accent/15" : isPeer ? "border border-sky-500/30 bg-sky-500/5" : "bg-muted/60",
      )}>
        <div className="mb-0.5 flex items-center gap-1.5 text-[10px] text-muted-foreground">
          {isPeer && <Globe2 className="h-3 w-3" />}
          <span className="font-medium">{mine ? "You" : sender?.display_name || m.sender_participant_id}</span>
          <span>{fmtTime(m.ts)}</span>
          {m.delivery === "fanout" && <span title="Delivered to the group's A2A peers">· sent to peers</span>}
        </div>
        <div className="whitespace-pre-wrap break-words">{m.text}</div>
      </div>
    </div>
  );
}

export function GroupConversation({ groupId, csrf }: { groupId: string; csrf: string }) {
  const qc = useQueryClient();
  const group = useQuery({
    queryKey: ["chat-groups", groupId],
    queryFn: ({ signal }) => getGroup(groupId, signal),
  });
  const messages = useQuery({
    queryKey: ["chat-groups", groupId, "messages"],
    queryFn: ({ signal }) => listMessages(groupId, signal),
    refetchInterval: MESSAGES_REFETCH_MS,
  });
  const [text, setText] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState("");
  const [membersOpen, setMembersOpen] = React.useState(false);
  const [confirmDelete, setConfirmDelete] = React.useState(false);
  const navigate = useNavigate();
  const endRef = React.useRef<HTMLDivElement>(null);

  const count = messages.data?.length ?? 0;
  React.useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [count]);

  // The first participant is the local operator: the session that created the
  // group, or "operator" for a group mirrored from another instance.
  const selfId = group.data?.participants[0]?.participant_id ?? "";
  const refreshGroup = () => {
    qc.invalidateQueries({ queryKey: ["chat-groups"] });
  };

  async function handleSend() {
    const body = text.trim();
    if (!body || !selfId) return;
    setBusy(true); setError("");
    try {
      await sendMessage(groupId, body, selfId, csrf);
      setText("");
      messages.refetch();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function handleDelete() {
    setError("");
    try {
      await deleteGroup(groupId, csrf);
      await qc.invalidateQueries({ queryKey: ["chat-groups"] });
      navigate("/app/chat", { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setConfirmDelete(false);
    }
  }

  if (group.isLoading) {
    return <div className="flex flex-1 items-center justify-center"><Loader2 className="h-5 w-5 animate-spin" /></div>;
  }
  if (group.error || !group.data) {
    return <p className="p-6 text-sm text-destructive">Group not found.</p>;
  }
  const g = group.data;
  const peerCount = g.participants.filter((p) => p.kind === "a2a_peer").length;

  return (
    <div className="flex min-h-0 flex-1 flex-col" data-testid="group-conversation">
      <div className="flex items-center justify-between gap-2 border-b border-border px-4 py-2.5">
        <div className="min-w-0">
          <h2 className="truncate font-serif text-lg">{g.title}</h2>
          <p className="text-[11px] text-muted-foreground">
            {plural(g.participants.length, "member")}{peerCount > 0 ? ` · ${peerCount} via A2A` : ""}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
          <Button size="sm" variant={membersOpen ? "accent" : "outline"} className="gap-1.5"
            onClick={() => setMembersOpen((v) => !v)} aria-expanded={membersOpen}>
            <Users className="h-3.5 w-3.5" /> Members
          </Button>
          {confirmDelete ? (
            <>
              <Button size="sm" variant="destructive" onClick={handleDelete}>Delete group</Button>
              <Button size="sm" variant="ghost" onClick={() => setConfirmDelete(false)}>Cancel</Button>
            </>
          ) : (
            <Button size="sm" variant="ghost" className="h-8 w-8 p-0" aria-label="Delete group"
              title="Delete this group on this instance" onClick={() => setConfirmDelete(true)}>
              <Trash2 className="h-3.5 w-3.5" />
            </Button>
          )}
        </div>
      </div>

      {membersOpen && (
        <div className="max-h-72 overflow-y-auto border-b border-border bg-card/40">
          <MembersSection group={g} csrf={csrf} selfId={selfId} onChanged={refreshGroup} />
          <AddPeerQuick group={g} csrf={csrf} onAdded={refreshGroup} />
        </div>
      )}

      <div className="flex-1 space-y-2 overflow-y-auto px-4 py-4">
        {count === 0 && (
          <p className="pt-10 text-center text-sm text-muted-foreground">
            No messages yet.{peerCount === 0 ? " Add a connected agent under Members to chat across instances." : ""}
          </p>
        )}
        {(messages.data ?? []).map((m) => <GroupMessageRow key={m.id} m={m} group={g} selfId={selfId} />)}
        <div ref={endRef} />
      </div>

      <div className="border-t border-border p-3">
        <div className="flex gap-2">
          <Textarea value={text} onChange={(e) => setText(e.target.value)} rows={1}
            placeholder={`Message ${g.title}…`} className="min-h-[40px] resize-none"
            aria-label="Group message"
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); handleSend(); } }} />
          <Button disabled={busy || !text.trim()} onClick={handleSend} aria-label="Send">
            {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
          </Button>
        </div>
        {error && <p className="mt-1 text-xs text-destructive">{error}</p>}
        {peerCount > 0 && (
          <Badge variant="outline" className="mt-2 text-[10px]">
            <Globe2 className="mr-1 h-3 w-3" /> Messages are delivered to {peerCount} external agent{peerCount > 1 ? "s" : ""}
          </Badge>
        )}
      </div>
    </div>
  );
}
