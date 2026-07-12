import * as React from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { QRCodeSVG } from "qrcode.react";
import {
  BookOpen,
  Check,
  ChevronLeft,
  ChevronRight,
  Cloud,
  Cpu,
  ExternalLink,
  Github,
  Hash,
  Lock,
  Loader2,
  Mail,
  MessageCircle,
  CheckCircle2,
  MessageSquare,
  QrCode,
  Send,
  Volume2,
  Wifi,
  WifiOff,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import {
  getBridgeSetup,
  startWhatsappBridge,
  type WhatsappStartResult,
  getEngineCatalog,
  getEngineProbes,
  getOsEngineSetting,
  getOsEngineHealth,
  setOsEngineSetting,
  getSetupStatus,
  postSetupComplete,
  postTestEngine,
  bootstrapHermes,
  runWelcomeCheck,
} from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";
import { HelpTooltip } from "@/components/ui/help-tooltip";
import { useVoicePlayback } from "@/lib/useVoicePlayback";

// ── Types ────────────────────────────────────────────────────────────────

type Step = "welcome" | "engine" | "bridge" | "done";

// ── Progress indicator ────────────────────────────────────────────────────

function StepDots({ current }: { current: Step }) {
  const visibleSteps: Step[] = ["engine", "bridge", "done"];
  return (
    <div className="flex items-center gap-2">
      {visibleSteps.map((s, i) => {
        const currentIdx = visibleSteps.indexOf(current);
        const isDone = i < currentIdx;
        const isActive = s === current;
        return (
          <React.Fragment key={s}>
            <div
              className={cn(
                "h-2 rounded-full transition-all duration-300",
                isActive && "w-6 bg-accent",
                isDone && "w-2 bg-accent/60",
                !isActive && !isDone && "w-2 bg-border",
              )}
            />
          </React.Fragment>
        );
      })}
    </div>
  );
}

// ── Engine icon ───────────────────────────────────────────────────────────

function EngineIconSmall({ engineId, className }: { engineId: string; className?: string }) {
  if (engineId === "hermes") return <Cpu className={className} />;
  if (engineId === "copilot") return <Github className={className} />;
  return <Cloud className={className} />;
}

// ── OS detection for Hermes/Ollama install guidance ─────────────────────────
// The console runs on localhost, so the browser's OS == the OS Ollama must run
// on. Detect it client-side for instant, OS-correct step-by-step instructions.
type OSKind = "windows" | "macos" | "linux";

function detectOS(): OSKind {
  const nav = navigator as unknown as { userAgentData?: { platform?: string }; platform?: string; userAgent?: string };
  const p = (nav.userAgentData?.platform || nav.platform || nav.userAgent || "").toLowerCase();
  if (p.includes("win")) return "windows";
  if (p.includes("mac") || p.includes("darwin") || p.includes("iphone") || p.includes("ipad")) return "macos";
  return "linux";
}

const OLLAMA_INSTALL: Record<OSKind, { label: string; steps: string[] }> = {
  windows: {
    label: "Windows",
    steps: [
      "Download the Ollama installer: ollama.com/download/windows",
      "Run OllamaSetup.exe — Ollama then runs automatically in the system tray.",
      "Click “Set up Hermes automatically” below — CorvinOS pulls the model + configures Hermes.",
      "Click “Test” → it turns green.",
    ],
  },
  macos: {
    label: "macOS",
    steps: [
      "Download Ollama: ollama.com/download/mac  (or run: brew install ollama)",
      "Open the Ollama app — it starts the local server (menu-bar icon).",
      "Click “Set up Hermes automatically” below — CorvinOS pulls the model + configures Hermes.",
      "Click “Test” → it turns green.",
    ],
  },
  linux: {
    label: "Linux",
    steps: [
      "Open a terminal.",
      "Install Ollama:  curl -fsSL https://ollama.com/install.sh | sh",
      "The installer starts the service automatically (otherwise run:  ollama serve).",
      "Click “Set up Hermes automatically” below — CorvinOS pulls the model + configures Hermes.",
      "Click “Test” → it turns green.",
    ],
  },
};

// ── Step 1: Welcome ───────────────────────────────────────────────────────

// First-boot spoken onboarding self-check (docs/first-run-language-and-
// voice-onboarding.md §2). Fires once on mount: runs the server-side
// health-check (L44 classifier, Hermes warm-up, real STT/TTS round-trip,
// engine connectivity) and speaks the resulting, honestly-worded greeting
// through the shared TTS playback hook. Per the concept's dialectical pass,
// this NEVER gates "Let's go" — a degraded/unreachable check only changes
// the wording, it never disables the button below.
function WelcomeStep({ onNext, csrf }: { onNext: () => void; csrf: string }) {
  const [checkState, setCheckState] = React.useState<"running" | "done" | "error">("running");
  const [greeting, setGreeting] = React.useState<string | null>(null);
  const { voiceState, playTts, playBlocked } = useVoicePlayback(csrf);
  const startedRef = React.useRef(false);

  React.useEffect(() => {
    // Guards against React 18 StrictMode's double-invoke in dev, which
    // would otherwise fire two overlapping checks (and two spoken greetings)
    // on a single mount.
    if (startedRef.current) return;
    startedRef.current = true;

    runWelcomeCheck(csrf)
      .then((result) => {
        if (result.state !== "done" || !result.greeting) {
          setCheckState("error");
          return;
        }
        setGreeting(result.greeting);
        setCheckState("done");
        // Autoplay is expected to be blocked on a genuinely first-ever page
        // load (no prior user gesture exists yet) — that's a browser policy
        // to respect, not a bug. useVoicePlayback surfaces it as
        // voiceState === "blocked" and the banner below lets the user tap
        // to hear it; the written greeting above is visible either way.
        playTts(result.greeting, result.lang ?? "en").catch(() => {});
      })
      .catch(() => setCheckState("error"));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="flex flex-col items-center gap-6 py-4 text-center">
      <div className="flex h-20 w-20 items-center justify-center rounded-2xl bg-accent/15">
        <CorvinMark className="h-10 w-10 text-accent" />
      </div>
      <div className="space-y-2">
        <h2 className="font-serif text-3xl font-light tracking-tight">Corvin</h2>
        {checkState === "done" && greeting ? (
          <p className="text-sm text-muted-foreground">{greeting}</p>
        ) : checkState === "running" ? (
          <p className="flex items-center justify-center gap-1.5 text-sm text-muted-foreground">
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
            Checking voice, engine and pipeline…
          </p>
        ) : (
          <>
            <p className="text-base text-muted-foreground">
              Your AI operating system is ready.
            </p>
            <p className="text-sm text-muted-foreground">
              Set up in 3 steps — takes less than 2 minutes.
            </p>
          </>
        )}
      </div>
      {voiceState === "blocked" && (
        <button
          onClick={playBlocked}
          className="flex animate-pulse items-center gap-1.5 rounded-full bg-amber-500/15 px-3 py-1 text-xs text-amber-700 hover:bg-amber-500/25 dark:text-amber-300"
        >
          <Volume2 className="h-3 w-3" />
          Tap to hear Corvin
        </button>
      )}
      <div className="flex flex-col gap-2 pt-2 w-full">
        <Button variant="accent" size="lg" className="w-full gap-2" onClick={onNext}>
          Let's go
          <ChevronRight className="h-4 w-4" />
        </Button>
      </div>
    </div>
  );
}

// ── Step 2: Engine ────────────────────────────────────────────────────────

function EngineStep({
  onNext,
  onBack,
  engineConnected,
  csrf,
}: {
  onNext: () => void;
  onBack: () => void;
  engineConnected: boolean;
  csrf: string;
}) {
  const [selected, setSelected] = React.useState<string>("claude_code");
  const [modelOverride, setModelOverride] = React.useState<string>("");
  const [synced, setSynced] = React.useState(false);
  const [testState, setTestState] = React.useState<"idle" | "loading" | "ok" | "err">(
    engineConnected ? "ok" : "idle",
  );
  const [testDetail, setTestDetail] = React.useState(
    engineConnected ? "Engine already configured" : "",
  );
  // OS-specific step-by-step setup instructions returned by the backend test
  // (e.g. how to install Ollama for Hermes on this exact OS).
  const [testSteps, setTestSteps] = React.useState<string[]>([]);

  const catalogQ = useQuery({
    queryKey: ["engine-catalog"],
    queryFn: ({ signal }) => getEngineCatalog(signal),
    staleTime: 5 * 60_000,
  });

  const probesQ = useQuery({
    queryKey: ["engine-probes"],
    queryFn: ({ signal }) => getEngineProbes(signal),
    staleTime: 60_000,
    retry: 1,
  });

  const probeMap = React.useMemo<Record<string, { found: boolean; detail: string }>>(() => {
    const m: Record<string, { found: boolean; detail: string }> = {};
    for (const p of probesQ.data?.engines ?? []) {
      m[p.engine_id] = { found: p.found, detail: p.detail };
    }
    return m;
  }, [probesQ.data]);

  const settingQ = useQuery({
    queryKey: ["os-engine-setting"],
    queryFn: ({ signal }) => getOsEngineSetting(signal),
  });

  const healthQ = useQuery({
    queryKey: ["os-engine-health"],
    queryFn: ({ signal }) => getOsEngineHealth(signal),
    refetchInterval: 15_000,
  });

  // Sync selected engine from current tenant config (once)
  React.useEffect(() => {
    if (!synced && settingQ.data) {
      setSelected(settingQ.data.default_engine ?? "claude_code");
      setModelOverride(settingQ.data.hermes_model ?? "");
      setSynced(true);
    }
  }, [settingQ.data, synced]);

  const saveMutation = useMutation({
    mutationFn: (body: { default_engine: string | null; hermes_model: string | null }) =>
      setOsEngineSetting(body, csrf),
  });

  const allEngines = catalogQ.data ?? [];
  const selectedMeta = allEngines.find((e) => e.id === selected);
  const hasModelAliases = (selectedMeta?.model_aliases ?? []).length > 0;
  const ollama = healthQ.data;
  const detectedOS = React.useMemo(detectOS, []);
  const ollamaGuide = OLLAMA_INSTALL[detectedOS];

  // Show only DETECTED engines (+ Hermes, the always-available local fallback,
  // and whatever is currently selected). Until the probes resolve, show all so
  // the list doesn't flash empty.
  const probesReady = !probesQ.isLoading && Object.keys(probeMap).length > 0;
  const visibleEngines = !probesReady
    ? allEngines
    : allEngines.filter(
        (e) => probeMap[e.id]?.found || e.id === "hermes" || e.id === selected,
      );

  const handleTest = async () => {
    setTestState("loading");
    try {
      const r = await postTestEngine(selected, csrf);
      setTestSteps(Array.isArray(r.steps) ? r.steps : []);
      if (r.ok) {
        setTestState("ok");
        setTestDetail(r.detail);
      } else {
        setTestState("err");
        setTestDetail(r.detail || "Connection failed");
      }
    } catch (e: unknown) {
      setTestState("err");
      setTestSteps([]);
      setTestDetail(e instanceof Error ? e.message : "Unknown error");
    }
  };

  // One-click Hermes onboarding: install Ollama (winget/brew/curl) if missing,
  // start `ollama serve`, and pull the default model (qwen3:8b). Long-running
  // (multi-minute download), so the button shows progress. On success we re-run
  // the connectivity test so the green "ready" state appears automatically.
  const [bootstrapPhase, setBootstrapPhase] = React.useState<string>("");
  const bootstrapMut = useMutation({
    mutationFn: () => bootstrapHermes(csrf, setBootstrapPhase),
    onSuccess: (r) => {
      setBootstrapPhase("");
      if (r.model_pulled) {
        void handleTest();
      } else {
        setTestState("err");
        setTestDetail(
          r.error ||
            "Hermes bootstrap did not complete — see Settings → Engines, or install Ollama from ollama.com.",
        );
      }
    },
    onError: (e: unknown) => {
      setTestState("err");
      setTestDetail(e instanceof Error ? e.message : "Hermes bootstrap failed");
    },
  });

  const handleContinue = async () => {
    try {
      await saveMutation.mutateAsync({
        default_engine: selected === "claude_code" ? null : selected,
        hermes_model: selected === "hermes" && modelOverride ? modelOverride : null,
      });
    } catch {
      // Non-fatal — user can adjust in settings
    }
    onNext();
  };

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center gap-3">
        <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-accent/15">
          <Wifi className="h-4.5 w-4.5 text-accent" />
        </div>
        <div>
          <div className="flex items-center gap-1.5">
            <h3 className="font-medium">Choose engine</h3>
            <HelpTooltip title="What is an AI engine?" side="right" width="lg">
              The engine is the AI model that reads your messages and generates replies.
              <br /><br />
              <strong>Claude Code</strong> — cloud-based, uses your Anthropic subscription. Best quality out of the box.
              <br /><br />
              <strong>Hermes</strong> — runs a local model via Ollama. 100% private, no data leaves your device.
              <br /><br />
              You can change this later in Settings.
            </HelpTooltip>
          </div>
          <p className="text-xs text-muted-foreground">
            Which AI engine powers Corvin
          </p>
        </div>
      </div>

      {/* Engine selection cards */}
      {catalogQ.isLoading ? (
        <div className="flex flex-col gap-2">
          <Skeleton className="h-14 w-full" />
          <Skeleton className="h-14 w-full" />
          <Skeleton className="h-14 w-full" />
        </div>
      ) : (
        <div className="flex flex-col gap-2">
          {visibleEngines.map((eng) => {
            const isWorkerOnly = !eng.os_capable;
            const isSelected = !isWorkerOnly && selected === eng.id;
            const isLocal = eng.local;
            return (
              <React.Fragment key={eng.id}>
              <button
                disabled={isWorkerOnly}
                onClick={() => {
                  if (!isWorkerOnly) {
                    setSelected(eng.id);
                    setModelOverride("");
                    setTestState("idle");
                    setTestDetail("");
                  }
                }}
                className={cn(
                  "rounded-xl border-2 px-4 py-3 text-left transition-all",
                  isWorkerOnly
                    ? "border-dashed border-border bg-muted/10 opacity-40 cursor-not-allowed"
                    : isSelected
                    ? isLocal
                      ? "border-emerald-500/50 bg-emerald-500/5"
                      : "border-accent bg-accent/5"
                    : "border-border bg-card hover:border-accent/40 hover:bg-muted/40",
                )}
              >
                <div className="flex items-center gap-2.5">
                  <div className={cn(
                    "flex h-7 w-7 items-center justify-center rounded-md flex-none",
                    isWorkerOnly
                      ? "bg-muted text-muted-foreground"
                      : isSelected
                      ? isLocal ? "bg-emerald-500/10 text-emerald-600" : "bg-accent/10 text-accent"
                      : "bg-muted text-muted-foreground",
                  )}>
                    <EngineIconSmall engineId={eng.id} className="h-3.5 w-3.5" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-1.5">
                      <span className="text-sm font-semibold">{eng.label}</span>
                      {/* ADR-0120: binary detection result */}
                      {probeMap[eng.id] !== undefined && (
                        probeMap[eng.id].found ? (
                          <Badge variant="outline" className="text-[9px] px-1 py-0 border-emerald-500/40 text-emerald-600">
                            installed
                          </Badge>
                        ) : (
                          <Badge variant="outline" className="text-[9px] px-1 py-0 border-amber-500/40 text-amber-600">
                            not found
                          </Badge>
                        )
                      )}
                      {isLocal && !isWorkerOnly && (
                        <Badge variant="outline" className="text-[9px] px-1 py-0 border-emerald-500/40 text-emerald-600">
                          local
                        </Badge>
                      )}
                      {isWorkerOnly && (
                        <Badge variant="outline" className="text-[9px] px-1 py-0 text-muted-foreground">
                          worker only
                        </Badge>
                      )}
                      {isWorkerOnly && (
                        <span onClick={(e) => e.stopPropagation()} className="pointer-events-auto">
                          <HelpTooltip title="Worker-only engine" side="top" width="sm">
                            This engine assists the main AI as a sub-agent. It cannot act as the primary engine for conversations. Choose Claude Code or Hermes instead.
                          </HelpTooltip>
                        </span>
                      )}
                    </div>
                    <div className="flex items-center gap-1.5 mt-0.5">
                      <p className="text-xs text-muted-foreground truncate">{eng.requires}</p>
                      {eng.id === "hermes" && !isWorkerOnly && (
                        <span className="flex-none ml-auto">
                          {ollama?.ollama_reachable ? (
                            <span className="flex items-center gap-1 text-[10px] text-emerald-600">
                              <Wifi className="h-2.5 w-2.5" />Ollama ✓
                            </span>
                          ) : (
                            <span className="flex items-center gap-1 text-[10px] text-amber-500">
                              <WifiOff className="h-2.5 w-2.5" />offline
                              <HelpTooltip title="Ollama not running" side="left" width="md">
                                Install Ollama from <strong>ollama.com</strong>, then start it:<br />
                                <code>ollama serve</code><br /><br />
                                Pull a model:<br />
                                <code>ollama pull qwen3:8b</code>
                              </HelpTooltip>
                            </span>
                          )}
                        </span>
                      )}
                    </div>
                  </div>
                  {isSelected && !isWorkerOnly && (
                    <Check className={cn("h-4 w-4 flex-none", isLocal ? "text-emerald-600" : "text-accent")} />
                  )}
                </div>
              </button>

              {/* Grouped with the Hermes card: OS-detected install steps +
                  one-click auto-setup, so everything Hermes needs is together. */}
              {eng.id === "hermes" && isSelected && (
                <div className="ml-2 space-y-2.5 rounded-xl border border-emerald-500/30 bg-emerald-500/5 p-3">
                  {!ollama?.ollama_reachable && (
                    <div className="space-y-1.5 text-xs">
                      <p className="font-medium text-foreground">
                        Install Ollama on {ollamaGuide.label} — step by step:
                      </p>
                      <ol className="ml-1 list-decimal space-y-1 pl-4 text-muted-foreground">
                        {ollamaGuide.steps.map((s, i) => (
                          <li key={i} className="break-words">{s}</li>
                        ))}
                      </ol>
                    </div>
                  )}
                  <Button
                    variant="accent"
                    size="sm"
                    className="gap-2"
                    disabled={bootstrapMut.isPending}
                    onClick={() => bootstrapMut.mutate()}
                  >
                    {bootstrapMut.isPending ? (
                      <>
                        <Loader2 className="h-4 w-4 animate-spin" />
                        Setting up Hermes…
                      </>
                    ) : (
                      <>
                        <Cpu className="h-4 w-4" />
                        Set up Hermes automatically
                      </>
                    )}
                  </Button>
                  {bootstrapMut.isPending && bootstrapPhase && (
                    <p className="text-[11px] text-accent animate-pulse">{bootstrapPhase}</p>
                  )}
                  <p className="text-[11px] text-muted-foreground">
                    One-time download (runs in the background — you can wait here or come back later). Afterwards Hermes runs fully local & offline — no API key, no data leaves your device.
                  </p>
                </div>
              )}
              </React.Fragment>
            );
          })}
        </div>
      )}

      {/* Model variant picker for engines with aliases (e.g. Hermes) */}
      {!catalogQ.isLoading && selectedMeta && hasModelAliases && (
        <div className="space-y-1.5">
          <Label className="text-xs">Model variant</Label>
          <Select
            value={modelOverride || selectedMeta.model_placeholder}
            onChange={(e) => setModelOverride(e.target.value)}
            className="h-8 text-xs"
          >
            <option value="">— default —</option>
            {selectedMeta.model_aliases.map((alias) => (
              <option key={alias} value={alias}>{alias}</option>
            ))}
          </Select>
        </div>
      )}

      {testState === "ok" && (
        <div className="flex items-center gap-2 rounded-lg border border-emerald-500/30 bg-emerald-500/10 px-4 py-3 text-sm text-emerald-700 dark:text-emerald-400">
          <Check className="h-4 w-4 flex-none" />
          {testDetail}
        </div>
      )}

      {testState === "err" && (
        <div className="space-y-2 rounded-lg border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive">
          <p>{testDetail || "Connection failed. Please check your configuration."}</p>
          {testSteps.length > 0 && (
            <ol className="ml-1 list-decimal space-y-1.5 pl-4 text-foreground/90">
              {testSteps.map((step, i) => (
                <li key={i} className="break-words">{step}</li>
              ))}
            </ol>
          )}
        </div>
      )}

      {/* (Hermes auto-setup button + OS install steps are now grouped directly
          under the Hermes card above.) */}

      <div className="flex gap-2 pt-1">
        <Button
          variant="ghost"
          size="sm"
          className="gap-1 text-muted-foreground"
          onClick={onBack}
        >
          <ChevronLeft className="h-4 w-4" />
          Back
        </Button>
        <Button
          variant="outline"
          className="gap-2"
          disabled={testState === "loading"}
          onClick={handleTest}
        >
          {testState === "loading" ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <Wifi className="h-4 w-4" />
          )}
          Test
        </Button>
        <Button
          variant={testState === "ok" ? "accent" : "outline"}
          className="flex-1 gap-1"
          disabled={saveMutation.isPending}
          onClick={handleContinue}
        >
          {saveMutation.isPending ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : testState === "ok" ? (
            <>Continue <ChevronRight className="h-4 w-4" /></>
          ) : (
            "Skip →"
          )}
        </Button>
      </div>
    </div>
  );
}

