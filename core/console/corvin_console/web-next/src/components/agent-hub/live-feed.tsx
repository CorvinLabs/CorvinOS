/**
 * Agent Hub — live feed of A2A conversations with media.
 *
 * Renders the tenant-local A2A content store (backend: routes/a2a_feed.py over
 * corvin_operator/bridges/shared/a2a_feed.py) as a chat, styled after the
 * console chat: this instance's messages on the right in the accent tint,
 * peer messages on the left in cards with an avatar. Attachments render
 * inline (images, audio, video, PDF, text previews). Polls every 2 s.
 */
import * as React from "react";
import {
  ArrowDown,
  Bot,
  CornerDownRight,
  Download,
  FileText,
  Globe2,
  Inbox,
  Loader2,
  Paperclip,
  Pause,
  Play,
  Send,
  Trash2,
  X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Markdown } from "@/components/markdown";
import { cn } from "@/lib/utils";
import { useAuth } from "@/lib/auth";
import {
  type A2AFeedAttachment,
  type A2AFeedMessage,
  type A2AFeedPeer,
  a2aFeedBlobUrl,
  clearA2AFeed,
  getA2AFeed,
  sendA2AFeedMessage,
} from "@/lib/api";
import {
  MAX_ATTACHMENTS_COUNT,
  MAX_ATTACHMENTS_TOTAL_BYTES,
  failureHint,
  fmtBytes,
  initials,
  isEmptyDelivery,
  mediaKind,
  mergeMessages,
  messageBody,
  pendingTaskIds,
  sanitizeAttachmentName,
  statusTone,
} from "@/lib/a2a-feed";

const POLL_MS = 2_000;
const ALL = "__all__";

// ── small pieces ───────────────────────────────────────────────────

function peerName(peers: Map<string, A2AFeedPeer>, id: string): string {
  return peers.get(id)?.label || id.slice(0, 8);
}

function fmtTime(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit" });
}

function fmtDay(ts: number): string {
  const d = new Date(ts * 1000);
  const today = new Date();
  const yesterday = new Date(Date.now() - 86_400_000);
  if (d.toDateString() === today.toDateString()) return "Today";
  if (d.toDateString() === yesterday.toDateString()) return "Yesterday";
  return d.toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" });
}

