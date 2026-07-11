import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Check,
  ChevronDown,
  Cloud,
  Cpu,
  Download,
  FileText,
  FolderOpen,
  Hammer,
  Loader2,
  Mic,
  MicOff,
  Paperclip,
  Pencil,
  Plus,
  RotateCcw,
  Send,
  ShieldCheck,
  Sparkles,
  Square,
  Trash2,
  User,
  Volume2,
  VolumeX,
  X,
} from "lucide-react";
import { Markdown } from "@/components/markdown";
import { WdatAuditPanel } from "@/components/WdatAuditPanel";
import { DualTrackAuditPanel } from "@/components/DualTrackAuditPanel";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import {
  ApiError,
  createChatSession,
  deleteChatSession,
  getChatTurns,
  getPerChatEngine,
  setPerChatEngine,
  clearPerChatEngine,
  getProfile,
  listChatSessions,
  transcribeAudio,
  ttsBlob,
  updateChatSessionTitle,
  openSessionWorkdir,
  uploadAttachments,
  type AttachmentMeta,
  type ChatSessionListResponse,
  type ChatSessionSummary,
  type ChatTurn,
} from "@/lib/api";
import { useNavigate, useParams } from "react-router-dom";
import { useAuth } from "@/lib/auth";
import { cn, formatDate } from "@/lib/utils";
import {
  PREF_KEYS,
  setString as setPref,
  usePersistedBool,
} from "@/lib/preferences";
import { useTasksWithLiveUpdates } from "@/hooks/use-tasks-with-live-updates";
import { useChatTaskStatus } from "@/hooks/use-chat-task-status";
import { TaskPanel } from "@/components/task-panel";
import { QuotaWarningBanner } from "@/components/quota-warning-banner";
import { exportTaskAsJSON, Task, deleteTask } from "@/lib/task-db";
import {
  cancelTurn as registryCancel,
  ensureConnected,
  loadHistory,
  sendMessage as registrySend,
  subscribeEvents,
  consumePendingTitle,
  closeSession,
  useChatSession,
  type StreamEvent,
} from "@/lib/chat-registry";
import {
  markStreaming,
  markDone,
  clearStreamState,
  useStreamStates,
} from "@/lib/streaming-state";

type VoiceState = "idle" | "loading" | "playing" | "blocked";

/**
 * Detect the language of a text for TTS playback and return a BCP-47 code.
 *
 * Priority:
 *   1. Script-level detection (CJK, Arabic, Hebrew, Cyrillic, Devanagari, …)
 *      — single character is sufficient, no ambiguity.
 *   2. German-specific characters (ä ö ü ß).
 *   3. Word-level scoring for DE vs EN on a 300-char sample.
 *   4. Falls back to `fallback` when the signal is ambiguous (neutral text).
 *
 * Rationale: Claude responds in the user's language, so we detect the
 * response language rather than relying on a static profile setting.
 */
function detectTtsLang(text: string, fallback: string): string {
  if (!text) return fallback;

  // ── Script-level signals (definitive, one character is enough) ──
  // Chinese (Simplified + Traditional), Japanese kanji, Korean Hanja
  if (/[一-鿿㐀-䶿豈-﫿]/.test(text)) {
    // Distinguish Simplified vs Traditional by checking Traditional-only chars
    // (rough heuristic — Traditional uses more strokes in common chars).
    // For TTS purposes "zh" works for both; the model handles the script.
    // If hiragana/katakana is present it's Japanese, not Chinese.
    if (/[぀-ヿ]/.test(text)) return "ja";   // hiragana or katakana
    if (/[가-힯]/.test(text)) return "ko";   // Korean Hangul mixed in
    return "zh";
  }
  if (/[가-힯]/.test(text)) return "ko";     // Korean Hangul only
  if (/[぀-ゟ]/.test(text)) return "ja";     // hiragana
  if (/[゠-ヿ]/.test(text)) return "ja";     // katakana
  if (/[؀-ۿ]/.test(text)) return "ar";     // Arabic
  if (/[֐-׿]/.test(text)) return "he";     // Hebrew
  if (/[Ѐ-ӿ]/.test(text)) return "ru";     // Cyrillic (Russian as default)
  if (/[ऀ-ॿ]/.test(text)) return "hi";     // Devanagari (Hindi as default)

  // ── German-specific Latin characters ────────────────────────────
  if (/[äöüßÄÖÜ]/.test(text)) return "de";

  // ── Word-level scoring for DE vs EN ─────────────────────────────
  const sample = text.slice(0, 300).toLowerCase();
  const deScore = (sample.match(
    /\b(der|die|das|und|ist|ich|sie|wir|nicht|mit|auf|für|von|ein|eine|dem|den|auch|aber|oder|wenn|dann|noch|schon|so|wie|was|wo|warum|dass|damit|durch|nach|vor|beim|vom|zum|zur|haben|sein|werden|können|müssen|sollen|dürfen|wollen|machen|sagen|gehen|kommen|sehen|wissen)\b/g
  ) || []).length;
  const enScore = (sample.match(
    /\b(the|is|are|was|were|have|has|had|will|would|could|should|can|may|might|must|of|in|to|for|on|at|by|with|from|about|into|through|this|that|these|those|it|its|we|our|they|their|you|your|do|does|did|be|been|being|make|go|come|see|know|think|say|get|use)\b/g
  ) || []).length;
  if (deScore > enScore) return "de";
  if (enScore > deScore) return "en";
  return fallback;
}

// MessagePart and ChatMessage are re-exported from chat-registry to share types.
type MessagePart = import("@/lib/chat-registry").MessagePart;
type ChatMessage = import("@/lib/chat-registry").ChatMessage;

export function ChatPage() {
  const qc = useQueryClient();
  const { session } = useAuth();

  const list = useQuery({
    queryKey: ["chat", "sessions"],
    queryFn: ({ signal }) => listChatSessions(signal),
    refetchInterval: 30_000,
  });

  const { sid: activeSid } = useParams<{ sid?: string }>();
  const navigate = useNavigate();

  // Auto-redirect to last/first session when landing on /app/chat without a sid.
  React.useEffect(() => {
    if (activeSid || !list.data || list.data.sessions.length === 0) return;
    let remembered: string | null = null;
    try {
      remembered = window.localStorage.getItem(PREF_KEYS.lastChatSid);
    } catch {
      /* localStorage may be blocked in private mode */
    }
    const match = remembered ? list.data.sessions.find((s) => s.sid === remembered) : undefined;
    const pick = match ?? list.data.sessions[0];
    navigate(`/app/chat/${pick.sid}`, { replace: true });
  }, [activeSid, list.data, navigate]);

  // Persist so the auto-redirect above can restore on next visit.
  React.useEffect(() => {
    if (activeSid) setPref(PREF_KEYS.lastChatSid, activeSid);
  }, [activeSid]);

  const createMut = useMutation({
    mutationFn: async () => createChatSession(session!.csrf_token, ""),
    onSuccess: async ({ session: s }) => {
      navigate(`/app/chat/${s.sid}`);
      await qc.invalidateQueries({ queryKey: ["chat"] });
    },
  });
  const deleteMut = useMutation({
    mutationFn: async (sid: string) => deleteChatSession(sid, session!.csrf_token),
    onSuccess: async (_d, sid) => {
      // Close WS and free registry entry to avoid memory/connection leaks.
      closeSession(sid);
      if (sid === activeSid) navigate("/app/chat", { replace: true });
      await qc.invalidateQueries({ queryKey: ["chat"] });
    },
  });

  // Inline-rename mutation. Optimistic update against the sessions cache so
  // the sidebar + chat header redraw immediately; on error we roll back the
  // cache slice to whatever the server last said. Mirrors the ChatGPT /
  // Claude-Desktop UX: type-and-Enter feels instant, no spinner flicker.
  const renameMut = useMutation({
    mutationFn: async ({ sid, title }: { sid: string; title: string }) =>
      updateChatSessionTitle(sid, title, session!.csrf_token),
    onMutate: async ({ sid, title }) => {
      await qc.cancelQueries({ queryKey: ["chat", "sessions"] });
      const prev = qc.getQueryData<ChatSessionListResponse>(["chat", "sessions"]);
      if (prev) {
        qc.setQueryData<ChatSessionListResponse>(["chat", "sessions"], {
          ...prev,
          sessions: prev.sessions.map((s) =>
            s.sid === sid ? { ...s, title: title.trim().slice(0, 120) } : s,
          ),
        });
      }
      return { prev };
    },
    onError: (_err, _vars, ctx) => {
      if (ctx?.prev) qc.setQueryData(["chat", "sessions"], ctx.prev);
    },
    onSettled: () => qc.invalidateQueries({ queryKey: ["chat", "sessions"] }),
  });

  return (
    <div className="-mx-6 -my-8 grid h-[calc(100vh-3.5rem)] grid-cols-[1fr_18rem] overflow-hidden">
      {/* Active chat pane — left + center, full-bleed */}
      <main className="flex min-h-0 flex-col overflow-hidden bg-background">
        {activeSid ? (
          <ChatPane
            key={activeSid}
            sid={activeSid}
            session={list.data?.sessions.find((s) => s.sid === activeSid)}
          />
        ) : (
          <EmptyState onNew={() => createMut.mutate()} pending={createMut.isPending} />
        )}
      </main>

      {/* Sessions sidebar — right side; chat-pane content stays centered. */}
      <aside className="flex min-h-0 flex-col border-l border-border bg-card/40">
        <div className="flex items-center justify-between gap-2 border-b border-border px-4 py-3">
          <span className="font-serif text-lg">Chats</span>
          <Button
            variant="accent"
            size="sm"
            disabled={createMut.isPending}
            onClick={() => createMut.mutate()}
          >
            {createMut.isPending ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Plus className="h-3.5 w-3.5" />
            )}
            New
          </Button>
        </div>
        <div className="flex-1 overflow-y-auto px-2 py-2">
          {list.isLoading && <Skeleton className="h-10 w-full" />}
          {list.data && list.data.sessions.length === 0 && (
            <p className="px-3 py-6 text-center text-xs text-muted-foreground">
              No chats yet. Click "New" to start.
            </p>
          )}
          {list.data?.sessions.map((s) => (
            <SessionListItem
              key={s.sid}
              s={s}
              active={s.sid === activeSid}
              onPick={() => navigate(`/app/chat/${s.sid}`)}
              onDelete={() => deleteMut.mutate(s.sid)}
              deleting={deleteMut.isPending && deleteMut.variables === s.sid}
              onRename={(title) => renameMut.mutate({ sid: s.sid, title })}
            />
          ))}
        </div>
        <div className="border-t border-border px-4 py-2 text-[10px] leading-relaxed text-muted-foreground">
          Chat sessions are scoped to this browser. Switch sessions using the list above.
        </div>
      </aside>
    </div>
  );
}

