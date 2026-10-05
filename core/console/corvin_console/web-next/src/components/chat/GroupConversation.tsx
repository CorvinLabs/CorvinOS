/**
 * Group conversation in the chat's main area (ADR-2216, ADR-2218).
 *
 * Visual language mirrors the Agent Hub relay panel
 * (components/agent-hub/live-feed.tsx) — tucked-corner bubbles, initials
 * avatars, radial-gradient message canvas, pill composer — and the
 * single-session chat pane (pages/chat.tsx::ChatPane), which was reskinned
 * to the same language. See components/chat/PeerConversation.tsx for the
 * same treatment applied to a direct A2A thread.
 *
 * A message posted here is stored locally AND delivered by the backend to
 * every A2A peer in the group (routes/chat_groups.py::send_message fan-out);
 * the peer's instance files it under the same group_id. Peer replies arrive
 * through the A2A receiver and show up on the next poll — there is no push
 * channel for group messages, so the list polls.
 */
import * as React from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Globe2, Loader2, Send, Trash2, UserPlus, Users, Paperclip, Mic, MicOff, FolderUp } from "lucide-react";
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
import { ChatAvatar } from "./ChatAvatar";
import { MembersSection } from "./MembersSection";
import { AttachmentChip } from "./AttachmentChip";
import { useAttachmentUpload } from "@/hooks/use-attachment-upload";
import { useFileDrop, supportsDirectoryDrop, MAX_DROPPED_FILES } from "@/hooks/use-file-drop";
import { useAutosizeTextarea } from "@/hooks/use-autosize-textarea";
import { DropOverlay } from "./DropOverlay";

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
  const name = sender?.display_name || m.sender_participant_id;
  const isPeer = sender?.kind === "a2a_peer";
  return (
    <div className={cn("flex gap-3", mine ? "justify-end" : "justify-start")}>
      {!mine && <div className="mt-5"><ChatAvatar label={name} icon={isPeer ? Globe2 : undefined} /></div>}
      <div className={cn("flex min-w-0 max-w-[85%] flex-col", mine ? "items-end" : "items-start")}>
        <div className="mb-1 flex items-center gap-1.5 px-1 text-[11px] text-muted-foreground">
          <span className="font-medium text-foreground/80">{mine ? "You" : name}</span>
          <span>·</span>
          <span>{fmtTime(m.ts)}</span>
          {m.delivery === "fanout" && <span title="Delivered to the group's A2A peers">· sent to peers</span>}
        </div>
        <div className={cn(
          "w-fit max-w-full rounded-2xl px-4 py-3 text-sm leading-relaxed",
          mine ? "rounded-tr-md bg-accent/15 text-foreground" : "rounded-tl-md border border-border bg-card text-card-foreground shadow-sm",
        )}>
          <div className="whitespace-pre-wrap break-words">{m.text}</div>
        </div>
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
  const [recording, setRecording] = React.useState(false);
  const navigate = useNavigate();
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
      /* group messages don't support attachments yet, but track them locally */
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
        // Group messages don't transcribe audio yet; just append a placeholder
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
    <div
      className="relative flex min-h-0 flex-1 flex-col"
      data-testid="group-conversation"
      {...paneDropHandlers}
    >
      <DropOverlay active={paneDragging} folders={supportsDirectoryDrop} />
      <header className="flex items-center gap-3 border-b border-border px-4 py-3">
        <ChatAvatar label={g.title} icon={Users} />
        <div className="min-w-0 flex-1">
          <div className="truncate font-serif text-lg font-light leading-tight">{g.title}</div>
          <div className="truncate text-[11px] text-muted-foreground">
            {plural(g.participants.length, "member")}{peerCount > 0 ? ` · ${plural(peerCount, "agent")} via A2A` : ""}
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <Button variant={membersOpen ? "accent" : "ghost"} size="sm"
            onClick={() => setMembersOpen((v) => !v)} aria-expanded={membersOpen}>
            <Users className="h-4 w-4" /> Members
          </Button>
          {confirmDelete ? (
            <>
              <Button size="sm" variant="destructive" onClick={handleDelete}>Delete group</Button>
              <Button size="sm" variant="ghost" onClick={() => setConfirmDelete(false)}>Cancel</Button>
            </>
          ) : (
            <Button variant="ghost" size="icon" className="h-8 w-8 text-muted-foreground" aria-label="Delete group"
              title="Delete this group on this instance" onClick={() => setConfirmDelete(true)}>
              <Trash2 className="h-4 w-4" />
            </Button>
          )}
        </div>
      </header>

      {membersOpen && (
        <div className="max-h-72 overflow-y-auto border-b border-border bg-card/40">
          <MembersSection group={g} csrf={csrf} selfId={selfId} onChanged={refreshGroup} />
          <AddPeerQuick group={g} csrf={csrf} onAdded={refreshGroup} />
        </div>
      )}

      <div className="relative min-h-0 flex-1 overflow-y-auto bg-[radial-gradient(ellipse_at_top,hsl(var(--accent)/0.06),transparent_60%)] px-4 py-5 md:px-6">
        <div className="mx-auto flex w-full max-w-4xl flex-col space-y-4">
          {count === 0 && (
            <p className="pt-10 text-center text-sm text-muted-foreground">
              No messages yet.{peerCount === 0 ? " Add a connected agent under Members to chat across instances." : ""}
            </p>
          )}
          {(messages.data ?? []).map((m) => <GroupMessageRow key={m.id} m={m} group={g} selfId={selfId} />)}
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
          {peerCount > 0 && (
            <Badge variant="outline" className="text-[10px]">
              <Globe2 className="mr-1 h-3 w-3" /> Messages are delivered to {plural(peerCount, "external agent")}
            </Badge>
          )}
          <div className="flex items-end gap-2 rounded-2xl border border-border bg-card px-2 py-1.5 shadow-sm transition-colors focus-within:border-accent/50 focus-within:ring-2 focus-within:ring-accent/15">
            <Button
              variant="ghost"
              size="icon"
              className="h-8 w-8 shrink-0 text-muted-foreground"
              onClick={() => fileInputRef.current?.click()}
              disabled={busy || uploading}
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
                disabled={busy || uploading}
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
              disabled={busy}
              title={recording ? "Stop recording" : "Start recording"}
            >
              {recording ? <MicOff className="h-4 w-4" /> : <Mic className="h-4 w-4" />}
            </Button>
            <Textarea ref={textareaRef} value={text} onChange={(e) => setText(e.target.value)} rows={1}
              placeholder={`Message ${g.title}…`}
              disabled={recording || busy}
              className="min-h-[2rem] flex-1 resize-none border-0 bg-transparent px-1 py-1 text-sm leading-relaxed shadow-none focus-visible:ring-0 focus-visible:ring-offset-0"
              aria-label="Group message"
              onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); handleSend(); } }} />
            <Button variant="accent" size="icon" className="h-8 w-8 shrink-0 rounded-full"
              disabled={busy || !text.trim()} onClick={handleSend} aria-label="Send">
              {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
            </Button>
          </div>
        </div>
      </footer>
    </div>
  );
}