// ── Step 3: Bridge ────────────────────────────────────────────────────────

const BRIDGE_HELP: Record<string, string> = {
  whatsapp: "Connect your phone by scanning a QR code — no developer account needed. Uses the WhatsApp Web protocol.",
  telegram: "Message @BotFather on Telegram to create a bot and get your token. Free and instant.",
  discord: "Create a bot at discord.com/developers. You need the Bot Token and Application ID from the portal.",
  slack: "Create a Slack app at api.slack.com. You need a Bot OAuth token (xoxb-…) and a Signing Secret.",
  signal: "Requires signal-cli installed and your phone number linked. The most private option — end-to-end encrypted.",
  email: "Corvin monitors an IMAP inbox and replies via SMTP. Use a dedicated email address for best results.",
};
// NOTE: only channels with a real backend setup guide in routes/setup.py
// (_BRIDGE_GUIDES) belong here — selecting a channel without a guide makes
// GET /setup/bridge/<channel> return 404 and breaks first-run onboarding.
// Supported during onboarding: whatsapp, telegram, discord, slack, signal, email.

const BRIDGE_OPTIONS = [
  {
    id: "whatsapp",
    label: "WhatsApp",
    desc: "Connect phone via QR code",
    icon: MessageCircle,
    color: "text-emerald-500 bg-emerald-500/10",
  },
  {
    id: "telegram",
    label: "Telegram",
    desc: "Create bot via @BotFather",
    icon: Send,
    color: "text-sky-500 bg-sky-500/10",
  },
  {
    id: "discord",
    label: "Discord",
    desc: "Bot token + application ID",
    icon: MessageSquare,
    color: "text-violet-500 bg-violet-500/10",
  },
  {
    id: "slack",
    label: "Slack",
    desc: "Bot OAuth token + signing secret",
    icon: Hash,
    color: "text-orange-500 bg-orange-500/10",
  },
  {
    id: "signal",
    label: "Signal",
    desc: "Via signal-cli (local socket)",
    icon: Lock,
    color: "text-blue-500 bg-blue-500/10",
  },
  {
    id: "email",
    label: "E-Mail",
    desc: "IMAP fetch + SMTP send",
    icon: Mail,
    color: "text-muted-foreground bg-muted/50",
  },
] as const;