function SessionListItem({
  s,
  active,
  onPick,
  onDelete,
  deleting,
  onRename,
}: {
  s: ChatSessionSummary;
  active: boolean;
  onPick: () => void;
  onDelete: () => void;
  deleting: boolean;
  onRename: (title: string) => void;
}) {
  const [editing, setEditing] = React.useState(false);
  // Track the input value locally while editing so the user can cancel
  // without committing — onRename only fires on Enter or the check button.
  const [draft, setDraft] = React.useState(s.title);
  const inputRef = React.useRef<HTMLInputElement>(null);

  // Check for running tasks in this chat
  const taskStatus = useChatTaskStatus(s.chat_key || "");

  // Subscribe to live streaming state — updates whenever any ChatPane
  // starts or stops streaming, even while this item is not the active chat.
  const streamStates = useStreamStates();
  const streamState = streamStates.get(s.sid); // "streaming" | "interrupted" | undefined

  React.useEffect(() => {
    if (editing) {
      setDraft(s.title);
      // Defer focus to the next paint so the input has mounted.
      requestAnimationFrame(() => {
        inputRef.current?.focus();
        inputRef.current?.select();
      });
    }
  }, [editing, s.title]);

  const commit = () => {
    const next = draft.trim();
    // Only call the API when the title actually changed — saves a round-trip
    // and avoids an unnecessary optimistic-update flash on the sidebar.
    if (next !== (s.title ?? "").trim()) {
      onRename(next);
    }
    setEditing(false);
  };

  const cancel = () => {
    setDraft(s.title);
    setEditing(false);
  };

  return (
    <div
      className={cn(
        "group flex items-center justify-between gap-1 rounded-md px-3 py-2 text-sm transition-colors",
        active ? "bg-accent/15 text-foreground" : "hover:bg-muted/60",
      )}
    >
      {editing ? (
        <div className="flex min-w-0 flex-1 items-center gap-1">
          <input
            ref={inputRef}
            type="text"
            maxLength={120}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                commit();
              } else if (e.key === "Escape") {
                e.preventDefault();
                cancel();
              }
            }}
            onBlur={commit}
            placeholder="Untitled chat"
            className="min-w-0 flex-1 rounded-sm border border-accent/40 bg-background px-1.5 py-0.5 text-sm focus:outline-none focus:ring-1 focus:ring-accent"
            aria-label="Rename chat"
          />
          <button
            // Mousedown (not click) so the input's onBlur cannot fire and
            // re-commit before the cancel/commit handler runs.
            onMouseDown={(e) => {
              e.preventDefault();
              commit();
            }}
            className="text-muted-foreground hover:text-accent"
            aria-label="Save title"
          >
            <Check className="h-3.5 w-3.5" />
          </button>
          <button
            onMouseDown={(e) => {
              e.preventDefault();
              cancel();
            }}
            className="text-muted-foreground hover:text-destructive"
            aria-label="Cancel rename"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      ) : (
        <>
          <button
            onClick={onPick}
            onDoubleClick={() => setEditing(true)}
            className="min-w-0 flex-1 text-left focus:outline-none"
            title="Click to open · double-click to rename"
          >
            <div className="flex items-center gap-2 truncate">
              <div className="truncate font-medium">
                {s.title || "New Chat"}
              </div>
              {streamState === "streaming" && (
                <span
                  className="h-2 w-2 rounded-full bg-amber-400 animate-pulse shrink-0"
                  title="Claude is responding"
                  aria-label="Chat is streaming a response"
                />
              )}
              {!streamState && taskStatus.status === "running" && (
                <span
                  className="h-2 w-2 rounded-full bg-emerald-500 animate-pulse shrink-0"
                  title="Task running"
                  aria-label="Task running in this chat"
                />
              )}
              {!streamState && taskStatus.status === "pending" && (
                <span
                  className="h-2 w-2 rounded-full bg-blue-500 animate-pulse shrink-0"
                  title="Task pending"
                  aria-label="Task pending in this chat"
                />
              )}
            </div>
            <div className="flex items-center gap-2 text-[10px] text-muted-foreground">
              <span>{s.turn_count} turn{s.turn_count === 1 ? "" : "s"}</span>
              <span>·</span>
              <span>{formatDate(s.last_active_at)}</span>
            </div>
          </button>
          <button
            className="opacity-0 transition-opacity hover:text-accent group-hover:opacity-100"
            onClick={() => setEditing(true)}
            aria-label="Rename session"
          >
            <Pencil className="h-3.5 w-3.5" />
          </button>
          <button
            className="opacity-0 transition-opacity hover:text-destructive group-hover:opacity-100"
            onClick={onDelete}
            disabled={deleting}
            aria-label="Delete session"
          >
            {deleting ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Trash2 className="h-3.5 w-3.5" />
            )}
          </button>
        </>
      )}
    </div>
  );
}

function EmptyState({ onNew, pending }: { onNew: () => void; pending: boolean }) {
  return (
    <div className="flex flex-1 items-center justify-center">
      <Card className="max-w-md border-dashed">
        <CardContent className="space-y-3 py-10 text-center">
          <h2 className="font-serif text-xl">Pick a chat — or start a new one.</h2>
          <p className="text-sm text-muted-foreground">
            Each chat is its own working directory. Sessions persist across browser refreshes;
            <span className="font-mono"> --continue</span> resumes claude across turns.
          </p>
          <Button variant="accent" onClick={onNew} disabled={pending}>
            {pending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plus className="h-4 w-4" />}
            New chat
          </Button>
        </CardContent>
      </Card>
    </div>
  );
}

// ── Per-chat engine selector ─────────────────────────────────────────────

const ENGINE_META: Record<string, { label: string; icon: React.ComponentType<{className?: string}>; local: boolean }> = {
  claude_code:  { label: "Claude Code", icon: Cloud, local: false },
  codex_cli:    { label: "Codex",       icon: Cloud, local: false },
  opencode:     { label: "OpenCode",    icon: Cloud, local: false },
  hermes:       { label: "Hermes",      icon: Cpu,   local: true  },
  copilot:      { label: "Copilot",     icon: Cloud, local: false },
};

const HERMES_MODEL_OPTIONS = [
  { value: "",                label: "Hermes — default model" },
  { value: "hermes-fast",     label: "hermes-fast (7B)" },
  { value: "hermes-balanced", label: "hermes-balanced (13B)" },
  { value: "hermes-capable",  label: "hermes-capable (Hermes-3 8B)" },
  { value: "hermes-large",    label: "hermes-large (70B)" },
];

// ── Slash commands ────────────────────────────────────────────────────────

const SLASH_COMMANDS = [
  // ── Session management ──
  { cmd: "/stop",             args: "",                desc: "Abort the running task (aliases: /cancel, /halt)" },
  { cmd: "/new",              args: "",                desc: "Start a new session" },
  { cmd: "/clear",            args: "",                desc: "Clear conversation history" },
  { cmd: "/reset",            args: "",                desc: "Reset session and history" },
  // ── CCC — entity creation (ADR-0168 M6) ──
  { cmd: "/create workflow",  args: '[name="…"] [schedule="*/5 * * * *"]', desc: "CCC: create a workflow" },
  { cmd: "/create task",      args: '[name="…"]',      desc: "CCC: create an ATS task" },
  { cmd: "/create tool",      args: '[name="…"]',      desc: "CCC: forge a new tool" },
  { cmd: "/create skill",     args: '[name="…"]',      desc: "CCC: create a skill" },
  { cmd: "/erase",            args: "user uid=<id>",   desc: "CCC: GDPR Art. 17 erasure request" },
  { cmd: "/audit",            args: "[last <n>]",      desc: "Show recent audit events" },
  // ── Config ──
  { cmd: "/engine",           args: "<name>",          desc: "Switch engine: claude_code · hermes · codex · opencode · copilot" },
  { cmd: "/persona",          args: "<name>",          desc: "Pin a persona for this chat" },
  { cmd: "/forget",           args: "",                desc: "Delete your memory (GDPR Art. 17)" },
  { cmd: "/quota",            args: "",                desc: "Check your message quota" },
  { cmd: "/share",            args: "",                desc: "Grant single-session consent (this console session)" },
  { cmd: "/go",               args: "[steering]",      desc: "Execute a pending proposal" },
  { cmd: "/propose",          args: "<text>",          desc: "Queue a proposal" },
  { cmd: "/btw",              args: "<text>",          desc: "Inject a note into an active stream" },
  { cmd: "/skills",           args: "",                desc: "List active skills" },
  { cmd: "/memory",           args: "",                desc: "Show memory summary" },
  { cmd: "/whoami",           args: "",                desc: "Show your identity and role" },
  { cmd: "/role",             args: "",                desc: "Show your current role" },
  { cmd: "/dialectic-on",     args: "",                desc: "Enable dialectic reasoning" },
  { cmd: "/dialectic-off",    args: "",                desc: "Disable dialectic reasoning" },
];

function CommandPalette({
  matches,
  selected,
  onSelect,
}: {
  matches: typeof SLASH_COMMANDS;
  selected: number;
  onSelect: (cmd: string, hasArgs: boolean) => void;
}) {
  if (matches.length === 0) return null;
  return (
    <div className="absolute bottom-full left-0 right-0 z-50 mb-1 overflow-hidden rounded-lg border border-border bg-popover shadow-xl">
      <div className="max-h-72 overflow-y-auto py-1">
        {matches.map((item, i) => (
          <button
            key={item.cmd}
            onMouseDown={(e) => {
              e.preventDefault(); // prevent textarea blur before we insert
              onSelect(item.cmd, item.args !== "");
            }}
            className={cn(
              "flex w-full items-baseline gap-3 px-3 py-2 text-left transition-colors",
              i === selected
                ? "bg-accent/15 text-foreground"
                : "text-muted-foreground hover:bg-muted hover:text-foreground",
            )}
          >
            <span className="w-36 shrink-0 font-mono text-[13px] font-medium text-foreground">
              {item.cmd}
            </span>
            {item.args && (
              <span className="shrink-0 font-mono text-[11px] text-muted-foreground">
                {item.args}
              </span>
            )}
            <span className="min-w-0 truncate text-xs text-muted-foreground">
              {item.desc}
            </span>
          </button>
        ))}
      </div>
      <div className="border-t border-border/60 px-3 py-1.5 text-[10px] text-muted-foreground">
        <kbd className="rounded bg-muted px-1 font-mono">↑↓</kbd> navigate ·{" "}
        <kbd className="rounded bg-muted px-1 font-mono">Tab</kbd> complete ·{" "}
        <kbd className="rounded bg-muted px-1 font-mono">Esc</kbd> close ·{" "}
        <kbd className="rounded bg-muted px-1 font-mono">Enter</kbd> send as-is
      </div>
    </div>
  );
}

