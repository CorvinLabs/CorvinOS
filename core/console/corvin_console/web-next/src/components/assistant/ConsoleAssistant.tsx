import * as React from "react";
import { useLocation, useNavigate } from "react-router-dom";
import {
  AlertTriangle,
  Loader2,
  Mic,
  MicOff,
  Send,
  Volume2,
  VolumeX,
  X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  type AssistantHistoryEntry,
  postAssistantMessage,
  getSetupStatus,
  ttsBlob,
} from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";

// ── Language helpers ──────────────────────────────────────────────────────

// UI-chrome language (buttons, labels) is always English — repo rule:
// the console UI must always be English. The ASSISTANT still detects the
// user's message language (detectTextLang) and replies in it; only the
// static UI chrome is fixed to English.
type UiLang = "en";

function uiLangFromBrowser(): UiLang {
  return "en";
}

// Detect the script/language of typed text so TTS and speech-recognition
// stay in sync. Returns a BCP-47 primary tag (e.g. "de", "fr", "ja").
// Falls back to the current browser language when no hint is found.
function detectTextLang(text: string): string | null {
  if (!text.trim()) return null;
  // CJK unified ideographs
  if (/[一-鿿぀-ヿ가-힯]/.test(text)) {
    const bl = navigator.language.toLowerCase();
    if (bl.startsWith("ja")) return "ja";
    if (bl.startsWith("ko")) return "ko";
    return "zh";
  }
  // Arabic / Persian
  if (/[؀-ۿ]/.test(text)) return "ar";
  // Cyrillic
  if (/[Ѐ-ӿ]/.test(text)) return "ru";
  // German specific characters
  if (/[äöüÄÖÜß]/.test(text)) return "de";
  // Common German words
  if (/\b(wie|was|wo|wann|warum|ich|du|wir|ist|sind|kann|bitte|danke|hilfe|zeige|mach|geh|starte|verbinde|kannst|sollte)\b/i.test(text)) return "de";
  // French indicators
  if (/[àâæçéèêëîïôœùûü]/.test(text) || /\b(je|tu|nous|vous|est|sont|avec|pour|comment|pourquoi|bonjour|merci)\b/i.test(text)) return "fr";
  // Spanish/Portuguese indicators
  if (/[áéíóúñ¿¡]/.test(text) || /\b(cómo|qué|dónde|cuándo|hola|gracias|por favor|esto|están|puede)\b/i.test(text)) return "es";
  // Dutch indicators
  if (/\b(hoe|wat|waar|wanneer|waarom|ik|jij|wij|zijn|kan|dank|hallo|help)\b/i.test(text)) return "nl";
  return null;
}

// ── Localised strings ─────────────────────────────────────────────────────

const T: Record<UiLang, {
  title: string;
  placeholder: string;
  listening: string;
  errorMsg: string;
  greeting: (page: string) => string;
  enableVoice: string;
  disableVoice: string;
  voiceInput: string;
  stopRecording: string;
  close: string;
  confirmChange: string;
  execute: string;
  cancel: string;
  done: string;
  failed: string;
}> = {
  en: {
    title: "Assistant",
    placeholder: "Ask a question…",
    listening: "Listening…",
    errorMsg: "Sorry, an error occurred. Please try again.",
    greeting: (page: string) =>
      `Hello! I'm the Corvin assistant. You're on "${page}". How can I help?`,
    enableVoice: "Enable voice output",
    disableVoice: "Disable voice output",
    voiceInput: "Voice input",
    stopRecording: "Stop recording",
    close: "Close",
    confirmChange: "Change setting?",
    execute: "Run",
    cancel: "Cancel",
    done: "✓ Done",
    failed: "✗ Failed to run",
  },
};

// ── Page context chips ────────────────────────────────────────────────────

