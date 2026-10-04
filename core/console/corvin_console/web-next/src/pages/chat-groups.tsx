/**
 * Group chat + chat-native friendship tokens (ADR-2216).
 *
 * Deliberately a SEPARATE panel, not inline inside chat.tsx's message
 * stream: the backend's chat-staged MCP tools (a2a_send, a2a_friendship_
 * token_create) return a pending_id in their tool RESULT, but
 * chat_runtime.py only streams a "tool_use" event — never the result — so
 * there is no live signal to render an inline confirm card from. Fixing
 * that is a separate, larger change to the WS streaming protocol. This
 * panel instead POLLS the two pending-list endpoints, which is additive
 * and carries zero risk to the production chat page.
 *
 * API: core/console/corvin_console/routes/chat_groups.py,
 *      routes/a2a_feed.py (send pending list/confirm),
 *      routes/a2a_pair.py (friendship-token pending list/confirm + the
 *      pre-existing direct create/import routes).
 */
import * as React from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Loader2, Users, UserPlus, Send, Trash2, KeyRound, Copy, Check,
  AlertTriangle, Bot, User as UserIcon, Globe2, Plus, Bell,
} from "lucide-react";
import { useAuth } from "@/lib/auth";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogTrigger,
} from "@/components/ui/dialog";
import { Select, SelectItem } from "@/components/ui/select";
import {
  listGroups, createGroup, getGroup, addParticipant, removeParticipant,
  listMessages, sendMessage,
  type ChatGroup, type ParticipantKind,
} from "@/lib/api/chat-groups";
import {
  listPendingSends, confirmSend, listPendingTokenRequests, confirmTokenRequest,
  createFriendshipTokenDirect, importFriendshipToken,
  type FriendshipTokenResult,
} from "@/lib/api/pending-actions";

const KIND_ICON: Record<ParticipantKind, React.ComponentType<{ className?: string }>> = {
  human: UserIcon, agent: Bot, a2a_peer: Globe2,
};
const KIND_LABEL: Record<ParticipantKind, string> = {
  human: "Mensch", agent: "Agent", a2a_peer: "A2A-Peer",
};

function fmtTime(ts: number): string {
  return new Date(ts * 1000).toLocaleString();
}

// ── Friendship tokens (direct create + accept pasted) ─────────────────────

function TokenResultCard({ result }: { result: FriendshipTokenResult }) {
  const [copied, setCopied] = React.useState(false);
  return (
    <div className="rounded border border-border/60 bg-muted/30 p-3 space-y-2">
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium">
          {result.label ? `Token für "${result.label}"` : "Neuer Freundschaftstoken"}
        </span>
        <Button size="sm" variant="outline" className="h-6 px-2 text-[10px] gap-1"
          onClick={() => { navigator.clipboard.writeText(result.token); setCopied(true); setTimeout(() => setCopied(false), 1500); }}>
          {copied ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />} Kopieren
        </Button>
      </div>
      <code className="block text-[10px] break-all bg-background rounded p-2 border border-border/40">
        {result.token}
      </code>
      <p className="text-[11px] text-muted-foreground">
        Diesen Code außerhalb der Konsole (z.B. per E-Mail/Chat) an die Person senden, mit der du dich verbinden willst —
        sie fügt ihn unter "Freundschaftstoken annehmen" ein.
        {result.expires ? ` Gültig bis ${fmtTime(result.expires)}.` : " Läuft nicht ab."}
      </p>
    </div>
  );
}