// Mobile setup URL — shown as QR code for each bridge channel
const CHANNEL_MOBILE_URL: Record<string, { url: string; hint: string }> = {
  telegram: {
    url: "https://t.me/BotFather",
    hint: "Scan to open @BotFather in Telegram and create your bot.",
  },
  discord: {
    url: "https://discord.com/developers/applications",
    hint: "Scan to open the Discord Developer Portal.",
  },
  slack: {
    url: "https://api.slack.com/apps",
    hint: "Scan to open the Slack App Management page.",
  },
  email: {
    url: "https://myaccount.google.com/apppasswords",
    hint: "Scan to create a Google App Password on your phone.",
  },
  signal: {
    url: "https://signal.org/download/",
    hint: "Scan to download Signal on your phone.",
  },
};

// ── Bridge guide + optional QR panel ─────────────────────────────────────

function BridgeGuidePanel({ channel }: { channel: string }) {
  const { session } = useAuth();
  const csrf = session?.csrf_token ?? "";
  const query = useQuery({
    queryKey: ["bridge-setup", channel],
    queryFn: ({ signal }) => getBridgeSetup(channel, signal),
    staleTime: 60_000,
    refetchInterval: channel === "whatsapp" ? 5_000 : false,
  });

  const [waLog, setWaLog] = React.useState<string[]>([]);
  const [waError, setWaError] = React.useState<WhatsappStartResult | null>(null);
  const [waStarted, setWaStarted] = React.useState(false);
  const pushPhase = React.useCallback((p: string) => {
    setWaLog((prev) => (prev[prev.length - 1] === p ? prev : [...prev, p]));
  }, []);
  const startWaMut = useMutation({
    mutationFn: () => startWhatsappBridge(csrf, pushPhase),
    onSuccess: (r) => {
      setWaError(r.ok ? null : r);
      setWaStarted(r.ok);
      void query.refetch();
    },
    onError: () => setWaError({ ok: false, error: "Failed to start the WhatsApp bridge." }),
  });
  const startWa = () => { setWaError(null); setWaStarted(false); setWaLog([]); startWaMut.mutate(); };

  if (query.isLoading) return <Skeleton className="h-28 w-full rounded-xl" />;
  if (!query.data) return null;

  const { guide, qr_available, qr_url, configured } = query.data;

  return (
    <div className="rounded-xl border border-accent/25 bg-accent/4 p-4 space-y-3">
      {/* WhatsApp session QR + one-click start */}
      {channel === "whatsapp" && (
        <div className="flex flex-col items-center gap-3 pb-1">
          {!qr_available && configured ? (
            <div className="flex w-full items-center gap-3 rounded-xl border border-emerald-500/30 bg-emerald-500/5 px-4 py-3">
              <CheckCircle2 className="h-6 w-6 flex-none text-emerald-500" />
              <div className="min-w-0">
                <p className="text-sm font-medium text-emerald-600">WhatsApp is already linked</p>
                <p className="text-[11px] text-muted-foreground">
                  No QR needed — this device is connected. To link a different phone, use
                  <span className="font-mono"> Settings → Bridges → WhatsApp → Re-link</span>.
                </p>
              </div>
            </div>
          ) : qr_available && qr_url ? (
            <>
              <div className="rounded-xl border-2 border-border bg-white p-3 shadow-inner">
                <img
                  src={`${qr_url}?t=${Date.now()}`}
                  alt="WhatsApp QR code"
                  className="h-44 w-44 rounded-lg"
                />
              </div>
              <div className="flex items-center gap-2 text-xs text-muted-foreground">
                <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-amber-400" />
                Waiting for you to scan…
              </div>
            </>
          ) : (
            <div className="flex w-full flex-col items-center gap-3">
              <div className="flex h-32 w-32 flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed border-border bg-muted/30">
                {startWaMut.isPending || waStarted ? (
                  <Loader2 className="h-8 w-8 animate-spin text-accent/50" />
                ) : (
                  <QrCode className="h-9 w-9 text-muted-foreground/30" />
                )}
                <p className="text-center text-[10px] text-muted-foreground px-2">
                  {startWaMut.isPending ? "Setting up…" : waStarted ? "Waiting for QR…" : "QR appears once the bridge runs"}
                </p>
              </div>

              {!startWaMut.isPending && !waStarted && !waError && (
                <Button variant="accent" size="sm" className="gap-2" onClick={startWa}>
                  <MessageCircle className="h-4 w-4" /> Start WhatsApp bridge
                </Button>
              )}

              {/* Phase log — persistent, shows every step so the user sees progress */}
              {(startWaMut.isPending || waStarted) && (
                <div className="w-full rounded-lg border border-accent/25 bg-accent/5 px-3 py-2 space-y-1">
                  <p className="text-[10px] font-semibold uppercase tracking-wide text-accent">Progress</p>
                  {waLog.map((p, i) => {
                    const done = i < waLog.length - 1 || (waStarted && !startWaMut.isPending);
                    return (
                      <p key={i} className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
                        {done ? (
                          <CheckCircle2 className="h-3 w-3 flex-none text-emerald-500" />
                        ) : (
                          <Loader2 className="h-3 w-3 flex-none animate-spin text-accent" />
                        )}
                        {p}
                      </p>
                    );
                  })}
                  {waStarted && !startWaMut.isPending && (
                    <p className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
                      <Loader2 className="h-3 w-3 flex-none animate-spin text-accent" />
                      Waiting for WhatsApp to generate the QR (a few seconds)…
                    </p>
                  )}
                  <p className="pt-1 text-[10px] text-muted-foreground/70">
                    First run downloads Node.js (~25&nbsp;MB) + WhatsApp dependencies — up to 1–2&nbsp;minutes.
                  </p>
                </div>
              )}

              {/* Error — persistent, with the real cause + retry */}
              {waError && (
                <div className="w-full space-y-2 rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-[11px] text-muted-foreground">
                  <p className="font-medium text-destructive">{waError.error}</p>
                  {waError.node_steps && (
                    <ol className="list-decimal space-y-1 pl-4">
                      {waError.node_steps.steps.map((s, i) => <li key={i}>{s}</li>)}
                    </ol>
                  )}
                  <Button variant="outline" size="sm" className="gap-1.5" onClick={startWa}>
                    <Loader2 className="h-3.5 w-3.5" /> Retry
                  </Button>
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* Setup portal QR — all non-WhatsApp channels */}
      {channel !== "whatsapp" && CHANNEL_MOBILE_URL[channel] && (
        <div className="flex items-center gap-3 rounded-lg border border-border/50 bg-background/60 p-3">
          <div className="flex-none rounded-lg border border-border bg-white p-1.5 shadow-sm">
            <QRCodeSVG
              value={CHANNEL_MOBILE_URL[channel].url}
              size={72}
              bgColor="#ffffff"
              fgColor="#0f172a"
              level="M"
            />
          </div>
          <div className="min-w-0 space-y-0.5">
            <p className="text-xs font-medium">Open on your phone</p>
            <p className="text-[11px] text-muted-foreground leading-snug">
              {CHANNEL_MOBILE_URL[channel].hint}
            </p>
          </div>
        </div>
      )}

      {/* Step list */}
      <div className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-accent">
        <BookOpen className="h-3 w-3" />
        Setup steps
      </div>
      <ol className="space-y-2.5">
        {guide.steps.map((step: string, i: number) => (
          <li key={i} className="flex gap-2.5 text-xs text-muted-foreground">
            <span className="flex h-4 w-4 flex-none items-center justify-center rounded-full bg-accent/15 text-[9px] font-bold text-accent mt-px">
              {i + 1}
            </span>
            <span
              className="leading-relaxed"
              dangerouslySetInnerHTML={{
                __html: step
                  .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
                  .replace(
                    /`(.+?)`/g,
                    "<code class='font-mono bg-muted/80 px-1 rounded text-[10px]'>$1</code>",
                  ),
              }}
            />
          </li>
        ))}
      </ol>

      {/* Portal link */}
      {guide.setup_url && (
        <a
          href={guide.setup_url}
          target="_blank"
          rel="noreferrer"
          className="flex items-center gap-1.5 text-xs text-accent hover:underline pt-0.5"
        >
          <ExternalLink className="h-3 w-3 flex-none" />
          Open {guide.display} portal
        </a>
      )}
    </div>
  );
}

// ── Step 3: Bridge ────────────────────────────────────────────────────────

function BridgeStep({
  onNext,
  onBack,
  onSkip,
  configuredBridges,
}: {
  onNext: (chosen: string | null) => void;
  onBack: () => void;
  onSkip: () => void;
  configuredBridges: string[];
}) {
  const [chosen, setChosen] = React.useState<string | null>(null);
  const alreadyConfigured = configuredBridges.length > 0;

  return (
    <div className="flex flex-col gap-5">
      <div className="flex items-center gap-3">
        <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-accent/15">
          <MessageCircle className="h-4.5 w-4.5 text-accent" />
        </div>
        <div>
          <div className="flex items-center gap-1.5">
            <h3 className="font-medium">Choose first channel</h3>
            <HelpTooltip title="What is a channel?" side="right" width="lg">
              A channel is the messaging platform your AI assistant connects to. Users send messages to a bot there, and Corvin responds through the same app.
              <br /><br />
              You can connect multiple channels at any time in the <strong>Bridges</strong> section. This step is optional.
            </HelpTooltip>
          </div>
          <p className="text-xs text-muted-foreground">
            Optional — you can also do this later
          </p>
        </div>
      </div>

      {alreadyConfigured ? (
        <div className="flex items-center gap-2 rounded-lg border border-emerald-500/30 bg-emerald-500/10 px-4 py-3 text-sm text-emerald-700 dark:text-emerald-400">
          <Check className="h-4 w-4 flex-none" />
          {configuredBridges.join(", ")} already configured.
        </div>
      ) : (
        <>
          <div className="grid gap-2">
            {BRIDGE_OPTIONS.map((opt) => {
              const Icon = opt.icon;
              const isSelected = chosen === opt.id;
              return (
                <button
                  key={opt.id}
                  onClick={() => setChosen(isSelected ? null : opt.id)}
                  className={cn(
                    "flex items-center gap-3 rounded-xl border px-4 py-3 text-left transition-all",
                    isSelected
                      ? "border-accent bg-accent/10"
                      : "border-border bg-card hover:border-accent/40 hover:bg-muted/40",
                  )}
                >
                  <div className={cn("flex h-8 w-8 items-center justify-center rounded-lg", opt.color)}>
                    <Icon className="h-4 w-4" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="text-sm font-medium">{opt.label}</div>
                    <div className="text-xs text-muted-foreground">{opt.desc}</div>
                  </div>
                  {isSelected ? (
                    <Check className="h-4 w-4 text-accent flex-none" />
                  ) : (
                    <div onClick={(e) => e.stopPropagation()} className="flex-none">
                      <HelpTooltip title={opt.label} side="left" width="sm">
                        {BRIDGE_HELP[opt.id] ?? opt.desc}
                      </HelpTooltip>
                    </div>
                  )}
                </button>
              );
            })}
          </div>
          {chosen && <BridgeGuidePanel channel={chosen} />}
        </>
      )}

      <div className="flex gap-2 pt-1">
        <Button
          variant="ghost"
          size="sm"
          className="gap-1 text-muted-foreground"
          onClick={onBack}
        >
          <ChevronLeft className="h-4 w-4" />
          Back
        </Button>
        <Button variant="ghost" size="sm" className="text-muted-foreground" onClick={onSkip}>
          Skip
        </Button>
        <Button
          variant="accent"
          className="flex-1 gap-1"
          onClick={() => (alreadyConfigured ? onSkip() : onNext(chosen))}
        >
          Continue
          <ChevronRight className="h-4 w-4" />
        </Button>
      </div>
    </div>
  );
}

// ── Step 4: Done ──────────────────────────────────────────────────────────

function DoneStep({
  engineConnected,
  bridges,
  onBack,
  onFinish,
}: {
  engineConnected: boolean;
  bridges: string[];
  onBack: () => void;
  onFinish: () => void;
}) {
  return (
    <div className="flex flex-col items-center gap-6 py-4 text-center">
      <div className="flex h-16 w-16 items-center justify-center rounded-full bg-emerald-500/15">
        <Check className="h-8 w-8 text-emerald-500" />
      </div>
      <div className="space-y-1.5">
        <h3 className="font-serif text-2xl font-light">Corvin is online</h3>
        <p className="text-sm text-muted-foreground">
          Your system is ready.
        </p>
      </div>

      <div className="w-full space-y-2 text-left">
        <StatusRow
          label="Engine"
          value={engineConnected ? "Connected" : "Not connected"}
          ok={engineConnected}
        />
        <StatusRow
          label="Channels"
          value={bridges.length > 0 ? bridges.join(", ") : "None configured yet"}
          ok={bridges.length > 0}
          neutral={bridges.length === 0}
        />
      </div>

      <div className="flex w-full gap-2">
        <Button
          variant="ghost"
          size="sm"
          className="gap-1 text-muted-foreground"
          onClick={onBack}
        >
          <ChevronLeft className="h-4 w-4" />
          Back
        </Button>
        <Button variant="accent" size="lg" className="flex-1" onClick={onFinish}>
          Open dashboard
        </Button>
      </div>
    </div>
  );
}

function StatusRow({
  label,
  value,
  ok,
  neutral,
}: {
  label: string;
  value: string;
  ok: boolean;
  neutral?: boolean;
}) {
  return (
    <div className="flex items-center justify-between rounded-lg border border-border bg-muted/30 px-4 py-2.5 text-sm">
      <span className="text-muted-foreground">{label}</span>
      <div className="flex items-center gap-1.5">
        <span className="font-medium">{value}</span>
        <div
          className={cn(
            "h-2 w-2 rounded-full",
            ok && "bg-emerald-500",
            neutral && "bg-muted-foreground/40",
            !ok && !neutral && "bg-amber-500",
          )}
        />
      </div>
    </div>
  );
}

// ── SetupGate root ────────────────────────────────────────────────────────

export function SetupGate() {
  const { status, session } = useAuth();

  // The server (GET /setup/status) is the single source of truth. A prior
  // version cached its answer in localStorage and, worse, used the cached
  // value to DISABLE the query entirely — so a "corvin_setup_complete=1"
  // left over from a previous install permanently skipped onboarding in
  // that browser, even after a full uninstall+reinstall wiped the server
  // state (there is no way for a server-side uninstall to reach into a
  // browser's localStorage). The query now always runs while authenticated;
  // nothing here second-guesses its result.
  const statusQuery = useQuery({
    queryKey: ["setup-status"],
    queryFn: ({ signal }) => getSetupStatus(signal),
    enabled: status === "authenticated",
    staleTime: 60_000,
    retry: 1,
  });

  const queryClient = useQueryClient();
  const completeMutation = useMutation({
    mutationFn: () => postSetupComplete(session?.csrf_token ?? ""),
    onSuccess: () => {
      // Re-fetch rather than trust a local flag — the server just recorded
      // completion, so its next answer for THIS query key already reflects
      // that; no need for a second, independent "am I done" cache.
      queryClient.invalidateQueries({ queryKey: ["setup-status"] });
    },
  });

  const [step, setStep] = React.useState<Step>("welcome");

  // Not authenticated → render nothing
  if (status !== "authenticated") return null;
  // Still loading the status check, or it errored → render nothing (no flicker)
  if (statusQuery.isLoading || statusQuery.isError) return null;
  // Server says already complete → render nothing
  if (statusQuery.data?.setup_complete) return null;

  const engineConnected = statusQuery.data?.engine_connected ?? false;
  const bridges = statusQuery.data?.bridges_configured ?? [];

  const finish = () => completeMutation.mutate();

  return (
    <div
      className="fixed inset-0 z-50 flex flex-col items-center justify-start overflow-y-auto corvin-hero"
      style={{ background: "var(--tw-bg-opacity)" }}
    >
      {/* Gradient backdrop */}
      <div className="pointer-events-none fixed inset-0 bg-background/92 backdrop-blur-md" />

      {/* Card */}
      <div className="relative z-10 mx-auto mt-[8vh] w-full max-w-md px-4 pb-16">
        <div className="overflow-hidden rounded-2xl border border-border bg-card shadow-2xl shadow-black/20">
          {/* Header bar */}
          <div className="flex items-center justify-between border-b border-border px-6 py-4">
            <div className="flex items-center gap-2">
              <CorvinMark className="h-5 w-5 text-accent" />
              <span className="text-sm font-medium">Setup</span>
            </div>
            {step !== "welcome" && <StepDots current={step} />}
          </div>

          {/* Step content */}
          <div className="px-6 py-6">
            {step === "welcome" && (
              <WelcomeStep onNext={() => setStep("engine")} csrf={session?.csrf_token ?? ""} />
            )}
            {step === "engine" && (
              <EngineStep
                onNext={() => setStep("bridge")}
                onBack={() => setStep("welcome")}
                engineConnected={engineConnected}
                csrf={session?.csrf_token ?? ""}
              />
            )}
            {step === "bridge" && (
              <BridgeStep
                onNext={() => setStep("done")}
                onBack={() => setStep("engine")}
                onSkip={() => setStep("done")}
                configuredBridges={bridges}
              />
            )}
            {step === "done" && (
              <DoneStep
                engineConnected={engineConnected}
                bridges={bridges}
                onBack={() => setStep("bridge")}
                onFinish={finish}
              />
            )}
          </div>
        </div>

        {/* Fine print */}
        <p className="mt-4 text-center text-xs text-muted-foreground/60">
          Apache-2.0 · EU AI Act 2026 · GDPR-aligned
        </p>
      </div>
    </div>
  );
}

// ── Inline CorvinMark (avoids importing from layout) ─────────────────────

function CorvinMark({ className }: { className?: string }) {
  return (
    <svg viewBox="12 12 96 96" aria-hidden="true" className={cn("text-foreground", className)}>
      <path fill="none" stroke="currentColor" strokeWidth="8" strokeLinecap="round" strokeLinejoin="round" d="M28 40 L56 60 L28 80"/>
      <rect fill="currentColor" x="66" y="72" width="30" height="9" rx="2"/>
      <circle cx="80" cy="50" r="10" fill="#C9A227"/>
      <circle cx="80" cy="50" r="10" fill="none" stroke="currentColor" strokeWidth="2"/>
    </svg>
  );
}
