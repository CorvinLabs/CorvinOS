/**
 * Engine Control Center — ADR-0069 M5
 * Route: /app/engine-control
 *
 * Two-panel layout:
 *   Left  — OS Engine (main conversation host)
 *   Right — Capability Matrix + ECI Commands
 */
import * as React from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  Circle,
  Cpu,
  Download,
  Info,
  Key,
  Lock,
  LogIn,
  RefreshCw,
  Server,
  Shield,
  Star,
  Terminal,
  Wifi,
  WifiOff,
  Zap,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  getOsEngineSetting,
  getOsEngineHealth,
  getEngineCatalog,
  getEngineCapabilities,
  setOsEngineSetting,
  detectEngines,
  getClaudeLocalSetting,
  setClaudeLocalSetting,
  type EngineCapabilityMatrix,
  type CredentialSource,
  type ClaudeLocalSetting,
} from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";

// ── Transport badge helpers ───────────────────────────────────────

const TRANSPORT_META: Record<
  string,
  { label: string; color: string; icon: React.FC<{ className?: string }> }
> = {
  stdin_json: {
    label: "Live inject",
    color: "bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-200",
    icon: Zap,
  },
  buffered: {
    label: "Buffered",
    color: "bg-yellow-100 text-yellow-800 dark:bg-yellow-900 dark:text-yellow-200",
    icon: Activity,
  },
  sidecar: {
    label: "Sidecar",
    color: "bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-200",
    icon: Server,
  },
};

function TransportBadge({ transport }: { transport: string | null }) {
  if (!transport) {
    return (
      <span className="inline-flex items-center gap-1 rounded px-2 py-0.5 text-xs bg-red-100 text-red-700 dark:bg-red-900 dark:text-red-300">
        <Circle className="h-3 w-3" /> not supported
      </span>
    );
  }
  const meta = TRANSPORT_META[transport];
  if (!meta) {
    return (
      <span className="inline-flex items-center gap-1 rounded px-2 py-0.5 text-xs bg-muted text-muted-foreground">
        {transport}
      </span>
    );
  }
  const Icon = meta.icon;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded px-2 py-0.5 text-xs",
        meta.color,
      )}
    >
      <Icon className="h-3 w-3" />
      {meta.label}
    </span>
  );
}

// ── Capability cell ───────────────────────────────────────────────

type CellValue = "native" | "teb" | "fcb" | "buffered" | "none" | "unknown";

function capValue(
  engineId: string,
  key: string,
  matrix: EngineCapabilityMatrix,
): CellValue {
  const entry = matrix.engines[engineId];
  if (!entry) return "unknown";
  const caps = entry.capabilities;
  const manifest = entry.command_manifest;

  if (key === "btw") {
    const t = manifest?.mid_stream_inject;
    if (!t) return "none";
    if (t === "stdin_json") return engineId === "claude_code" ? "native" : "teb";
    if (t === "buffered") return "buffered";
    if (t === "fcb") return "fcb";
    return "unknown";
  }
  if (key === "hooks") return caps.hooks ? "native" : "teb";
  if (key === "l33") return caps.hooks ? "native" : "teb";
  if (key === "mcp") {
    if (caps.mcp) return engineId === "claude_code" ? "native" : "teb";
    return "fcb";
  }
  if (key === "skills") return "teb";
  if (key === "plan_mode") return caps.permission_modes &&
    Array.isArray(caps.permission_modes) &&
    (caps.permission_modes as string[]).includes("plan") ? "native" : "none";
  if (key === "local") {
    return engineId === "hermes" ? "native" : "none";
  }
  return "unknown";
}

const CELL_META: Record<
  CellValue,
  { label: string; className: string }
> = {
  native: { label: "✅ native", className: "text-green-700 dark:text-green-400 font-medium" },
  teb:    { label: "✅ TEB",   className: "text-emerald-600 dark:text-emerald-400" },
  fcb:    { label: "🔶 FCB",   className: "text-amber-600 dark:text-amber-400" },
  buffered:{ label:"📦 buffer",className: "text-yellow-600 dark:text-yellow-400" },
  none:   { label: "✗",        className: "text-red-500 dark:text-red-400" },
  unknown:{ label: "—",        className: "text-muted-foreground" },
};