const ChatStatusBar = React.memo(function ChatStatusBar({
  effectiveEngine,
  personaName,
  voiceOut,
  onVoiceToggle,
}: {
  effectiveEngine: string;
  personaName: string | null | undefined;
  voiceOut: boolean;
  onVoiceToggle: () => void;
}) {
  const navigate = useNavigate();
  const meta = ENGINE_META[effectiveEngine] ?? ENGINE_META["claude_code"];
  const Icon = meta.icon;
  return (
    <div className="border-t border-border/30 bg-background/60 px-8 py-1.5">
      <div className="mx-auto flex w-full max-w-4xl items-center gap-2">
        <button
          onClick={() => navigate("/app/engines")}
          title="AI Engine — click to change"
          className={cn(
            "flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] transition-colors",
            meta.local
              ? "border-emerald-500/30 bg-emerald-500/5 text-emerald-700 hover:bg-emerald-500/10 dark:text-emerald-400"
              : "border-border/50 bg-muted/40 text-muted-foreground hover:bg-muted hover:text-foreground",
          )}
        >
          <Icon className="h-3 w-3" />
          {meta.label}
        </button>
        <button
          onClick={() => navigate("/app/personas")}
          title="Active persona — click to manage"
          className="flex items-center gap-1 rounded-full border border-border/50 bg-muted/40 px-2 py-0.5 text-[11px] text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
        >
          <Sparkles className="h-3 w-3" />
          {personaName ?? "auto"}
        </button>
        <button
          onClick={onVoiceToggle}
          title={voiceOut ? "Voice output on — click to disable" : "Voice output off — click to enable"}
          className={cn(
            "flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] transition-colors",
            voiceOut
              ? "border-accent/30 bg-accent/5 text-accent hover:bg-accent/10"
              : "border-border/50 bg-muted/40 text-muted-foreground hover:bg-muted hover:text-foreground",
          )}
        >
          {voiceOut ? <Volume2 className="h-3 w-3" /> : <VolumeX className="h-3 w-3" />}
          Voice {voiceOut ? "on" : "off"}
        </button>
        <span className="ml-auto text-[10px] text-muted-foreground/50">
          type <kbd className="rounded bg-muted/60 px-1 font-mono text-[9px]">/</kbd> for commands
        </span>
      </div>
    </div>
  );
});

