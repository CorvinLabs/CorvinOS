/**
 * Direct A2A thread with one peer, in the chat's main area.
 *
 * Visual language mirrors the Agent Hub relay panel
 * (components/agent-hub/live-feed.tsx) — tucked-corner bubbles, initials
 * avatars, radial-gradient message canvas, pill composer — see
 * GroupConversation.tsx's header comment for why.
 *
 * Reads the tenant-local A2A content store (GET /a2a/feed?peer_id=…) and
 * sends through POST /a2a/feed/send — the operator's own browser session,
 * so no confirmation step applies here (that gate exists for sends a chat
 * TURN stages, see PendingConfirmations).
 */
import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, Globe2, Loader2, Paperclip, Send, Mic, MicOff, FolderUp } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";
import {
  a2aFeedBlobUrl, getA2AFeed, sendA2AFeedMessage, type A2AFeedMessage,
} from "@/lib/api/a2a";
import { ChatAvatar } from "./ChatAvatar";
import { AttachmentChip } from "./AttachmentChip";
import { useAttachmentUpload } from "@/hooks/use-attachment-upload";
import { useFileDrop, supportsDirectoryDrop, MAX_DROPPED_FILES } from "@/hooks/use-file-drop";
import { useAutosizeTextarea } from "@/hooks/use-autosize-textarea";
import { DropOverlay } from "./DropOverlay";

const FEED_REFETCH_MS = 4_000;

