import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { QRCodeSVG } from "qrcode.react";
import {
  AlertTriangle,
  BookOpen,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Eye,
  EyeOff,
  ExternalLink,
  Hash,
  HelpCircle,
  Info,
  Loader2,
  Lock,
  Mail,
  MessageCircle,
  Network,
  Plus,
  Power,
  PowerOff,
  QrCode,
  Save,
  Send,
  Settings2,
  Sliders,
  Trash2,
  Unplug,
  X,
  XCircle,
  Zap,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import { ReauthDialog } from "@/components/reauth-dialog";
import { CommandsHelpModal } from "@/components/commands-help-modal";
import {
  getBridgeSettings,
  getBridgeSetup,
  startWhatsappBridge,
  type WhatsappStartResult,
  getCommands,
  listBridges,
  listWebhookChannels,
  putBridgeSettings,
  setBridgeEnabled,
  disconnectBridge,
  type BridgeListItem,
} from "@/lib/api";
import {
  Card,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";
import { HelpTooltip } from "@/components/ui/help-tooltip";

// ── Channel metadata ──────────────────────────────────────────────────────

interface ChannelMeta {
  label: string;
  hint: string;
  icon: React.ComponentType<{ className?: string }>;
  color: string;
  isQrBased?: boolean;
  setupUrl?: string;
  // Deep-link for mobile (renders as QR in the wizard)
  mobileSetupUrl?: string;
  // Shown on the tile before the user clicks Connect
  requirements: string[];
  timeMinutes: number;
}

const CHANNEL_META: Record<string, ChannelMeta> = {
  telegram: {
    label: "Telegram",
    hint: "Bot token from @BotFather. Whitelist = numeric user IDs as strings.",
    icon: Send,
    color: "text-sky-500 bg-sky-500/10",
    setupUrl: "https://t.me/BotFather",
    mobileSetupUrl: "https://t.me/BotFather",
    requirements: ["Telegram account", "No server needed"],
    timeMinutes: 3,
  },
  discord: {
    label: "Discord",
    hint: "Bot token + application ID. Slash commands register on clientReady.",
    icon: Zap,
    color: "text-indigo-500 bg-indigo-500/10",
    setupUrl: "https://discord.com/developers/applications",
    // No mobileSetupUrl — Discord developer portal is desktop-only
    requirements: ["Discord account", "Server admin access"],
    timeMinutes: 5,
  },
  slack: {
    label: "Slack",
    hint: "Bot user OAuth token + signing secret. Whitelist by Slack user ID.",
    icon: Hash,
    color: "text-amber-500 bg-amber-500/10",
    setupUrl: "https://api.slack.com/apps",
    // No mobileSetupUrl — Slack API portal is desktop-only
    requirements: ["Slack workspace admin"],
    timeMinutes: 8,
  },
  whatsapp: {
    label: "WhatsApp",
    hint: "WhatsApp Web session via QR code — no token needed.",
    icon: MessageCircle,
    color: "text-emerald-500 bg-emerald-500/10",
    isQrBased: true,
    requirements: ["WhatsApp on your phone", "No token needed"],
    timeMinutes: 2,
  },
  email: {
    label: "E-Mail",
    hint: "IMAP fetch + SMTP send. App-passwords work better than OAuth.",
    icon: Mail,
    color: "text-slate-500 bg-slate-500/10",
    setupUrl: "https://myaccount.google.com/apppasswords",
    mobileSetupUrl: "https://myaccount.google.com/apppasswords",
    requirements: ["Gmail or IMAP account", "App password (not main password)"],
    timeMinutes: 5,
  },
  signal: {
    label: "Signal",
    hint: "Uses signal-cli over the local socket. Requires linked device.",
    icon: Lock,
    color: "text-teal-500 bg-teal-500/10",
    setupUrl: "https://github.com/bbernhard/signal-cli-rest-api",
    // No mobileSetupUrl — signal-cli is a server-side tool, cannot be set up from the Signal phone app
    requirements: ["Signal account", "signal-cli installed"],
    timeMinutes: 10,
  },
  teams: {
    label: "Teams",
    hint: "Microsoft Bot Framework webhook. App ID + password.",
    icon: Network,
    color: "text-violet-500 bg-violet-500/10",
    setupUrl:
      "https://portal.azure.com/#create/Microsoft.BotServiceConnectivityGatewayContent",
    // No mobileSetupUrl — Azure portal setup cannot be done from mobile
    requirements: ["Microsoft Azure account", "Teams admin access"],
    timeMinutes: 15,
  },
};

// ── Per-bridge field schemas ───────────────────────────────────────────────

type FieldType = "secret" | "text" | "number" | "boolean" | "array" | "select";

interface FieldSchema {
  key: string;
  label: string;
  type: FieldType;
  placeholder?: string;
  hint?: string;
  options?: string[];
  min?: number;
  max?: number;
}

const BRIDGE_FIELDS: Record<string, FieldSchema[]> = {
  telegram: [
    {
      key: "telegram_token",
      label: "Bot Token",
      type: "secret",
      placeholder: "123456789:ABCdef…",
    },
    {
      key: "whitelist",
      label: "Whitelist",
      type: "array",
      hint: "Telegram user IDs (numeric). Empty = open to anyone.",
    },
    {
      key: "read_only",
      label: "Read-only users",
      type: "array",
      hint: "Can read replies but cannot trigger the bot.",
    },
    {
      key: "rate_limit_per_hour",
      label: "Rate limit / hour",
      type: "number",
      min: 0,
      max: 9999,
      hint: "Per-user inbound rate cap. 0 = unlimited.",
    },
    {
      key: "pin",
      label: "PIN (optional)",
      type: "secret",
      hint: "Shared secret for /pin elevation.",
    },
  ],
  discord: [
    {
      key: "discord_token",
      label: "Bot Token",
      type: "secret",
      placeholder: "Paste your Discord bot token…",
    },
    {
      key: "whitelist",
      label: "Whitelist",
      type: "array",
      hint: "Discord user IDs. Empty = open to anyone.",
    },
    {
      key: "read_only",
      label: "Read-only users",
      type: "array",
      hint: "Can read replies but cannot trigger the bot.",
    },
    {
      key: "rate_limit_per_hour",
      label: "Rate limit / hour",
      type: "number",
      min: 0,
      max: 9999,
    },
    {
      key: "pin",
      label: "PIN (optional)",
      type: "secret",
      hint: "Shared secret for /pin elevation.",
    },
  ],
  slack: [
    {
      key: "slack_bot_token",
      label: "Bot Token (xoxb-…)",
      type: "secret",
      placeholder: "xoxb-…",
    },
    {
      key: "slack_signing_secret",
      label: "Signing Secret",
      type: "secret",
      placeholder: "Your Slack app signing secret",
    },
    {
      key: "whitelist",
      label: "Whitelist",
      type: "array",
      hint: "Slack user IDs (Uxxxxxxxxxx). Empty = open to anyone.",
    },
    { key: "read_only", label: "Read-only users", type: "array" },
    {
      key: "rate_limit_per_hour",
      label: "Rate limit / hour",
      type: "number",
      min: 0,
      max: 9999,
    },
    { key: "pin", label: "PIN (optional)", type: "secret" },
  ],
  whatsapp: [
    {
      key: "whitelist",
      label: "Whitelist",
      type: "array",
      hint: "Phone numbers or chat IDs. Empty = open to anyone.",
    },
    {
      key: "always_voice",
      label: "Always voice",
      type: "boolean",
      hint: "Convert all replies to voice notes automatically.",
    },
    {
      key: "voice_threshold_chars",
      label: "Voice threshold (chars)",
      type: "number",
      min: 0,
      hint: "Replies longer than this will be sent as voice.",
    },
    {
      key: "voice_summary_mode",
      label: "Voice summary mode",
      type: "select",
      options: ["always", "auto", "never"],
      hint: "When to generate a text summary alongside voice notes.",
    },
    {
      key: "rate_limit_per_hour",
      label: "Rate limit / hour",
      type: "number",
      min: 0,
      max: 9999,
    },
    { key: "pin", label: "PIN (optional)", type: "secret" },
  ],
  email: [
    {
      key: "whitelist",
      label: "Whitelist",
      type: "array",
      hint: "Email addresses allowed to contact the bot. Empty = open.",
    },
    {
      key: "rate_limit_per_hour",
      label: "Rate limit / hour",
      type: "number",
      min: 0,
      max: 9999,
    },
    { key: "pin", label: "PIN (optional)", type: "secret" },
  ],
  signal: [
    {
      key: "signal_phone_number",
      label: "Phone number (+49…)",
      type: "text",
      placeholder: "+49123456789",
    },
    {
      key: "whitelist",
      label: "Whitelist",
      type: "array",
      hint: "Signal phone numbers allowed. Empty = open.",
    },
    { key: "read_only", label: "Read-only users", type: "array" },
    {
      key: "rate_limit_per_hour",
      label: "Rate limit / hour",
      type: "number",
      min: 0,
      max: 9999,
    },
    { key: "pin", label: "PIN (optional)", type: "secret" },
  ],
  teams: [
    {
      key: "teams_app_id",
      label: "App ID",
      type: "text",
      placeholder: "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
    },
    { key: "teams_app_password", label: "App Password", type: "secret" },
    {
      key: "whitelist",
      label: "Whitelist",
      type: "array",
      hint: "Teams user IDs or emails. Empty = open.",
    },
    {
      key: "rate_limit_per_hour",
      label: "Rate limit / hour",
      type: "number",
      min: 0,
      max: 9999,
    },
    { key: "pin", label: "PIN (optional)", type: "secret" },
  ],
};

// Keys that are shown in the structured form — everything else goes to Advanced JSON
function knownKeys(channel: string): Set<string> {
  return new Set((BRIDGE_FIELDS[channel] ?? []).map((f) => f.key));
}

// ── Small helpers ─────────────────────────────────────────────────────────

function SecretInput({
  value,
  onChange,
  placeholder,
  autoFocus,
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  autoFocus?: boolean;
}) {
  const [show, setShow] = React.useState(false);
  return (
    <div className="relative">
      <Input
        type={show ? "text" : "password"}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder ?? "••••••••"}
        className="font-mono text-sm pr-9"
        autoFocus={autoFocus}
        autoComplete="off"
      />
      <button
        type="button"
        className="absolute right-2.5 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
        onClick={() => setShow((v) => !v)}
        tabIndex={-1}
      >
        {show ? (
          <EyeOff className="h-3.5 w-3.5" />
        ) : (
          <Eye className="h-3.5 w-3.5" />
        )}
      </button>
    </div>
  );
}

function ArrayEditor({
  value,
  onChange,
  placeholder,
}: {
  value: string[];
  onChange: (v: string[]) => void;
  placeholder?: string;
}) {
  const [draft, setDraft] = React.useState("");

  const add = () => {
    const trimmed = draft.trim();
    if (!trimmed || value.includes(trimmed)) return;
    onChange([...value, trimmed]);
    setDraft("");
  };

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-1.5 min-h-[28px]">
        {value.map((item) => (
          <span
            key={item}
            className="flex items-center gap-1 rounded-md border border-border bg-muted/50 px-2 py-0.5 text-xs font-mono"
          >
            {item}
            <button
              type="button"
              className="ml-0.5 text-muted-foreground hover:text-destructive"
              onClick={() => onChange(value.filter((v) => v !== item))}
            >
              <X className="h-3 w-3" />
            </button>
          </span>
        ))}
        {value.length === 0 && (
          <span className="text-xs text-muted-foreground/60 py-0.5">
            (empty — open to anyone)
          </span>
        )}
      </div>
      <div className="flex gap-2">
        <Input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder={placeholder ?? "Add an ID…"}
          className="font-mono text-xs h-8"
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              add();
            }
          }}
        />
        <Button
          type="button"
          size="sm"
          variant="outline"
          className="h-8 gap-1"
          onClick={add}
        >
          <Plus className="h-3.5 w-3.5" />
          Add
        </Button>
      </div>
    </div>
  );
}