function ChatEngineSelector({ chatKey, csrf }: { chatKey: string; csrf: string }) {
  const qc = useQueryClient();
  const [open, setOpen] = React.useState(false);
  const ref = React.useRef<HTMLDivElement>(null);

  const q = useQuery({
    queryKey: ["chat-engine-pref", chatKey],
    queryFn: ({ signal }) => getPerChatEngine(chatKey, signal),
    staleTime: 30_000,
  });

  const setMut = useMutation({
    mutationFn: ({ engine, model }: { engine: string; model: string | null }) =>
      setPerChatEngine(chatKey, engine, model, csrf),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["chat-engine-pref", chatKey] });
      setOpen(false);
    },
  });

  const clearMut = useMutation({
    mutationFn: () => clearPerChatEngine(chatKey, csrf),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["chat-engine-pref", chatKey] });
      setOpen(false);
    },
  });

  // Close on outside click
  React.useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);

  const pref = q.data;
  const effective = pref?.effective_engine ?? "claude_code";
  const meta = ENGINE_META[effective] ?? ENGINE_META["claude_code"];
  const Icon = meta.icon;
  const isOverride = pref?.source === "per_chat";

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen((v) => !v)}
        className={cn(
          "flex items-center gap-1.5 rounded-md border px-2 py-1 text-xs transition-colors hover:bg-muted",
          meta.local
            ? "border-emerald-500/30 bg-emerald-500/5 text-emerald-700 dark:text-emerald-400"
            : "border-border bg-muted/30 text-muted-foreground",
        )}
      >
        <Icon className="h-3 w-3" />
        <span className="font-medium">{meta.label}</span>
        {isOverride && (
          <span className="rounded bg-accent/15 px-1 text-[9px] font-semibold uppercase tracking-wide text-accent">
            override
          </span>
        )}
        <ChevronDown className="h-3 w-3 opacity-60" />
      </button>

      {open && (
        <div className="absolute right-0 top-full z-50 mt-1.5 w-64 rounded-lg border border-border bg-popover shadow-lg">
          <div className="border-b border-border px-3 py-2">
            <p className="text-xs font-medium text-foreground">Engine for this chat</p>
            <p className="text-[10px] text-muted-foreground mt-0.5">
              {isOverride ? "Per-chat override active" : `Using ${pref?.source === "tenant_default" ? "tenant default" : "system default"}`}
            </p>
          </div>
          <div className="p-1.5 space-y-0.5">
            {Object.entries(ENGINE_META).map(([id, m]) => {
              const Ico = m.icon;
              const isActive = effective === id && isOverride;
              return (
                <button
                  key={id}
                  onClick={() => setMut.mutate({ engine: id, model: null })}
                  className={cn(
                    "flex w-full items-center gap-2 rounded px-2.5 py-2 text-xs transition-colors hover:bg-muted text-left",
                    isActive && "bg-accent/10 font-medium",
                  )}
                >
                  <Ico className={cn("h-3.5 w-3.5 shrink-0", m.local ? "text-emerald-500" : "text-muted-foreground")} />
                  <span className="flex-1">{m.label}</span>
                  {m.local && (
                    <span className="text-[9px] text-emerald-600 font-semibold uppercase tracking-wide">local</span>
                  )}
                  {isActive && <Check className="h-3 w-3 text-accent" />}
                </button>
              );
            })}
          </div>
          {/* Hermes model picker — only when hermes is the active override */}
          {isOverride && effective === "hermes" && (
            <div className="border-t border-border px-3 py-2 space-y-1">
              <p className="text-[10px] text-muted-foreground">Model</p>
              <select
                defaultValue={pref?.per_chat_model ?? ""}
                onChange={(e) => setMut.mutate({ engine: "hermes", model: e.target.value || null })}
                className="w-full rounded border border-input bg-background px-2 py-1 text-xs"
              >
                {HERMES_MODEL_OPTIONS.map(o => (
                  <option key={o.value} value={o.value}>{o.label}</option>
                ))}
              </select>
            </div>
          )}
          {/* Clear override */}
          {isOverride && (
            <div className="border-t border-border px-3 py-2">
              <button
                onClick={() => clearMut.mutate()}
                className="text-[11px] text-muted-foreground hover:text-foreground underline underline-offset-2"
              >
                Remove override — use tenant default
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}


function ChatPane({
  sid,
  session: meta,
}: {
  sid: string;
  session?: ChatSessionSummary;
}) {
  const { session: auth, refresh: refreshAuth } = useAuth();
  const csrf = auth!.csrf_token;
  const qc = useQueryClient();
  // Messages and streaming state come from the persistent registry instead of
  // local state. The registry keeps WS connections alive across chat switches
  // so streaming continues in the background.
  const chatSession = useChatSession(sid);
  const messages = chatSession.messages;
  const streaming = chatSession.streaming;

  const [input, setInput] = React.useState("");
  const [paletteOpen, setPaletteOpen] = React.useState(false);
  const [paletteSel, setPaletteSel] = React.useState(0);
  const [pendingAttachments, setPendingAttachments] = React.useState<AttachmentMeta[]>([]);
  const [uploadError, setUploadError] = React.useState<string | null>(null);
  const [uploading, setUploading] = React.useState(false);
  const fileInputRef = React.useRef<HTMLInputElement>(null);
  const [persistedTasks, setPersistedTasks] = React.useState<Task[]>([]);
  const [cccActions, setCccActions] = React.useState<StreamEvent[]>([]);
  // Reset CCC action cards when the session changes.
  React.useEffect(() => { setCccActions([]); }, [sid]);
  const [auditOpen, setAuditOpen] = React.useState(false);
  const [auditTab, setAuditTab] = React.useState<"single" | "dual-track">("single");
  const [workdirInfo, setWorkdirInfo] = React.useState<string | null>(null);
  // Voice-out is on by default — the operator can flip it off via the
  // toggle in the chat header, the choice is then session-local.
  // Voice-out default ON; the choice persists across page reloads.
  const [voiceOut, setVoiceOut] = usePersistedBool(PREF_KEYS.voiceOut, true);
  const [voiceState, setVoiceState] = React.useState<VoiceState>("idle");
  const [recording, setRecording] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  // Last completed TTS text + detected language — used for the replay button.
  const [lastTts, setLastTts] = React.useState<{ text: string; lang: string } | null>(null);
  const scrollRef = React.useRef<HTMLDivElement>(null);
  // Whether the view should keep following new content to the bottom.
  // Updated only from real user scroll events (see effect below) — NOT
  // recomputed from scrollHeight/scrollTop inside the messages-effect,
  // because during fast token-by-token streaming `messages` gets a new
  // reference on every chunk, so that effect's cleanup cancels its own
  // pending rAF almost every time (starving it) and the geometry read
  // that did land could be racing a not-yet-painted DOM update. Either
  // way, one missed correction pushes the gap past the old 120px
  // threshold and it never recovers — the view visibly stops following
  // the conversation. Tracking intent from actual scroll input sidesteps
  // that race entirely.
  const stickToBottom = React.useRef(true);
  // Persistent audio element — one across the chat's lifetime to avoid
  // browser quirks where a fresh Audio() per turn can lose autoplay
  // grant from the original user gesture.
  const audioRef = React.useRef<HTMLAudioElement | null>(null);
  const blobUrlRef = React.useRef<string | null>(null);

  // Read TTS language from the operator's profile (display_language).
  // Falls back to "en" (console UI default); detectTtsLang still auto-detects
  // the response language per turn, so this only matters for neutral text.
  const profileQ = useQuery({
    queryKey: ["profile"],
    queryFn: ({ signal }) => getProfile(signal),
    staleTime: 5 * 60_000,
    retry: 0,
  });
  // Stable chat_key derived from sid — avoids a queryKey flip when the
  // sessions list resolves later and meta.chat_key becomes available.
  // The web bridge always uses "web:<sid>" as the chat_key.
  const chatKey = `web:${sid}`;
  // Engine preference for the status bar — same key as ChatEngineSelector so
  // updates are shared from the React Query cache.
  const enginePrefQ = useQuery({
    queryKey: ["chat-engine-pref", chatKey],
    queryFn: ({ signal }) => getPerChatEngine(chatKey, signal),
    staleTime: 30_000,
    retry: 0,
  });
  const effectiveEngine = enginePrefQ.data?.effective_engine ?? "claude_code";
  // The web console does not expose per-chat persona pins via the
  // chat-settings REST API (that API covers bridge channels only, not
  // web sessions). Show the operator-configured global default instead.
  const activePersona = profileQ.data?.profile?.identity?.default_persona;
  // Profile display_language as fallback for detectTtsLang when the
  // signal is ambiguous (e.g. very short or language-neutral text).
  const ttsLang: string = React.useMemo(() => {
    const raw = profileQ.data?.profile?.identity?.display_language ?? "";
    return raw.trim() || "en";
  }, [profileQ.data]);

  // The WebSocket onmessage handler is bound once per `sid` mount.
  // Without refs, any state that changes afterwards (the user clicks
  // the Voice toggle, the profile loads a different display_language)
  // would be invisible to the captured handler — the classic
  // stale-closure trap. We mirror the two state values we read from
  // inside `ws.onmessage → handleEvent` into refs so the captured
  // handler always sees the current value.
  const voiceOutRef = React.useRef(voiceOut);
  const ttsLangRef = React.useRef(ttsLang);
  React.useEffect(() => {
    voiceOutRef.current = voiceOut;
  }, [voiceOut]);
  React.useEffect(() => {
    ttsLangRef.current = ttsLang;
  }, [ttsLang]);

  // Stable callbacks for useTasksWithLiveUpdates.
  // These MUST be wrapped in useCallback — inline arrow functions would create
  // a new reference on every render, causing an infinite reload loop in the
  // hook's useEffect and a constant SSE reconnect storm.
  const handleTasksLoaded = React.useCallback((tasks: Task[]) => {
    setPersistedTasks(tasks);
    console.log(`[Task-Updates] Loaded ${tasks.length} persisted tasks for chat ${sid}`);
  }, [sid]);

  const handleTaskUpdated = React.useCallback((task: Task) => {
    // Upsert: update if already in list, add if new (e.g. task created while
    // the pane was already mounted and not yet in the initial IDB load).
    setPersistedTasks((prev) => {
      const exists = prev.some((t) => t.task_id === task.task_id);
      if (exists) return prev.map((t) => (t.task_id === task.task_id ? task : t));
      return [...prev, task];
    });
  }, []);

  const handleTaskError = React.useCallback((error: Error) => {
    console.warn(`[Task-Updates] Error:`, error.message);
  }, []);

  // Task live updates: load + stream + persist across chat switches
  const { isConnected: tasksConnected, isPolling: tasksPolling } =
    useTasksWithLiveUpdates({
      chatKey: chatKey,
      sessionId: sid,
      onTasksLoaded: handleTasksLoaded,
      onTaskUpdated: handleTaskUpdated,
      onError: handleTaskError,
    });

  // Sync streaming state to the global external store so the sidebar shows
  // a live indicator for this chat even when viewing another chat.
  //
  // On mount:  clear any stale "interrupted" state left from a prior visit.
  // While live: keep the store updated as streaming starts/stops normally.
  // On unmount: if we were still streaming when the user switched away,
  //             downgrade to "interrupted" so the sidebar shows a warning dot.
  //             The existing streamingRef (used for push-to-talk) tells us
  //             the state at cleanup time without a stale closure.
  // On mount: check if this chat was mid-stream when the user last left,
  // then clear the global + sessionStorage flags.
  React.useEffect(() => {
    clearStreamState(sid);
    return () => {
      // The WS registry keeps the connection alive across unmounts — streaming
      // continues in the background even when the user switches chats.
      // If still streaming on unmount, keep the yellow sidebar dot so the user
      // can see the other session is still generating (streamingRef is current
      // because it is updated by its own effect on every render).
      if (streamingRef.current) {
        markStreaming(sid);
      } else {
        clearStreamState(sid);
      }
    };
  }, [sid]);

  // Keep global streaming store in sync with the sidebar live indicator.
  React.useEffect(() => {
    if (streaming) {
      markStreaming(sid);
    } else {
      markDone(sid);
    }
  }, [sid, streaming]);

  // ── Session setup: connect WS + load history ──────────────────────────────
  // The registry keeps the WS alive across unmounts (chat switches), so
  // streaming continues in the background. On remount we just re-subscribe;
  // history is only loaded once per session (registry guards duplicate loads).
  React.useEffect(() => {
    setError(null);

    // Connect (no-op if already open).
    ensureConnected(sid);

    // Load server-side history into the registry — only applied on first visit.
    let cancelled = false;
    getChatTurns(sid)
      .then((res) => {
        if (cancelled) return;
        const hydrated: ChatMessage[] = res.turns.map((t: ChatTurn, i: number) => ({
          id: `h-${t.ts}-${i}`,
          role: t.role === "system" ? "system" : t.role,
          ts: t.ts,
          parts: (t.parts ?? []).map((p): MessagePart => {
            if (p.kind === "text") return { kind: "text", text: String(p.text ?? "") };
            if (p.kind === "artifact") return {
              kind: "artifact",
              name: String(p.name ?? ""),
              path: String(p.path ?? ""),
              mime: String(p.mime ?? "application/octet-stream"),
              size: Number(p.size ?? 0),
              sid,
              ...(p.label ? { label: String(p.label) } : {}),
            };
            return { kind: "tool", name: String(p.name ?? ""), input: (p.input ?? {}) as Record<string, unknown> };
          }),
        }));
        loadHistory(sid, hydrated);
      })
      .catch(() => { /* empty chat on first visit — expected */ });

    // Subscribe to raw stream events for TTS and React Query title updates.
    // These side-effects only apply while this ChatPane is mounted (i.e. the
    // user is actually looking at this chat).
    const unsubEvents = subscribeEvents(sid, (evt: StreamEvent) => {
      if (evt.type === "result" && evt.text) {
        // Auto-detect language from the response text so TTS speaks the
        // language Claude actually answered in (not a static profile setting).
        const lang = detectTtsLang(evt.text, ttsLangRef.current);
        setLastTts({ text: evt.text, lang });
        if (voiceOutRef.current) {
          playTts(evt.text, lang).catch(() => { /* surface in playTts */ });
        }
      }
      if (evt.type === "session_title" && evt.title) {
        qc.setQueryData(["chat", "sessions"], (old: { sessions: ChatSessionSummary[] } | undefined) => {
          if (!old) return old;
          return {
            ...old,
            sessions: old.sessions.map((s) => (s.sid === sid ? { ...s, title: evt.title! } : s)),
          };
        });
        consumePendingTitle(sid);
      }
      if (evt.type === "error") {
        setError(evt.message ?? "error");
      }
      if (evt.type === "ccc_action" && evt.action_id) {
        setCccActions((prev) => {
          if (prev.some((e) => e.action_id === evt.action_id)) return prev;
          return [...prev, evt];
        });
      }
    });

    return () => {
      cancelled = true;
      unsubEvents();
      // DO NOT close the WS here — the registry keeps it alive for background
      // streaming. The WS is only closed on explicit chat deletion (closeSession).
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sid]);

  // ── Sync terminal errors from registry state ───────────────────────────────
  // Only TERMINAL errors (session expired / not found) surface as a destructive
  // banner. Transient reconnects (server restart 1012, network blip) are NOT
  // errors — they use the calm `reconnectingVisible` notice below instead, so
  // the command-centre no longer flashes a scary "Connection error" after a task.
  React.useEffect(() => {
    setError(chatSession.error ?? null);
  }, [chatSession.error]);

  // ── Calm, grace-delayed reconnect notice ───────────────────────────────────
  // Reveal a low-key "Reconnecting…" notice only if the reconnect is still in
  // progress after a 1 s grace, so a fast recovery (a quick uvicorn restart)
  // doesn't flash anything at all. Auto-hides the instant the socket re-opens.
  const [reconnectingVisible, setReconnectingVisible] = React.useState(false);
  React.useEffect(() => {
    if (!chatSession.reconnecting) {
      setReconnectingVisible(false);
      return;
    }
    const t = setTimeout(() => setReconnectingVisible(true), 1000);
    return () => clearTimeout(t);
  }, [chatSession.reconnecting]);

  // ── Track whether the view should stick to the bottom ──────────────────────
  // Driven only by real scroll events, not by re-deriving it from geometry on
  // every `messages` update (see stickToBottom comment above for why that was
  // unreliable). Scrolling within 64px of the bottom re-engages sticky mode;
  // scrolling further up disengages it so reading history isn't interrupted.
  React.useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const onScroll = () => {
      stickToBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 64;
    };
    el.addEventListener("scroll", onScroll, { passive: true });
    return () => el.removeEventListener("scroll", onScroll);
  }, [sid]);

  // ── Auto-scroll on new content ──────────────────────────────────────────────
  // On the very first batch of messages (initial session load), always jump to
  // the bottom so the user sees the latest exchange without having to scroll.
  // On subsequent updates, follow to the bottom whenever stickToBottom is true
  // (see effect above) — unconditionally, not gated on a fresh geometry read,
  // so a burst of fast streaming updates can't starve the correction.
  // isInitialLoad resets to true automatically on every remount (key={activeSid}).
  const isInitialLoad = React.useRef(true);
  React.useEffect(() => {
    const el = scrollRef.current;
    if (!el || !messages.length) return;
    if (isInitialLoad.current) {
      isInitialLoad.current = false;
      stickToBottom.current = true;
      requestAnimationFrame(() => {
        if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
      });
      return;
    }
    if (!stickToBottom.current) return;
    // Defer to next animation frame so the browser has painted the new node
    // before we read scrollHeight — avoids a forced synchronous layout on every
    // streaming token (which was causing jank during fast streaming). Unlike
    // before, a starved/cancelled frame here just means the current chunk's
    // scroll lands with the next one — it can't get permanently stuck, since
    // we're not conditioning the jump on a geometry snapshot that may already
    // be stale.
    const raf = requestAnimationFrame(() => {
      const container = scrollRef.current;
      if (container) container.scrollTop = container.scrollHeight;
    });
    return () => cancelAnimationFrame(raf);
  }, [messages]);

  // Sending a message is an explicit signal the user wants to follow the
  // conversation again, even if they'd scrolled up to read history.
  const scrollToBottomOnSend = React.useCallback(() => {
    stickToBottom.current = true;
  }, []);

  // ── Upload files when selected ────────────────────────────────────────────
  const handleFileSelect = async (evt: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(evt.target.files ?? []);
    if (files.length === 0) return;
    evt.target.value = "";
    setUploading(true);
    setUploadError(null);
    try {
      const metas = await uploadAttachments(sid, files, csrf);
      setPendingAttachments((prev) => [...prev, ...metas]);
    } catch (e) {
      setUploadError(e instanceof Error ? e.message : "Upload failed");
    } finally {
      setUploading(false);
    }
  };

  // ── Send user message via registry ─────────────────────────────────────────
  const sendUser = (text: string) => {
    const hasText = text.trim().length > 0;
    const hasAttachments = pendingAttachments.length > 0;
    if ((!hasText && !hasAttachments) || streaming) return;

    let fullText = text;
    if (hasAttachments) {
      const lines = pendingAttachments.map(
        (a) => `- ${a.path} (${(a.size / 1024).toFixed(1)} KB, ${a.mime})`,
      );
      const header = [
        "[Dateien im Session-Workdir — mit dem Read-Tool oder Bash-Tool zugreifen]",
        ...lines,
      ].join("\n");
      fullText = hasText ? `${header}\n\n${text}` : header;
    }

    const result = registrySend(sid, fullText);
    if (!result) {
      // WS not open — trigger reconnect and reveal the calm notice so the user
      // knows to retry in a moment (no scary error wording).
      ensureConnected(sid);
      setReconnectingVisible(true);
      return;
    }
    scrollToBottomOnSend();
    setError(null);
    setInput("");
    setPendingAttachments([]);
    setUploadError(null);
  };

  // ── Cancel a running engine turn via registry ────────────────────────────────
  const cancelTurn = () => {
    registryCancel(sid);
  };

  const stopVoice = React.useCallback(() => {
    const a = audioRef.current;
    if (a) {
      try {
        a.pause();
        a.currentTime = 0;
      } catch {
        /* ignore */
      }
    }
    if (blobUrlRef.current) {
      URL.revokeObjectURL(blobUrlRef.current);
      blobUrlRef.current = null;
    }
    setVoiceState("idle");
  }, []);

  // Clean up the audio element on unmount.
  React.useEffect(() => () => stopVoice(), [stopVoice]);

  const handleOpenWorkdir = React.useCallback(async () => {
    try {
      const { path } = await openSessionWorkdir(sid, csrf, true);
      setWorkdirInfo(path);
      // Auto-dismiss after 8 s so the banner doesn't linger forever.
      setTimeout(() => setWorkdirInfo(null), 8_000);
    } catch {
      setWorkdirInfo("Could not open artifacts folder.");
      setTimeout(() => setWorkdirInfo(null), 4_000);
    }
  }, [sid, csrf]);

  const playTts = async (text: string, lang?: string) => {
    // Latest answer wins — stop any in-flight playback first.
    stopVoice();
    if (!text.trim()) return;
    setVoiceState("loading");
    // Use the provided lang (auto-detected at call site) or fall back to the
    // profile-driven language stored in the ref.
    const ttsLanguage = lang ?? ttsLangRef.current;
    let blob: Blob;
    try {
      blob = await ttsBlob(text, ttsLanguage, csrf);
    } catch (e) {
      setVoiceState("idle");
      setError(e instanceof Error ? `TTS failed: ${e.message}` : "TTS failed");
      return;
    }
    if (!blob.size) { setVoiceState("idle"); return; }
    const url = URL.createObjectURL(blob);
    blobUrlRef.current = url;
    let audio = audioRef.current;
    if (!audio) {
      audio = new Audio();
      audioRef.current = audio;
    }
    audio.onended = () => {
      if (blobUrlRef.current === url) {
        URL.revokeObjectURL(url);
        blobUrlRef.current = null;
      }
      setVoiceState("idle");
    };
    audio.onerror = () => setVoiceState("idle");
    audio.src = url;
    try {
      await audio.play();
      setVoiceState("playing");
    } catch {
      // Browser blocked autoplay (no user gesture in scope). The audio
      // is ready; show a banner so the user can tap to start it.
      setVoiceState("blocked");
    }
  };

  const playBlocked = async () => {
    const a = audioRef.current;
    if (!a) return;
    try {
      await a.play();
      setVoiceState("playing");
    } catch {
      // Still blocked — leave state as-is so the banner stays visible.
    }
  };

  // ── Voice recording ──────────────────────────────────────────────
  const mediaRef = React.useRef<MediaRecorder | null>(null);
  const chunksRef = React.useRef<Blob[]>([]);
  // Holds a live SpeechRecognition instance when the Web Speech API path is active.
  const recognitionRef = React.useRef<any>(null);

  const startRecording = async () => {
    // Prefer the browser's built-in Web Speech API: works on Chrome and Edge
    // without any API key (audio goes to Google/Microsoft, no user credential
    // required).  Falls back to MediaRecorder → Python STT for Firefox or if
    // the browser API errors out.
    const SpeechRecognitionImpl =
      (window as any).SpeechRecognition ?? (window as any).webkitSpeechRecognition;

    if (SpeechRecognitionImpl) {
      try {
        // Capture the text present before the hold, and reset the accumulator.
        pttBaseRef.current = inputRef.current;
        sttAccumRef.current = "";
        sttStoppingRef.current = false;

        const recognition = new SpeechRecognitionImpl();
        recognition.continuous = true;        // don't stop on a speech pause
        recognition.interimResults = false;
        recognition.lang = navigator.language || "de-DE";
        recognition.onresult = (event: any) => {
          // Append only the NEW final results (event.resultIndex forward) so the
          // accumulator survives the auto-restarts below without duplicating.
          for (let i = event.resultIndex; i < event.results.length; i++) {
            const res = event.results[i];
            if (res.isFinal && res[0]?.transcript) {
              sttAccumRef.current += res[0].transcript + " ";
            }
          }
          const base = pttBaseRef.current;
          const acc = sttAccumRef.current.trim();
          setInputRef.current(base ? `${base} ${acc}` : acc);
        };
        recognition.onerror = (ev: any) => {
          // A permissions/service error is terminal; a 'no-speech'/'aborted' during
          // the hold is not — let onend decide whether to restart.
          if (ev?.error === "not-allowed" || ev?.error === "service-not-allowed") {
            sttStoppingRef.current = true;
          }
        };
        recognition.onend = () => {
          // Push-to-talk: Chrome ends recognition on silence, but the user is still
          // holding Space — restart and keep listening until they release (which
          // sets sttStoppingRef via stopRecording()).
          if (!sttStoppingRef.current) {
            try { recognition.start(); return; } catch (_e) { /* fall through */ }
          }
          recognitionRef.current = null;
          setRecording(false);
        };
        recognition.start();
        recognitionRef.current = recognition;
        setRecording(true);
        return;
      } catch (_e) {
        // Web Speech API start failed — fall through to MediaRecorder
      }
    }

    // MediaRecorder → Python STT (Firefox, or Web Speech API unavailable)
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      chunksRef.current = [];
      const mr = new MediaRecorder(stream, { mimeType: pickMime() });
      mr.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data);
      };
      mr.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop());
        const blob = new Blob(chunksRef.current, { type: mr.mimeType || "audio/webm" });
        try {
          const r = await transcribeAudio(blob, csrf);
          setInput((prev) => (prev ? `${prev} ${r.text}` : r.text));
        } catch (e) {
          // 403 = stale CSRF token (e.g. console restart) — refresh session silently
          if (e instanceof ApiError && e.status === 403) {
            refreshAuth();
            setError("Session abgelaufen — bitte nochmal sprechen.");
          } else {
            setError(e instanceof Error ? e.message : "Transkription fehlgeschlagen");
          }
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
    if (recognitionRef.current) {
      // Signal the release FIRST so onend finalizes instead of auto-restarting.
      sttStoppingRef.current = true;
      try { recognitionRef.current.stop(); } catch (_e) {}
      recognitionRef.current = null;
    } else {
      mediaRef.current?.stop();
      mediaRef.current = null;
    }
    setRecording(false);
  };

  // ── Push-to-talk: hold Space (≥400 ms) to record ────────────────
  // Works even when the chat textarea has focus — a short tap types a
  // normal space, a held press (≥400 ms) starts speech-to-text.
  // When PTT activates from the textarea the stray space that landed
  // during the hold is trimmed from the input before recording starts.
  // Buttons, links, <select>, and contentEditable own Space natively
  // (click / scroll / activation) and are never intercepted.
  const recordingRef = React.useRef(false);
  // Push-to-talk speech accumulation across Web-Speech auto-restarts: the browser
  // recognizer ends on a pause, but we must keep listening until Space is released.
  const sttAccumRef = React.useRef("");       // final transcript so far this hold
  const pttBaseRef = React.useRef("");        // input text before the hold started
  const sttStoppingRef = React.useRef(false); // true once the user released the key
  // pttPendingRef is set synchronously in the hold-timer callback before the
  // async startRecording() call.  onKeyUp checks it so that a keyUp arriving
  // before React commits the recording state still stops the recording.
  const pttPendingRef = React.useRef(false);
  const streamingRef = React.useRef(streaming);
  const startRecRef = React.useRef(startRecording);
  const stopRecRef = React.useRef(stopRecording);
  const inputRef = React.useRef(input);
  const setInputRef = React.useRef(setInput);
  const sendUserRef = React.useRef(sendUser);
  const textareaRef = React.useRef<HTMLTextAreaElement>(null);
  React.useEffect(() => {
    recordingRef.current = recording;
    if (recording) pttPendingRef.current = false; // recording confirmed — clear pending
  }, [recording]);
  React.useEffect(() => { streamingRef.current = streaming; }, [streaming]);
  React.useEffect(() => { startRecRef.current = startRecording; });
  React.useEffect(() => { stopRecRef.current = stopRecording; });
  React.useEffect(() => { inputRef.current = input; }, [input]);
  React.useEffect(() => { setInputRef.current = setInput; }, [setInput]);
  React.useEffect(() => { sendUserRef.current = sendUser; });

  React.useEffect(() => {
    // Elements where Space has a native role we must not override.
    const isNativeSpaceTarget = (el: EventTarget | null): boolean => {
      if (!(el instanceof HTMLElement)) return false;
      const tag = el.tagName.toLowerCase();
      if (tag === "input" || tag === "select") return true;
      if (el.isContentEditable) return true;
      if (tag === "button" || tag === "a") return true;
      return false;
    };
    const isTextarea = (el: EventTarget | null): boolean =>
      el instanceof HTMLElement && el.tagName.toLowerCase() === "textarea";

    let holdTimer: ReturnType<typeof setTimeout> | null = null;

    const onKeyDown = (e: KeyboardEvent) => {
      if (e.code !== "Space" || e.repeat || e.metaKey || e.ctrlKey || e.altKey) return;
      if (isNativeSpaceTarget(e.target)) return;
      if (recordingRef.current || streamingRef.current) return;

      // Outside textarea → prevent default immediately (no character typed).
      // Inside textarea → let the space land; trim it if hold completes.
      const inTextarea = isTextarea(e.target);
      if (!inTextarea) e.preventDefault();

      holdTimer = setTimeout(() => {
        holdTimer = null;
        if (recordingRef.current || streamingRef.current) return;
        if (inTextarea) {
          const cur = inputRef.current;
          if (cur.endsWith(" ")) setInputRef.current(cur.slice(0, -1));
        }
        pttPendingRef.current = true; // mark pending synchronously before async call
        void startRecRef.current();
      }, 400);
    };

    const onKeyUp = (e: KeyboardEvent) => {
      if (e.code !== "Space") return;
      if (holdTimer !== null) {
        // Short press — timer never fired, recording never started; nothing to do.
        clearTimeout(holdTimer);
        holdTimer = null;
        return;
      }
      // Check both: recordingRef (committed state) and pttPendingRef (async gap
      // where startRecording() was called but React hasn't committed yet).
      if (!recordingRef.current && !pttPendingRef.current) return;
      pttPendingRef.current = false;
      e.preventDefault();
      stopRecRef.current();
    };

    // Elements where Enter has a native role we must not override — same
    // list as Space, plus ALL textareas (the main composer already sends
    // on Enter via its own onKeyDown; any other textarea on the page, e.g.
    // inside a dialog, keeps its normal newline/submit behaviour).
    const isNativeEnterTarget = (el: EventTarget | null): boolean => {
      if (!(el instanceof HTMLElement)) return false;
      const tag = el.tagName.toLowerCase();
      if (tag === "input" || tag === "select" || tag === "textarea") return true;
      if (el.isContentEditable) return true;
      if (tag === "button" || tag === "a") return true;
      return false;
    };

    // Global "Enter sends" — so a reply can be sent without first clicking
    // into the composer (e.g. right after push-to-talk, or while reading
    // the transcript). Skips native targets above so it never hijacks
    // Enter inside some other field/dialog elsewhere on the page.
    const onGlobalEnter = (e: KeyboardEvent) => {
      if (e.key !== "Enter" || e.repeat || e.shiftKey || e.metaKey || e.ctrlKey || e.altKey) return;
      // Ignore IME composition (CJK candidate confirmation) and any Enter a
      // menu/dialog/other handler already acted on — otherwise the global
      // send hijacks Enter meant for another control.
      if (e.isComposing || (e as KeyboardEvent & { keyCode?: number }).keyCode === 229) return;
      if (e.defaultPrevented) return;
      if (isNativeEnterTarget(e.target)) return;
      if (recordingRef.current || streamingRef.current) return;
      e.preventDefault();
      setPaletteOpen(false);
      sendUserRef.current(inputRef.current);
    };

    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("keyup", onKeyUp);
    window.addEventListener("keydown", onGlobalEnter);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("keyup", onKeyUp);
      window.removeEventListener("keydown", onGlobalEnter);
      if (holdTimer !== null) clearTimeout(holdTimer);
    };
  }, []);

  const paletteMatches: typeof SLASH_COMMANDS =
    paletteOpen && input.startsWith("/")
      ? SLASH_COMMANDS.filter(({ cmd }) => {
          const q = input.toLowerCase();
          if (cmd.startsWith(q)) return true;
          // Multi-word match: each typed token must prefix the corresponding command token.
          const qParts = q.split(/\s+/);
          const cParts = cmd.split(/\s+/);
          return qParts.every((tok, i) => i < cParts.length && cParts[i].startsWith(tok));
        })
      : [];

  // CCC M6: lightweight frontend entity-type hint (mirrors entity_extract.py heuristics).
  // Shows a badge when the typed text strongly implies a CCC entity — no server round-trip.
  const cccEntityHint = React.useMemo<string | null>(() => {
    const t = input.trim();
    if (!t || t.length < 4) return null;
    if (/^ats\s*:/i.test(t))       return "ATS Task";
    if (/^a2a\s*:/i.test(t))       return "A2A Session";
    if (/^workflow\s*:/i.test(t))  return "Workflow";
    if (/^forge\s*:/i.test(t))     return "Forge Tool";
    if (/^skill\s*:/i.test(t))     return "Skill";
    if (/^rag\s*:/i.test(t))       return "RAG Source";
    if (/\b(workflow|awpkg|flow|pipeline)\b/i.test(t) && t.length > 8) return "Workflow";
    if (/\b(ats[\s_-]task|ats:)\b/i.test(t)) return "ATS Task";
    if (/\b(a2a|agent[\s-]to[\s-]agent|mesh)\b/i.test(t)) return "A2A";
    if (/\b(forge[\s-]tool|werkzeug)\b/i.test(t)) return "Forge Tool";
    if (/\b(skillforge|skill[\s-]forge)\b/i.test(t)) return "Skill";
    if (/\b(erasure|lösch\w+|erase)\b/i.test(t)) return "Erasure";
    if (/\b(vault|geheimnis|secret[\s-]vault)\b/i.test(t)) return "Vault";
    if (/\b(audit[\s-]log|hash[\s-]chain)\b/i.test(t)) return "Audit";
    return null;
  }, [input]);

  const handleVoiceToggle = React.useCallback(() => {
    const next = !voiceOutRef.current;
    setVoiceOut(next);
    if (!next) stopVoice();
  }, [setVoiceOut, stopVoice]);

  return (
    <>
      <header className="flex items-center justify-between gap-2 border-b border-border bg-background/80 px-4 py-2 backdrop-blur">
        <div className="min-w-0">
          <div className="truncate font-medium">{meta?.title || "New Chat"}</div>
          <div className="flex items-center gap-2 text-[10px] text-muted-foreground">
            <Badge variant="outline" className="font-mono">
              web:{sid.slice(0, 10)}
            </Badge>
            <span>{meta?.turn_count ?? 0} turns</span>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant={auditOpen ? "accent" : "ghost"}
            size="sm"
            onClick={() => setAuditOpen((v) => !v)}
            title="Audit Trail"
          >
            <ShieldCheck className="h-4 w-4" />
            Audit
          </Button>
          <ChatEngineSelector chatKey={chatKey} csrf={csrf} />
          {voiceOut && voiceState !== "idle" && (
            <VoicePlaybackChip
              state={voiceState}
              lang={lastTts?.lang ?? ttsLang}
              onStop={stopVoice}
              onPlayBlocked={playBlocked}
            />
          )}
          {/* Replay last response — visible when voice is on, idle, and we have a result */}
          {voiceOut && voiceState === "idle" && !streaming && lastTts && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => playTts(lastTts.text, lastTts.lang).catch(() => {})}
              title={`Replay last response (${lastTts.lang.toUpperCase()})`}
              aria-label="Replay last response"
            >
              <RotateCcw className="h-4 w-4" />
            </Button>
          )}
          <Button
            variant={voiceOut ? "accent" : "ghost"}
            size="sm"
            onClick={handleVoiceToggle}
            title={voiceOut ? "Disable voice output" : "Enable voice output"}
          >
            {voiceOut ? <Volume2 className="h-4 w-4" /> : <VolumeX className="h-4 w-4" />}
            Voice {voiceOut ? "on" : "off"}
          </Button>
          <Button
            variant="ghost"
            size="icon"
            onClick={handleOpenWorkdir}
            title="Open session artifacts folder"
          >
            <FolderOpen className="h-4 w-4" />
          </Button>
        </div>
      </header>

      {/* Artifacts workdir path banner — auto-dismissed after 8 s */}
      {workdirInfo && (
        <div className="flex items-center gap-2 border-b border-amber-400/30 bg-amber-50/60 px-4 py-1.5 text-xs text-amber-800 dark:bg-amber-950/30 dark:text-amber-300">
          <FolderOpen className="h-3.5 w-3.5 shrink-0" />
          <span className="font-mono truncate flex-1">{workdirInfo}</span>
          <button
            onClick={() => { void navigator.clipboard.writeText(workdirInfo); }}
            className="rounded px-1.5 py-0.5 hover:bg-amber-200/60 dark:hover:bg-amber-800/40"
            title="Copy path"
          >
            Copy
          </button>
          <button onClick={() => setWorkdirInfo(null)} title="Dismiss">
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      )}

      <QuotaWarningBanner />

      {/* Calm reconnect notice — shown only after a 1 s grace while the WS is
          re-establishing (e.g. after an operator deploy). Non-destructive
          styling: the command-centre is recovering, not broken. */}
      {reconnectingVisible && !error && (
        <div className="flex items-center gap-2 border-b border-amber-400/30 bg-amber-50/60 px-4 py-1.5 text-xs text-amber-800 dark:bg-amber-950/30 dark:text-amber-300">
          <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin" />
          <span>Reconnecting to the console…</span>
        </div>
      )}

      {/* Audit Trail panel — replaces message list when open */}
      {auditOpen && (
        <div className="flex flex-col flex-1 min-h-0 border-b border-border">
          {/* tab bar */}
          <div className="flex items-center gap-0 border-b border-slate-800 bg-slate-950 flex-shrink-0">
            <button
              onClick={() => setAuditTab("single")}
              className={`px-3 py-1.5 text-[10px] font-semibold border-b-2 transition-colors ${
                auditTab === "single"
                  ? "border-indigo-500 text-indigo-400 bg-indigo-950/30"
                  : "border-transparent text-slate-500 hover:text-slate-300"
              }`}
            >
              Single-Chain
            </button>
            <button
              onClick={() => setAuditTab("dual-track")}
              className={`px-3 py-1.5 text-[10px] font-semibold border-b-2 transition-colors ${
                auditTab === "dual-track"
                  ? "border-orange-500 text-orange-400 bg-orange-950/20"
                  : "border-transparent text-slate-500 hover:text-slate-300"
              }`}
            >
              Dual-Track
            </button>
          </div>
          {/* panel body */}
          <div className="flex-1 min-h-0">
            {auditTab === "single" ? (
              <WdatAuditPanel sid={sid} />
            ) : (
              <DualTrackAuditPanel sid={sid} />
            )}
          </div>
        </div>
      )}

      <div ref={scrollRef} className={cn("relative min-h-0 overflow-y-auto px-8 py-8", auditOpen ? "hidden" : "flex-1")}>
        {recording && <RecordingOverlay onStop={stopRecording} />}
        <div className="mx-auto w-full max-w-4xl space-y-6">
          {persistedTasks.length > 0 && (
            <>
              {/* Task update status indicator */}
              <div className="text-xs text-gray-600 dark:text-gray-400 mb-2">
                {tasksConnected ? (
                  <span className="inline-flex items-center gap-1">
                    <span className="w-2 h-2 rounded-full bg-green-500 animate-pulse" />
                    Live updates active
                  </span>
                ) : tasksPolling ? (
                  <span className="inline-flex items-center gap-1">
                    <span className="w-2 h-2 rounded-full bg-yellow-500 animate-pulse" />
                    Polling for updates
                  </span>
                ) : (
                  <span className="inline-flex items-center gap-1">
                    <span className="w-2 h-2 rounded-full bg-gray-500" />
                    Task updates paused (offline)
                  </span>
                )}
              </div>
            </>
          )}
          {persistedTasks.length > 0 && (
            <TaskPanel
              tasks={persistedTasks}
              onDeleteTask={async (taskId) => {
                await deleteTask(taskId);
                setPersistedTasks((prev) =>
                  prev.filter((t) => t.task_id !== taskId)
                );
              }}
              onExportTask={async (taskId) => {
                const json = await exportTaskAsJSON(taskId);
                if (json) {
                  const blob = new Blob([json], {
                    type: "application/json",
                  });
                  const url = URL.createObjectURL(blob);
                  const a = document.createElement("a");
                  a.href = url;
                  a.download = `task-${taskId}.json`;
                  a.click();
                  URL.revokeObjectURL(url);
                }
              }}
            />
          )}
          {messages.length === 0 && <EmptyChat onTry={(t) => setInput(t)} />}
          {messages.map((m) => (
            <MessageBubble key={m.id} m={m} />
          ))}
          {/* CCC M5 — entity action cards inline below messages */}
          {cccActions.length > 0 && (
            <div className="flex flex-col gap-1 pt-1">
              {cccActions.map((evt) => (
                <CCCActionCard key={evt.action_id} evt={evt} />
              ))}
            </div>
          )}
          {error && (
            <p className="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive">
              {error}
            </p>
          )}
        </div>
      </div>

      <ChatStatusBar
        effectiveEngine={effectiveEngine}
        personaName={activePersona}
        voiceOut={voiceOut}
        onVoiceToggle={handleVoiceToggle}
      />

      <footer className="bg-background/95 px-8 py-4">
        <div className="mx-auto w-full max-w-4xl space-y-2">
          {/* Hidden file input */}
          <input
            ref={fileInputRef}
            type="file"
            multiple
            accept=".txt,.csv,.md,.json,.yaml,.yml,.toml,.pdf,.xlsx,.xls,.png,.jpg,.jpeg,.gif,.webp,.svg,.py,.js,.ts,.html,.css,.sql"
            className="sr-only"
            aria-label="Attach files"
            onChange={handleFileSelect}
            data-testid="file-input"
          />
          {/* Pending-attachment chips */}
          {pendingAttachments.length > 0 && (
            <div className="flex flex-wrap gap-1.5" data-testid="attachment-preview-bar">
              {pendingAttachments.map((a, i) => (
                <AttachmentChip
                  key={`${a.path}-${i}`}
                  attachment={a}
                  onRemove={() =>
                    setPendingAttachments((prev) => prev.filter((_, idx) => idx !== i))
                  }
                />
              ))}
            </div>
          )}
          {/* Upload error */}
          {uploadError && (
            <p className="text-xs text-destructive" data-testid="upload-error">{uploadError}</p>
          )}
          {/* CCC M6 — entity hint (shown when NLP detects a domain keyword) */}
          {cccEntityHint && !paletteOpen && (
            <p className="text-[10px] text-muted-foreground/70">
              Erkannt als <span className="font-semibold text-accent-foreground">{cccEntityHint}</span> — wird nach dem Senden im passenden Tab aktualisiert
            </p>
          )}
          {/* Main input row */}
          <div className="flex items-end gap-2">
            <div className="relative flex-1">
              <CommandPalette
                matches={paletteMatches}
                selected={Math.min(paletteSel, Math.max(0, paletteMatches.length - 1))}
                onSelect={(cmd, hasArgs) => {
                  setInput(cmd + (hasArgs ? " " : ""));
                  setPaletteOpen(false);
                }}
              />
              <Textarea
                ref={textareaRef}
                value={input}
                onChange={(e) => {
                  const v = e.target.value;
                  setInput(v);
                  if (v.startsWith("/") && !v.includes("\n")) {
                    setPaletteOpen(true);
                    setPaletteSel(0);
                  } else {
                    setPaletteOpen(false);
                  }
                }}
                onKeyDown={(e) => {
                  if (paletteOpen && paletteMatches.length > 0) {
                    if (e.key === "ArrowUp") {
                      e.preventDefault();
                      setPaletteSel((s) => (s - 1 + paletteMatches.length) % paletteMatches.length);
                      return;
                    }
                    if (e.key === "ArrowDown") {
                      e.preventDefault();
                      setPaletteSel((s) => (s + 1) % paletteMatches.length);
                      return;
                    }
                    if (e.key === "Escape") {
                      e.preventDefault();
                      setPaletteOpen(false);
                      return;
                    }
                    if (e.key === "Tab") {
                      e.preventDefault();
                      const m = paletteMatches[paletteSel];
                      if (m) {
                        setInput(m.cmd + (m.args ? " " : ""));
                        setPaletteOpen(false);
                      }
                      return;
                    }
                  }
                  if (e.key === "Enter" && !e.shiftKey && !e.metaKey && !e.ctrlKey) {
                    e.preventDefault();
                    setPaletteOpen(false);
                    sendUser(input);
                  }
                }}
                placeholder={recording ? "Listening — release Space to send" : "Message Corvin… (hold Space to speak)"}
                disabled={streaming || recording}
                className="min-h-[10rem] resize-y font-sans text-sm leading-relaxed"
                rows={5}
              />
            </div>
            <div className="flex flex-col gap-1">
              <Button
                variant="ghost"
                size="icon"
                onClick={() => fileInputRef.current?.click()}
                disabled={streaming || uploading}
                title="Attach files (CSV, PDF, images, …)"
                data-testid="attach-button"
              >
                {uploading
                  ? <Loader2 className="h-4 w-4 animate-spin" />
                  : <Paperclip className="h-4 w-4" />
                }
              </Button>
              <Button
                variant={recording ? "destructive" : "ghost"}
                size="icon"
                onClick={recording ? stopRecording : startRecording}
                disabled={streaming}
                title={recording ? "Stop recording (or release Space)" : "Start recording (or hold Space)"}
              >
                {recording ? <MicOff className="h-4 w-4" /> : <Mic className="h-4 w-4" />}
              </Button>
              {streaming ? (
                <Button
                  variant="destructive"
                  size="icon"
                  onClick={cancelTurn}
                  title="Stop generation"
                >
                  <Square className="h-4 w-4" />
                </Button>
              ) : (
                <Button
                  variant="accent"
                  size="icon"
                  onClick={() => sendUser(input)}
                  disabled={!input.trim() && pendingAttachments.length === 0}
                  title="Send (Enter · Shift+Enter for newline)"
                  data-testid="send-button"
                >
                  <Send className="h-4 w-4" />
                </Button>
              )}
            </div>
          </div>
        </div>
      </footer>
    </>
  );
}

