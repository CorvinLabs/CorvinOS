/**
 * Group chat + chat-native friendship tokens (ADR-2216).
 *
 * GroupDetail is the command-center view: AdminSidebar (Pending/Members/
 * Tokens, see components/chat/AdminSidebar.tsx) + message stream in one
 * screen, replacing the former stacked-Cards layout. This stays a SEPARATE
 * route from the single-user chat (/app/chat/<sid>) because the two are
 * different backend entities — a chat session has exactly one operator +
 * Claude, a ChatGroup has N participants (human/agent/a2a_peer) — not a
 * stylistic choice.
 *
 * Pending confirmations are still POLLED (AdminSidebar → PendingConfirmations),
 * not pushed: the backend's chat-staged MCP tools (a2a_send, a2a_friendship_
 * token_create) return a pending_id in their tool RESULT, but
 * chat_runtime.py only streams a "tool_use" event — never the result — so
 * there is no live signal to render an inline confirm card from yet. Fixing
 * that is a separate, larger change to the WS streaming protocol.
 *
 * API: core/console/corvin_console/routes/chat_groups.py,
 *      routes/a2a_feed.py (send pending list/confirm),
 *      routes/a2a_pair.py (friendship-token pending list/confirm + the
 *      pre-existing direct create/import routes).
 */
import * as React from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Loader2, Users, Send, Plus,
} from "lucide-react";
import { useAuth } from "@/lib/auth";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogTrigger,
} from "@/components/ui/dialog";
import {
  listGroups, createGroup, getGroup,
  listMessages, sendMessage,
  type ChatGroup,
} from "@/lib/api/chat-groups";
import { AdminSidebar } from "@/components/chat/AdminSidebar";
import { useAdminSidebarState } from "@/hooks/use-admin-sidebar-state";

function fmtTime(ts: number): string {
  return new Date(ts * 1000).toLocaleString();
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
  const [sidebarCollapsed, toggleSidebar] = useAdminSidebarState();

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

  if (group.isLoading) return <div className="p-6"><Loader2 className="h-5 w-5 animate-spin" /></div>;
  if (group.error || !group.data) return <p className="p-6 text-sm text-destructive">Gruppe nicht gefunden.</p>;

  return (
    <div className="flex h-full">
      <AdminSidebar
        group={group.data}
        csrf={csrf}
        selfId={selfId}
        collapsed={sidebarCollapsed}
        onToggle={toggleSidebar}
        onGroupChanged={refetchGroup}
      />
      <div className="flex min-w-0 flex-1 flex-col">
      <div className="border-b border-border/60 p-3">
        <h3 className="text-sm font-semibold">{group.data.title}</h3>
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
          Bestätigungen, Mitglieder und Tokens liegen jetzt direkt in der Admin-Sidebar der Gruppe.
        </p>
      </div>

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
