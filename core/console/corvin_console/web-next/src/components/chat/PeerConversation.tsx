/**
 * Direct A2A thread with one peer, in the chat's main area.
 *
 * Visual language mirrors the Agent Hub relay panel
 * (the now-deleted Agent Hub relay panel) — tucked-corner bubbles, initials
 * avatars, radial-gradient message canvas, pill composer — see
 * GroupConversation.tsx's header comment for why.
 *
 * Reads the tenant-local A2A content store (GET /a2a/feed?peer_id=…) and
 * sends through POST /a2a/feed/send — the operator's own browser session,
 * so no confirmation step applies here (that gate exists for sends a chat
 * TURN stages, see PendingConfirmations).
 */
import * as React from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { AlertTriangle, Bot, Check, CheckCheck, CircleHelp, Clock, Globe2, Loader2, Paperclip, Send, Mic, MicOff, FolderUp, Square, XCircle, type LucideIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";
import { CommandPalette, applyCommandInsertion, useSlashCommandPalette } from "./SlashCommandPalette";
import {
  a2aFeedBlobUrl, encodeFilesForA2A, getA2AFeed, getPeerThreadCommands,
  sendA2AFeedMessage, sendPeerThreadCommand,
  A2AAttachmentLimitError, A2A_MAX_ATTACHMENTS_COUNT, A2A_MAX_ATTACHMENTS_TOTAL_BYTES,
  type A2AFeedMessage, type A2AStageInfo,
} from "@/lib/api/a2a";
import {
  formatAge, messageStatusView, type MessageStatusView, type StatusIcon, type StatusTone,
} from "@/lib/a2a-message-status";
import {
  inlineImageNames, isMinePeerRole, isObserverModeEmptyReply, mediaKind, messageMarkdown,
  peerMessageRole, peerRoleLabel, referencedImageAttachment,
} from "@/lib/a2a-feed";
import { listConversations, stopConversation, type ConversationSummary } from "@/lib/api/federation";
import { Markdown } from "@/components/markdown";
import { presenceView } from "@/lib/a2a-presence";
import { useVoiceInput } from "@/hooks/use-voice-input";
import { ChatAvatar } from "./ChatAvatar";
import { AttachmentChip } from "./AttachmentChip";
import { useAttachmentUpload } from "@/hooks/use-attachment-upload";
import { useFileDrop, supportsDirectoryDrop, MAX_DROPPED_FILES } from "@/hooks/use-file-drop";
import { useAutosizeTextarea } from "@/hooks/use-autosize-textarea";
import { DropOverlay } from "./DropOverlay";

const FEED_REFETCH_MS = 4_000;
/** Faster while a message of ours is still on its way, so the symbol follows the peer. */
const FEED_REFETCH_INFLIGHT_MS = 2_000;

function fmtTime(ts: number): string {
  return new Date(ts * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

const STATUS_ICON: Record<StatusIcon, LucideIcon> = {
  clock: Clock, check: Check, "check-check": CheckCheck, spinner: Loader2, x: XCircle, help: CircleHelp,
};
const STATUS_TONE: Record<StatusTone, string> = {
  muted: "text-muted-foreground",
  info: "text-sky-600 dark:text-sky-400",
  working: "text-sky-600 dark:text-sky-400",
  success: "text-emerald-600 dark:text-emerald-400",
  warning: "text-amber-700 dark:text-amber-400",
  danger: "text-destructive",
};
const CHAIN_DOT: Record<string, string> = {
  done: "bg-current opacity-80", current: "bg-current ring-2 ring-current/30",
  pending: "bg-muted-foreground/30", failed: "bg-destructive",
};

/**
 * The status symbol that stays on EVERY message, whatever its state — a tick, a clock, a
 * spinner, a cross or a question mark — with the whole chain for messages we sent
 * (queued → sent → delivered → accepted → working → done). The mapping lives in
 * lib/a2a-message-status.ts; this only draws it.
 */
export function MessageStatusSymbol({ v, mine }: { v: MessageStatusView; mine: boolean }) {
  const Icon = STATUS_ICON[v.icon];
  const chainText = v.chain.length
    ? `\n${v.chain.map((c) => `${c.state === "done" ? "✓" : c.state === "current" ? "●" : c.state === "failed" ? "✗" : "○"} ${c.label}`).join("\n")}`
    : "";
  return (
    <div data-testid="peer-message-status" data-status={v.key}
      title={`${v.detail}${chainText}`}
      className={cn("mt-1 flex items-center gap-1 px-1 text-[11px]", STATUS_TONE[v.tone], mine ? "justify-end" : "justify-start")}>
      <Icon aria-hidden className={cn("h-3.5 w-3.5 shrink-0", v.icon === "spinner" && "motion-safe:animate-spin")} />
      <span>{v.label}</span>
      {v.chain.length > 0 && (
        <span className="ml-0.5 flex items-center gap-[3px]" aria-hidden data-testid="peer-message-chain">
          {v.chain.map((c) => (
            // On a failure only the step that failed is red; the steps before it are
            // history, not five failures.
            <span key={c.key} data-state={c.state}
              className={cn("h-1.5 w-1.5 rounded-full",
                c.state === "done" && v.tone === "danger" ? "bg-muted-foreground/60" : CHAIN_DOT[c.state])} />
          ))}
        </span>
      )}
      {v.observedAgeS !== null && v.icon !== "check-check" && v.tone !== "danger" && (
        <span className="text-muted-foreground">· {formatAge(v.observedAgeS)}</span>
      )}
      <span className="sr-only">{v.detail}</span>
    </div>
  );
}

const PeerMessageRow = React.memo(function PeerMessageRow({ m, label, reply, stage, now }: {
  m: A2AFeedMessage; label: string; reply: A2AFeedMessage | null; stage: A2AStageInfo | null; now: number;
}) {
  // Four actors, not two (ADR-2235): who authored this line is derived from
  // (direction, kind) — or from thread_ref when a moderated conversation's
  // turn-prompt overrides it — never guessed from the text itself.
  const role = peerMessageRole(m);
  const mine = isMinePeerRole(role);
  const isAgent = role === "local_agent" || role === "peer_agent";
  const roleLabel = peerRoleLabel(role, label);
  const observerModeReply = isObserverModeEmptyReply(m);
  // "unconfirmed": the request may have reached the peer — not a failure to
  // resend (a resend runs it twice). "queued": accepted, waiting to be sent.
  const unconfirmed = m.status === "unconfirmed";
  const failed = !unconfirmed && (Boolean(m.error) || ["rejected", "timeout", "error"].includes(m.status));
  // The status symbol: what the feed knows about this message, its reply and the
  // stage the peer reported — one pure mapping (lib/a2a-message-status.ts).
  const statusView = messageStatusView(m, { reply, stage, now });
  // Same Markdown renderer as the chat. Peer-authored text never loads a URL
  // it chose (blockRemoteImages); an image it names by attachment filename is
  // shown inline from this console's own blob store and not listed twice.
  const body = m.kind === "response" ? messageMarkdown(m) : m.text;
  const inlined = inlineImageNames(body, m.attachments);
  const listed = m.attachments.filter((a) => !inlined.has(a.name));
  const resolveImageSrc = React.useCallback((src: string) => {
    const att = referencedImageAttachment(src, m.attachments);
    return att ? a2aFeedBlobUrl(att) : null;
  }, [m.attachments]);
  return (
    <div data-testid="peer-message" className={cn("flex gap-3", mine ? "justify-end" : "justify-start")}>
      {!mine && <div className="mt-5"><ChatAvatar label={label} icon={Globe2} /></div>}
      <div className={cn("flex min-w-0 max-w-[85%] flex-col", mine ? "items-end" : "items-start")}>
        <div className="mb-1 flex flex-wrap items-center gap-1.5 px-1 text-[11px] text-muted-foreground">
          <span className="font-medium text-foreground/80" data-testid="peer-message-role">{roleLabel}</span>
          {isAgent && <Bot className="h-3 w-3 text-muted-foreground" aria-label="agent-authored" />}
          <span>·</span>
          <span>{m.kind === "task" ? "message" : "reply"}</span>
          <span>·</span>
          <span>{fmtTime(m.ts)}</span>
        </div>
        <div className={cn(
          "w-fit max-w-full rounded-2xl px-4 py-3 text-sm leading-relaxed",
          mine ? "rounded-tr-md bg-accent/15 text-foreground" : "rounded-tl-md border border-border bg-card text-card-foreground shadow-sm",
          failed && "border border-destructive/40",
        )}>
          {observerModeReply && (
            <p className="text-xs italic text-muted-foreground">
              no agent — {label} has not granted Executor permission
            </p>
          )}
          {!observerModeReply && body && (
            <Markdown text={body} compact blockRemoteImages resolveImageSrc={resolveImageSrc}
              className="break-words" />
          )}
          {m.error && (
            <p className={cn("mt-1 flex items-start gap-1 text-[11px]", unconfirmed ? "text-amber-700 dark:text-amber-400" : "text-destructive")}>
              <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" /> {m.error}
            </p>
          )}
          {listed.length > 0 && (
            <div className="mt-1.5 space-y-1.5">
              {listed.map((a) => {
                const kind = mediaKind(a);
                const url = a2aFeedBlobUrl(a);
                if (kind === "image") {
                  return (
                    <a key={a.sha256} href={url} target="_blank" rel="noreferrer" className="block">
                      <img src={url} alt={a.name} className="max-h-60 max-w-full rounded-lg border border-border/40" />
                    </a>
                  );
                }
                if (kind === "audio") {
                  return <audio key={a.sha256} controls preload="metadata" src={url} className="h-8 w-full" />;
                }
                if (kind === "video") {
                  return <video key={a.sha256} controls preload="metadata" src={url} className="max-h-60 w-full rounded-lg border border-border/40 bg-black" />;
                }
                return (
                  <a key={a.sha256} href={url} target="_blank" rel="noreferrer"
                    className="flex items-center gap-1 text-[11px] text-sky-600 hover:underline dark:text-sky-400">
                    <Paperclip className="h-3 w-3" /> {a.name}
                  </a>
                );
              })}
            </div>
          )}
        </div>
        <MessageStatusSymbol v={statusView} mine={mine} />
      </div>
    </div>
  );
});

/**
 * Inline status for a `/talk` conversation running in this thread (ADR-2235
 * Phase 3, step 2). Collapses to nothing once the conversation is no longer
 * `running` — the full transcript stays reachable from Agent conversations.
 */
function ActiveConversationBanner({
  conv, peerLabel, onStop, stopping,
}: {
  conv: ConversationSummary; peerLabel: string; onStop: () => void; stopping: boolean;
}) {
  if (conv.status !== "running") return null;
  return (
    <div
      data-testid="active-conversation-banner"
      className="mx-4 mt-3 flex items-center gap-2 rounded-lg border border-accent/30 bg-accent/10 px-3 py-2 text-xs md:mx-6"
    >
      <Bot className="h-3.5 w-3.5 shrink-0 text-accent" />
      <span className="truncate font-medium text-foreground/90">{conv.topic || "Agent conversation"}</span>
      <Badge variant="outline" className="shrink-0 px-1.5 py-0 text-[9px]">
        {conv.turns} turn{conv.turns === 1 ? "" : "s"}
      </Badge>
      <span className="truncate text-muted-foreground">with {peerLabel}</span>
      <Button variant="ghost" size="sm" className="ml-auto h-6 shrink-0 gap-1 px-2 text-[11px]"
        onClick={onStop} disabled={stopping}>
        <Square className="h-3 w-3" /> Stop
      </Button>
    </div>
  );
}

export function PeerConversation({ peerId, csrf }: { peerId: string; csrf: string }) {
  const feed = useQuery({
    queryKey: ["a2a", "feed", "peer", peerId],
    queryFn: ({ signal }) => getA2AFeed({ peer_id: peerId, limit: 200, include_former: true }, signal),
    refetchInterval: (q) => {
      const d = q.state.data;
      if (!d) return FEED_REFETCH_MS;
      const replied = new Set(d.messages.filter((m) => m.kind === "response").map((m) => m.task_id));
      const inFlight = d.messages.some((m) => m.direction === "out" && m.kind === "task"
        && !replied.has(m.task_id) && (m.status === "queued" || m.status === "sent")
        && !["completed", "failed", "rejected", "timeout"].includes(d.stages?.[m.task_id]?.stage ?? ""));
      return inFlight ? FEED_REFETCH_INFLIGHT_MS : FEED_REFETCH_MS;
    },
  });
  // Polled faster while a conversation in THIS thread is running, so the
  // inline banner's turn counter and the "collapses when done" transition
  // feel live — same idea as agent-conversations.tsx's LIVE_POLL_MS.
  const conversations = useQuery({
    queryKey: ["federation-conversations"],
    queryFn: listConversations,
    refetchInterval: (q) => {
      const running = (q.state.data?.conversations ?? []).some(
        (c) => c.peer?.endpoint_id === peerId && c.status === "running");
      return running ? 1_000 : 5_000;
    },
  });
  const activeConversation = React.useMemo(() => {
    const mine = (conversations.data?.conversations ?? []).filter(
      (c) => c.peer?.endpoint_id === peerId);
    return mine.sort((a, b) => (b.started_at ?? 0) - (a.started_at ?? 0))[0] ?? null;
  }, [conversations.data, peerId]);
  const stopConvMutation = useMutation({
    mutationFn: (id: string) => stopConversation(id, csrf),
    onSuccess: () => conversations.refetch(),
  });
  const [text, setText] = React.useState("");
  const peerThreadCommands = useQuery({
    queryKey: ["peer-thread-commands"],
    queryFn: ({ signal }) => getPeerThreadCommands(signal),
    staleTime: 5 * 60_000,
  });
  const slashPalette = useSlashCommandPalette(text, peerThreadCommands.data?.commands ?? []);
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState("");
  const [commandNotice, setCommandNotice] = React.useState("");
  const endRef = React.useRef<HTMLDivElement>(null);
  const fileInputRef = React.useRef<HTMLInputElement>(null);
  const folderInputRef = React.useRef<HTMLInputElement>(null);
  const textareaRef = React.useRef<HTMLTextAreaElement>(null);
  useAutosizeTextarea(textareaRef, text);
  const [dropTruncated, setDropTruncated] = React.useState<number | null>(null);
  const {
    pendingAttachments, uploading, uploadError, addFiles,
    removeAttachment, clearAttachments, onFileInputChange,
  } = useAttachmentUpload({
    // The A2A envelope caps (count + total bytes) apply to the whole message,
    // not to one pick: check what is already staged plus the new files.
    uploadFn: (files) => {
      const staged = stagedRef.current;
      if (staged.length + files.length > A2A_MAX_ATTACHMENTS_COUNT) {
        return Promise.reject(new A2AAttachmentLimitError(
          `Too many files for a peer message — max ${A2A_MAX_ATTACHMENTS_COUNT} (A2A envelope cap)`));
      }
      const total = staged.reduce((n, a) => n + a.size, 0) + files.reduce((n, f) => n + f.size, 0);
      if (total > A2A_MAX_ATTACHMENTS_TOTAL_BYTES) {
        return Promise.reject(new A2AAttachmentLimitError(
          `Attachments too large for a peer message — max ${(A2A_MAX_ATTACHMENTS_TOTAL_BYTES / 1024).toFixed(0)} KiB total (A2A envelope cap)`));
      }
      return encodeFilesForA2A(files, staged.map((a) => a.name));
    },
    disabled: busy,
    formatError: (e) => e instanceof A2AAttachmentLimitError ? e.message
      : e instanceof Error ? e.message : "Upload failed",
  });
  const stagedRef = React.useRef(pendingAttachments);
  stagedRef.current = pendingAttachments;
  const { recording, startRecording, stopRecording } = useVoiceInput({
    value: text, onChange: setText, csrf, disabled: busy || feed.data?.peers.find((p) => p.peer_id === peerId)?.can_send === false,
    onError: setError,
  });
  const { isDragging: paneDragging, dropHandlers: paneDropHandlers } = useFileDrop(
    (files) => { setDropTruncated(null); void addFiles(files); },
    { disabled: busy || uploading, onTruncated: setDropTruncated },
  );

  const peer = feed.data?.peers.find((p) => p.peer_id === peerId);
  const label = peer?.label || peerId;
  const msgs = React.useMemo(() => feed.data?.messages ?? [], [feed.data]);
  // task_id -> the reply record, so the symbol of a task knows HOW it was answered
  // (ok / refused / unconfirmed), not just that it was.
  const repliesByTask = React.useMemo(() => {
    const byTask = new Map<string, A2AFeedMessage>();
    for (const m of msgs) if (m.kind === "response") byTask.set(m.task_id, m);
    return byTask;
  }, [msgs]);
  const stages = feed.data?.stages;
  const nowS = Math.floor((feed.dataUpdatedAt || Date.now()) / 1000);
  // Reachable is not the same as accepting: a peer can answer pings while
  // refusing every task (e.g. it requires a verified CorvinOS identity).
  const lastReply = React.useMemo(() => {
    for (let i = msgs.length - 1; i >= 0; i--) {
      const m = msgs[i];
      if (m.kind === "response" && m.direction === "in") return m;
    }
    return null;
  }, [msgs]);
  React.useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [msgs.length]);

  async function handleSend() {
    const body = text.trim();
    if ((!body && pendingAttachments.length === 0) || uploading) return;
    setBusy(true); setError(""); setCommandNotice("");
    slashPalette.close();
    try {
      // A `/` line is never sent to the peer as plain text — it goes through
      // the server-side dispatcher first (ADR-2235 Phase 2). That dispatcher
      // is the fix for the reported bug: before it existed, `/ask @mine …`
      // had no interception and reached the PEER's own worker as the task
      // instruction, which answered it instead of the local agent.
      if (body.startsWith("/")) {
        const result = await sendPeerThreadCommand(peerId, body, csrf);
        if (!result.executed) {
          setError(typeof result.reason === "string" ? result.reason : "unknown command — not sent");
        } else {
          setCommandNotice(
            result.kind === "ask_mine" && typeof result.text === "string" && result.text
              ? String(result.text)
              : `/${String(result.kind ?? "command")} — done. See Agent conversations for the full exchange.`,
          );
        }
        setText("");
        conversations.refetch();
      } else {
        await sendA2AFeedMessage({
          peer_id: peerId,
          text: body,
          attachments: pendingAttachments.map((a) => ({ name: a.name, mime: a.mime, content_b64: a.content_b64 })),
        }, csrf);
        setText("");
        clearAttachments();
      }
      feed.refetch();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      className="relative flex min-h-0 flex-1 flex-col"
      data-testid="peer-conversation"
      {...paneDropHandlers}
    >
      <DropOverlay active={paneDragging} folders={supportsDirectoryDrop} />
      <header className="flex items-center gap-3 border-b border-border px-4 py-3">
        <ChatAvatar label={label} icon={Globe2} />
        <div className="min-w-0 flex-1">
          <div className="truncate font-serif text-lg font-light leading-tight">{label}</div>
          <div className="flex items-center gap-1.5 truncate text-[11px] text-muted-foreground">
            {peer && (() => {
              const pv = presenceView(peer);
              return (
                <span className="inline-flex items-center gap-1" title={pv.title} data-testid="peer-presence" data-presence={pv.presence}>
                  <span aria-hidden className={cn("h-1.5 w-1.5 rounded-full", pv.dotClass)} /> {pv.label} ·
                </span>
              );
            })()}
            {lastReply?.status === "rejected" && (
              <span className="inline-flex items-center gap-1 text-amber-700 dark:text-amber-400"
                data-testid="peer-last-rejected" title={lastReply.error ?? "The peer refused the last message"}>
                <AlertTriangle className="h-3 w-3" /> last message refused ·
              </span>
            )}
            <span className="truncate">
              Direct A2A conversation
              {peer ? ` · ${peer.can_send ? "can send" : "send disabled"} · ${peer.can_receive ? "accepts their tasks" : "their tasks blocked"}` : ""}
            </span>
          </div>
        </div>
      </header>

      {activeConversation && (
        <ActiveConversationBanner
          conv={activeConversation} peerLabel={label}
          stopping={stopConvMutation.isPending}
          onStop={() => stopConvMutation.mutate(activeConversation.conversation_id)}
        />
      )}

      <div className="relative min-h-0 flex-1 overflow-y-auto bg-[radial-gradient(ellipse_at_top,hsl(var(--accent)/0.06),transparent_60%)] px-4 py-5 md:px-6">
        <div className="mx-auto flex w-full max-w-4xl flex-col space-y-4">
          {feed.isLoading && <Loader2 className="mx-auto h-5 w-5 animate-spin" />}
          {feed.error && (
            <p className="text-sm text-destructive">
              {feed.error instanceof Error ? feed.error.message : "A2A feed unavailable"}
            </p>
          )}
          {feed.data && msgs.length === 0 && (
            <p className="pt-10 text-center text-sm text-muted-foreground">No messages with this agent yet.</p>
          )}
          {msgs.map((m) => <PeerMessageRow key={m.id} m={m} label={label}
            reply={m.kind === "task" ? repliesByTask.get(m.task_id) ?? null : null}
            stage={m.kind === "task" && m.direction === "out" ? stages?.[m.task_id] ?? null : null}
            now={nowS} />)}
          {error && (
            <p className="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive">
              {error}
            </p>
          )}
          {commandNotice && (
            <p className="rounded-md border border-border bg-muted/40 px-3 py-2 text-xs text-muted-foreground">
              {commandNotice}
            </p>
          )}
          <div ref={endRef} />
        </div>
      </div>

      <footer className="px-4 py-3 md:px-6">
        <div className="mx-auto w-full max-w-4xl space-y-1.5 rounded-2xl" data-testid="composer-dropzone">
          {/* Hidden file input */}
          <input
            ref={fileInputRef}
            type="file"
            multiple
            className="sr-only"
            aria-label="Attach files"
            onChange={onFileInputChange}
            data-testid="file-input"
          />
          {/* Hidden folder input — only wired to a visible button where the
              browser supports webkitdirectory (Chromium/Firefox, not Safari). */}
          <input
            ref={folderInputRef}
            type="file"
            multiple
            // @ts-expect-error -- webkitdirectory has no TS lib.dom typing
            webkitdirectory=""
            directory=""
            className="sr-only"
            aria-label="Attach a folder"
            onChange={(e) => {
              const files = Array.from(e.target.files ?? []).slice(0, MAX_DROPPED_FILES);
              if (e.target.files && e.target.files.length > MAX_DROPPED_FILES) setDropTruncated(MAX_DROPPED_FILES);
              e.target.value = "";
              if (files.length > 0) void addFiles(files);
            }}
            data-testid="folder-input"
          />
          {dropTruncated != null && (
            <p className="text-xs text-amber-600 dark:text-amber-400" data-testid="drop-truncated-notice">
              Only the first {dropTruncated} files were attached — drop fewer at once for the rest.
            </p>
          )}
          {/* Pending-attachment chips */}
          {pendingAttachments.length > 0 && (
            <div className="flex flex-wrap gap-1.5" data-testid="attachment-preview-bar">
              {pendingAttachments.map((a, i) => (
                <AttachmentChip
                  key={`${a.name}-${i}`}
                  attachment={a}
                  onRemove={() => removeAttachment(i)}
                />
              ))}
            </div>
          )}
          {/* Upload error */}
          {uploadError && (
            <p className="text-xs text-destructive" data-testid="upload-error">{uploadError}</p>
          )}
          <div className="flex items-end gap-2 rounded-2xl border border-border bg-card px-2 py-1.5 shadow-sm transition-colors focus-within:border-accent/50 focus-within:ring-2 focus-within:ring-accent/15">
            <Button
              variant="ghost"
              size="icon"
              className="h-8 w-8 shrink-0 text-muted-foreground"
              onClick={() => fileInputRef.current?.click()}
              disabled={busy || uploading || peer?.can_send === false}
              title="Attach files"
              data-testid="attach-button"
            >
              {uploading
                ? <Loader2 className="h-4 w-4 animate-spin" />
                : <Paperclip className="h-4 w-4" />
              }
            </Button>
            {supportsDirectoryDrop && (
              <Button
                variant="ghost"
                size="icon"
                className="h-8 w-8 shrink-0 text-muted-foreground"
                onClick={() => folderInputRef.current?.click()}
                disabled={busy || uploading || peer?.can_send === false}
                title="Attach a folder"
                data-testid="attach-folder-button"
              >
                <FolderUp className="h-4 w-4" />
              </Button>
            )}
            <Button
              variant={recording ? "destructive" : "ghost"}
              size="icon"
              className={cn("h-8 w-8 shrink-0", !recording && "text-muted-foreground")}
              onClick={recording ? stopRecording : startRecording}
              disabled={busy || peer?.can_send === false}
              title={recording ? "Stop recording" : "Start recording"}
            >
              {recording ? <MicOff className="h-4 w-4" /> : <Mic className="h-4 w-4" />}
            </Button>
            <div className="relative min-w-0 flex-1">
              <CommandPalette
                matches={slashPalette.matches}
                selected={slashPalette.selected}
                onSelect={(match) => {
                  applyCommandInsertion(match, setText, textareaRef);
                  slashPalette.close();
                }}
              />
              <Textarea ref={textareaRef} value={text} onChange={(e) => {
                const v = e.target.value;
                setText(v);
                slashPalette.onChange(v);
              }} rows={1}
                placeholder={peer?.presence === "removed"
                  ? "Pairing removed — this conversation is read-only"
                  : peer?.can_send === false ? "Sending to this agent is disabled" : `Message ${label}…`}
                disabled={peer?.can_send === false || recording || busy}
                className="min-h-[2rem] flex-1 resize-none border-0 bg-transparent px-1 py-1 text-sm leading-relaxed shadow-none focus-visible:ring-0 focus-visible:ring-offset-0"
                aria-label="Message to agent"
                onKeyDown={(e) => {
                  if (slashPalette.onKeyDown(e, (match) => applyCommandInsertion(match, setText, textareaRef))) return;
                  if (e.key !== "Enter" || e.shiftKey || e.nativeEvent.isComposing) return;
                  e.preventDefault();
                  handleSend();
                }} />
            </div>
            <Button variant="accent" size="icon" className="h-8 w-8 shrink-0 rounded-full"
              disabled={busy || uploading || (!text.trim() && pendingAttachments.length === 0) || peer?.can_send === false}
              onClick={handleSend} aria-label="Send">
              {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
            </Button>
          </div>
        </div>
      </footer>
    </div>
  );
}
