/**
 * One agent conversation, laid out like the peer chat: tucked-corner bubbles with authorship,
 * system rows centred, and a composer that is open while the conversation runs — the operator
 * is a participant, not a viewer. Every operator action is a request the moderator writes at the
 * next turn boundary (single writer), so a posted line shows up as "queued" until it lands.
 */
import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Bot, Globe2, Loader2, Pause, Play, Send, SlidersHorizontal, Square, Trash2, User,
} from "lucide-react";
import { useAuth } from "@/lib/auth";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Textarea } from "@/components/ui/textarea";
import { Markdown } from "@/components/markdown";
import { cn } from "@/lib/utils";
import { useAutosizeTextarea } from "@/hooks/use-autosize-textarea";
import { ChatAvatar } from "@/components/chat/ChatAvatar";
import {
  configureConversation, deleteConversation, getConversation, listConversationCommands,
  pauseConversation, postConversationMessage, resumeConversation, runConversationCommand,
  stopConversation, type Conversation, type ConversationMessage, type ConversationStatus,
} from "@/lib/api/federation";
import {
  LIMITS, audienceLabel, eventText, settingsDiff, sideOf, timeline,
} from "@/lib/agent-conversation-view";
import { SettingsPanel } from "./SettingsPanel";

const LIVE_POLL_MS = 1000;