// ── Setup QR card ─────────────────────────────────────────────────────────

function SetupQrCard({
  url,
  label,
  hint,
}: {
  url: string;
  label: string;
  hint?: string;
}) {
  return (
    <div className="flex items-center gap-4 rounded-xl border border-border/60 bg-muted/20 p-4">
      <div className="rounded-xl border-2 border-border bg-white p-2.5 shadow-sm flex-none">
        <QRCodeSVG
          value={url}
          size={88}
          bgColor="#ffffff"
          fgColor="#0f172a"
          level="M"
        />
      </div>
      <div className="space-y-1 min-w-0">
        <p className="text-sm font-medium">Open {label} on your phone</p>
        {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
        <a
          href={url}
          target="_blank"
          rel="noreferrer"
          className="flex items-center gap-1 text-xs text-accent hover:underline break-all"
        >
          <ExternalLink className="h-3 w-3 flex-none" />
          {url}
        </a>
      </div>
    </div>
  );
}

// ── WhatsApp QR panel ─────────────────────────────────────────────────────

function WhatsAppQrPanel({ onDone }: { onDone: () => void }) {
  const { session } = useAuth();
  const csrf = session?.csrf_token ?? "";
  const query = useQuery({
    queryKey: ["bridge-setup", "whatsapp"],
    queryFn: ({ signal }) => getBridgeSetup("whatsapp", signal),
    refetchInterval: 3_000,
    staleTime: 2_000,
  });

  const qrUrl = query.data?.qr_url;
  const qrAvailable = query.data?.qr_available ?? false;
  const configured = query.data?.configured ?? false;

  const [waPhase, setWaPhase] = React.useState("");
  const [waError, setWaError] = React.useState<WhatsappStartResult | null>(null);
  const startWaMut = useMutation({
    mutationFn: () => startWhatsappBridge(csrf, setWaPhase),
    onSuccess: (r) => { setWaPhase(""); setWaError(r.ok ? null : r); void query.refetch(); },
    onError: () => setWaError({ ok: false, error: "Failed to start the WhatsApp bridge." }),
  });

  React.useEffect(() => {
    if (configured) onDone();
  }, [configured, onDone]);

  return (
    <div className="flex flex-col items-center gap-5 py-2">
      {qrAvailable && qrUrl ? (
        <div className="rounded-2xl border-2 border-border bg-white p-4 shadow-inner">
          <img
            src={`${qrUrl}?t=${Date.now()}`}
            alt="WhatsApp QR code"
            className="h-56 w-56 rounded-lg"
          />
        </div>
      ) : configured ? (
        <div className="flex w-full max-w-sm items-center gap-3 rounded-xl border border-emerald-500/30 bg-emerald-500/5 px-4 py-3">
          <CheckCircle2 className="h-6 w-6 flex-none text-emerald-500" />
          <div className="min-w-0">
            <p className="text-sm font-medium text-emerald-600">WhatsApp is already linked</p>
            <p className="text-[11px] text-muted-foreground">
              No QR needed — this device is connected. Use <strong>Re-link</strong> below only to
              switch to a different phone.
            </p>
          </div>
        </div>
      ) : (
        <div className="flex flex-col items-center gap-3">
          <div className="flex h-56 w-56 flex-col items-center justify-center gap-3 rounded-2xl border-2 border-dashed border-border bg-muted/30">
            {startWaMut.isPending ? (
              <Loader2 className="h-8 w-8 animate-spin text-muted-foreground/40" />
            ) : (
              <QrCode className="h-8 w-8 text-muted-foreground/30" />
            )}
            <p className="text-center text-xs text-muted-foreground px-4">
              {startWaMut.isPending ? (waPhase || "Bringing the bridge up…") : "QR appears once the bridge is running"}
            </p>
          </div>
          <Button
            variant="accent"
            size="sm"
            className="gap-2"
            disabled={startWaMut.isPending}
            onClick={() => { setWaError(null); startWaMut.mutate(); }}
          >
            {startWaMut.isPending ? (
              <><Loader2 className="h-4 w-4 animate-spin" /> Starting…</>
            ) : (
              <><MessageCircle className="h-4 w-4" /> Start WhatsApp bridge</>
            )}
          </Button>
          {waError && (
            <div className="w-full max-w-xs rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-[11px] text-muted-foreground">
              <p className="text-destructive">{waError.error}</p>
              {waError.node_steps && (
                <ol className="mt-1.5 space-y-1 list-decimal pl-4">
                  {waError.node_steps.steps.map((s, i) => <li key={i}>{s}</li>)}
                </ol>
              )}
            </div>
          )}
        </div>
      )}

      {qrAvailable && (
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-amber-400" />
          Waiting for scan…
        </div>
      )}

      <ol className="w-full space-y-2">
        {[
          "Open WhatsApp on your phone",
          "Settings → Linked Devices → Link a Device",
          "Scan the QR code above",
        ].map((step, i) => (
          <li
            key={i}
            className="flex items-center gap-3 text-sm text-muted-foreground"
          >
            <span className="flex h-5 w-5 flex-none items-center justify-center rounded-full bg-accent/15 text-[10px] font-bold text-accent">
              {i + 1}
            </span>
            {step}
          </li>
        ))}
      </ol>

      <Button
        variant="ghost"
        size="sm"
        className="gap-1.5 text-muted-foreground"
        onClick={() => query.refetch()}
      >
        <Loader2 className="h-3.5 w-3.5" /> Refresh QR
      </Button>
    </div>
  );
}

// ── Bridge tile ───────────────────────────────────────────────────────────

function BridgeTile({
  bridge,
  onConnect,
  onManage,
}: {
  bridge: BridgeListItem;
  onConnect: () => void;
  onManage: () => void;
}) {
  const meta = CHANNEL_META[bridge.channel] ?? {
    label: bridge.channel,
    hint: "",
    icon: Network,
    color: "text-muted-foreground bg-muted",
    requirements: [],
    timeMinutes: 5,
  };
  const Icon = meta.icon;
  // Disconnected, but a configuration is still on disk (whitelist, PIN, ...).
  // That is a different state from "never set up" and gets its own affordances:
  // reconnecting keeps the settings, and Manage stays reachable so the leftover
  // configuration can still be deleted.
  const leftovers = !bridge.configured && !!bridge.has_settings;

  return (
    <div
      className={cn(
        "relative flex flex-col gap-4 rounded-xl border border-border bg-card p-5",
        "transition-all duration-150 hover:border-accent/30 hover:shadow-sm",
      )}
    >
      {/* Status dot */}
      <div
        className={cn(
          "absolute right-4 top-4 h-2.5 w-2.5 rounded-full ring-2 ring-card",
          !bridge.configured && "bg-muted-foreground/30",
          bridge.configured &&
            bridge.enabled &&
            "bg-emerald-500 shadow-sm shadow-emerald-500/40",
          bridge.configured && !bridge.enabled && "bg-amber-400",
        )}
      />

      {/* Icon */}
      <div
        className={cn(
          "flex h-10 w-10 items-center justify-center rounded-xl",
          meta.color,
        )}
      >
        <Icon className="h-5 w-5" />
      </div>

      {/* Label + state */}
      <div className="space-y-0.5">
        <div className="flex items-center gap-1.5">
          <div className="font-medium">{meta.label}</div>
          {meta.hint && (
            <HelpTooltip title={meta.label} side="top" width="md">
              {meta.hint}
            </HelpTooltip>
          )}
        </div>
        <div className="text-xs text-muted-foreground">
          {!bridge.configured
            ? leftovers
              ? "Disconnected · settings kept"
              : "Not connected"
            : bridge.enabled
              ? "Active"
              : "Configured · inactive"}
        </div>
      </div>

      {/* Requirements preview (only for channels never set up — pointless once
          the user already has a configuration waiting to be reconnected). */}
      {!bridge.configured && !leftovers && meta.requirements.length > 0 && (
        <div className="rounded-lg bg-muted/40 px-3 py-2.5 space-y-1.5">
          <p className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground/60">
            What you need
          </p>
          <ul className="space-y-1">
            {meta.requirements.map((r) => (
              <li key={r} className="flex items-center gap-1.5 text-xs text-muted-foreground">
                <CheckCircle2 className="h-3 w-3 shrink-0 text-accent/60" />
                {r}
              </li>
            ))}
            <li className="flex items-center gap-1.5 text-xs text-muted-foreground">
              <CheckCircle2 className="h-3 w-3 shrink-0 text-accent/60" />
              ~{meta.timeMinutes} min setup
            </li>
          </ul>
        </div>
      )}

      {/* Action */}
      <div className="mt-auto space-y-1.5">
        <Button
          size="sm"
          variant={bridge.configured ? "outline" : "accent"}
          className="w-full gap-1.5"
          onClick={bridge.configured ? onManage : onConnect}
        >
          {bridge.configured ? (
            <>
              <Settings2 className="h-3.5 w-3.5" /> Manage
            </>
          ) : (
            <>
              {leftovers ? "Reconnect" : "Connect"}
              <ChevronRight className="h-3.5 w-3.5" />
            </>
          )}
        </Button>
        {/* Without this, a disconnected channel's leftover configuration would
            be unreachable — the primary button goes to the setup wizard. */}
        {leftovers && (
          <Button
            size="sm"
            variant="ghost"
            className="w-full gap-1.5 text-xs text-muted-foreground"
            onClick={onManage}
          >
            <Settings2 className="h-3.5 w-3.5" /> Settings
          </Button>
        )}
      </div>
    </div>
  );
}

// ── Bridge wizard dialog ───────────────────────────────────────────────────

// Three wizard phases:
//   prepare     → read setup guide, open portal
//   credentials → paste token (or WhatsApp QR)
//   done        → saved confirmation
type WizardPhase = "prepare" | "credentials" | "done";

const PHASE_LABELS: Record<WizardPhase, string> = {
  prepare: "Prepare",
  credentials: "Add credentials",
  done: "Done",
};
const PHASES: WizardPhase[] = ["prepare", "credentials", "done"];

// QR mobile-open hints per channel
const MOBILE_HINTS: Record<string, string> = {
  telegram: "Scan to open @BotFather in Telegram on your phone.",
  discord: "Scan to open the Discord Developer Portal on your phone.",
  slack: "Scan to open Slack App Management on your phone.",
  email: "Scan to create a Google App Password on your phone.",
  signal: "Scan to visit the signal-cli setup guide.",
  teams: "Scan to open the Microsoft Bot Framework portal.",
};

function BridgeWizardDialog({
  channel,
  onClose,
  onComplete,
}: {
  channel: string;
  onClose: () => void;
  onComplete: () => void;
}) {
  const { session } = useAuth();
  const qc = useQueryClient();
  const meta = CHANNEL_META[channel] ?? {
    label: channel,
    hint: "",
    icon: Network,
    color: "",
    requirements: [],
    timeMinutes: 5,
  };
  const Icon = meta.icon;
  const fields = BRIDGE_FIELDS[channel] ?? [];
  const primaryField = fields.find((f) => f.type === "secret" || f.type === "text");

  // WhatsApp skips "prepare" (no token needed) and goes straight to QR
  const initialPhase: WizardPhase = meta.isQrBased ? "credentials" : "prepare";
  const [phase, setPhase] = React.useState<WizardPhase>(initialPhase);
  const [tokenValue, setTokenValue] = React.useState("");
  const [qrOpen, setQrOpen] = React.useState(false);
  const [reauthOpen, setReauthOpen] = React.useState(false);

  // For QR-based channels (WhatsApp): kick off the bridge the moment the wizard opens
  // so the QR panel never shows a dead state — the user just sees a spinner then the code.
  React.useEffect(() => {
    if (meta.isQrBased && session) {
      void setBridgeEnabled(channel, true, session.csrf_token, session.fingerprint);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const settingsQuery = useQuery({
    queryKey: ["bridges", channel],
    queryFn: ({ signal }) => getBridgeSettings(channel, signal),
  });

  const guideQuery = useQuery({
    queryKey: ["bridge-setup", channel],
    queryFn: ({ signal }) => getBridgeSetup(channel, signal),
    enabled: !meta.isQrBased,
    staleTime: 60_000,
  });

  const saveMutation = useMutation({
    mutationFn: async () => {
      const current = settingsQuery.data?.settings ?? {};
      const updated = primaryField
        ? { ...current, [primaryField.key]: tokenValue }
        : current;
      return putBridgeSettings(channel, updated, session!.csrf_token, session!.fingerprint);
    },
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ["bridges"] });
      setPhase("done");
    },
  });

  // Stepper: which phases to show (WhatsApp skips "prepare")
  const visiblePhases: WizardPhase[] = meta.isQrBased
    ? ["credentials", "done"]
    : PHASES;

  return (
    <DialogContent className="max-w-2xl p-0 overflow-hidden gap-0">
      {/* ── Two-column layout ── */}
      <div className="flex min-h-[420px]">
        {/* Left: stepper */}
        <div className="w-40 shrink-0 border-r border-border/60 bg-muted/30 flex flex-col pt-6 pb-4 px-4 gap-1">
          {/* Icon + title */}
          <div className="flex items-center gap-2 mb-5">
            <div className={cn("flex h-7 w-7 items-center justify-center rounded-lg shrink-0", meta.color)}>
              <Icon className="h-3.5 w-3.5" />
            </div>
            <span className="font-medium text-sm leading-tight">{meta.label}</span>
          </div>

          {visiblePhases.map((p, idx) => {
            const currentIdx = visiblePhases.indexOf(phase);
            const isDone = idx < currentIdx;
            const isActive = p === phase;
            return (
              <div key={p} className="flex items-center gap-2.5 py-1.5">
                <div
                  className={cn(
                    "flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[10px] font-bold transition-colors",
                    isActive && "bg-accent text-accent-foreground",
                    isDone && "bg-emerald-500 text-white",
                    !isActive && !isDone && "bg-muted-foreground/20 text-muted-foreground",
                  )}
                >
                  {isDone ? <Check className="h-3 w-3" /> : idx + 1}
                </div>
                <span
                  className={cn(
                    "text-xs transition-colors",
                    isActive ? "font-medium text-foreground" : "text-muted-foreground",
                  )}
                >
                  {PHASE_LABELS[p]}
                </span>
              </div>
            );
          })}

          {/* Time estimate */}
          <div className="mt-auto pt-4 text-[10px] text-muted-foreground/50 flex items-center gap-1">
            ~{meta.timeMinutes} min
          </div>
        </div>

        {/* Right: content */}
        <div className="flex-1 overflow-y-auto p-6">
          {/* ── Phase: prepare ── */}
          {phase === "prepare" && (
            <div className="space-y-5">
              <div>
                <h2 className="font-medium text-base">
                  {guideQuery.data?.guide?.display ?? `Set up ${meta.label}`}
                </h2>
                <p className="text-xs text-muted-foreground mt-0.5">{meta.hint}</p>
              </div>

              {/* Portal button + QR accordion */}
              {meta.setupUrl && (
                <div className="space-y-2">
                  <a
                    href={meta.setupUrl}
                    target="_blank"
                    rel="noreferrer"
                    className="flex items-center gap-2 rounded-lg border border-accent/40 bg-accent/5 px-4 py-2.5 text-sm font-medium text-accent hover:bg-accent/10 transition-colors"
                  >
                    <ExternalLink className="h-4 w-4" />
                    Open {meta.label} portal
                  </a>

                  {/* Mobile QR accordion */}
                  {meta.mobileSetupUrl && (
                    <button
                      type="button"
                      onClick={() => setQrOpen((v) => !v)}
                      className="flex w-full items-center gap-2 text-xs text-muted-foreground hover:text-foreground transition-colors py-1"
                    >
                      <QrCode className="h-3.5 w-3.5" />
                      <span>Open on phone instead</span>
                      <ChevronDown className={cn("ml-auto h-3.5 w-3.5 transition-transform", qrOpen && "rotate-180")} />
                    </button>
                  )}
                  {qrOpen && meta.mobileSetupUrl && (
                    <div className="rounded-lg border border-border/60 p-3">
                      <SetupQrCard
                        url={meta.mobileSetupUrl}
                        label={meta.label}
                        hint={MOBILE_HINTS[channel]}
                      />
                    </div>
                  )}
                </div>
              )}

              {/* Setup steps — always expanded */}
              {guideQuery.isLoading && (
                <div className="space-y-2">
                  {Array.from({ length: 4 }).map((_, i) => (
                    <Skeleton key={i} className="h-5 w-full" />
                  ))}
                </div>
              )}
              {guideQuery.data?.guide?.steps && (
                <ol className="space-y-3">
                  {guideQuery.data.guide.steps.map((s: string, i: number) => (
                    <li key={i} className="flex gap-3 text-sm text-muted-foreground">
                      <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-accent/15 text-[10px] font-bold text-accent mt-0.5">
                        {i + 1}
                      </span>
                      <span
                        className="leading-relaxed"
                        dangerouslySetInnerHTML={{
                          __html: s
                            .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
                            .replace(
                              /`(.+?)`/g,
                              "<code class='font-mono bg-muted px-1 rounded text-[10px]'>$1</code>",
                            ),
                        }}
                      />
                    </li>
                  ))}
                </ol>
              )}

              <div className="flex justify-end pt-2">
                <Button
                  variant="accent"
                  className="gap-1.5"
                  onClick={() => setPhase("credentials")}
                >
                  Next — add token <ChevronRight className="h-4 w-4" />
                </Button>
              </div>
            </div>
          )}

          {/* ── Phase: credentials ── */}
          {phase === "credentials" && (
            <div className="space-y-5">
              <div>
                <h2 className="font-medium text-base">
                  {meta.isQrBased ? `Scan with ${meta.label}` : `Paste your ${meta.label} token`}
                </h2>
                <p className="text-xs text-muted-foreground mt-0.5">
                  {meta.isQrBased
                    ? "Open WhatsApp on your phone and scan the QR code below."
                    : "Copy the token from the portal and paste it here."}
                </p>
              </div>

              {/* WhatsApp: inline QR */}
              {meta.isQrBased ? (
                <WhatsAppQrPanel
                  onDone={() => {
                    void qc.invalidateQueries({ queryKey: ["bridges"] });
                    setPhase("done");
                  }}
                />
              ) : (
                <>
                  {primaryField && (
                    <div className="space-y-1.5">
                      <label className="text-sm font-medium">{primaryField.label}</label>
                      <SecretInput
                        value={tokenValue}
                        onChange={setTokenValue}
                        placeholder={primaryField.placeholder}
                        autoFocus
                      />
                    </div>
                  )}

                  {channel === "email" && (
                    <p className="flex items-start gap-2 rounded-md bg-muted/40 px-3 py-2 text-xs text-muted-foreground">
                      <Info className="mt-0.5 h-3.5 w-3.5 flex-none" />
                      E-Mail credentials are stored in{" "}
                      <code className="font-mono bg-muted px-1 rounded">~/.config/corvin-voice/service.env</code>.
                      Set <code className="font-mono bg-muted px-1 rounded">GMAIL_USER</code> and{" "}
                      <code className="font-mono bg-muted px-1 rounded">GMAIL_APP_PASSWORD</code> there.
                    </p>
                  )}

                  {saveMutation.isError && (
                    <p className="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive">
                      {(saveMutation.error as Error).message}
                    </p>
                  )}

                  <div className="flex items-center justify-between pt-2">
                    {!meta.isQrBased && (
                      <Button
                        variant="ghost"
                        size="sm"
                        className="text-muted-foreground gap-1"
                        onClick={() => setPhase("prepare")}
                      >
                        ← Back
                      </Button>
                    )}
                    <Button
                      variant="accent"
                      className="gap-1.5 ml-auto"
                      disabled={primaryField ? !tokenValue.trim() : false}
                      onClick={() => setReauthOpen(true)}
                    >
                      {saveMutation.isPending ? (
                        <Loader2 className="h-4 w-4 animate-spin" />
                      ) : (
                        <>
                          Save &amp; connect <ChevronRight className="h-4 w-4" />
                        </>
                      )}
                    </Button>
                  </div>
                </>
              )}
            </div>
          )}

          {/* ── Phase: done ── */}
          {phase === "done" && (
            <div className="flex flex-col items-center gap-5 py-6 text-center">
              <div className="flex h-16 w-16 items-center justify-center rounded-full bg-emerald-500/15">
                <Check className="h-8 w-8 text-emerald-500" />
              </div>
              <div className="space-y-1">
                <p className="font-medium text-lg">{meta.label} is connected!</p>
                <p className="text-sm text-muted-foreground">
                  The bridge is active. You can manage settings any time from its tile.
                </p>
              </div>
              <div className="flex gap-2">
                <Button
                  variant="outline"
                  onClick={onClose}
                >
                  Close
                </Button>
                <Button
                  variant="accent"
                  onClick={() => {
                    onComplete();
                    onClose();
                  }}
                >
                  Done
                </Button>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Close button in top-right */}
      <button
        className="absolute right-4 top-4 rounded-sm opacity-70 ring-offset-background transition-opacity hover:opacity-100"
        onClick={onClose}
      >
        <X className="h-4 w-4" />
        <span className="sr-only">Close</span>
      </button>

      <ReauthDialog
        open={reauthOpen}
        onOpenChange={setReauthOpen}
        title={`Connect · ${meta.label}`}
        description="Bridge settings will be saved. Confirm to proceed."
        onConfirm={async () => {
          await saveMutation.mutateAsync();
        }}
      />
    </DialogContent>
  );
}

// ── Structured settings form ───────────────────────────────────────────────

function SettingsForm({
  channel,
  settings,
  onChange,
}: {
  channel: string;
  settings: Record<string, unknown>;
  onChange: (updated: Record<string, unknown>) => void;
}) {
  const fields = BRIDGE_FIELDS[channel] ?? [];
  if (fields.length === 0) return null;

  const update = (key: string, value: unknown) =>
    onChange({ ...settings, [key]: value });

  return (
    <div className="space-y-4">
      {fields.map((field) => {
        const raw = settings[field.key];

        return (
          <div key={field.key} className="space-y-1.5">
            <div className="flex items-center gap-1.5">
              <label className="text-sm font-medium">{field.label}</label>
              {field.hint && (
                <HelpTooltip title={field.label} side="top" width="md">
                  {field.hint}
                </HelpTooltip>
              )}
            </div>

            {field.type === "secret" && (
              <SecretInput
                value={typeof raw === "string" ? raw : ""}
                onChange={(v) => update(field.key, v)}
                placeholder={field.placeholder}
              />
            )}

            {field.type === "text" && (
              <Input
                value={typeof raw === "string" ? raw : ""}
                onChange={(e) => update(field.key, e.target.value)}
                placeholder={field.placeholder ?? field.label}
                className="font-mono text-sm"
              />
            )}

            {field.type === "number" && (
              <Input
                type="number"
                value={typeof raw === "number" ? raw : ""}
                onChange={(e) =>
                  update(
                    field.key,
                    e.target.value === "" ? undefined : Number(e.target.value),
                  )
                }
                min={field.min}
                max={field.max}
                className="w-40 font-mono text-sm"
              />
            )}

            {field.type === "boolean" && (
              <div className="flex items-center gap-3">
                <button
                  type="button"
                  onClick={() => update(field.key, !raw)}
                  className={cn(
                    "relative h-6 w-11 rounded-full transition-colors",
                    raw ? "bg-accent" : "bg-muted",
                  )}
                >
                  <span
                    className={cn(
                      "absolute top-0.5 h-5 w-5 rounded-full bg-white shadow transition-transform",
                      raw ? "translate-x-5" : "translate-x-0.5",
                    )}
                  />
                </button>
                <span className="text-sm text-muted-foreground">
                  {raw ? "On" : "Off"}
                </span>
              </div>
            )}

            {field.type === "array" && (
              <ArrayEditor
                value={Array.isArray(raw) ? (raw as string[]) : []}
                onChange={(v) => update(field.key, v)}
                placeholder={`Add ${field.label.toLowerCase()}…`}
              />
            )}

            {field.type === "select" && (
              <div className="flex gap-1.5">
                {(field.options ?? []).map((opt) => (
                  <button
                    key={opt}
                    type="button"
                    onClick={() => update(field.key, opt)}
                    className={cn(
                      "rounded-md border px-3 py-1.5 text-xs font-medium transition-colors",
                      raw === opt
                        ? "border-accent bg-accent/15 text-accent"
                        : "border-border text-muted-foreground hover:border-accent/40 hover:text-foreground",
                    )}
                  >
                    {opt}
                  </button>
                ))}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

// ── Manage dialog ─────────────────────────────────────────────────────────

function BridgeManageDialog({
  channel,
  initial,
  onClose,
}: {
  channel: string;
  initial: BridgeListItem;
  onClose: () => void;
}) {
  const { session } = useAuth();
  const qc = useQueryClient();
  const meta = CHANNEL_META[channel] ?? {
    label: channel,
    hint: "",
    icon: Network,
    color: "",
  };
  const Icon = meta.icon;

  const detail = useQuery({
    queryKey: ["bridges", channel],
    queryFn: ({ signal }) => getBridgeSettings(channel, signal),
  });

  // Form state: structured known fields
  const [formSettings, setFormSettings] = React.useState<
    Record<string, unknown>
  >({});
  // Advanced JSON: everything minus known fields
  const [advancedDraft, setAdvancedDraft] = React.useState("{}");
  const [advancedError, setAdvancedError] = React.useState<string | null>(null);

  const [reauthOpen, setReauthOpen] = React.useState(false);
  const [toggleReauthOpen, setToggleReauthOpen] = React.useState(false);
  const [pendingEnabled, setPendingEnabled] = React.useState<boolean | null>(
    null,
  );
  // Which destructive action the user is confirming, if any.
  const [confirmMode, setConfirmMode] = React.useState<
    "disconnect" | "delete" | null
  >(null);
  const [toast, setToast] = React.useState<{
    kind: "ok" | "err";
    msg: string;
  } | null>(null);

  const known = knownKeys(channel);

  React.useEffect(() => {
    if (!detail.data) return;
    const all = detail.data.settings as Record<string, unknown>;
    // Split into known (form) and rest (advanced JSON)
    const formPart: Record<string, unknown> = {};
    const restPart: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(all)) {
      if (known.has(k)) formPart[k] = v;
      else restPart[k] = v;
    }
    // Ensure all known keys are present in formPart, using defaults for missing fields
    // This prevents fields from being lost on save (especially array fields like whitelist)
    for (const field of BRIDGE_FIELDS[channel] ?? []) {
      if (!(field.key in formPart)) {
        if (field.type === 'array') {
          formPart[field.key] = [];
        } else if (field.type === 'boolean') {
          formPart[field.key] = false;
        }
      }
    }
    setFormSettings(formPart);
    setAdvancedDraft(JSON.stringify(restPart, null, 2));
    setAdvancedError(null);
  }, [detail.data, channel]); // eslint-disable-line react-hooks/exhaustive-deps

  const validateAdvanced = (text: string) => {
    setAdvancedDraft(text);
    try {
      const obj = JSON.parse(text);
      setAdvancedError(
        typeof obj !== "object" || obj == null || Array.isArray(obj)
          ? "Top-level must be an object."
          : null,
      );
    } catch (e) {
      setAdvancedError(e instanceof Error ? e.message : "Invalid JSON");
    }
  };

  const buildFinalSettings = (): Record<string, unknown> => {
    const advParsed = JSON.parse(advancedDraft);
    const final = { ...advParsed, ...formSettings };
    // Ensure all known fields are present in the final output
    // This prevents fields like whitelist from being lost on save
    for (const field of BRIDGE_FIELDS[channel] ?? []) {
      if (!(field.key in final)) {
        if (field.type === 'array') {
          final[field.key] = [];
        } else if (field.type === 'boolean') {
          final[field.key] = false;
        }
      }
    }
    return final;
  };

  const saveMutation = useMutation({
    mutationFn: async () =>
      putBridgeSettings(channel, buildFinalSettings(), session!.csrf_token, session!.fingerprint),
    onSuccess: async () => {
      setToast({ kind: "ok", msg: "Saved." });
      await qc.invalidateQueries({ queryKey: ["bridges", channel] });
      await qc.invalidateQueries({ queryKey: ["bridges", "list"] });
    },
    onError: (e: Error) => setToast({ kind: "err", msg: e.message }),
  });

  const enabledMutation = useMutation({
    mutationFn: async ({ enabled }: { enabled: boolean }) =>
      setBridgeEnabled(channel, enabled, session!.csrf_token, session!.fingerprint),
    onSuccess: async (data) => {
      const verb = data.enabled ? "activated" : "deactivated";
      setToast({ kind: "ok", msg: `${meta.label} ${verb}.` });
      await qc.invalidateQueries({ queryKey: ["bridges"] });
    },
    onError: (e: Error) => setToast({ kind: "err", msg: e.message }),
  });

  const disconnectMutation = useMutation({
    mutationFn: async (mode: "disconnect" | "delete") =>
      disconnectBridge(channel, mode, session!.csrf_token, session!.fingerprint),
    onSuccess: async (data) => {
      // restart_needed=true means the runtime could not stop the daemon
      // (no supervisor/systemd on this host) — credentials are wiped on disk,
      // but the process may still hold them in memory until restarted.
      setToast(
        data.restart_needed
          ? {
              kind: "err",
              msg:
                `${meta.label}: credentials removed, but the daemon could not be ` +
                `stopped automatically — restart the bridge process (bridge.sh) ` +
                `to complete the ${data.mode === "delete" ? "deletion" : "disconnect"}.`,
            }
          : {
              kind: "ok",
              msg:
                data.mode === "delete"
                  ? `${meta.label} deleted. Connect again to set it up from scratch.`
                  : `${meta.label} disconnected — your settings were kept. Connect again with new credentials.`,
            },
      );
      await qc.invalidateQueries({ queryKey: ["bridges"] });
      // The dialog's own detail query is now stale (credentials are gone).
      await qc.invalidateQueries({ queryKey: ["bridges", channel] });
      setConfirmMode(null);
      // A deleted channel has nothing left to manage — drop back to the grid.
      if (data.mode === "delete") onClose();
    },
    onError: (e: Error) => {
      setToast({ kind: "err", msg: e.message });
      setConfirmMode(null);
    },
  });

  return (
    <DialogContent className="max-w-2xl">
      <DialogHeader>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div
              className={cn(
                "flex h-9 w-9 items-center justify-center rounded-xl",
                meta.color,
              )}
            >
              <Icon className="h-4.5 w-4.5" />
            </div>
            <div>
              <DialogTitle className="flex items-center gap-2">
                {meta.label}
                {initial.enabled ? (
                  <Badge
                    variant="outline"
                    className="border-emerald-500/40 text-emerald-500 text-[10px]"
                  >
                    <Power className="mr-1 h-2.5 w-2.5" /> active
                  </Badge>
                ) : (
                  <Badge
                    variant="outline"
                    className="border-amber-500/40 text-amber-500 text-[10px]"
                  >
                    <PowerOff className="mr-1 h-2.5 w-2.5" /> inactive
                  </Badge>
                )}
              </DialogTitle>
              <DialogDescription className="text-xs">
                {meta.hint}
              </DialogDescription>
            </div>
          </div>
          <Button
            variant="outline"
            size="sm"
            disabled={enabledMutation.isPending}
            onClick={() => {
              setPendingEnabled(!initial.enabled);
              setToggleReauthOpen(true);
            }}
          >
            {enabledMutation.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : initial.enabled ? (
              <>
                <PowerOff className="h-4 w-4" /> Disable
              </>
            ) : (
              <>
                <Power className="h-4 w-4" /> Enable
              </>
            )}
          </Button>
        </div>
      </DialogHeader>

      <div className="space-y-4">
        {detail.isLoading && <Skeleton className="h-60 w-full" />}

        {detail.data && (
          <Tabs defaultValue="settings">
            <TabsList>
              <TabsTrigger value="settings" className="gap-1.5">
                <Sliders className="h-3.5 w-3.5" /> Settings
              </TabsTrigger>
              {channel === "whatsapp" && (
                <TabsTrigger value="qr" className="gap-1.5">
                  <QrCode className="h-3.5 w-3.5" /> QR / Re-link
                </TabsTrigger>
              )}
              {meta.mobileSetupUrl && channel !== "whatsapp" && (
                <TabsTrigger value="setup" className="gap-1.5">
                  <BookOpen className="h-3.5 w-3.5" /> Setup guide
                </TabsTrigger>
              )}
              <TabsTrigger value="advanced" className="gap-1.5">
                <Settings2 className="h-3.5 w-3.5" /> Advanced
              </TabsTrigger>
              <TabsTrigger
                value="connection"
                className="gap-1.5 data-[state=active]:text-destructive"
              >
                <Unplug className="h-3.5 w-3.5" /> Connection
              </TabsTrigger>
            </TabsList>

            {/* Settings tab */}
            <TabsContent value="settings">
              <SettingsForm
                channel={channel}
                settings={formSettings}
                onChange={setFormSettings}
              />
              <p className="mt-3 flex items-start gap-2 rounded-md bg-muted/40 px-3 py-2 text-[11px] text-muted-foreground">
                <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                Secret fields show <span className="font-mono">****last4</span>.
                Leave unchanged to keep the existing value. Extra fields like{" "}
                <span className="font-mono">chat_profiles</span> are in the
                Advanced tab.
              </p>
            </TabsContent>

            {/* WhatsApp QR tab */}
            {channel === "whatsapp" && (
              <TabsContent value="qr">
                <WhatsAppQrPanel
                  onDone={() => qc.invalidateQueries({ queryKey: ["bridges"] })}
                />
              </TabsContent>
            )}

            {/* Setup guide tab (non-WhatsApp) */}
            {meta.mobileSetupUrl && channel !== "whatsapp" && (
              <TabsContent value="setup">
                <SetupQrCard
                  url={meta.mobileSetupUrl}
                  label={meta.label}
                  hint="Scan this QR code on your phone to open the setup portal."
                />
              </TabsContent>
            )}

            {/* Advanced JSON tab */}
            <TabsContent value="advanced">
              <div className="space-y-2">
                <p className="text-xs text-muted-foreground">
                  Extra settings (e.g.{" "}
                  <code className="font-mono bg-muted px-1 rounded">
                    chat_profiles
                  </code>
                  , per-chat overrides). The fields from the Settings tab are
                  merged in on save.
                </p>
                <Textarea
                  spellCheck={false}
                  value={advancedDraft}
                  onChange={(e) => validateAdvanced(e.target.value)}
                  className="min-h-[220px] font-mono text-xs"
                />
                {advancedError && (
                  <p className="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive">
                    {advancedError}
                  </p>
                )}
              </div>
            </TabsContent>

            {/* Connection tab — disconnect / delete so the channel can be set
                up again with different credentials. */}
            <TabsContent value="connection">
              <div className="space-y-4">
                <p className="text-xs text-muted-foreground">
                  Use this to hand {meta.label} to a different bot or account,
                  or to revoke a credential you no longer trust. Both actions
                  stop the bridge and switch the channel off; a backup of the
                  previous configuration is kept on the server.
                </p>

                {/* Level 1 — disconnect, keep preferences */}
                <div className="rounded-lg border border-amber-500/30 bg-amber-500/5 p-3">
                  <div className="flex items-start justify-between gap-4">
                    <div className="space-y-1">
                      <p className="flex items-center gap-1.5 text-sm font-medium">
                        <Unplug className="h-3.5 w-3.5 text-amber-500" />
                        Disconnect
                      </p>
                      <p className="text-[11px] leading-relaxed text-muted-foreground">
                        Removes the credential and any device pairing. Your
                        whitelist, PIN, rate limit and per-chat profiles stay.
                        Connect again to plug in new credentials.
                      </p>
                    </div>
                    <Button
                      variant="outline"
                      size="sm"
                      className="shrink-0 border-amber-500/40 text-amber-600 hover:bg-amber-500/10 dark:text-amber-400"
                      disabled={disconnectMutation.isPending}
                      onClick={() => setConfirmMode("disconnect")}
                    >
                      Disconnect
                    </Button>
                  </div>
                </div>

                {/* Level 2 — full delete */}
                <div className="rounded-lg border border-destructive/30 bg-destructive/5 p-3">
                  <div className="flex items-start justify-between gap-4">
                    <div className="space-y-1">
                      <p className="flex items-center gap-1.5 text-sm font-medium text-destructive">
                        <Trash2 className="h-3.5 w-3.5" />
                        Delete configuration
                      </p>
                      <p className="text-[11px] leading-relaxed text-muted-foreground">
                        Removes the whole configuration file, including your
                        whitelist and preferences. The channel goes back to
                        never-configured.
                      </p>
                    </div>
                    <Button
                      variant="destructive"
                      size="sm"
                      className="shrink-0"
                      disabled={disconnectMutation.isPending}
                      onClick={() => setConfirmMode("delete")}
                    >
                      Delete
                    </Button>
                  </div>
                </div>

                {/* Inline confirmation — deliberately not a native confirm(),
                    and it names the exact consequence of the chosen level. */}
                {confirmMode && (
                  <div className="rounded-lg border border-destructive/40 bg-destructive/10 p-3 space-y-3">
                    <p className="flex items-start gap-2 text-xs text-destructive">
                      <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                      {confirmMode === "delete" ? (
                        <span>
                          Delete the entire {meta.label} configuration? Your
                          whitelist and preferences go too. This stops the
                          bridge immediately.
                        </span>
                      ) : (
                        <span>
                          Disconnect {meta.label}? The credential is removed and
                          the bridge stops. Your settings are kept.
                        </span>
                      )}
                    </p>
                    <div className="flex justify-end gap-2">
                      <Button
                        variant="ghost"
                        size="sm"
                        disabled={disconnectMutation.isPending}
                        onClick={() => setConfirmMode(null)}
                      >
                        Cancel
                      </Button>
                      <Button
                        variant="destructive"
                        size="sm"
                        disabled={disconnectMutation.isPending}
                        onClick={() => disconnectMutation.mutate(confirmMode)}
                      >
                        {disconnectMutation.isPending ? (
                          <Loader2 className="h-4 w-4 animate-spin" />
                        ) : confirmMode === "delete" ? (
                          "Yes, delete"
                        ) : (
                          "Yes, disconnect"
                        )}
                      </Button>
                    </div>
                  </div>
                )}
              </div>
            </TabsContent>
          </Tabs>
        )}

        {toast && (
          <div
            className={cn(
              "flex items-center justify-between rounded-lg px-4 py-2.5 text-sm",
              toast.kind === "ok"
                ? "border border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400"
                : "border border-destructive/30 bg-destructive/10 text-destructive",
            )}
          >
            {toast.kind === "ok" ? (
              <CheckCircle2 className="h-4 w-4" />
            ) : (
              <XCircle className="h-4 w-4" />
            )}
            {toast.msg}
            <button onClick={() => setToast(null)}>
              <X className="h-4 w-4 opacity-60 hover:opacity-100" />
            </button>
          </div>
        )}

        <div className="flex justify-end gap-2 pt-1">
          <Button
            variant="ghost"
            size="sm"
            className="text-muted-foreground"
            onClick={onClose}
          >
            Close
          </Button>
          <Button
            variant="accent"
            disabled={
              !!advancedError || saveMutation.isPending || detail.isLoading
            }
            onClick={() => setReauthOpen(true)}
          >
            {saveMutation.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <>
                <Save className="h-4 w-4" /> Save
              </>
            )}
          </Button>
        </div>
      </div>

      <ReauthDialog
        open={reauthOpen}
        onOpenChange={setReauthOpen}
        title={`Save · ${meta.label}`}
        description="Bridge settings may contain tokens. Confirm to proceed."
        onConfirm={async () => {
          await saveMutation.mutateAsync();
        }}
      />
      <ReauthDialog
        open={toggleReauthOpen}
        onOpenChange={setToggleReauthOpen}
        title={`${pendingEnabled ? "Enable" : "Disable"} · ${meta.label}`}
        description={
          pendingEnabled
            ? "Daemon will be started via supervisorctl."
            : "Daemon will be stopped via supervisorctl."
        }
        onConfirm={async () => {
          if (pendingEnabled === null) return;
          await enabledMutation.mutateAsync({ enabled: pendingEnabled });
        }}
      />
    </DialogContent>
  );
}

// ── Webhook channels bridge card ──────────────────────────────────────────

function WebhookChannelsBridgeCard() {
  const query = useQuery({
    queryKey: ["webhook-channels"],
    queryFn: ({ signal }) => listWebhookChannels(signal),
    staleTime: 60_000,
  });

  const count = query.data?.channels?.length ?? 0;

  return (
    <Card className="border-border/60">
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <CardTitle className="text-sm font-medium">
              Custom Webhook Channels
            </CardTitle>
            {!query.isLoading && (
              <Badge variant="secondary" className="text-xs tabular-nums">
                {count}
              </Badge>
            )}
            {query.isLoading && (
              <Skeleton className="h-5 w-8 rounded-full" />
            )}
          </div>
          <Button
            size="sm"
            variant="outline"
            className="gap-1.5 shrink-0"
            onClick={() => { window.location.href = "/console/app/compliance"; }}
          >
            <ExternalLink className="h-3.5 w-3.5" />
            Manage Channels
          </Button>
        </div>
        <CardDescription className="text-xs leading-relaxed">
          Register custom inbound webhook channels that external systems can POST
          to. Channels are managed in Audit &amp; Compliance &rarr; Webhook
          Channels.
        </CardDescription>
      </CardHeader>
    </Card>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────

export function BridgesPage() {
  const list = useQuery({
    queryKey: ["bridges", "list"],
    queryFn: ({ signal }) => listBridges(signal),
    refetchInterval: 30_000,
  });

  const commandsQuery = useQuery({
    queryKey: ["commands"],
    queryFn: ({ signal }) => getCommands(signal),
    staleTime: Infinity,
    enabled: true, // Preload commands on page load
  });

  const [wizardChannel, setWizardChannel] = React.useState<string | null>(null);
  const [manageChannel, setManageChannel] = React.useState<string | null>(null);
  const [helpOpen, setHelpOpen] = React.useState(false);
  const qc = useQueryClient();

  const configured = list.data?.bridges.filter((b) => b.configured) ?? [];
  const unconfigured = list.data?.bridges.filter((b) => !b.configured) ?? [];
  const hasAny = configured.length > 0;

  return (
    <div className="mx-auto max-w-5xl space-y-8">
      {/* Header */}
      <div className="flex items-end justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <h1 className="font-serif text-3xl font-light tracking-tight">
              Channels
            </h1>
            <HelpTooltip title="What are bridges?" side="right" width="lg">
              Bridges connect Corvin to messaging platforms like Discord,
              Telegram, or WhatsApp.
              <br />
              <br />
              Each bridge runs as a daemon that forwards messages to the AI
              engine and sends replies back. Connect as many as you like — each
              one is independent.
            </HelpTooltip>
          </div>
          <p className="mt-1 text-sm text-muted-foreground">
            Connect Corvin to your messaging channels.
          </p>
        </div>
        <div className="flex items-center gap-3">
          <Button
            variant="outline"
            size="sm"
            onClick={() => setHelpOpen(true)}
            className="gap-2"
          >
            <HelpCircle className="h-4 w-4" />
            Commands
          </Button>
          <Badge variant="outline" className="text-xs">
            {configured.length} / {list.data?.count ?? "—"} connected
          </Badge>
        </div>
      </div>

      {list.isLoading && (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {Array.from({ length: 7 }).map((_, i) => (
            <Skeleton key={i} className="h-44 rounded-xl" />
          ))}
        </div>
      )}

      {list.data && (
        <>
          {/* Connected bridges */}
          {configured.length > 0 && (
            <section className="space-y-3">
              <h2 className="text-xs font-semibold uppercase tracking-widest text-muted-foreground/70 px-0.5">
                Connected
              </h2>
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
                {configured.map((b) => (
                  <BridgeTile
                    key={b.channel}
                    bridge={b}
                    onConnect={() => setWizardChannel(b.channel)}
                    onManage={() => setManageChannel(b.channel)}
                  />
                ))}
              </div>
            </section>
          )}

          {/* Not connected bridges */}
          {unconfigured.length > 0 && (
            <section className="space-y-3">
              <h2 className="text-xs font-semibold uppercase tracking-widest text-muted-foreground/70 px-0.5">
                {hasAny ? "More channels" : "Set up channels"}
              </h2>
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
                {unconfigured.map((b) => (
                  <BridgeTile
                    key={b.channel}
                    bridge={b}
                    onConnect={() => setWizardChannel(b.channel)}
                    onManage={() => setManageChannel(b.channel)}
                  />
                ))}
              </div>
            </section>
          )}

          {/* Empty hint */}
          {!hasAny && list.data.bridges.length === 0 && (
            <div className="flex flex-col items-center gap-4 rounded-2xl border border-dashed border-border py-16 text-center">
              <Network className="h-10 w-10 text-muted-foreground/30" />
              <div className="space-y-1">
                <p className="font-medium text-muted-foreground">
                  No bridges configured
                </p>
                <p className="text-sm text-muted-foreground/60">
                  Bridge configuration is missing — make sure the bridges are
                  installed.
                </p>
              </div>
            </div>
          )}
        </>
      )}

      {/* Wizard dialog */}
      <Dialog
        open={!!wizardChannel}
        onOpenChange={(v) => !v && setWizardChannel(null)}
      >
        {wizardChannel && (
          <BridgeWizardDialog
            channel={wizardChannel}
            onClose={() => setWizardChannel(null)}
            onComplete={() => {
              void qc.invalidateQueries({ queryKey: ["bridges", "list"] });
              setWizardChannel(null);
            }}
          />
        )}
      </Dialog>

      {/* Manage dialog */}
      <Dialog
        open={!!manageChannel}
        onOpenChange={(v) => !v && setManageChannel(null)}
      >
        {manageChannel && list.data && (
          <BridgeManageDialog
            channel={manageChannel}
            initial={
              list.data.bridges.find((b) => b.channel === manageChannel)!
            }
            onClose={() => setManageChannel(null)}
          />
        )}
      </Dialog>

      {/* Commands help dialog */}
      <Dialog open={helpOpen} onOpenChange={setHelpOpen}>
        {helpOpen && commandsQuery.data && (
          <CommandsHelpModal
            commands={commandsQuery.data.categories}
            tip={commandsQuery.data.tip}
          />
        )}
      </Dialog>

      {/* Webhook channels informational card */}
      <WebhookChannelsBridgeCard />
    </div>
  );
}