function RecordingOverlay({ onStop }: { onStop: () => void }) {
  return (
    <div className="pointer-events-none sticky top-0 z-10 -mx-8 -mt-8 mb-8 flex justify-center px-8 pt-3">
      <div className="pointer-events-auto flex items-center gap-3 rounded-full border border-red-500/40 bg-red-500/10 px-4 py-2 text-sm shadow-lg backdrop-blur">
        <span className="relative flex h-2.5 w-2.5">
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-red-500 opacity-75" />
          <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-red-500" />
        </span>
        <span className="font-medium text-red-700 dark:text-red-300">
          Recording — release <kbd className="rounded bg-background/60 px-1.5 py-0.5 font-mono text-[11px]">Space</kbd> to send
        </span>
        <button
          onClick={onStop}
          className="rounded-full bg-red-500 px-2 py-0.5 text-[11px] font-medium text-white hover:bg-red-600"
        >
          Stop
        </button>
      </div>
    </div>
  );
}

function VoicePlaybackChip({
  state,
  lang,
  onStop,
  onPlayBlocked,
}: {
  state: VoiceState;
  lang: string;
  onStop: () => void;
  onPlayBlocked: () => void;
}) {
  if (state === "idle") return null;
  if (state === "loading") {
    return (
      <div className="flex items-center gap-2 rounded-full bg-muted/60 px-3 py-1 text-xs text-muted-foreground">
        <Loader2 className="h-3 w-3 animate-spin" />
        Loading audio…
      </div>
    );
  }
  if (state === "blocked") {
    return (
      <button
        onClick={onPlayBlocked}
        className="flex animate-pulse items-center gap-1.5 rounded-full bg-amber-500/15 px-3 py-1 text-xs text-amber-700 hover:bg-amber-500/25 dark:text-amber-300"
      >
        <Volume2 className="h-3 w-3" />
        Tap to play
      </button>
    );
  }
  // playing
  return (
    <button
      onClick={onStop}
      className="flex items-center gap-1.5 rounded-full bg-accent/15 px-3 py-1 text-xs text-foreground hover:bg-accent/25"
      title="Stop playback"
    >
      <SpeakingPulse />
      Speaking · {lang.toUpperCase()}
      <Square className="h-3 w-3" />
    </button>
  );
}