export const STATUS_VARIANT: Record<ConversationStatus, string> = {
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

export function StatusBadge({ status, paused }: { status: ConversationStatus; paused?: boolean }) {
  return (
    <Badge className={cn("border-0 font-normal", STATUS_VARIANT[status])} data-testid="conversation-status">
      {paused ? "paused" : status}
    </Badge>
  );
}

function fmtTime(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

const MessageRow = React.memo(function MessageRow({
  m, names,
}: { m: ConversationMessage; names: { local?: string; peer?: string } }) {
  const mine = sideOf(m) === "mine";
  const operator = m.speaker === "operator";
  const audience = audienceLabel(m, names);
  return (
    <div data-testid="conversation-message" data-speaker={m.speaker}
         className={cn("flex gap-3", mine ? "justify-end" : "justify-start")}>
      {!mine && <div className="mt-5"><ChatAvatar label={m.agent_id} icon={Globe2} /></div>}
      <div className={cn("flex min-w-0 max-w-[85%] flex-col", mine ? "items-end" : "items-start")}>
        <div className="mb-1 flex flex-wrap items-center gap-1.5 px-1 text-[11px] text-muted-foreground">
          <span className="font-medium text-foreground/80">{operator ? "You" : m.agent_id}</span>
          {!operator && <Bot className="h-3 w-3" aria-label="agent-authored" />}
          {audience && <><span>·</span><span>{audience}</span></>}
          {!operator && <><span>·</span><span>{m.speaker === "local" ? "your agent" : "peer agent"}</span></>}
          <span>·</span><span>{fmtTime(m.ts)}</span>
          {m.duration_ms ? <><span>·</span><span>{(m.duration_ms / 1000).toFixed(1)} s</span></> : null}
        </div>
        <div className={cn(
          "w-fit max-w-full rounded-2xl px-4 py-3 text-sm leading-relaxed",
          mine ? "rounded-tr-md bg-accent/15 text-foreground"
               : "rounded-tl-md border border-border bg-card text-card-foreground shadow-sm",
          m.status === "error" && "border border-destructive/40",
        )}>
          {m.status === "error"
            ? <p className="italic text-destructive">No reply — {REASON_TEXT[m.error ?? ""] ?? m.error}</p>
            : operator
              ? <p className="whitespace-pre-wrap break-words">{m.text}</p>
              : <Markdown text={m.text} compact blockRemoteImages className="break-words" />}
        </div>
      </div>
      {mine && <div className="mt-5"><ChatAvatar label={m.agent_id} icon={operator ? User : Bot} /></div>}
    </div>
  );
});

type Target = "both" | "local" | "peer";

function Composer({
  c, csrf, onNotice,
}: { c: Conversation; csrf: string; onNotice: (text: string, error?: boolean) => void }) {
  const qc = useQueryClient();
  const [text, setText] = React.useState("");
  const [target, setTarget] = React.useState<Target>("both");
  const ref = React.useRef<HTMLTextAreaElement>(null);
  useAutosizeTextarea(ref, text);
  const running = c.status === "running";
  const commands = useQuery({ queryKey: ["federation", "conversation-commands"],
                              queryFn: listConversationCommands, staleTime: 5 * 60_000 });
  const isCommand = text.startsWith("/") && !text.includes("\n");
  const hints = isCommand
    ? (commands.data?.commands ?? []).filter((x) => x.cmd.startsWith(text.split(/\s/)[0].toLowerCase()))
    : [];

  const send = useMutation({
    mutationFn: async () => {
      const line = text.trim();
      if (line.startsWith("/")) return { notice: (await runConversationCommand(c.conversation_id, line, csrf)).notice };
      await postConversationMessage(c.conversation_id, line, target === "both" ? null : target, csrf);
      return { notice: null as string | null };
    },
    onSuccess: (r) => {
      setText("");
      if (r.notice) onNotice(r.notice);
      void qc.invalidateQueries({ queryKey: ["federation", "conversation", c.conversation_id] });
    },
    onError: (e) => onNotice((e as Error).message, true),
  });
  const submit = () => { if (text.trim() && running && !send.isPending) send.mutate(); };

  const names = { local: c.local?.agent_id, peer: c.peer?.agent_id };
  return (
    <footer className="px-4 py-3 md:px-6" data-testid="conversation-composer">
      <div className="mx-auto w-full max-w-4xl space-y-1.5">
        {running && (
          <div className="flex flex-wrap items-center gap-1 text-[11px] text-muted-foreground" role="radiogroup" aria-label="Send to">
            <span className="mr-1">To</span>
            {([["both", "Both agents"], ["local", names.local ?? "Your agent"], ["peer", names.peer ?? "Peer agent"]] as const).map(([v, label]) => (
              <button key={v} type="button" role="radio" aria-checked={target === v} data-testid={`target-${v}`}
                      onClick={() => setTarget(v)}
                      className={cn("rounded-full border px-2 py-0.5 transition-colors",
                                    target === v ? "border-accent/60 bg-accent/15 text-foreground" : "hover:bg-muted")}>
                {label}
              </button>
            ))}
            {c.pending > 0 && <span className="ml-auto" data-testid="pending-count">{c.pending} waiting for the next turn</span>}
          </div>
        )}
        {hints.length > 0 && (
          <ul className="rounded-lg border bg-card text-xs shadow-sm" data-testid="command-hints">
            {hints.map((h) => (
              <li key={h.cmd}>
                <button type="button" className="flex w-full gap-2 px-3 py-1.5 text-left hover:bg-muted"
                        onClick={() => { setText(`${h.cmd}${h.args ? " " : ""}`); ref.current?.focus(); }}>
                  <span className="font-mono">{h.cmd}</span>
                  <span className="text-muted-foreground">{h.args}</span>
                  <span className="ml-auto text-muted-foreground">{h.desc}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
        <div className="flex items-end gap-2 rounded-2xl border border-border bg-card px-2 py-1.5 shadow-sm transition-colors focus-within:border-accent/50 focus-within:ring-2 focus-within:ring-accent/15">
          <Textarea ref={ref} value={text} rows={1} maxLength={LIMITS.message} disabled={!running}
            onChange={(e) => setText(e.target.value)}
            placeholder={running
              ? "Join in…  (/ for commands)"
              : "This conversation has ended."}
            aria-label="Message to the conversation" data-testid="conversation-input"
            className="min-h-[2rem] flex-1 resize-none border-0 bg-transparent px-2 py-1 text-sm leading-relaxed shadow-none focus-visible:ring-0 focus-visible:ring-offset-0"
            onKeyDown={(e) => {
              if (e.key !== "Enter" || e.shiftKey || e.nativeEvent.isComposing) return;
              e.preventDefault();
              submit();
            }} />
          <Button variant="accent" size="icon" className="h-8 w-8 shrink-0 rounded-full" aria-label="Send"
                  disabled={!running || !text.trim() || send.isPending} onClick={submit} data-testid="conversation-send">
            {send.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
          </Button>
        </div>
      </div>
    </footer>
  );
}

export function ConversationThread({ id, onDeleted }: { id: string; onDeleted: () => void }) {
  const { session } = useAuth();
  const csrf = session?.csrf_token ?? "";
  const qc = useQueryClient();
  const key = ["federation", "conversation", id];
  const conv = useQuery({
    queryKey: key, queryFn: () => getConversation(id),
    refetchInterval: (q) => (q.state.data?.status === "running" ? LIVE_POLL_MS : false),
  });
  const refresh = () => qc.invalidateQueries({ queryKey: key });
  const stop = useMutation({ mutationFn: () => stopConversation(id, csrf), onSettled: refresh });
  const pause = useMutation({
    mutationFn: (p: boolean) => (p ? pauseConversation(id, csrf) : resumeConversation(id, csrf)),
    onSettled: refresh,
  });
  const del = useMutation({
    mutationFn: () => deleteConversation(id, csrf),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["federation", "conversations"] }); onDeleted(); },
  });
  // The settings column is part of the layout (like the peer chat's context sidebar), open by default;
  // the choice to hide it is remembered per browser.
  const [drawer, setDrawer] = React.useState(() => {
    try { return localStorage.getItem("agent-conversations.sidebar") !== "closed"; } catch { return true; }
  });
  const toggleDrawer = () => setDrawer((v) => {
    try { localStorage.setItem("agent-conversations.sidebar", v ? "closed" : "open"); } catch { /* private mode */ }
    return !v;
  });
  const [draft, setDraft] = React.useState<Conversation["settings"] | null>(null);
  const [notice, setNotice] = React.useState<{ text: string; error: boolean } | null>(null);
  const apply = useMutation({
    mutationFn: (changes: ReturnType<typeof settingsDiff>) => configureConversation(id, changes, csrf),
    onSuccess: () => { setDraft(null); setNotice({ text: "Settings queued — they apply from the next turn.", error: false }); void refresh(); },
    onError: (e) => setNotice({ text: (e as Error).message, error: true }),
  });
  const endRef = React.useRef<HTMLDivElement>(null);
  const rows = React.useMemo(() => (conv.data ? timeline(conv.data) : []), [conv.data]);
  React.useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth" }); }, [rows.length, notice]);
  React.useEffect(() => { setDraft(null); setNotice(null); }, [id]);

  if (conv.isLoading) return <Loader2 className="m-6 h-5 w-5 animate-spin text-muted-foreground" />;
  if (conv.isError || !conv.data) return <div className="p-6 text-sm text-red-600">Could not load this conversation.</div>;
  const c = conv.data;
  const running = c.status === "running";
  const names = { local: c.local?.agent_id, peer: c.peer?.agent_id };
  const shown = draft ?? c.settings;
  const dirty = Object.keys(settingsDiff(c.settings, shown)).length > 0;

  return (
    <div className="flex h-full min-h-0" data-testid="conversation-thread">
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex flex-wrap items-center gap-2 border-b px-4 py-3">
          <span className="font-medium">{c.local?.agent_id}</span>
          <span className="text-muted-foreground">↔</span>
          <span className="font-medium">{c.peer?.agent_id}</span>
          <span className="hidden text-xs text-muted-foreground sm:inline">{c.peer?.address}</span>
          <StatusBadge status={c.status} paused={c.paused} />
          <span className="text-xs text-muted-foreground">{c.turns}/{c.max_turns} turns</span>
          <div className="ml-auto flex gap-2">
            {running && (
              <>
                <Button size="sm" variant="outline" onClick={() => pause.mutate(!c.paused)} disabled={pause.isPending}
                        data-testid="pause-toggle">
                  {c.paused ? <Play className="mr-1 h-3 w-3" /> : <Pause className="mr-1 h-3 w-3" />}
                  {c.paused ? "Resume" : "Pause"}
                </Button>
                <Button size="sm" variant="outline" onClick={() => stop.mutate()} disabled={stop.isPending}>
                  <Square className="mr-1 h-3 w-3" /> Stop
                </Button>
              </>
            )}
            <Button size="sm" variant={drawer ? "secondary" : "outline"} onClick={toggleDrawer}
                    aria-pressed={drawer} data-testid="settings-toggle">
              <SlidersHorizontal className="mr-1 h-3 w-3" /> Settings
            </Button>
            {!running && (
              <Button size="sm" variant="outline" onClick={() => del.mutate()} disabled={del.isPending}>
                <Trash2 className="mr-1 h-3 w-3" /> Delete
              </Button>
            )}
          </div>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto bg-[radial-gradient(ellipse_at_top,hsl(var(--accent)/0.05),transparent_60%)]">
          <div className="mx-auto w-full max-w-4xl space-y-4 px-4 py-5 md:px-6">
            {rows.map((r) => r.kind === "message"
              ? <MessageRow key={`m${r.seq}`} m={r.message} names={names} />
              : <div key={`e${r.seq}`} data-testid="conversation-event"
                     className="text-center text-[11px] text-muted-foreground">{eventText(r.event)}</div>)}
            {running && !c.paused && (
              <div className="flex items-center gap-2 text-xs text-muted-foreground">
                <Loader2 className="h-3 w-3 animate-spin" /> Waiting for the next turn…
              </div>
            )}
            {running && c.paused && (
              <div className="text-center text-xs text-muted-foreground">Paused — resume to continue.</div>
            )}
            {!running && c.reason && (
              <div className="text-center text-xs text-muted-foreground" data-testid="conversation-ended">
                Ended: {REASON_TEXT[c.reason] ?? c.reason}
              </div>
            )}
            {notice && (
              <p className={cn("rounded-md border px-3 py-2 text-xs",
                               notice.error ? "border-destructive/40 text-destructive" : "bg-muted/40 text-muted-foreground")}
                 data-testid="conversation-notice">{notice.text}</p>
            )}
            <div ref={endRef} />
          </div>
        </div>
        <Composer c={c} csrf={csrf} onNotice={(text, error) => setNotice({ text, error: Boolean(error) })} />
      </div>
      {drawer && (
        <aside className="hidden w-80 shrink-0 space-y-5 overflow-y-auto border-l p-4 md:block" data-testid="settings-drawer">
          <section className="space-y-2" data-testid="sidebar-participants">
            <div className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Participants</div>
            {([["Your agent", c.local], ["Peer agent", c.peer]] as const).map(([role, p]) => (
              <div key={role} className="flex items-center gap-2">
                <ChatAvatar label={p?.agent_id ?? "?"} icon={role === "Your agent" ? Bot : Globe2} />
                <div className="min-w-0 text-xs">
                  <div className="truncate font-medium">{p?.agent_id}</div>
                  <div className="truncate text-muted-foreground">{role} · {p?.model}</div>
                </div>
              </div>
            ))}
            <div className="flex items-center gap-2">
              <ChatAvatar label="You" icon={User} />
              <div className="text-xs"><div className="font-medium">You</div><div className="text-muted-foreground">Moderator — can join any time</div></div>
            </div>
          </section>
          <section className="space-y-1 text-xs" data-testid="sidebar-status">
            <div className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Status</div>
            <div className="flex justify-between"><span className="text-muted-foreground">Turns</span><span>{c.turns} / {c.max_turns}</span></div>
            <div className="flex justify-between"><span className="text-muted-foreground">State</span><span>{c.paused ? "paused" : c.status}</span></div>
            {running && <div className="flex justify-between"><span className="text-muted-foreground">Waiting messages</span><span>{c.pending}</span></div>}
          </section>
          <section className="space-y-3">
            <div className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Settings</div>
            <SettingsPanel value={shown} onChange={setDraft} names={names} disabled={!running} />
            {running
              ? <Button size="sm" className="w-full" disabled={!dirty || apply.isPending} data-testid="apply-settings"
                        onClick={() => apply.mutate(settingsDiff(c.settings, shown))}>
                  {apply.isPending ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}Apply
                </Button>
              : <p className="text-xs text-muted-foreground">Settings are fixed once a conversation has ended.</p>}
          </section>
        </aside>
      )}
    </div>
  );
}