function FriendshipCard({ csrf }: { csrf: string }) {
  const [label, setLabel] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState("");
  const [result, setResult] = React.useState<FriendshipTokenResult | null>(null);

  const [pasted, setPasted] = React.useState("");
  const [importBusy, setImportBusy] = React.useState(false);
  const [importError, setImportError] = React.useState("");
  const [importOk, setImportOk] = React.useState("");

  async function handleCreate() {
    setBusy(true); setError("");
    try {
      const res = await createFriendshipTokenDirect({ label: label.trim() || undefined }, csrf);
      setResult(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function handleImport() {
    setImportBusy(true); setImportError(""); setImportOk("");
    try {
      const res = await importFriendshipToken(pasted.trim(), csrf);
      setImportOk(`Verbunden — Status: ${res.state}${res.label ? ` ("${res.label}")` : ""}.`);
      setPasted("");
    } catch (err) {
      setImportError(err instanceof Error ? err.message : String(err));
    } finally {
      setImportBusy(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-sm">
          <KeyRound className="h-4 w-4" /> Freundschaftstoken
        </CardTitle>
        <CardDescription>
          Wie bei WhatsApp: einen Code erzeugen und senden, oder einen erhaltenen Code einfügen.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="space-y-2">
          <div className="flex gap-2">
            <Input placeholder="Label (optional, z.B. 'Partner-Instanz')" value={label}
              onChange={(e) => setLabel(e.target.value)} className="h-8 text-xs" />
            <Button size="sm" className="h-8 gap-1" disabled={busy} onClick={handleCreate}>
              {busy ? <Loader2 className="h-3 w-3 animate-spin" /> : <Plus className="h-3 w-3" />} Token erzeugen
            </Button>
          </div>
          {error && <p className="text-xs text-destructive">{error}</p>}
          {result && <TokenResultCard result={result} />}
        </div>
        <div className="space-y-2 border-t border-border/40 pt-3">
          <p className="text-xs font-medium">Freundschaftstoken annehmen</p>
          <div className="flex gap-2">
            <Input placeholder="corvin-a2a:ft1:…" value={pasted}
              onChange={(e) => setPasted(e.target.value)} className="h-8 text-xs font-mono" />
            <Button size="sm" variant="outline" className="h-8" disabled={importBusy || !pasted.trim()}
              onClick={handleImport}>
              {importBusy ? <Loader2 className="h-3 w-3 animate-spin" /> : "Annehmen"}
            </Button>
          </div>
          {importError && <p className="text-xs text-destructive">{importError}</p>}
          {importOk && <p className="text-xs text-emerald-600 dark:text-emerald-400">{importOk}</p>}
        </div>
      </CardContent>
    </Card>
  );
}

// ── Pending confirmations (chat-staged a2a_send / friendship-token) ───────

function PendingCard({ csrf, onChanged }: { csrf: string; onChanged: () => void }) {
  const sends = useQuery({
    queryKey: ["pending", "sends"],
    queryFn: ({ signal }) => listPendingSends(signal),
    refetchInterval: 10_000,
  });
  const tokens = useQuery({
    queryKey: ["pending", "tokens"],
    queryFn: ({ signal }) => listPendingTokenRequests(signal),
    refetchInterval: 10_000,
  });
  const [busyId, setBusyId] = React.useState<string | null>(null);
  const [error, setError] = React.useState("");
  const [lastToken, setLastToken] = React.useState<FriendshipTokenResult | null>(null);

  const total = (sends.data?.length ?? 0) + (tokens.data?.length ?? 0);
  if (total === 0) return null;

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

  return (
    <Card className="border-amber-500/40">
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-sm">
          <Bell className="h-4 w-4" /> Bestätigungen ausstehend ({total})
        </CardTitle>
        <CardDescription>
          Der Assistent hat diese Aktionen nur vorbereitet — nichts wurde gesendet oder erzeugt, bis du hier bestätigst.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-2">
        {error && <p className="text-xs text-destructive">{error}</p>}
        {lastToken && <TokenResultCard result={lastToken} />}
        {(sends.data ?? []).map((p) => (
          <div key={p.pending_id} className="flex items-center justify-between rounded border border-border/60 p-2 text-xs">
            <span>A2A-Nachricht an <strong>{p.peer_id}</strong>: "{p.text.slice(0, 80)}"</span>
            <Button size="sm" className="h-6 px-2 text-[10px]" disabled={busyId === p.pending_id}
              onClick={() => handleConfirmSend(p.pending_id)}>
              {busyId === p.pending_id ? <Loader2 className="h-3 w-3 animate-spin" /> : "Senden bestätigen"}
            </Button>
          </div>
        ))}
        {(tokens.data ?? []).map((p) => (
          <div key={p.pending_id} className="flex items-center justify-between rounded border border-border/60 p-2 text-xs">
            <span>Freundschaftstoken{p.label ? ` "${p.label}"` : ""} erzeugen</span>
            <Button size="sm" className="h-6 px-2 text-[10px]" disabled={busyId === p.pending_id}
              onClick={() => handleConfirmToken(p.pending_id)}>
              {busyId === p.pending_id ? <Loader2 className="h-3 w-3 animate-spin" /> : "Erzeugen bestätigen"}
            </Button>
          </div>
        ))}
      </CardContent>
    </Card>
  );
}

// ── Groups list + detail ───────────────────────────────────────────────────

function CreateGroupDialog({ csrf, onCreated }: { csrf: string; onCreated: (g: ChatGroup) => void }) {
  const [open, setOpen] = React.useState(false);
  const [title, setTitle] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState("");

  async function handleCreate() {
    setBusy(true); setError("");
    try {
      const g = await createGroup(title.trim(), csrf);
      setOpen(false); setTitle("");
      onCreated(g);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button size="sm" className="gap-1.5"><Plus className="h-3.5 w-3.5" /> Neue Gruppe</Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader><DialogTitle>Neue Gruppe erstellen</DialogTitle></DialogHeader>
        <Input placeholder="Gruppenname" value={title} onChange={(e) => setTitle(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && title.trim()) handleCreate(); }} />
        {error && <p className="text-xs text-destructive">{error}</p>}
        <DialogFooter>
          <Button disabled={busy || !title.trim()} onClick={handleCreate}>
            {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : "Erstellen"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function AddParticipantDialog({
  groupId, csrf, onAdded,
}: { groupId: string; csrf: string; onAdded: () => void }) {
  const [open, setOpen] = React.useState(false);
  const [kind, setKind] = React.useState<ParticipantKind>("human");
  const [participantId, setParticipantId] = React.useState("");
  const [displayName, setDisplayName] = React.useState("");
  const [peerEndpointId, setPeerEndpointId] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState("");

  async function handleAdd() {
    setBusy(true); setError("");
    try {
      await addParticipant(groupId, {
        participant_id: participantId.trim(),
        kind,
        display_name: displayName.trim() || undefined,
        peer_endpoint_id: kind === "a2a_peer" ? peerEndpointId.trim() : undefined,
      }, csrf);
      setOpen(false);
      setParticipantId(""); setDisplayName(""); setPeerEndpointId("");
      onAdded();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button size="sm" variant="outline" className="h-7 gap-1 text-xs">
          <UserPlus className="h-3 w-3" /> Teilnehmer hinzufügen
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader><DialogTitle>Teilnehmer hinzufügen</DialogTitle></DialogHeader>
        <div className="space-y-3">
          <Select value={kind} onChange={(e) => setKind(e.target.value as ParticipantKind)}>
            <SelectItem value="human">Mensch (intern)</SelectItem>
            <SelectItem value="agent">Agent (intern)</SelectItem>
            <SelectItem value="a2a_peer">A2A-Peer (fremder Agent — braucht aktive Freundschaft)</SelectItem>
          </Select>
          <Input placeholder={kind === "a2a_peer" ? "Peer-Endpoint-ID" : "Teilnehmer-ID"}
            value={participantId} onChange={(e) => setParticipantId(e.target.value)} />
          {kind === "a2a_peer" && (
            <Input placeholder="Peer-Endpoint-ID (aus Agent Hub)" value={peerEndpointId}
              onChange={(e) => setPeerEndpointId(e.target.value)} />
          )}
          <Input placeholder="Anzeigename (optional)" value={displayName}
            onChange={(e) => setDisplayName(e.target.value)} />
          {error && (
            <p className="text-xs text-destructive flex items-start gap-1">
              <AlertTriangle className="h-3 w-3 mt-0.5 shrink-0" /> {error}
            </p>
          )}
        </div>
        <DialogFooter>
          <Button disabled={busy || !participantId.trim() || (kind === "a2a_peer" && !peerEndpointId.trim())}
            onClick={handleAdd}>
            {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : "Hinzufügen"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function GroupDetail({ groupId, csrf }: { groupId: string; csrf: string }) {
  const queryClient = useQueryClient();
  const group = useQuery({
    queryKey: ["chat-groups", groupId],
    queryFn: ({ signal }) => getGroup(groupId, signal),
  });
  const messages = useQuery({
    queryKey: ["chat-groups", groupId, "messages"],
    queryFn: ({ signal }) => listMessages(groupId, signal),
    refetchInterval: 8_000,
  });
  const [text, setText] = React.useState("");
  const [sendBusy, setSendBusy] = React.useState(false);
  const [sendError, setSendError] = React.useState("");
  const [removeError, setRemoveError] = React.useState("");

  const refetchGroup = () => queryClient.invalidateQueries({ queryKey: ["chat-groups", groupId] });

  // The operator's own session fingerprint is the "self" participant_id
  // the backend assigned on group creation — see routes/chat_groups.py
  // create_group(). First participant is always the creator.
  const selfId = group.data?.participants[0]?.participant_id ?? "";

  async function handleSend() {
    if (!text.trim() || !selfId) return;
    setSendBusy(true); setSendError("");
    try {
      await sendMessage(groupId, text.trim(), selfId, csrf);
      setText("");
      messages.refetch();
    } catch (err) {
      setSendError(err instanceof Error ? err.message : String(err));
    } finally {
      setSendBusy(false);
    }
  }

  async function handleRemove(participantId: string) {
    setRemoveError("");
    try {
      await removeParticipant(groupId, participantId, csrf);
      refetchGroup();
    } catch (err) {
      setRemoveError(err instanceof Error ? err.message : String(err));
    }
  }

  if (group.isLoading) return <div className="p-6"><Loader2 className="h-5 w-5 animate-spin" /></div>;
  if (group.error || !group.data) return <p className="p-6 text-sm text-destructive">Gruppe nicht gefunden.</p>;

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-border/60 p-3 space-y-2">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold">{group.data.title}</h3>
          <AddParticipantDialog groupId={groupId} csrf={csrf} onAdded={refetchGroup} />
        </div>
        <div className="flex flex-wrap gap-1.5">
          {group.data.participants.map((p) => {
            const Icon = KIND_ICON[p.kind];
            return (
              <Badge key={p.participant_id} variant="secondary" className="gap-1 pr-1">
                <Icon className="h-3 w-3" /> {p.display_name || p.participant_id}
                <span className="text-[9px] opacity-60">({KIND_LABEL[p.kind]})</span>
                {p.participant_id !== selfId && (
                  <button className="ml-1 opacity-50 hover:opacity-100" title="Entfernen"
                    onClick={() => handleRemove(p.participant_id)}>
                    <Trash2 className="h-3 w-3" />
                  </button>
                )}
              </Badge>
            );
          })}
        </div>
        {removeError && <p className="text-xs text-destructive">{removeError}</p>}
      </div>
      <div className="flex-1 overflow-y-auto p-3 space-y-2">
        {(messages.data ?? []).length === 0 && (
          <p className="text-xs text-muted-foreground">Noch keine Nachrichten.</p>
        )}
        {(messages.data ?? []).map((m) => (
          <div key={m.id} className="rounded bg-muted/40 px-3 py-2 text-sm">
            <div className="flex items-center gap-2 text-[10px] text-muted-foreground mb-0.5">
              <span className="font-medium">{m.sender_participant_id}</span>
              <span>{fmtTime(m.ts)}</span>
              {m.delivery !== "local" && <Badge variant="outline" className="text-[9px]">{m.delivery}</Badge>}
            </div>
            {m.text}
          </div>
        ))}
      </div>
      <div className="border-t border-border/60 p-3 flex gap-2">
        <Textarea value={text} onChange={(e) => setText(e.target.value)} rows={1}
          placeholder="Nachricht…" className="min-h-[36px] resize-none"
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); handleSend(); } }} />
        <Button size="sm" disabled={sendBusy || !text.trim()} onClick={handleSend}>
          {sendBusy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
        </Button>
      </div>
      {sendError && <p className="px-3 pb-2 text-xs text-destructive">{sendError}</p>}
    </div>
  );
}

export function ChatGroupsPage() {
  const { session } = useAuth();
  const csrf = session?.csrf_token ?? "";
  const [selected, setSelected] = React.useState<string | null>(null);

  const groups = useQuery({
    queryKey: ["chat-groups"],
    queryFn: ({ signal }) => listGroups(signal),
    refetchInterval: 15_000,
  });

  return (
    <div className="flex h-full flex-col gap-4 p-4 overflow-y-auto">
      <div>
        <h1 className="text-lg font-semibold flex items-center gap-2">
          <Users className="h-5 w-5" /> Gruppenchats
        </h1>
        <p className="text-sm text-muted-foreground">
          Menschen und Agenten — intern oder über A2A verbundene fremde Instanzen — in einer Gruppe.
        </p>
      </div>

      <PendingCard csrf={csrf} onChanged={() => groups.refetch()} />
      <FriendshipCard csrf={csrf} />

      <div className="flex flex-1 min-h-[420px] gap-4">
        <Card className="w-72 shrink-0 flex flex-col">
          <CardHeader className="flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm">Gruppen</CardTitle>
            <CreateGroupDialog csrf={csrf} onCreated={(g) => { groups.refetch(); setSelected(g.group_id); }} />
          </CardHeader>
          <CardContent className="flex-1 overflow-y-auto space-y-1 px-2">
            {groups.isLoading && <Loader2 className="h-4 w-4 animate-spin mx-auto" />}
            {(groups.data ?? []).length === 0 && !groups.isLoading && (
              <p className="text-xs text-muted-foreground px-2">Noch keine Gruppen.</p>
            )}
            {(groups.data ?? []).map((g) => (
              <button key={g.group_id}
                onClick={() => setSelected(g.group_id)}
                className={`w-full text-left rounded px-2 py-1.5 text-sm hover:bg-muted/60 ${selected === g.group_id ? "bg-muted" : ""}`}>
                <div className="font-medium truncate">{g.title}</div>
                <div className="text-[10px] text-muted-foreground">{g.participants.length} Teilnehmer</div>
              </button>
            ))}
          </CardContent>
        </Card>

        <Card className="flex-1 overflow-hidden">
          {selected ? (
            <GroupDetail groupId={selected} csrf={csrf} />
          ) : (
            <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
              Wähle eine Gruppe oder erstelle eine neue.
            </div>
          )}
        </Card>
      </div>
    </div>
  );
}

export default ChatGroupsPage;