// ── CCC M5 — entity action card ──────────────────────────────────────────────

// In-app navigation targets. NOTE: <BrowserRouter basename="/console"> means
// navigate() args are relative to that basename — they must NOT repeat the
// "/console" prefix or they resolve to /console/console/* and hit the NotFound
// catch-all. Every live route lives under /app/* (see App.tsx). There is no
// dedicated tasks route; ATS tasks surface on the dashboard and in-chat panels.
const CCC_ENTITY_LINKS: Record<string, string> = {
  ats_task:        "/app/dashboard",
  workflow:        "/app/workflows",
  forge_tool:      "/app/forge",
  skill:           "/app/skills",
  audit_query:     "/app/compliance",
  erasure_request: "/app/compliance",
  vault_entry:     "/app/compliance",
  worker_engine:   "/app/engines",
  rag_source:      "/app/rag",
  a2a_session:     "/app/engines",
};

const CCC_STATUS_COLORS: Record<string, string> = {
  created:         "bg-green-500/20 text-green-700 border-green-300",
  queued:          "bg-yellow-500/20 text-yellow-700 border-yellow-300",
  error:           "bg-red-500/20 text-red-700 border-red-300",
  not_implemented: "bg-muted text-muted-foreground border-muted-foreground/30",
};

function CCCActionCard({ evt }: { evt: StreamEvent }) {
  const navigate = useNavigate();
  const entityType = evt.entity_type ?? "unknown";
  const status     = evt.status     ?? "queued";
  const link       = CCC_ENTITY_LINKS[entityType];
  const colorClass = CCC_STATUS_COLORS[status] ?? CCC_STATUS_COLORS.queued;

  return (
    <div
      className={cn(
        "flex items-center gap-2 rounded-md border px-3 py-1.5 text-xs",
        colorClass,
      )}
    >
      <span className="font-mono font-semibold">{entityType}</span>
      <span className="opacity-60">·</span>
      <span className="capitalize">{status}</span>
      {evt.entity_id && (
        <>
          <span className="opacity-60">·</span>
          <span className="font-mono opacity-70 truncate max-w-[12ch]">{evt.entity_id}</span>
        </>
      )}
      {evt.message && (
        <>
          <span className="opacity-60">·</span>
          <span className="opacity-80 truncate max-w-[24ch]">{evt.message}</span>
        </>
      )}
      {link && (
        <button
          onClick={() => navigate(link)}
          className="ml-auto shrink-0 rounded px-1.5 py-0.5 text-[10px] font-medium underline underline-offset-2 hover:opacity-80"
        >
          →
        </button>
      )}
    </div>
  );
}