const PAGE_CHIPS: Record<string, Record<UiLang, { label: string; prompt: string }[]>> = {
  "/app/dashboard": {
    en: [
      { label: "View sessions", prompt: "How do I see active sessions?" },
      { label: "Bridge status", prompt: "What does the bridge status mean?" },
    ],
  },
  "/app/bridges": {
    en: [
      { label: "Connect WhatsApp", prompt: "How do I connect WhatsApp?" },
      { label: "Set up Discord", prompt: "How do I set up the Discord bot?" },
      { label: "Set up Telegram", prompt: "How do I set up Telegram?" },
    ],
  },
  "/app/personas": {
    en: [
      { label: "What is a persona?", prompt: "What is a persona and how do I configure it?" },
      { label: "New persona", prompt: "How do I create a new persona?" },
      { label: "Enable LDD", prompt: "How do I enable LDD for a persona?" },
    ],
  },
  "/app/workflows": {
    en: [
      { label: "Create workflow", prompt: "How do I create a new workflow?" },
      { label: "Trigger workflow", prompt: "How do I manually trigger a workflow?" },
    ],
  },
  "/app/engines": {
    en: [
      { label: "Set up Hermes", prompt: "How do I set up Hermes (local Ollama)?" },
      { label: "Switch engine", prompt: "How do I change the default engine?" },
    ],
  },
  "/app/skills": {
    en: [
      { label: "Create skill", prompt: "How do I create a new skill?" },
      { label: "Promote skill", prompt: "How do I promote a skill to the next scope?" },
    ],
  },
  "/app/tools": {
    en: [
      { label: "Create tool", prompt: "How do I create a forge tool?" },
      { label: "Test tool", prompt: "How do I test a tool?" },
    ],
  },
  "/app/members": {
    en: [
      { label: "Add user", prompt: "How do I add a new user?" },
      { label: "Assign role", prompt: "How do I assign a role to a user?" },
    ],
  },
  "/app/audit": {
    en: [
      { label: "Verify audit", prompt: "How do I verify the audit chain?" },
      { label: "Filter events", prompt: "How do I filter audit events by type?" },
    ],
  },
  "/app/agent-hub": {
    en: [
      { label: "Connect agent", prompt: "How do I connect a remote agent?" },
      { label: "Explain A2A", prompt: "What is the A2A protocol?" },
    ],
  },
  "/app/license": {
    en: [
      { label: "Apply key", prompt: "How do I apply my license key?" },
      { label: "Compare tiers", prompt: "What are the differences between license tiers?" },
    ],
  },
  "/app/settings": {
    en: [
      { label: "Edit whitelist", prompt: "How do I edit the user whitelist?" },
      { label: "Rate limit", prompt: "How do I configure rate limiting?" },
    ],
  },
  "/app/setup": {
    en: [
      { label: "Connect engine", prompt: "How do I connect my engine?" },
      { label: "First bridge", prompt: "Which bridge should I set up first?" },
    ],
  },
};

// ── Types ─────────────────────────────────────────────────────────────────

interface Message {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  isTyping?: boolean;
}

let _idCounter = 0;
function nextId() {
  return `msg-${++_idCounter}`;
}

// ── Typewriter hook ───────────────────────────────────────────────────────

function useTypewriter(target: string, speed = 14): string {
  const [displayed, setDisplayed] = React.useState("");
  const targetRef = React.useRef(target);
  const posRef = React.useRef(0);

  React.useEffect(() => {
    if (target !== targetRef.current) {
      targetRef.current = target;
      posRef.current = 0;
      setDisplayed("");
    }
  }, [target]);

  React.useEffect(() => {
    if (posRef.current >= targetRef.current.length) return;
    const interval = setInterval(() => {
      posRef.current = Math.min(posRef.current + 3, targetRef.current.length);
      setDisplayed(targetRef.current.slice(0, posRef.current));
      if (posRef.current >= targetRef.current.length) clearInterval(interval);
    }, speed);
    return () => clearInterval(interval);
  }, [speed, target]);

  return displayed;
}

// ── Message bubble ────────────────────────────────────────────────────────

function MessageBubble({ msg, isLast }: { msg: Message; isLast: boolean }) {
  const text = useTypewriter(
    msg.role === "assistant" && isLast && !msg.isTyping ? msg.content : "",
    14,
  );
  const content = msg.role === "assistant" && isLast && !msg.isTyping ? text : msg.content;

  if (msg.role === "system") {
    return (
      <div className="flex justify-center">
        <span className="text-[11px] text-muted-foreground/60">{msg.content}</span>
      </div>
    );
  }

  return (
    <div className={cn("flex", msg.role === "user" ? "justify-end" : "justify-start")}>
      <div
        className={cn(
          "max-w-[84%] rounded-2xl px-3.5 py-2.5 text-sm leading-relaxed",
          msg.role === "user"
            ? "rounded-br-sm bg-accent/20 text-foreground"
            : "rounded-bl-sm bg-muted text-foreground",
        )}
      >
        {msg.isTyping ? (
          <span className="flex items-center gap-1 py-0.5">
            <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-muted-foreground/60 [animation-delay:0ms]" />
            <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-muted-foreground/60 [animation-delay:150ms]" />
            <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-muted-foreground/60 [animation-delay:300ms]" />
          </span>
        ) : (
          <span className="whitespace-pre-wrap">{content}</span>
        )}
      </div>
    </div>
  );
}