function fmtTime(ts: number): string {
  return new Date(ts * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

function responseText(m: A2AFeedMessage): string {
  if (m.text) return m.text;
  const keys = Object.keys(m.data ?? {});
  return keys.length ? JSON.stringify(m.data, null, 2) : "";
}

function PeerMessageRow({ m, label }: { m: A2AFeedMessage; label: string }) {
  const mine = m.direction === "out";
  const failed = Boolean(m.error) || ["rejected", "timeout", "error"].includes(m.status);
  const body = m.kind === "response" ? responseText(m) : m.text;
  return (
    <div className={cn("flex gap-3", mine ? "justify-end" : "justify-start")}>
      {!mine && <div className="mt-5"><ChatAvatar label={label} icon={Globe2} /></div>}
      <div className={cn("flex min-w-0 max-w-[85%] flex-col", mine ? "items-end" : "items-start")}>
        <div className="mb-1 flex flex-wrap items-center gap-1.5 px-1 text-[11px] text-muted-foreground">
          <span className="font-medium text-foreground/80">{mine ? "You" : label}</span>
          <span>·</span>
          <span>{m.kind === "task" ? "message" : "reply"}</span>
          <span>·</span>
          <span>{fmtTime(m.ts)}</span>
          {m.status && m.status !== "ok" && m.status !== "received" && (
            <Badge variant={failed ? "danger" : "outline"} className="px-1.5 py-0 text-[9px]">{m.status}</Badge>
          )}
        </div>
        <div className={cn(
          "w-fit max-w-full rounded-2xl px-4 py-3 text-sm leading-relaxed",
          mine ? "rounded-tr-md bg-accent/15 text-foreground" : "rounded-tl-md border border-border bg-card text-card-foreground shadow-sm",
          failed && "border border-destructive/40",
        )}>
          {body && <div className="whitespace-pre-wrap break-words">{body}</div>}
          {m.error && (
            <p className="mt-1 flex items-start gap-1 text-[11px] text-destructive">
              <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" /> {m.error}
            </p>
          )}
          {m.attachments.length > 0 && (
            <div className="mt-1.5 space-y-0.5">
              {m.attachments.map((a) => (
                <a key={a.sha256} href={a2aFeedBlobUrl(a)} target="_blank" rel="noreferrer"
                  className="flex items-center gap-1 text-[11px] text-sky-600 hover:underline dark:text-sky-400">
                  <Paperclip className="h-3 w-3" /> {a.name}
                </a>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export function PeerConversation({ peerId, csrf }: { peerId: string; csrf: string }) {
  const feed = useQuery({
    queryKey: ["a2a", "feed", "peer", peerId],
    queryFn: ({ signal }) => getA2AFeed({ peer_id: peerId, limit: 200 }, signal),
    refetchInterval: FEED_REFETCH_MS,
  });
  const [text, setText] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState("");
  const [recording, setRecording] = React.useState(false);
  const endRef = React.useRef<HTMLDivElement>(null);
  const mediaRef = React.useRef<MediaRecorder | null>(null);
  const chunksRef = React.useRef<Blob[]>([]);
  const fileInputRef = React.useRef<HTMLInputElement>(null);
  const folderInputRef = React.useRef<HTMLInputElement>(null);
  const textareaRef = React.useRef<HTMLTextAreaElement>(null);
  useAutosizeTextarea(textareaRef, text);
  const [dropTruncated, setDropTruncated] = React.useState<number | null>(null);
  const {
    pendingAttachments, uploading, uploadError, addFiles,
    removeAttachment, onFileInputChange,
  } = useAttachmentUpload({
    uploadFn: async (files) => {
      /* peer messages don't support attachments yet, but track them locally */
      return files.map((f) => ({
        name: f.name,
        size: f.size,
        mime: f.type,
      }));
    },
    disabled: busy,
  });
  const { isDragging: paneDragging, dropHandlers: paneDropHandlers } = useFileDrop(
    (files) => { setDropTruncated(null); void addFiles(files); },
    { disabled: busy || uploading, onTruncated: setDropTruncated },
  );

  const peer = feed.data?.peers.find((p) => p.peer_id === peerId);
  const label = peer?.label || peerId;
  const msgs = feed.data?.messages ?? [];
  React.useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [msgs.length]);

  const startRecording = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      chunksRef.current = [];
      const mr = new MediaRecorder(stream);
      mr.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data);
      };
      mr.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop());
        // Peer messages don't transcribe audio yet; just append a placeholder
        try {
          setText((prev) => prev ? `${prev} [audio]` : "[audio]");
        } catch {
          setError("Audio processing failed");
        }
      };
      mr.start();
      mediaRef.current = mr;
      setRecording(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : "microphone access denied");
    }
  };

  const stopRecording = () => {
    mediaRef.current?.stop();
    mediaRef.current = null;
    setRecording(false);
  };

  async function handleSend() {
    const body = text.trim();
    if (!body) return;
    setBusy(true); setError("");
    try {
      await sendA2AFeedMessage({ peer_id: peerId, text: body, attachments: [] }, csrf);
      setText("");
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
          <div className="truncate text-[11px] text-muted-foreground">
            Direct A2A conversation
            {peer ? ` · ${peer.can_send ? "can send" : "send disabled"} · ${peer.can_receive ? "accepts their tasks" : "their tasks blocked"}` : ""}
          </div>
        </div>
      </header>

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
          {msgs.map((m) => <PeerMessageRow key={m.id} m={m} label={label} />)}
          {error && (
            <p className="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive">
              {error}
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
            <Textarea ref={textareaRef} value={text} onChange={(e) => setText(e.target.value)} rows={1}
              placeholder={peer?.can_send === false ? "Sending to this agent is disabled" : `Message ${label}…`}
              disabled={peer?.can_send === false || recording || busy}
              className="min-h-[2rem] flex-1 resize-none border-0 bg-transparent px-1 py-1 text-sm leading-relaxed shadow-none focus-visible:ring-0 focus-visible:ring-offset-0"
              aria-label="Message to agent"
              onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); handleSend(); } }} />
            <Button variant="accent" size="icon" className="h-8 w-8 shrink-0 rounded-full"
              disabled={busy || !text.trim() || peer?.can_send === false} onClick={handleSend} aria-label="Send">
              {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
            </Button>
          </div>
        </div>
      </footer>
    </div>
  );
}