function fmtDuration(ms: number | null): string | null {
  if (ms === null || ms === undefined) return null;
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`;
}

function stateDot(state: string | null): string {
  if (state === "ACTIVE") return "bg-emerald-500";
  if (state === "UNREACHABLE") return "bg-destructive";
  return "bg-amber-400";
}

function PeerAvatar({ label, state, size = "md" }: { label: string; state?: string | null; size?: "sm" | "md" }) {
  return (
    <div className="relative shrink-0">
      <div
        className={cn(
          "flex items-center justify-center rounded-full bg-accent/15 font-medium text-accent ring-1 ring-accent/25",
          size === "md" ? "h-9 w-9 text-xs" : "h-7 w-7 text-[10px]",
        )}
      >
        {initials(label)}
      </div>
      {state !== undefined && (
        <span
          className={cn(
            "absolute -bottom-0.5 -right-0.5 h-2.5 w-2.5 rounded-full ring-2 ring-background",
            stateDot(state),
          )}
        />
      )}
    </div>
  );
}

function StatusChip({ status }: { status: string }) {
  const tone = statusTone(status);
  return (
    <span
      className={cn(
        "rounded-full px-1.5 py-px font-mono text-[10px]",
        tone === "ok" && "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400",
        tone === "warn" && "bg-amber-500/10 text-amber-700 dark:text-amber-400",
        tone === "error" && "bg-destructive/10 text-destructive",
        tone === "neutral" && "bg-muted text-muted-foreground",
      )}
    >
      {status}
    </span>
  );
}

function TypingDots() {
  return (
    <span className="inline-flex items-center gap-1" aria-hidden>
      {[0, 150, 300].map((d) => (
        <span
          key={d}
          className="h-1.5 w-1.5 animate-bounce rounded-full bg-accent/70"
          style={{ animationDelay: `${d}ms` }}
        />
      ))}
    </span>
  );
}

// ── attachments ────────────────────────────────────────────────────

function TextPreview({ url }: { url: string }) {
  const [text, setText] = React.useState<string | null>(null);
  React.useEffect(() => {
    const ctl = new AbortController();
    fetch(url, { signal: ctl.signal, credentials: "include" })
      .then((r) => r.text())
      .then((t) => setText(t.slice(0, 6000)))
      .catch(() => setText(null));
    return () => ctl.abort();
  }, [url]);
  if (text === null) return <div className="px-3 py-2 text-xs text-muted-foreground">Loading preview…</div>;
  return (
    <pre className="max-h-56 overflow-auto whitespace-pre-wrap break-words px-3 py-2 font-mono text-[11px] leading-relaxed text-muted-foreground">
      {text}
    </pre>
  );
}

function AttachmentView({ att, onOpenImage }: { att: A2AFeedAttachment; onOpenImage: (a: A2AFeedAttachment) => void }) {
  const url = a2aFeedBlobUrl(att);
  const kind = mediaKind(att);
  const [showText, setShowText] = React.useState(false);

  if (kind === "image") {
    return (
      <button
        type="button"
        onClick={() => onOpenImage(att)}
        className="group relative block w-fit max-w-full overflow-hidden rounded-xl border border-border/60 bg-black/5"
        title={`${att.name} · ${fmtBytes(att.size)}`}
      >
        {/* Natural size up to the bubble width — a tiny image is never blown up. */}
        <img
          src={url}
          alt={att.name}
          className="block h-auto max-h-80 w-auto max-w-full min-w-[3rem] object-contain transition-transform duration-300 group-hover:scale-[1.02]"
        />
        <span className="absolute inset-x-0 bottom-0 truncate bg-gradient-to-t from-black/60 to-transparent px-2 pb-1 pt-4 text-left text-[10px] text-white/90 opacity-0 transition-opacity group-hover:opacity-100">
          {att.name}
        </span>
      </button>
    );
  }
  if (kind === "audio") {
    return (
      <div className="rounded-xl border border-border/60 bg-muted/30 px-3 py-2">
        <div className="mb-1 truncate text-[11px] text-muted-foreground">{att.name}</div>
        <audio controls preload="metadata" src={url} className="h-8 w-full" />
      </div>
    );
  }
  if (kind === "video") {
    return (
      <video controls preload="metadata" src={url} className="max-h-80 w-full rounded-xl border border-border/60 bg-black" />
    );
  }
  return (
    <div className="overflow-hidden rounded-xl border border-border/60 bg-muted/30">
      <div className="flex items-center gap-2 px-3 py-2">
        <FileText className="h-4 w-4 shrink-0 text-accent" />
        <div className="min-w-0 flex-1">
          <div className="truncate text-xs font-medium">{att.name}</div>
          <div className="text-[10px] text-muted-foreground">
            {att.mime || "file"} · {fmtBytes(att.size)}
          </div>
        </div>
        {kind === "text" && (
          <button
            type="button"
            onClick={() => setShowText((v) => !v)}
            className="rounded px-1.5 py-0.5 text-[10px] text-muted-foreground hover:bg-muted hover:text-foreground"
          >
            {showText ? "Hide" : "Preview"}
          </button>
        )}
        <a
          href={url}
          target={kind === "pdf" ? "_blank" : undefined}
          rel="noreferrer"
          download={kind === "pdf" ? undefined : att.name}
          className="rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
          title={kind === "pdf" ? "Open" : "Download"}
        >
          <Download className="h-3.5 w-3.5" />
        </a>
      </div>
      {showText && <div className="border-t border-border/60"><TextPreview url={url} /></div>}
    </div>
  );
}

function Lightbox({ att, onClose }: { att: A2AFeedAttachment; onClose: () => void }) {
  React.useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/85 p-6 backdrop-blur-sm"
      onClick={onClose}
      role="dialog"
      aria-label={att.name}
    >
      <img src={a2aFeedBlobUrl(att)} alt={att.name} className="max-h-full max-w-full rounded-lg shadow-2xl" />
      <div className="absolute left-6 top-5 text-sm text-white/80">
        {att.name} <span className="text-white/50">· {fmtBytes(att.size)}</span>
      </div>
      <button className="absolute right-5 top-4 rounded-full p-2 text-white/80 hover:bg-white/10" onClick={onClose}>
        <X className="h-5 w-5" />
      </button>
    </div>
  );
}

// ── message bubble ─────────────────────────────────────────────────

function Bubble({
  m,
  peers,
  taskText,
  showPeer,
  onOpenImage,
}: {
  m: A2AFeedMessage;
  peers: Map<string, A2AFeedPeer>;
  taskText: string | undefined;
  showPeer: boolean;
  onOpenImage: (a: A2AFeedAttachment) => void;
}) {
  const mine = m.direction === "out";
  const name = peerName(peers, m.peer_id);
  const { text, rest } = messageBody(m);
  const restKeys = Object.keys(rest);
  const images = m.attachments.filter((a) => mediaKind(a) === "image");
  const others = m.attachments.filter((a) => mediaKind(a) !== "image");
  const empty = isEmptyDelivery(m);
  const hint = failureHint(m, mine);
  const duration = fmtDuration(m.duration_ms);

  return (
    <div className={cn("flex gap-3", mine ? "justify-end" : "justify-start")} data-testid="a2a-feed-message">
      {!mine && <div className="mt-5"><PeerAvatar label={name} size="sm" /></div>}
      <div className={cn("flex min-w-0 max-w-[78%] flex-col", mine ? "items-end" : "items-start")}>
        <div className={cn("mb-1 flex items-center gap-1.5 px-1 text-[11px] text-muted-foreground", mine && "flex-row-reverse")}>
          <span className="font-medium text-foreground/80">
            {mine ? (showPeer ? `You → ${name}` : "You") : name}
          </span>
          <span>·</span>
          <span>{m.kind === "task" ? "task" : "reply"}</span>
          <span>·</span>
          <span className="tabular-nums">{fmtTime(m.ts)}</span>
          {m.kind === "response" && <StatusChip status={m.status} />}
          {duration && m.kind === "response" && <span className="tabular-nums">{duration}</span>}
        </div>
        <div
          className={cn(
            "w-fit max-w-full rounded-2xl px-4 py-3 text-sm leading-relaxed",
            mine
              ? "rounded-tr-md bg-accent/15 text-foreground"
              : "rounded-tl-md border border-border bg-card text-card-foreground shadow-sm",
          )}
        >
          {m.kind === "response" && taskText && (
            <div className="mb-2 flex items-start gap-1 border-l-2 border-accent/40 pl-2 text-[11px] text-muted-foreground">
              <CornerDownRight className="mt-0.5 h-3 w-3 shrink-0" />
              <span className="line-clamp-2">{taskText}</span>
            </div>
          )}
          {text && (
            m.kind === "task"
              ? <p className="whitespace-pre-wrap break-words">{text}</p>
              : <div className="break-words"><Markdown text={text} compact /></div>
          )}
          {empty && (
            <p className="text-xs italic text-muted-foreground">
              Delivered — the {mine ? "reply" : "peer"} carried no content (no worker ran on the answering side).
            </p>
          )}
          {hint && (
            <p className="text-xs text-muted-foreground">{hint}</p>
          )}
          {m.error && (
            <p className="mt-1 rounded-md border border-destructive/30 bg-destructive/10 px-2 py-1 text-xs text-destructive">
              {m.error}
            </p>
          )}
          {images.length > 0 && (
            <div className={cn("mt-2 grid gap-2", images.length > 1 ? "grid-cols-2" : "grid-cols-1")}>
              {images.map((a) => <AttachmentView key={a.sha256 + a.name} att={a} onOpenImage={onOpenImage} />)}
            </div>
          )}
          {others.length > 0 && (
            <div className="mt-2 flex flex-col gap-2">
              {others.map((a) => <AttachmentView key={a.sha256 + a.name} att={a} onOpenImage={onOpenImage} />)}
            </div>
          )}
          {restKeys.length > 0 && (
            <details className="mt-2 rounded-lg border border-border/60 bg-muted/30 text-xs">
              <summary className="cursor-pointer select-none px-2 py-1 text-muted-foreground">
                Structured data · {restKeys.length} field{restKeys.length === 1 ? "" : "s"}
              </summary>
              <pre className="max-h-64 overflow-auto px-2 pb-2 font-mono text-[11px]">
                {JSON.stringify(rest, null, 2)}
              </pre>
            </details>
          )}
        </div>
      </div>
    </div>
  );
}

function PendingBubble({ name, mine, since }: { name: string; mine: boolean; since: number }) {
  const [, tick] = React.useReducer((x: number) => x + 1, 0);
  React.useEffect(() => {
    const t = setInterval(tick, 1000);
    return () => clearInterval(t);
  }, []);
  const secs = Math.max(0, Math.round(Date.now() / 1000 - since));
  return (
    <div className={cn("flex gap-3", mine ? "justify-end" : "justify-start")} data-testid="a2a-feed-pending">
      {!mine && <PeerAvatar label={name} size="sm" />}
      <div
        className={cn(
          "flex items-center gap-2 rounded-2xl px-4 py-2.5 text-xs text-muted-foreground",
          mine ? "rounded-tr-md bg-accent/10" : "rounded-tl-md border border-dashed border-border bg-card/60",
        )}
      >
        <TypingDots />
        <span>{mine ? "Your agent is working" : `${name} is working`} · {secs}s</span>
      </div>
    </div>
  );
}

// ── composer ───────────────────────────────────────────────────────

interface PendingFile {
  name: string;
  mime: string;
  size: number;
  b64: string;
  preview: string | null;
}

function readFile(f: File): Promise<PendingFile> {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => {
      const s = String(r.result);
      resolve({
        name: sanitizeAttachmentName(f.name),
        mime: f.type || "application/octet-stream",
        size: f.size,
        b64: s.slice(s.indexOf(",") + 1),
        preview: f.type.startsWith("image/") && f.type !== "image/svg+xml" ? URL.createObjectURL(f) : null,
      });
    };
    r.onerror = () => reject(r.error);
    r.readAsDataURL(f);
  });
}

function uniqueName(name: string, taken: Set<string>): string {
  if (!taken.has(name)) return name;
  const dot = name.lastIndexOf(".");
  const [base, ext] = dot > 0 ? [name.slice(0, dot), name.slice(dot)] : [name, ""];
  for (let i = 2; ; i++) {
    const n = `${base}-${i}${ext}`;
    if (!taken.has(n)) return n;
  }
}

function Composer({ peer, onSent }: { peer: A2AFeedPeer; onSent: () => void }) {
  const { session } = useAuth();
  const [text, setText] = React.useState("");
  const [files, setFiles] = React.useState<PendingFile[]>([]);
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [dragging, setDragging] = React.useState(false);
  const fileRef = React.useRef<HTMLInputElement>(null);
  const taRef = React.useRef<HTMLTextAreaElement>(null);
  const total = files.reduce((s, f) => s + f.size, 0);
  const label = peer.label || peer.peer_id.slice(0, 8);

  React.useEffect(() => {
    const ta = taRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    ta.style.height = `${Math.min(ta.scrollHeight, 192)}px`;
  }, [text]);

  async function addFiles(list: FileList | File[]) {
    setError(null);
    const incoming = Array.from(list);
    if (files.length + incoming.length > MAX_ATTACHMENTS_COUNT) {
      setError(`At most ${MAX_ATTACHMENTS_COUNT} attachments per message.`);
      return;
    }
    const size = incoming.reduce((s, f) => s + f.size, total);
    if (size > MAX_ATTACHMENTS_TOTAL_BYTES) {
      setError(`Attachments are limited to ${fmtBytes(MAX_ATTACHMENTS_TOTAL_BYTES)} per message (A2A protocol cap).`);
      return;
    }
    try {
      const read = await Promise.all(incoming.map(readFile));
      setFiles((prev) => {
        const taken = new Set(prev.map((p) => p.name));
        return [
          ...prev,
          ...read.map((r) => {
            const name = uniqueName(r.name, taken);
            taken.add(name);
            return { ...r, name };
          }),
        ];
      });
    } catch {
      setError("Could not read the file.");
    }
  }

  async function send() {
    if (busy || (!text.trim() && files.length === 0)) return;
    setBusy(true);
    setError(null);
    try {
      await sendA2AFeedMessage(
        {
          peer_id: peer.peer_id,
          text,
          attachments: files.map((f) => ({ name: f.name, mime: f.mime, content_b64: f.b64 })),
        },
        session?.csrf_token ?? "",
      );
      files.forEach((f) => f.preview && URL.revokeObjectURL(f.preview));
      setText("");
      setFiles([]);
      onSent();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Send failed.");
    } finally {
      setBusy(false);
      taRef.current?.focus();
    }
  }

  return (
    <div
      className={cn("border-t border-border bg-background/60 p-3 backdrop-blur", dragging && "bg-accent/5")}
      onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
      onDragLeave={() => setDragging(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDragging(false);
        if (e.dataTransfer.files.length) void addFiles(e.dataTransfer.files);
      }}
    >
      {files.length > 0 && (
        <div className="mb-2 flex flex-wrap gap-2">
          {files.map((f, i) => (
            <div key={f.name} className="group relative flex items-center gap-2 rounded-lg border border-border bg-card px-2 py-1.5 text-xs">
              {f.preview
                ? <img src={f.preview} alt="" className="h-8 w-8 rounded object-cover" />
                : <FileText className="h-4 w-4 text-accent" />}
              <div className="max-w-[10rem]">
                <div className="truncate">{f.name}</div>
                <div className="text-[10px] text-muted-foreground">{fmtBytes(f.size)}</div>
              </div>
              <button
                type="button"
                className="rounded-full p-0.5 text-muted-foreground hover:bg-muted hover:text-foreground"
                onClick={() => {
                  if (f.preview) URL.revokeObjectURL(f.preview);
                  setFiles((prev) => prev.filter((_, idx) => idx !== i));
                }}
                aria-label={`Remove ${f.name}`}
              >
                <X className="h-3 w-3" />
              </button>
            </div>
          ))}
          <span className="self-center text-[10px] tabular-nums text-muted-foreground">
            {fmtBytes(total)} / {fmtBytes(MAX_ATTACHMENTS_TOTAL_BYTES)}
          </span>
        </div>
      )}
      {error && <p className="mb-2 text-xs text-destructive">{error}</p>}
      <div className="flex items-end gap-2 rounded-2xl border border-border bg-card px-2 py-1.5 shadow-sm focus-within:border-accent/50 focus-within:ring-2 focus-within:ring-accent/15">
        <input
          ref={fileRef}
          type="file"
          multiple
          className="hidden"
          onChange={(e) => {
            if (e.target.files?.length) void addFiles(e.target.files);
            e.target.value = "";
          }}
        />
        <Button
          variant="ghost"
          size="icon"
          className="h-8 w-8 shrink-0 text-muted-foreground"
          onClick={() => fileRef.current?.click()}
          title="Attach images, audio, PDFs, … (max 1 MB total)"
        >
          <Paperclip className="h-4 w-4" />
        </Button>
        <textarea
          ref={taRef}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onPaste={(e) => {
            const pasted = Array.from(e.clipboardData.files);
            if (pasted.length) {
              e.preventDefault();
              void addFiles(pasted);
            }
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              void send();
            }
          }}
          rows={1}
          placeholder={`Message ${label}…`}
          className="max-h-48 min-h-[2rem] flex-1 resize-none bg-transparent py-1.5 text-sm leading-relaxed outline-none placeholder:text-muted-foreground"
          data-testid="a2a-feed-input"
        />
        <Button
          size="icon"
          className="h-8 w-8 shrink-0 rounded-full"
          onClick={() => void send()}
          disabled={busy || (!text.trim() && files.length === 0)}
          title="Send (Enter)"
          data-testid="a2a-feed-send"
        >
          {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
        </Button>
      </div>
      <p className="mt-1.5 px-2 text-[10px] text-muted-foreground">
        Signed A2A task envelope · Enter to send, Shift+Enter for a new line · drop or paste files
      </p>
    </div>
  );
}

// ── main ───────────────────────────────────────────────────────────

export function AgentLiveFeed() {
  const { session } = useAuth();
  const [messages, setMessages] = React.useState<A2AFeedMessage[]>([]);
  const [peersList, setPeersList] = React.useState<A2AFeedPeer[]>([]);
  const [retention, setRetention] = React.useState(30);
  const [loaded, setLoaded] = React.useState(false);
  const [loadError, setLoadError] = React.useState<string | null>(null);
  const [live, setLive] = React.useState(true);
  const [selected, setSelected] = React.useState<string | null>(null);
  const [lightbox, setLightbox] = React.useState<A2AFeedAttachment | null>(null);
  const [unseen, setUnseen] = React.useState(0);
  const scrollRef = React.useRef<HTMLDivElement>(null);
  const contentRef = React.useRef<HTMLDivElement>(null);
  const atBottom = React.useRef(true);
  const lastTs = React.useRef<number>(0);

  const poll = React.useCallback(async (signal?: AbortSignal) => {
    try {
      const res = await getA2AFeed(lastTs.current ? { since: lastTs.current } : { limit: 500 }, signal);
      setPeersList(res.peers);
      setRetention(res.retention_days);
      if (res.messages.length) {
        lastTs.current = res.messages[res.messages.length - 1].ts;
        setMessages((prev) => mergeMessages(prev, res.messages));
      }
      setLoadError(null);
      setLoaded(true);
    } catch (e) {
      if ((e as Error).name === "AbortError") return;
      setLoadError(e instanceof Error ? e.message : "Failed to load feed.");
      setLoaded(true);
    }
  }, []);

  React.useEffect(() => {
    const ctl = new AbortController();
    void poll(ctl.signal);
    if (!live) return () => ctl.abort();
    const t = setInterval(() => {
      if (!document.hidden) void poll(ctl.signal);
    }, POLL_MS);
    return () => { clearInterval(t); ctl.abort(); };
  }, [live, poll]);

  const peers = React.useMemo(() => new Map(peersList.map((p) => [p.peer_id, p])), [peersList]);

  // Peers that appear only in the feed (e.g. a since-removed connection).
  const allPeers = React.useMemo(() => {
    const out = [...peersList];
    for (const m of messages) {
      if (!peers.has(m.peer_id) && !out.some((p) => p.peer_id === m.peer_id)) {
        out.push({ peer_id: m.peer_id, label: m.peer_label, state: null, can_send: false, can_receive: false, enabled: false });
      }
    }
    return out;
  }, [peersList, messages, peers]);

  // Default selection: the only peer, else "all".
  React.useEffect(() => {
    if (selected === null && loaded) setSelected(allPeers.length === 1 ? allPeers[0].peer_id : ALL);
  }, [selected, loaded, allPeers]);

  const view = selected && selected !== ALL ? messages.filter((m) => m.peer_id === selected) : messages;
  const pending = React.useMemo(() => pendingTaskIds(view), [view]);
  const taskText = React.useMemo(() => {
    const map = new Map<string, string>();
    for (const m of messages) if (m.kind === "task") map.set(m.task_id, m.text || (m.attachments.length ? `${m.attachments.length} attachment(s)` : ""));
    return map;
  }, [messages]);
  const lastByPeer = React.useMemo(() => {
    const map = new Map<string, A2AFeedMessage>();
    for (const m of messages) map.set(m.peer_id, m);
    return map;
  }, [messages]);

  // Stick to the bottom when the reader is already there; otherwise count.
  const prevLen = React.useRef(0);
  React.useLayoutEffect(() => {
    const el = scrollRef.current;
    const grew = view.length > prevLen.current;
    const first = prevLen.current === 0;
    prevLen.current = view.length;
    if (!el || !grew) return;
    if (atBottom.current || first) {
      el.scrollTo({ top: el.scrollHeight, behavior: first ? "auto" : "smooth" });
      setUnseen(0);
    } else {
      setUnseen((n) => n + 1);
    }
  }, [view.length, pending.size]);

  React.useEffect(() => { prevLen.current = 0; atBottom.current = true; }, [selected]);

  // Media loads after the scroll-to-bottom above and grows the content; keep
  // a reader who is at the bottom pinned there.
  React.useEffect(() => {
    const el = scrollRef.current;
    const content = contentRef.current;
    if (!el || !content || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => {
      if (atBottom.current) el.scrollTop = el.scrollHeight;
    });
    ro.observe(content);
    return () => ro.disconnect();
  }, []);

  function jumpDown() {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
    setUnseen(0);
  }

  async function clearAll() {
    if (!window.confirm("Delete every stored A2A message and attachment on this instance? The audit trail is not affected.")) return;
    try {
      await clearA2AFeed(session?.csrf_token ?? "");
      setMessages([]);
      lastTs.current = 0;
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : "Clear failed.");
    }
  }

  const current = selected && selected !== ALL ? allPeers.find((p) => p.peer_id === selected) : undefined;

  // Day separators + pending bubbles interleaved in time order.
  const rows: React.ReactNode[] = [];
  let lastDay = "";
  for (const m of view) {
    const day = fmtDay(m.ts);
    if (day !== lastDay) {
      lastDay = day;
      rows.push(
        <div key={`day-${m.id}`} className="flex items-center gap-3 py-1 text-[10px] uppercase tracking-wider text-muted-foreground">
          <div className="h-px flex-1 bg-border" />
          {day}
          <div className="h-px flex-1 bg-border" />
        </div>,
      );
    }
    rows.push(
      <Bubble
        key={m.id}
        m={m}
        peers={peers}
        taskText={m.kind === "response" ? taskText.get(m.task_id) : undefined}
        showPeer={selected === ALL}
        onOpenImage={setLightbox}
      />,
    );
    if (m.kind === "task" && pending.has(m.task_id) && Date.now() / 1000 - m.ts < 600) {
      rows.push(
        <PendingBubble
          key={`pending-${m.id}`}
          name={peerName(peers, m.peer_id)}
          mine={m.direction === "in"}
          since={m.ts}
        />,
      );
    }
  }

  return (
    <div className="grid h-[calc(100vh-15rem)] min-h-[34rem] grid-cols-1 overflow-hidden rounded-2xl border border-border bg-card/30 md:grid-cols-[16rem_1fr]">
      {/* ── agent rail ── */}
      <aside className="hidden flex-col border-r border-border bg-muted/20 md:flex">
        <div className="flex items-center justify-between px-4 pb-2 pt-4">
          <span className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">Agents</span>
          <span className="text-[10px] text-muted-foreground">{allPeers.length}</span>
        </div>
        <div className="flex-1 space-y-0.5 overflow-y-auto px-2 pb-3">
          <button
            type="button"
            onClick={() => setSelected(ALL)}
            className={cn(
              "flex w-full items-center gap-3 rounded-xl px-2 py-2 text-left transition-colors",
              selected === ALL ? "bg-accent/15" : "hover:bg-muted/60",
            )}
          >
            <div className="flex h-9 w-9 items-center justify-center rounded-full bg-muted text-muted-foreground">
              <Globe2 className="h-4 w-4" />
            </div>
            <div className="min-w-0">
              <div className="text-sm font-medium">All conversations</div>
              <div className="text-[11px] text-muted-foreground">{messages.length} messages</div>
            </div>
          </button>
          {allPeers.map((p) => {
            const last = lastByPeer.get(p.peer_id);
            const label = p.label || p.peer_id.slice(0, 8);
            const preview = last ? (messageBody(last).text || (last.attachments.length ? `📎 ${last.attachments[0].name}` : last.status)) : "No messages yet";
            return (
              <button
                key={p.peer_id}
                type="button"
                onClick={() => setSelected(p.peer_id)}
                className={cn(
                  "flex w-full items-center gap-3 rounded-xl px-2 py-2 text-left transition-colors",
                  selected === p.peer_id ? "bg-accent/15" : "hover:bg-muted/60",
                )}
                data-testid="a2a-feed-peer"
              >
                <PeerAvatar label={label} state={p.state} />
                <div className="min-w-0 flex-1">
                  <div className="flex items-baseline justify-between gap-2">
                    <span className="truncate text-sm font-medium">{label}</span>
                    {last && <span className="shrink-0 text-[10px] tabular-nums text-muted-foreground">{fmtTime(last.ts)}</span>}
                  </div>
                  <div className="truncate text-[11px] text-muted-foreground">
                    {last?.direction === "out" && "You: "}{preview}
                  </div>
                </div>
              </button>
            );
          })}
        </div>
        <div className="border-t border-border px-4 py-2.5 text-[10px] leading-snug text-muted-foreground">
          Stored on this instance for {retention} days. The audit trail keeps metadata only.
        </div>
      </aside>

      {/* ── conversation ── */}
      <section className="relative flex min-h-0 flex-col">
        <header className="flex items-center gap-3 border-b border-border px-4 py-3">
          {current ? (
            <PeerAvatar label={current.label || current.peer_id.slice(0, 8)} state={current.state} />
          ) : (
            <div className="flex h-9 w-9 items-center justify-center rounded-full bg-muted text-muted-foreground">
              <Globe2 className="h-4 w-4" />
            </div>
          )}
          <div className="min-w-0 flex-1">
            <div className="truncate font-serif text-lg font-light leading-tight">
              {current ? current.label || current.peer_id.slice(0, 8) : "All conversations"}
            </div>
            <div className="truncate text-[11px] text-muted-foreground">
              {current
                ? `${current.state ?? "unknown"} · ${current.can_send ? "you can message this agent" : "receive only"}${current.can_receive ? (current.spawn_worker ? " · their tasks run a worker here" : " · their tasks get no worker here") : ""}`
                : "Every A2A exchange of this instance, newest at the bottom"}
            </div>
          </div>
          {/* Mobile peer picker */}
          <select
            className="rounded-md border border-border bg-background px-2 py-1 text-xs md:hidden"
            value={selected ?? ALL}
            onChange={(e) => setSelected(e.target.value)}
          >
            <option value={ALL}>All</option>
            {allPeers.map((p) => <option key={p.peer_id} value={p.peer_id}>{p.label || p.peer_id.slice(0, 8)}</option>)}
          </select>
          <button
            type="button"
            onClick={() => setLive((v) => !v)}
            className={cn(
              "flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] transition-colors",
              live ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400" : "border-border text-muted-foreground",
            )}
            title={live ? "Pause live updates" : "Resume live updates"}
            data-testid="a2a-feed-live"
          >
            {live ? (
              <>
                <span className="relative flex h-2 w-2">
                  <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-500 opacity-60" />
                  <span className="relative inline-flex h-2 w-2 rounded-full bg-emerald-500" />
                </span>
                Live
                <Pause className="h-3 w-3 opacity-60" />
              </>
            ) : (
              <>
                <Play className="h-3 w-3" /> Paused
              </>
            )}
          </button>
          <Button variant="ghost" size="icon" className="h-8 w-8 text-muted-foreground" onClick={() => void clearAll()} title="Delete stored messages">
            <Trash2 className="h-4 w-4" />
          </Button>
        </header>

        <div
          ref={scrollRef}
          onScroll={(e) => {
            const el = e.currentTarget;
            atBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
            if (atBottom.current) setUnseen(0);
          }}
          className="flex-1 overflow-y-auto bg-[radial-gradient(ellipse_at_top,hsl(var(--accent)/0.06),transparent_60%)] px-4 py-5 md:px-6"
          data-testid="a2a-feed-scroll"
        >
          <div ref={contentRef} className="flex min-h-full flex-col space-y-4">
          {!loaded && (
            <div className="flex flex-1 items-center justify-center text-muted-foreground">
              <Loader2 className="h-5 w-5 animate-spin" />
            </div>
          )}
          {loadError && (
            <p className="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive">{loadError}</p>
          )}
          {loaded && view.length === 0 && !loadError && (
            <div className="flex flex-1 flex-col items-center justify-center gap-3 text-center text-muted-foreground">
              <div className="flex h-14 w-14 items-center justify-center rounded-full bg-accent/10 text-accent">
                {current ? <Bot className="h-6 w-6" /> : <Inbox className="h-6 w-6" />}
              </div>
              <div>
                <p className="text-sm font-medium text-foreground">No messages yet</p>
                <p className="mt-1 max-w-sm text-xs">
                  {current?.can_send
                    ? `Send ${current.label || "this agent"} a task below — text, images, audio or documents.`
                    : "A2A tasks this instance sends or receives will appear here live, with their attachments."}
                </p>
              </div>
            </div>
          )}
          {rows}
          </div>
        </div>

        {unseen > 0 && (
          <button
            type="button"
            onClick={jumpDown}
            className="absolute bottom-28 left-1/2 flex -translate-x-1/2 items-center gap-1 rounded-full border border-border bg-card px-3 py-1 text-xs shadow-md hover:bg-muted"
          >
            <ArrowDown className="h-3 w-3" /> {unseen} new
          </button>
        )}

        {current?.can_send ? (
          <Composer peer={current} onSent={() => setTimeout(() => void poll(), 300)} />
        ) : (
          <div className="border-t border-border px-4 py-3 text-center text-xs text-muted-foreground">
            {selected === ALL ? "Pick an agent on the left to message it." : "This peer has no enabled endpoint — it can message you, but you cannot reply from here."}
          </div>
        )}
      </section>

      {lightbox && <Lightbox att={lightbox} onClose={() => setLightbox(null)} />}
    </div>
  );
}