function SpeakingPulse() {
  return (
    <span className="inline-flex items-end gap-[2px]" aria-hidden>
      <span className="h-2 w-[3px] animate-[pulse_1s_ease-in-out_infinite] rounded-sm bg-accent [animation-delay:-0.3s]" />
      <span className="h-3 w-[3px] animate-[pulse_1s_ease-in-out_infinite] rounded-sm bg-accent [animation-delay:-0.1s]" />
      <span className="h-2 w-[3px] animate-[pulse_1s_ease-in-out_infinite] rounded-sm bg-accent" />
    </span>
  );
}

const MessageBubble = React.memo(function MessageBubble({ m }: { m: ChatMessage }) {
  const isUser = m.role === "user";
  return (
    <div className={cn("flex gap-3", isUser ? "justify-end" : "justify-start")}>
      {!isUser && (
        <div className="mt-1 h-8 w-8 shrink-0 rounded-full bg-accent/20 text-accent">
          <Sparkles className="m-2 h-4 w-4" />
        </div>
      )}
      <div
        className={cn(
          "rounded-2xl px-4 py-3 text-sm leading-relaxed",
          isUser
            ? "max-w-[80%] bg-accent/15 text-foreground"
            : "max-w-[85%] border border-border bg-card text-card-foreground",
        )}
      >
        {m.parts.length === 0 && m.streaming && (
          <span className="inline-flex items-center gap-1 text-muted-foreground">
            <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-accent" />
            thinking…
          </span>
        )}
        {m.parts.map((p, i) =>
          p.kind === "text" ? (
            <div key={i} className="break-words">
              {isUser ? (
                <p className="whitespace-pre-wrap leading-relaxed">{p.text}</p>
              ) : (
                <Markdown text={p.text} compact />
              )}
              {m.streaming && i === m.parts.length - 1 && (
                <span className="ml-0.5 inline-block h-3 w-1 animate-pulse bg-accent align-middle" />
              )}
            </div>
          ) : p.kind === "artifact" ? (
            <ArtifactCard key={i} artifact={p} />
          ) : (
            <ToolUseCard key={i} name={p.name} input={p.input} />
          ),
        )}
        {m.error && (
          <p className="mt-2 rounded-md border border-destructive/30 bg-destructive/10 px-2 py-1 text-xs text-destructive">
            {m.error}
          </p>
        )}
      </div>
      {isUser && (
        <div className="mt-1 h-8 w-8 shrink-0 rounded-full bg-muted text-muted-foreground">
          <User className="m-2 h-4 w-4" />
        </div>
      )}
    </div>
  );
});