const CAP_ROWS: { key: string; label: string; tooltip: string }[] = [
  { key: "btw",       label: "Mid-stream inject", tooltip: "Inject a note into the AI's response while it is streaming" },
  { key: "hooks",     label: "Security hooks",    tooltip: "Pre/post-tool hooks for security checks and audit logging" },
  { key: "l33",       label: "Artifact storage",  tooltip: "Automatically saves files, images, and exports to session storage" },
  { key: "mcp",       label: "MCP Tools",         tooltip: "Custom tools available to this engine via the tool server" },
  { key: "skills",    label: "Skills",            tooltip: "Injects active skills into the AI's instructions" },
  { key: "plan_mode", label: "Plan Mode",         tooltip: "The AI can enter a planning mode before acting" },
  { key: "local",     label: "Runs locally",      tooltip: "All processing stays on this server — no data sent to the cloud" },
];

// ── ECI Command Panel ─────────────────────────────────────────────

function EciCommandPanel({
  engineId,
  matrix,
}: {
  engineId: string;
  matrix: EngineCapabilityMatrix;
}) {
  const entry = matrix.engines[engineId];
  const manifest = entry?.command_manifest;
  const gaps = entry?.eaos_gaps ?? [];

  if (!manifest) {
    return (
      <p className="text-sm text-muted-foreground">
        No command information available for this engine.
      </p>
    );
  }

  const nativeCmds = Object.entries(manifest.native_commands ?? {});

  return (
    <div className="space-y-3">
      <div className="rounded-md border bg-muted/40 p-3 font-mono text-xs space-y-1">
        <div className="flex items-start gap-2">
          <span className="text-muted-foreground w-24 shrink-0">/btw &lt;text&gt;</span>
          <TransportBadge transport={manifest.mid_stream_inject} />
        </div>
        {manifest.cancel && (
          <div className="flex items-start gap-2">
            <span className="text-muted-foreground w-24 shrink-0">/cancel</span>
            <span className="text-foreground">{manifest.cancel}</span>
          </div>
        )}
        {manifest.compact && (
          <div className="flex items-start gap-2">
            <span className="text-muted-foreground w-24 shrink-0">/compact</span>
            <span className="text-foreground">{manifest.compact}</span>
          </div>
        )}
        {nativeCmds.map(([cmd, spec]) => (
          <div key={cmd} className="flex items-start gap-2">
            <span className="text-blue-600 dark:text-blue-400 w-24 shrink-0">
              /e:{cmd}
            </span>
            <span className="text-foreground">{spec.description}</span>
            {spec.usage && (
              <span className="text-muted-foreground">{spec.usage}</span>
            )}
          </div>
        ))}
      </div>

      {gaps.length > 0 && (
        <div className="space-y-1">
          <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
            Structural gaps
          </p>
          {gaps.map((gap) => (
            <div key={gap} className="flex items-center gap-1.5 text-xs text-amber-600 dark:text-amber-400">
              <AlertTriangle className="h-3 w-3 shrink-0" />
              {gap}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── ADR-0125: Credential badge (compact) ─────────────────────────

function CredentialBadge({ source }: { source: CredentialSource }) {
  if (source === "subscription") return (
    <span className="inline-flex items-center gap-0.5 rounded px-1.5 py-0.5 text-[9px] font-semibold bg-emerald-500/15 text-emerald-700 dark:text-emerald-400">
      <Star className="h-2.5 w-2.5" /> subscription
    </span>
  );
  if (source === "env_var") return (
    <span className="inline-flex items-center gap-0.5 rounded px-1.5 py-0.5 text-[9px] bg-blue-500/10 text-blue-600">
      <Key className="h-2.5 w-2.5" /> API key
    </span>
  );
  if (source === "config_file") return (
    <span className="inline-flex items-center gap-0.5 rounded px-1.5 py-0.5 text-[9px] bg-emerald-500/10 text-emerald-600">
      <Wifi className="h-2.5 w-2.5" /> running
    </span>
  );
  if (source === "none") return (
    <span className="inline-flex items-center gap-0.5 rounded px-1.5 py-0.5 text-[9px] bg-amber-500/10 text-amber-600">
      <LogIn className="h-2.5 w-2.5" /> login needed
    </span>
  );
  return (
    <span className="inline-flex items-center gap-0.5 rounded px-1.5 py-0.5 text-[9px] bg-muted text-muted-foreground">
      <Download className="h-2.5 w-2.5" /> not installed
    </span>
  );
}

// ── OS Engine Selector ────────────────────────────────────────────

const ENGINE_DISPLAY: Record<string, { label: string; description: string; local: boolean; workerOnly?: boolean }> = {
  claude_code: {
    label: "Claude Code",
    description: "Full EAOS capabilities — hooks, /btw live, plan mode",
    local: false,
  },
  hermes: {
    label: "Hermes (Ollama)",
    description: "Local, no cloud egress — CONFIDENTIAL class, /btw buffered",
    local: true,
  },
  opencode: {
    label: "OpenCode",
    description: "Provider-agnostic — Claude, OpenAI or local Ollama model",
    local: false,
  },
  codex_cli: {
    label: "Codex CLI",
    description: "OpenAI Codex — MCP + stream_json, no /btw",
    local: false,
    workerOnly: true,
  },
  copilot: {
    label: "GitHub Copilot",
    description: "GitHub Copilot CLI — zero incremental cost for Copilot Business/Enterprise",
    local: false,
    workerOnly: true,
  },
};

function OsEngineCard({
  engineId,
  selected,
  healthy,
  credentialSource,
  onSelect,
}: {
  engineId: string;
  selected: boolean;
  healthy?: boolean;
  credentialSource?: CredentialSource;
  onSelect: () => void;
}) {
  const meta = ENGINE_DISPLAY[engineId] ?? {
    label: engineId,
    description: "",
    local: false,
    workerOnly: false,
  };
  return (
    <button
      onClick={onSelect}
      disabled={meta.workerOnly}
      className={cn(
        "w-full text-left rounded-lg border p-3 transition-colors",
        meta.workerOnly
          ? "opacity-50 cursor-not-allowed border-dashed border-border"
          : selected
          ? "border-primary bg-primary/5 ring-1 ring-primary"
          : "border-border hover:border-primary/50 hover:bg-muted/40",
      )}
    >
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 flex-wrap">
          <div
            className={cn(
              "h-2 w-2 rounded-full shrink-0",
              meta.workerOnly
                ? "bg-muted"
                : healthy === undefined
                ? "bg-muted"
                : healthy
                ? "bg-green-500"
                : "bg-red-400",
            )}
          />
          <span className="font-medium text-sm">{meta.label}</span>
          {meta.local && (
            <Badge variant="secondary" className="text-xs py-0">local</Badge>
          )}
          {meta.workerOnly && (
            <Badge variant="outline" className="text-xs py-0 text-muted-foreground">worker only</Badge>
          )}
          {credentialSource !== undefined && !meta.workerOnly && (
            <CredentialBadge source={credentialSource} />
          )}
        </div>
        {selected && <CheckCircle2 className="h-4 w-4 text-primary shrink-0" />}
      </div>
      <p className="mt-1 text-xs text-muted-foreground pl-4">{meta.description}</p>
    </button>
  );
}

// ── Milestone badges ──────────────────────────────────────────────

function MilestoneBadge({ id, status }: { id: string; status: string }) {
  const done = status === "done";
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium",
        done
          ? "bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-200"
          : "bg-muted text-muted-foreground",
      )}
    >
      {done ? <CheckCircle2 className="h-3 w-3" /> : <Circle className="h-3 w-3" />}
      {id}
    </span>
  );
}

// ── ADR-0126: Claude Code Local Backend section ───────────────────

function LocalBackendSection({
  csrf,
  onSaved,
}: {
  csrf: string;
  onSaved?: () => void;
}) {
  const qc = useQueryClient();
  const localQ = useQuery({
    queryKey: ["claude-local"],
    queryFn: ({ signal }) => getClaudeLocalSetting(signal),
    staleTime: 30_000,
  });

  const [open, setOpen] = React.useState(false);
  const [enabled, setEnabled] = React.useState(false);
  const [baseUrl, setBaseUrl] = React.useState("http://localhost:11434");
  const [model, setModel] = React.useState("");
  const [advanced, setAdvanced] = React.useState(false);
  const [sonnet, setSonnet] = React.useState("");
  const [haiku, setHaiku] = React.useState("");
  const [opus, setOpus] = React.useState("");
  const [savedOk, setSavedOk] = React.useState(false);

  // Sync from server data once loaded
  React.useEffect(() => {
    if (!localQ.data) return;
    const d = localQ.data;
    setEnabled(d.enabled);
    setOpen(d.enabled);
    setBaseUrl(d.base_url || "http://localhost:11434");
    setSonnet(d.sonnet_model || "");
    setHaiku(d.haiku_model || "");
    setOpus(d.opus_model || "");
    // Simple model: if all three agree, show in unified dropdown
    if (d.sonnet_model === d.haiku_model && d.haiku_model === d.opus_model) {
      setModel(d.sonnet_model || "");
    } else if (d.sonnet_model || d.haiku_model || d.opus_model) {
      setAdvanced(true);
    }
  }, [localQ.data]);

  const data: ClaudeLocalSetting | undefined = localQ.data;
  const reachable = data?.ollama_reachable ?? false;
  const models = data?.available_models ?? [];

  const saveMut = useMutation({
    mutationFn: (body: Parameters<typeof setClaudeLocalSetting>[0]) =>
      setClaudeLocalSetting(body, csrf),
    onSuccess: () => {
      setSavedOk(true);
      setTimeout(() => setSavedOk(false), 2500);
      qc.invalidateQueries({ queryKey: ["claude-local"] });
      onSaved?.();
    },
  });

  const handleToggle = () => {
    const next = !enabled;
    setEnabled(next);
    setOpen(next);
  };

  const handleSave = () => {
    const resolvedModel = advanced ? "" : model;
    saveMut.mutate({
      enabled,
      base_url: baseUrl,
      sonnet_model: advanced ? sonnet : resolvedModel,
      haiku_model: advanced ? haiku : resolvedModel,
      opus_model: advanced ? opus : resolvedModel,
    });
  };

  return (
    <div className="mt-3 rounded-lg border border-border/60 bg-muted/20">
      {/* Header row */}
      <button
        className="flex w-full items-center justify-between px-4 py-3 text-sm font-medium"
        onClick={() => {
          if (!enabled) setOpen((o) => !o);
          else handleToggle();
        }}
      >
        <span className="flex items-center gap-2">
          <Zap className="h-3.5 w-3.5 text-violet-500" />
          Local Backend (Ollama / LM Studio)
          {enabled && (
            <span className="inline-flex items-center gap-0.5 rounded px-1.5 py-0.5 text-[9px] font-semibold bg-violet-500/15 text-violet-700 dark:text-violet-400">
              <Lock className="h-2.5 w-2.5" /> local
            </span>
          )}
        </span>
        <span className="flex items-center gap-2">
          {/* Toggle switch */}
          <span
            role="switch"
            aria-checked={enabled}
            onClick={(e) => { e.stopPropagation(); handleToggle(); }}
            className={cn(
              "relative inline-flex h-5 w-9 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors",
              enabled ? "bg-violet-500" : "bg-input",
            )}
          >
            <span
              className={cn(
                "pointer-events-none block h-4 w-4 rounded-full bg-background shadow-lg ring-0 transition-transform",
                enabled ? "translate-x-4" : "translate-x-0",
              )}
            />
          </span>
          <ChevronDown
            className={cn(
              "h-3.5 w-3.5 text-muted-foreground transition-transform",
              open && "rotate-180",
            )}
            onClick={(e) => { e.stopPropagation(); setOpen((o) => !o); }}
          />
        </span>
      </button>

      {/* Expandable body */}
      {open && (
        <div className="border-t border-border/60 px-4 pb-4 pt-3 space-y-3">
          {/* URL field */}
          <div className="space-y-1">
            <label className="text-xs font-medium text-muted-foreground">Ollama URL</label>
            <div className="flex items-center gap-2">
              <input
                type="text"
                value={baseUrl}
                onChange={(e) => setBaseUrl(e.target.value)}
                placeholder="http://localhost:11434"
                className="flex-1 rounded-md border border-input bg-background px-3 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-ring"
              />
              <span
                className={cn(
                  "h-2 w-2 shrink-0 rounded-full",
                  reachable ? "bg-green-500" : "bg-red-400",
                )}
                title={reachable ? "Reachable" : "Unreachable"}
              />
            </div>
            {reachable ? (
              <p className="text-xs text-green-600 dark:text-green-400">
                <Wifi className="inline h-3 w-3 mr-1" />
                Reachable · {models.length} model{models.length !== 1 ? "s" : ""} available
              </p>
            ) : (
              <p className="text-xs text-red-500">
                <WifiOff className="inline h-3 w-3 mr-1" />
                Ollama not reachable at this URL
              </p>
            )}
          </div>

          {/* Model selector (simple mode) */}
          {!advanced && (
            <div className="space-y-1">
              <label className="text-xs font-medium text-muted-foreground">
                Model <span className="font-normal">(all tiers: Sonnet / Haiku / Opus)</span>
              </label>
              {models.length > 0 ? (
                <select
                  value={model}
                  onChange={(e) => setModel(e.target.value)}
                  className="w-full rounded-md border border-input bg-background px-3 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-ring"
                >
                  <option value="">— select model —</option>
                  {models.map((m) => (
                    <option key={m} value={m}>{m}</option>
                  ))}
                </select>
              ) : (
                <input
                  type="text"
                  value={model}
                  onChange={(e) => setModel(e.target.value)}
                  placeholder="e.g. qwen3:8b"
                  className="w-full rounded-md border border-input bg-background px-3 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-ring"
                />
              )}
            </div>
          )}

          {/* Advanced per-tier controls */}
          <button
            className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
            onClick={() => setAdvanced((a) => !a)}
          >
            <ChevronDown className={cn("h-3 w-3 transition-transform", advanced && "rotate-180")} />
            {advanced ? "Hide" : "Advanced"}: set per tier
          </button>
          {advanced && (
            <div className="space-y-2 pl-4 border-l border-border/40">
              {[
                { label: "Sonnet tier", val: sonnet, set: setSonnet },
                { label: "Haiku tier", val: haiku, set: setHaiku },
                { label: "Opus tier", val: opus, set: setOpus },
              ].map(({ label, val, set }) => (
                <div key={label} className="space-y-0.5">
                  <label className="text-xs font-medium text-muted-foreground">{label}</label>
                  {models.length > 0 ? (
                    <select
                      value={val}
                      onChange={(e) => set(e.target.value)}
                      className="w-full rounded-md border border-input bg-background px-3 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-ring"
                    >
                      <option value="">— select model —</option>
                      {models.map((m) => (
                        <option key={m} value={m}>{m}</option>
                      ))}
                    </select>
                  ) : (
                    <input
                      type="text"
                      value={val}
                      onChange={(e) => set(e.target.value)}
                      placeholder="e.g. qwen3:8b"
                      className="w-full rounded-md border border-input bg-background px-3 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-ring"
                    />
                  )}
                </div>
              ))}
            </div>
          )}

          {/* CONFIDENTIAL note */}
          <p className="flex items-start gap-1.5 text-xs text-violet-600 dark:text-violet-400 bg-violet-500/5 rounded-md px-2.5 py-2">
            <Shield className="h-3.5 w-3.5 shrink-0 mt-0.5" />
            <span>
              <strong>CONFIDENTIAL-capable</strong> when enabled — data stays on device, no Anthropic API calls
            </span>
          </p>

          {/* Save button */}
          <div className="flex items-center gap-2 pt-1">
            <Button size="sm" onClick={handleSave} disabled={saveMut.isPending}>
              {savedOk ? "Saved ✓" : "Save"}
            </Button>
            {saveMut.isError && (
              <span className="text-xs text-red-500">Error: {String(saveMut.error)}</span>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// ── Main Page ─────────────────────────────────────────────────────

export function EngineControlPage({ embedded = false }: { embedded?: boolean } = {}) {
  const { session } = useAuth();
  const csrf = session?.csrf_token ?? "";
  const qc = useQueryClient();

  const settingQ = useQuery({
    queryKey: ["os-engine-setting"],
    queryFn: ({ signal }) => getOsEngineSetting(signal),
  });
  const healthQ = useQuery({
    queryKey: ["os-engine-health"],
    queryFn: ({ signal }) => getOsEngineHealth(signal),
    refetchInterval: 15_000,
  });
  const catalogQ = useQuery({
    queryKey: ["engine-catalog"],
    queryFn: ({ signal }) => getEngineCatalog(signal),
  });
  const capQ = useQuery({
    queryKey: ["engine-capabilities"],
    queryFn: ({ signal }) => getEngineCapabilities(signal),
    staleTime: 30_000,
  });
  const detectQ = useQuery({
    queryKey: ["engine-detect"],
    queryFn: ({ signal }) => detectEngines(signal),
    staleTime: 3 * 60_000,
    refetchOnWindowFocus: false,
  });

  // Build a lookup: engine_id → credential_source
  const credMap = React.useMemo<Record<string, CredentialSource>>(() => {
    const out: Record<string, CredentialSource> = {};
    for (const r of detectQ.data?.results ?? []) {
      out[r.engine_id] = r.credential_source;
    }
    return out;
  }, [detectQ.data]);

  const [selected, setSelected] = React.useState<string | null>(null);
  const [activePanel, setActivePanel] = React.useState<string>("claude_code");
  const [saved, setSaved] = React.useState(false);

  React.useEffect(() => {
    if (settingQ.data && selected === null) {
      const eng = settingQ.data.default_engine ?? "claude_code";
      setSelected(eng);
      setActivePanel(eng);
    }
  }, [settingQ.data, selected]);

  const saveMut = useMutation({
    mutationFn: (engine: string) =>
      setOsEngineSetting({ default_engine: engine, hermes_model: null }, csrf),
    onSuccess: () => {
      setSaved(true);
      setTimeout(() => setSaved(false), 2500);
      qc.invalidateQueries({ queryKey: ["os-engine-setting"] });
      qc.invalidateQueries({ queryKey: ["os-engine-setting"] });
    },
  });

  const catalog = catalogQ.data ?? [];
  const capMatrix = capQ.data;
  const milestones = capMatrix?.eaos_milestones ?? {};
  const ollama = healthQ.data;

  // Show only engines that are detected as installed on this system.
  // Falls back to full catalog until detect has loaded or if it returns nothing.
  const detectedEcIds = detectQ.data?.results?.length
    ? new Set(detectQ.data.results.filter((r) => r.installed).map((r) => r.engine_id))
    : null;
  const configuredEcId = settingQ.data?.default_engine ?? "claude_code";
  const engineIds = catalog.length
    ? (detectedEcIds
        ? catalog
            .filter((e) => detectedEcIds.has(e.id) || e.id === configuredEcId)
            .map((e) => e.id)
        : catalog.map((e) => e.id))
    : ["claude_code", "hermes", "opencode", "codex_cli", "copilot"];

  return (
    <div className={embedded ? "space-y-6" : "p-6 space-y-6 max-w-6xl mx-auto"}>
      {/* Header */}
      <div className="flex items-start justify-between gap-4">
        {!embedded && (
        <div>
          <h1 className="text-2xl font-semibold flex items-center gap-2">
            <Cpu className="h-6 w-6" />
            Engine Control Center
          </h1>
          <p className="text-sm text-muted-foreground mt-1">
            Choose and configure which AI engine powers your assistant
          </p>
        </div>
        )}
        <div className="flex flex-wrap items-center gap-1.5">
          {Object.entries(milestones).map(([id, status]) => (
            <MilestoneBadge key={id} id={id} status={status} />
          ))}
          <button
            onClick={() => qc.invalidateQueries({ queryKey: ["engine-detect"] })}
            className="inline-flex items-center gap-1 rounded px-2 py-1 text-xs text-muted-foreground hover:text-foreground border border-border hover:border-foreground/20 transition-colors"
            title="Re-detect installed engines"
          >
            <RefreshCw className={cn("h-3 w-3", detectQ.isFetching && "animate-spin")} />
            Re-detect
          </button>
        </div>
      </div>

      {/* Detection status strip */}
      {!detectQ.isLoading && detectQ.data && detectQ.data.results.length > 0 && (
        <div className="flex flex-wrap items-center gap-2 rounded-lg border bg-muted/20 px-4 py-2">
          <span className="text-xs text-muted-foreground font-medium shrink-0">Detected:</span>
          {detectQ.data.results.map((r) => (
            <span key={r.engine_id} className="flex items-center gap-1">
              <span className="text-xs font-medium">
                {r.engine_id === "claude_code" ? "Claude" :
                 r.engine_id === "hermes" ? "Hermes" :
                 r.engine_id === "copilot" ? "Copilot" :
                 r.engine_id === "opencode" ? "OpenCode" :
                 r.engine_id === "codex_cli" ? "Codex" : r.engine_id}
              </span>
              <CredentialBadge source={r.credential_source} />
            </span>
          ))}
        </div>
      )}

      {/* Two-panel: OS Engine + Capability side */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* LEFT: OS Engine Selector */}
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-base flex items-center gap-2">
              <Server className="h-4 w-4" />
              Primary AI Engine
              <span className="text-xs font-normal text-muted-foreground">
                (main conversation)
              </span>
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {settingQ.isPending ? (
              <Skeleton className="h-20 w-full" />
            ) : (
              engineIds.map((id) => (
                <OsEngineCard
                  key={id}
                  engineId={id}
                  selected={selected === id}
                  healthy={
                    id === "hermes"
                      ? ollama?.ollama_reachable
                      : id === "claude_code"
                      ? true
                      : undefined
                  }
                  credentialSource={credMap[id]}
                  onSelect={() => {
                    setSelected(id);
                    setActivePanel(id);
                  }}
                />
              ))
            )}

            <div className="pt-2 flex items-center gap-2">
              <Button
                size="sm"
                disabled={selected === settingQ.data?.default_engine || saveMut.isPending}
                onClick={() => selected && saveMut.mutate(selected)}
              >
                {saved ? "Saved ✓" : "Save"}
              </Button>
              {saveMut.isError && (
                <span className="text-xs text-red-500">Error saving</span>
              )}
            </div>

            {/* Hermes health detail */}
            {ollama && (
              <div className="mt-2 flex items-center gap-2 text-xs text-muted-foreground border-t pt-2">
                {ollama.ollama_reachable ? (
                  <Wifi className="h-3 w-3 text-green-500" />
                ) : (
                  <WifiOff className="h-3 w-3 text-red-400" />
                )}
                Ollama{" "}
                {ollama.ollama_reachable
                  ? `reachable — ${ollama.model_count} models`
                  : "not reachable"}
              </div>
            )}

            {/* ADR-0126: Claude Code Local Backend section */}
            {(selected === "claude_code" || (settingQ.data?.default_engine ?? "claude_code") === "claude_code") && (
              <LocalBackendSection
                csrf={csrf}
                onSaved={() => qc.invalidateQueries({ queryKey: ["os-engine-setting"] })}
              />
            )}
          </CardContent>
        </Card>

        {/* RIGHT: ECI Commands for selected engine */}
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-base flex items-center gap-2">
              <Terminal className="h-4 w-4" />
              ECI Commands
              <Badge variant="outline" className="text-xs font-mono">
                {activePanel}
              </Badge>
            </CardTitle>
          </CardHeader>
          <CardContent>
            {capQ.isPending ? (
              <Skeleton className="h-32 w-full" />
            ) : capMatrix ? (
              <EciCommandPanel engineId={activePanel} matrix={capMatrix} />
            ) : (
              <p className="text-sm text-muted-foreground">
                Capabilities could not be loaded.
              </p>
            )}
          </CardContent>
        </Card>
      </div>

      {/* Capability Matrix */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base flex items-center gap-2">
            <Activity className="h-4 w-4" />
            Capability Matrix
          </CardTitle>
        </CardHeader>
        <CardContent>
          {capQ.isPending ? (
            <Skeleton className="h-40 w-full" />
          ) : capMatrix ? (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b">
                    <th className="text-left py-2 pr-4 font-medium text-muted-foreground w-36">
                      Capability
                    </th>
                    {engineIds.map((id) => (
                      <th
                        key={id}
                        className={cn(
                          "text-center py-2 px-3 font-medium cursor-pointer transition-colors",
                          activePanel === id
                            ? "text-primary"
                            : "text-muted-foreground hover:text-foreground",
                        )}
                        onClick={() => setActivePanel(id)}
                      >
                        {ENGINE_DISPLAY[id]?.label ?? id}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {CAP_ROWS.map((row) => (
                    <tr key={row.key} className="border-b last:border-0 hover:bg-muted/30">
                      <td className="py-2 pr-4 text-xs" title={row.tooltip}>
                        <span className="flex items-center gap-1">
                          {row.label}
                          <Info className="h-3 w-3 text-muted-foreground/60" />
                        </span>
                      </td>
                      {engineIds.map((id) => {
                        const cv = capValue(id, row.key, capMatrix);
                        const m = CELL_META[cv];
                        return (
                          <td
                            key={id}
                            className={cn(
                              "text-center py-2 px-3 text-xs",
                              m.className,
                              activePanel === id && "bg-primary/5",
                            )}
                          >
                            {m.label}
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="mt-2 text-xs text-muted-foreground">
                ✅ native — direct engine support &nbsp;|&nbsp;
                ✅ TEB — via Tool Execution Broker &nbsp;|&nbsp;
                🔶 FCB — Function-Call Bridge &nbsp;|&nbsp;
                📦 buffered — takes effect on next turn &nbsp;|&nbsp;
                ✗ not available
              </p>
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">
              Matrix could not be loaded.
            </p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