// ── Props ─────────────────────────────────────────────────────────────────

interface ConsoleAssistantProps {
  open: boolean;
  onClose: () => void;
}

// ── Main widget ───────────────────────────────────────────────────────────

export function ConsoleAssistant({ open, onClose }: ConsoleAssistantProps) {
  const { status, session } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();

  // uiLang: always English for buttons and labels (console UI is English-only)
  const uiLang = uiLangFromBrowser();
  const t = T[uiLang];

  // convLang: full BCP-47 for TTS + speech recognition (updates when user types/speaks)
  const [convLang, setConvLang] = React.useState<string>(() => navigator.language);

  const [messages, setMessages] = React.useState<Message[]>([]);
  const [history, setHistory] = React.useState<AssistantHistoryEntry[]>([]);
  const [input, setInput] = React.useState("");
  const [loading, setLoading] = React.useState(false);
  const [voiceEnabled, setVoiceEnabled] = React.useState(true); // ✅ DEFAULT ON
  const [isListening, setIsListening] = React.useState(false);
  const [pendingAction, setPendingAction] = React.useState<{
    label: string;
    route: string;
    body: unknown;
  } | null>(null);

  const messagesEndRef = React.useRef<HTMLDivElement>(null);
  const inputRef = React.useRef<HTMLInputElement>(null);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const recognitionRef = React.useRef<any>(null);

  // Web Speech API — language follows detected lang
  const startListening = () => {
    type SR = {
      new(): {
        lang: string;
        interimResults: boolean;
        maxAlternatives: number;
        onresult: ((e: { results: { [0]: { [0]: { transcript: string } } } }) => void) | null;
        onerror: (() => void) | null;
        onend: (() => void) | null;
        start(): void;
        stop(): void;
      };
    };
    const win = window as unknown as {
      SpeechRecognition?: SR;
      webkitSpeechRecognition?: SR;
    };
    const SpeechRecognitionImpl = win.SpeechRecognition ?? win.webkitSpeechRecognition;
    if (!SpeechRecognitionImpl) return;
    const recognition = new SpeechRecognitionImpl();
    recognition.lang = convLang;
    recognition.interimResults = false;
    recognition.maxAlternatives = 1;
    recognition.onresult = (e) => {
      const transcript = e.results[0][0].transcript;
      // Try to refine convLang from the transcript text
      const detected = detectTextLang(transcript);
      if (detected) setConvLang(detected);
      setInput(transcript);
      setIsListening(false);
    };
    recognition.onerror = () => setIsListening(false);
    recognition.onend = () => setIsListening(false);
    recognitionRef.current = recognition;
    recognition.start();
    setIsListening(true);
  };

  const stopListening = () => {
    recognitionRef.current?.stop();
    setIsListening(false);
  };

  // TTS playback — language follows convLang (full BCP-47)
  const speakText = React.useCallback(
    async (text: string) => {
      if (!voiceEnabled || !session?.csrf_token) return;
      try {
        const blob = await ttsBlob(text.slice(0, 800), convLang, session.csrf_token);
        if (!blob.size) return;
        const url = URL.createObjectURL(blob);
        const audio = new Audio(url);
        audio.onended = () => URL.revokeObjectURL(url);
        void audio.play();
      } catch {
        /* TTS failure is non-fatal */
      }
    },
    [voiceEnabled, session?.csrf_token, convLang],
  );

  // Contextual greeting on first open
  React.useEffect(() => {
    if (!open || messages.length > 0) return;
    const pageLabel =
      location.pathname === "/app/dashboard"
        ? "Dashboard"
        : (location.pathname.split("/").pop() ?? "Dashboard");
    const greeting = t.greeting(pageLabel);
    setMessages([{ id: nextId(), role: "assistant", content: greeting }]);
  }, [open, location.pathname, messages.length, t]);

  // Reset on close
  React.useEffect(() => {
    if (!open) {
      setMessages([]);
      setHistory([]);
      setInput("");
      setPendingAction(null);
    }
  }, [open]);

  // Scroll to bottom
  React.useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  // Focus input when opened
  React.useEffect(() => {
    if (open) setTimeout(() => inputRef.current?.focus(), 120);
  }, [open]);

  const send = React.useCallback(
    async (text: string) => {
      const trimmed = text.trim();
      if (!trimmed || loading) return;

      // Detect language from typed text and update convLang for TTS/speech
      const detected = detectTextLang(trimmed);
      if (detected) setConvLang(detected);
      const activeLangHint = detected ?? convLang;

      setInput("");
      const userMsg: Message = { id: nextId(), role: "user", content: trimmed };
      const typingMsg: Message = { id: nextId(), role: "assistant", content: "", isTyping: true };
      setMessages((prev) => [...prev, userMsg, typingMsg]);
      setLoading(true);

      try {
        const setupSt = await getSetupStatus().catch(() => null);

        const result = await postAssistantMessage(
          trimmed,
          {
            current_page: location.pathname,
            setup_status: setupSt ?? undefined,
            language: activeLangHint,
          },
          session?.csrf_token ?? "",
          history,
        );

        let responseText = result.response;

        // Extract and execute embedded _actions JSON
        const actionMatch = responseText.match(/\{"_actions":\s*\[[\s\S]*?\]\}/);
        if (actionMatch) {
          responseText = responseText.replace(actionMatch[0], "").trim();
          try {
            const parsed = JSON.parse(actionMatch[0]) as {
              _actions: {
                type: string;
                path?: string;
                route?: string;
                body?: unknown;
                label?: string;
              }[];
            };
            for (const action of parsed._actions) {
              if (action.type === "navigate" && action.path) {
                navigate(action.path);
              } else if (action.type === "patch_setting" && action.route) {
                setPendingAction({
                  label: action.label ?? action.route,
                  route: action.route,
                  body: action.body,
                });
              }
            }
          } catch {
            /* ignore malformed action JSON */
          }
        }

        // Update history for next turn (cap at 10 entries = 5 exchanges)
        setHistory((prev) => {
          const next: AssistantHistoryEntry[] = [
            ...prev,
            { role: "user", content: trimmed },
            { role: "assistant", content: responseText },
          ];
          return next.slice(-10);
        });

        setMessages((prev) => {
          const updated = [...prev];
          for (let i = updated.length - 1; i >= 0; i--) {
            if (updated[i].isTyping) {
              updated[i] = { ...updated[i], content: responseText, isTyping: false };
              break;
            }
          }
          return updated;
        });

        void speakText(responseText);
      } catch {
        setMessages((prev) => {
          const updated = [...prev];
          for (let i = updated.length - 1; i >= 0; i--) {
            if (updated[i].isTyping) {
              updated[i] = {
                ...updated[i],
                content: t.errorMsg,
                isTyping: false,
              };
              break;
            }
          }
          return updated;
        });
      } finally {
        setLoading(false);
      }
    },
    [loading, convLang, t, location.pathname, navigate, session?.csrf_token, speakText, history],
  );

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void send(input);
    }
  };

  if (status !== "authenticated" || !open) return null;

  const pageChips = PAGE_CHIPS[location.pathname]?.[uiLang] ?? [];
  const showChips = !loading && messages.length <= 2 && pageChips.length > 0;

  return (
    <div
      className={cn(
        "fixed right-4 top-[3.375rem] z-40 flex w-[24rem] flex-col overflow-hidden",
        "rounded-2xl border border-border bg-card shadow-2xl shadow-black/20",
        "animate-in fade-in-0 slide-in-from-top-2 duration-200",
      )}
      style={{ maxHeight: "calc(100vh - 4.5rem)" }}
    >
      {/* Header */}
      <div className="flex items-center justify-between border-b border-border px-4 py-3">
        <div className="flex items-center gap-2">
          <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-accent/15">
            <CorvinMark className="h-4 w-4 text-accent" />
          </div>
          <span className="text-sm font-medium">{t.title}</span>
          {loading && <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />}
        </div>
        <div className="flex items-center gap-1">
          <Button
            variant="ghost"
            size="icon"
            className={cn(
              "h-7 w-7 transition-colors",
              voiceEnabled
                ? "text-accent hover:text-accent/80"
                : "text-muted-foreground hover:text-foreground",
            )}
            title={voiceEnabled ? t.disableVoice : t.enableVoice}
            onClick={() => setVoiceEnabled((v) => !v)}
          >
            {voiceEnabled ? <Volume2 className="h-3.5 w-3.5" /> : <VolumeX className="h-3.5 w-3.5" />}
          </Button>
          <Button
            variant="ghost"
            size="icon"
            className="h-7 w-7 text-muted-foreground hover:text-foreground"
            title={t.close}
            onClick={onClose}
          >
            <X className="h-3.5 w-3.5" />
          </Button>
        </div>
      </div>

      {/* patch_setting confirm banner */}
      {pendingAction && (
        <div className="border-b border-amber-500/20 bg-amber-500/8 px-4 py-3">
          <div className="flex items-start gap-2">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-500" />
            <div className="flex-1 space-y-2">
              <p className="text-sm font-medium">{t.confirmChange}</p>
              <p className="text-xs text-muted-foreground">{pendingAction.label}</p>
              <div className="flex gap-2">
                <Button
                  size="sm"
                  variant="accent"
                  className="h-7 text-xs"
                  onClick={async () => {
                    try {
                      const res = await fetch(`/v1/console${pendingAction.route}`, {
                        method: "PATCH",
                        credentials: "include",
                        headers: {
                          "Content-Type": "application/json",
                          "X-CSRF-Token": session?.csrf_token ?? "",
                        },
                        body: JSON.stringify(pendingAction.body),
                      });
                      if (!res.ok) throw new Error(`HTTP ${res.status}`);
                      setMessages((prev) => [
                        ...prev,
                        {
                          id: nextId(),
                          role: "system",
                          content: `${t.done}: ${pendingAction.label}`,
                        },
                      ]);
                    } catch {
                      setMessages((prev) => [
                        ...prev,
                        { id: nextId(), role: "system", content: t.failed },
                      ]);
                    }
                    setPendingAction(null);
                  }}
                >
                  {t.execute}
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  className="h-7 text-xs"
                  onClick={() => setPendingAction(null)}
                >
                  {t.cancel}
                </Button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Messages */}
      <div
        className="flex flex-1 flex-col gap-3 overflow-y-auto px-4 py-4"
        style={{ minHeight: 0, maxHeight: "28rem" }}
      >
        {messages.map((msg, i) => (
          <MessageBubble key={msg.id} msg={msg} isLast={i === messages.length - 1} />
        ))}
        <div ref={messagesEndRef} />
      </div>

      {/* Context chips */}
      {showChips && (
        <div className="flex flex-wrap gap-1.5 px-4 pb-2">
          {pageChips.map((chip) => (
            <button
              key={chip.label}
              onClick={() => void send(chip.prompt)}
              className={cn(
                "rounded-full border border-border bg-muted/60 px-3 py-1 text-xs",
                "text-muted-foreground hover:border-accent/40 hover:bg-accent/10 hover:text-foreground",
                "transition-colors",
              )}
            >
              {chip.label}
            </button>
          ))}
        </div>
      )}

      {/* Input row */}
      <div className="border-t border-border px-3 py-3">
        <div className="flex items-center gap-2">
          <Button
            variant="ghost"
            size="icon"
            className={cn(
              "h-9 w-9 shrink-0 rounded-xl transition-colors",
              isListening
                ? "bg-destructive/10 text-destructive hover:bg-destructive/20"
                : "text-muted-foreground hover:text-foreground",
            )}
            title={isListening ? t.stopRecording : t.voiceInput}
            onClick={isListening ? stopListening : startListening}
          >
            {isListening ? <MicOff className="h-3.5 w-3.5" /> : <Mic className="h-3.5 w-3.5" />}
          </Button>
          <input
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={isListening ? t.listening : t.placeholder}
            disabled={loading}
            className={cn(
              "flex-1 rounded-xl border border-border bg-muted/40 px-3.5 py-2 text-sm",
              "placeholder:text-muted-foreground/50 outline-none",
              "focus:border-accent/50 focus:bg-background transition-colors",
              "disabled:opacity-50",
              isListening && "border-destructive/40 bg-destructive/5",
            )}
          />
          <Button
            variant="accent"
            size="icon"
            className="h-9 w-9 shrink-0 rounded-xl"
            disabled={!input.trim() || loading}
            onClick={() => void send(input)}
          >
            <Send className="h-3.5 w-3.5" />
          </Button>
        </div>
      </div>
    </div>
  );
}

// ── Inline CorvinMark ─────────────────────────────────────────────────────

function CorvinMark({ className }: { className?: string }) {
  return (
    <svg viewBox="12 12 96 96" aria-hidden="true" className={className}>
      <path fill="none" stroke="currentColor" strokeWidth="8" strokeLinecap="round" strokeLinejoin="round" d="M28 40 L56 60 L28 80"/>
      <rect fill="currentColor" x="66" y="72" width="30" height="9" rx="2"/>
      <circle cx="80" cy="50" r="10" fill="#C9A227"/>
      <circle cx="80" cy="50" r="10" fill="none" stroke="currentColor" strokeWidth="2"/>
    </svg>
  );
}