function ArtifactCard({ artifact }: { artifact: Extract<MessagePart, { kind: "artifact" }> }) {
  const filePath = artifact.path || artifact.name;
  const url = `/v1/console/chat/sessions/${artifact.sid}/workdir/${filePath.split("/").map(encodeURIComponent).join("/")}`;
  const kb = (artifact.size / 1024).toFixed(1);

  const mime = artifact.mime || "";
  const ext = artifact.name.split(".").pop()?.toLowerCase() || "";
  const isImage = mime.startsWith("image/") && !mime.includes("svg");
  const isSvg = mime === "image/svg+xml" || ext === "svg";
  const isHtml = mime === "text/html" || ext === "html";
  const isPdf = mime === "application/pdf" || ext === "pdf";
  const isAudio = mime.startsWith("audio/") ||
    ["mp3", "wav", "ogg", "oga", "m4a", "flac", "aac", "opus", "weba"].includes(ext);
  const isVideo = mime.startsWith("video/") ||
    ["mp4", "webm", "mov", "mkv", "m4v", "ogv"].includes(ext);
  const isJson = (mime === "application/json" || ext === "json") && !isAudio && !isVideo;
  const isText = (mime.startsWith("text/") || ext === "txt" || ext === "md" || ext === "csv" || ext === "sql")
    && !isHtml && !isAudio && !isVideo;

  const [content, setContent] = React.useState<string | null>(null);
  const [loading, setLoading] = React.useState(false);
  const [fetchFailed, setFetchFailed] = React.useState(false);

  React.useEffect(() => {
    if ((isJson || isText) && !content && !fetchFailed) {
      const controller = new AbortController();
      setLoading(true);
      fetch(url, { signal: controller.signal })
        .then(r => r.text())
        .then(text => {
          const safeText = text.slice(0, 200_000);
          try {
            if (isJson) {
              setContent(JSON.stringify(JSON.parse(safeText), null, 2).slice(0, 10000));
            } else {
              setContent(safeText.slice(0, 10000));
            }
          } catch {
            setContent(safeText.slice(0, 10000));
          }
          setLoading(false);
        })
        .catch(err => {
          if ((err as Error).name === "AbortError") return;
          console.error("Failed to fetch artifact content:", err);
          setFetchFailed(true);
          setLoading(false);
        });
      return () => controller.abort();
    }
  }, [isJson, isText, url, content, fetchFailed]);

  return (
    <div className="my-3 overflow-hidden rounded-lg border border-border/60 bg-card/40">
      {/* Image Rendering */}
      {isImage && (
        <img
          src={url}
          alt={artifact.name}
          className="max-h-[32rem] w-full object-contain bg-black/20"
          loading="lazy"
        />
      )}

      {/* SVG Rendering */}
      {isSvg && (
        <div className="overflow-auto bg-white/5 max-h-[400px]">
          <img
            src={url}
            alt={artifact.name}
            className="w-full min-h-[300px]"
            style={{ minWidth: "100%" }}
          />
        </div>
      )}

      {/* HTML Dashboard/Report */}
      {isHtml && (
        <iframe
          src={url}
          className="w-full border-0"
          style={{ minHeight: "600px" }}
          sandbox="allow-scripts"
          title={artifact.name}
        />
      )}

      {/* PDF Rendering */}
      {isPdf && (
        <iframe
          src={`${url}#toolbar=0&navpanes=0`}
          className="w-full border-0"
          style={{ minHeight: "600px" }}
          sandbox="allow-same-origin"
          title={artifact.name}
        />
      )}

      {/* Audio playback */}
      {isAudio && (
        <div className="bg-black/20 px-3 py-3">
          <audio src={url} controls preload="metadata" className="w-full">
            Your browser does not support inline audio playback.
          </audio>
        </div>
      )}

      {/* Video playback */}
      {isVideo && (
        <video
          src={url}
          controls
          preload="metadata"
          className="max-h-[32rem] w-full bg-black object-contain"
        >
          Your browser does not support inline video playback.
        </video>
      )}

      {/* JSON/Text Content Preview */}
      {(isJson || isText) && (
        <div className="bg-black/30 border-t border-border/40">
          {loading ? (
            <div className="flex items-center justify-center py-8 text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin mr-2" />
              Loading…
            </div>
          ) : content ? (
            <pre className="overflow-auto max-h-[400px] p-4 font-mono text-xs leading-relaxed text-foreground/80 whitespace-pre-wrap break-words">
              {content}
              {content.length >= 10000 && (
                <div className="text-yellow-500 mt-2">… (truncated, download for full content)</div>
              )}
            </pre>
          ) : (
            <div className="p-4 text-muted-foreground text-sm">Could not load preview</div>
          )}
        </div>
      )}

      {/* File Header with Download */}
      <div className="flex items-center gap-2 px-3 py-2 text-xs text-muted-foreground bg-muted/40 border-t border-border/40">
        <FileText className="h-3.5 w-3.5 shrink-0" />
        <span className="min-w-0 flex-1 truncate font-mono font-medium">{artifact.name}</span>
        {artifact.label && (
          <span className={
            "shrink-0 rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide " +
            (artifact.label === "Graph"   ? "bg-purple-500/20 text-purple-400" :
             artifact.label === "live"    ? "bg-blue-500/20 text-blue-400" :
             artifact.label === "compute" ? "bg-orange-500/20 text-orange-400" :
                                            "bg-muted text-muted-foreground")
          }>
            {artifact.label}
          </span>
        )}
        <span className="shrink-0 text-xs opacity-70">{kb} KB</span>
        <a
          href={url}
          download={artifact.name}
          className="shrink-0 rounded p-1 hover:bg-accent/20 hover:text-accent transition-colors"
          title="Download file"
        >
          <Download className="h-3.5 w-3.5" />
        </a>
      </div>
    </div>
  );
}

function ToolUseCard({ name, input }: { name: string; input: Record<string, unknown> }) {
  const [open, setOpen] = React.useState(false);
  // Format the most-likely-interesting field on a single line if possible
  // (e.g. Read tool's "file_path" or Bash tool's "command").
  const summary = React.useMemo(() => {
    for (const key of ["command", "file_path", "path", "url", "query", "pattern"]) {
      if (typeof input[key] === "string") return `${key}: ${input[key] as string}`;
    }
    return null;
  }, [input]);
  return (
    <div className="my-2 rounded-md border border-border/60 bg-muted/30">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-start gap-2 px-2.5 py-1.5 text-left text-[11px] focus:outline-none"
      >
        <Hammer className="mt-0.5 h-3.5 w-3.5 shrink-0 text-accent" />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="font-mono font-medium">{name}</span>
            <span className="text-[10px] text-muted-foreground">
              {Object.keys(input).length} param{Object.keys(input).length === 1 ? "" : "s"}
            </span>
          </div>
          {summary && (
            <div className="truncate font-mono text-[10.5px] text-muted-foreground">{summary}</div>
          )}
        </div>
      </button>
      {open && (
        <pre className="max-h-60 overflow-auto border-t border-border/60 bg-background/40 px-3 py-2 font-mono text-[10.5px] leading-relaxed">
          {JSON.stringify(input, null, 2)}
        </pre>
      )}
    </div>
  );
}

function EmptyChat({ onTry }: { onTry: (text: string) => void }) {
  const samples = [
    "What is the probability of rain in Berlin tomorrow?",
    "Write me a short Python function that filters prime numbers up to n.",
    "How do I add a new messaging channel?",
    "Which files were last changed under core/console/?",
  ];
  return (
    <div className="flex flex-col items-center gap-6 py-16 text-center">
      <Sparkles className="h-10 w-10 text-accent" />
      <div className="space-y-2">
        <h2 className="font-serif text-2xl font-light tracking-tight">
          What's on your mind today?
        </h2>
        <p className="text-sm text-muted-foreground">
          Type something, hold the mic — or try one of these cards.
        </p>
      </div>
      <div className="grid w-full max-w-2xl gap-2 sm:grid-cols-2">
        {samples.map((s) => (
          <button
            key={s}
            onClick={() => onTry(s)}
            className="rounded-lg border border-border/60 bg-card/40 px-3 py-2.5 text-left text-sm text-muted-foreground transition-colors hover:border-accent/40 hover:bg-card hover:text-foreground"
          >
            {s}
          </button>
        ))}
      </div>
      <p className="text-[11px] text-muted-foreground">
        <kbd className="rounded bg-muted px-1.5 py-0.5 font-mono">⌘+Enter</kbd> to send ·
        Hold <kbd className="rounded bg-muted px-1.5 py-0.5 font-mono">Space</kbd> (or tap the mic) to speak
      </p>
    </div>
  );
}

function AttachmentChip({
  attachment,
  onRemove,
}: {
  attachment: AttachmentMeta;
  onRemove: () => void;
}) {
  const isImage = attachment.mime.startsWith("image/");
  const isPdf = attachment.mime === "application/pdf" || attachment.name.endsWith(".pdf");
  const isCsv = attachment.mime === "text/csv" || attachment.name.endsWith(".csv");
  const kb = (attachment.size / 1024).toFixed(1);

  return (
    <div
      className="group flex items-center gap-1.5 rounded-lg border border-border/60 bg-card/60 px-2 py-1.5 text-xs"
      data-testid="attachment-chip"
      title={`${attachment.name} · ${kb} KB`}
    >
      <span className="text-accent shrink-0">
        {isImage ? "🖼" : isPdf ? "📄" : isCsv ? "📊" : "📎"}
      </span>
      <span className="max-w-[120px] truncate font-mono text-[11px] text-foreground">
        {attachment.name}
      </span>
      <span className="shrink-0 text-muted-foreground">{kb} KB</span>
      <button
        onClick={onRemove}
        className="ml-0.5 shrink-0 rounded p-0.5 text-muted-foreground opacity-60 transition-opacity hover:text-destructive hover:opacity-100"
        aria-label={`Remove ${attachment.name}`}
        data-testid="remove-attachment"
      >
        <X className="h-3 w-3" />
      </button>
    </div>
  );
}

function pickMime(): string {
  const candidates = [
    "audio/webm;codecs=opus",
    "audio/webm",
    "audio/ogg;codecs=opus",
    "audio/mp4",
  ];
  for (const c of candidates) {
    if (typeof MediaRecorder !== "undefined" && MediaRecorder.isTypeSupported(c)) return c;
  }
  return "";
}
