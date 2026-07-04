/**
 * Engines — AI engine configuration hub.
 * Route: /app/engines
 *
 * Architecture: Corvin is engine-agnostic. Five engines are available
 * across two roles:
 *
 *   OS engines (run the Corvin turn directly):
 *     Claude Code · OpenCode · Hermes
 *
 *   Worker engines (delegation targets via mcp__corvin_delegate__*):
 *     All OS engines + Codex CLI + GitHub Copilot
 *
 * This page lets operators configure:
 *   1. OS Engine     — which engine IS Corvin (tenant-level default)
 *   2. Worker Engine — which engine Corvin delegates sub-tasks to
 *   3. API keys      — cloud engine credentials
 *   4. Custom Engines — register third-party OpenAI-compat/Anthropic/Ollama endpoints
 */
import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowRight,
  Check,
  CheckCircle2,
  Cloud,
  Cpu,
  Download,
  ExternalLink,
  Eye,
  EyeOff,
  Github,
  Key,
  LogIn,
  RefreshCw,
  ScanSearch,
  Settings2,
  Sparkles,
  Star,
  Wifi,
  WifiOff,
  Zap,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { EngineControlPage } from "@/pages/engine-control";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ReauthDialog } from "@/components/reauth-dialog";
import { useAuth } from "@/lib/auth";
import {
  listEngines, updateEngineKey,
  getOsEngineSetting, setOsEngineSetting, getOsEngineHealth,
  getEngineCatalog, getLicenseInfo, getEngineModelRegistry,
  getEngineProviders, getProviderModels,
  listCustomEngines, registerCustomEngine, removeCustomEngine, pingCustomEngine,
  detectEngines, bootstrapHermes,
  type EngineInfo, type EngineCatalogEntry, type EngineModelConfig,
  type CustomEngineManifest, type CustomEngineRegisterRequest,
  type EngineProbeResult, type CredentialSource,
  type ProviderSpec, type ProviderModelsResponse,
} from "@/lib/api";
import { LicenseGate, isEngineAllowed } from "@/components/license-gate";
import { cn } from "@/lib/utils";
// Helper to safely convert model data to array
function asArray<T>(data: unknown): T[] {
  if (Array.isArray(data)) return data;
  if (data && typeof data === "object") return Object.values(data) as T[];
  return [];
}


// Helper to safely convert model data to array

function EngineIcon({ engineId, className }: { engineId: string; className?: string }) {
  if (engineId === "hermes") return <Cpu className={className} />;
  if (engineId === "copilot") return <Github className={className} />;
  return <Cloud className={className} />;
}

// ── ADR-0125: Credential badge ────────────────────────────────────

const CRED_SETUP: Record<string, { hint: string; cmd?: string; url?: string }> = {
  claude_code: {
    hint: "Run to authenticate:",
    cmd: "claude login",
    url: "https://claude.ai/code",
  },
  copilot: {
    hint: "Run to authenticate:",
    cmd: "copilot auth login",
    url: "https://github.com/github/copilot-cli",
  },
  hermes: {
    hint: "Start Ollama or pull a model:",
    cmd: "ollama serve",
  },
  opencode: {
    hint: "Set a provider key:",
    cmd: "export ANTHROPIC_API_KEY=sk-...",
  },
  codex_cli: {
    hint: "Set OpenAI key:",
    cmd: "export OPENAI_API_KEY=sk-...",
  },
};

function CredBadge({ source }: { source: CredentialSource }) {
  if (source === "subscription") return (
    <Badge className="text-[9px] px-1.5 py-0 h-4 bg-emerald-500/15 text-emerald-700 dark:text-emerald-400 border-emerald-400/40 gap-0.5 font-semibold">
      <Star className="h-2.5 w-2.5" /> subscription
    </Badge>
  );
  if (source === "env_var") return (
    <Badge variant="outline" className="text-[9px] px-1.5 py-0 h-4 text-blue-600 border-blue-400/40 gap-0.5">
      <Key className="h-2.5 w-2.5" /> API key
    </Badge>
  );
  if (source === "config_file") return (
    <Badge variant="outline" className="text-[9px] px-1.5 py-0 h-4 text-emerald-600 border-emerald-400/40 gap-0.5">
      <Wifi className="h-2.5 w-2.5" /> running
    </Badge>
  );
  if (source === "none") return (
    <Badge variant="outline" className="text-[9px] px-1.5 py-0 h-4 text-amber-600 border-amber-400/40 gap-0.5">
      <LogIn className="h-2.5 w-2.5" /> login needed
    </Badge>
  );
  if (source === "discovered") return (
    <Badge variant="outline" className="text-[9px] px-1.5 py-0 h-4 text-violet-600 border-violet-400/40 gap-0.5">
      <ScanSearch className="h-2.5 w-2.5" /> auto-detected
    </Badge>
  );
  // null = not installed (should not appear in UI after filtering)
  return (
    <Badge variant="outline" className="text-[9px] px-1.5 py-0 h-4 text-muted-foreground gap-0.5">
      <Download className="h-2.5 w-2.5" /> not installed
    </Badge>
  );
}

function EngineProbeCard({ probe }: { probe: EngineProbeResult }) {
  const isReady = probe.authenticated;
  const setup = CRED_SETUP[probe.engine_id];

  return (
    <div className={cn(
      "rounded-lg border p-3 transition-all",
      probe.credential_source === "discovered"
        ? "border-violet-400/25 bg-violet-500/5"
        : isReady
        ? probe.credential_source === "subscription"
          ? "border-emerald-500/30 bg-emerald-500/5"
          : probe.credential_source === "config_file"
          ? "border-emerald-400/25 bg-emerald-500/5"
          : "border-blue-400/25 bg-blue-500/5"
        : "border-amber-400/20 bg-amber-500/5",
    )}>
      <div className="flex items-center gap-2 mb-1">
        <EngineIcon
          engineId={probe.engine_id}
          className={cn(
            "h-3.5 w-3.5 shrink-0",
            probe.credential_source === "discovered"
              ? "text-violet-500"
              : isReady
              ? probe.credential_source === "subscription" ? "text-emerald-500" : "text-blue-500"
              : "text-amber-500",
          )}
        />
        <span className="font-medium text-sm">
          {probe.engine_id === "claude_code" ? "Claude Code"
            : probe.engine_id === "hermes" ? "Hermes"
            : probe.engine_id === "codex_cli" ? "Codex CLI"
            : probe.engine_id === "copilot" ? "GitHub Copilot"
            : probe.engine_id === "opencode" ? "OpenCode"
            : probe.engine_id}
        </span>
        <CredBadge source={probe.credential_source} />
        {probe.version && (
          <span className="text-[10px] text-muted-foreground font-mono ml-auto shrink-0">
            v{probe.version.replace(/^v/, "")}
          </span>
        )}
        {isReady && (
          <CheckCircle2 className={cn(
            "h-3.5 w-3.5 shrink-0",
            probe.version ? "" : "ml-auto",
            probe.credential_source === "subscription" ? "text-emerald-500" : "text-blue-500",
          )} />
        )}
      </div>

      {/* Status detail */}
      {probe.detail && (
        <p className="text-[11px] text-muted-foreground pl-5 leading-relaxed">
          {probe.detail}
        </p>
      )}

      {/* Hermes model list */}
      {probe.engine_id === "hermes" && probe.models.length > 0 && (
        <div className="pl-5 mt-1 flex flex-wrap gap-1">
          {probe.models.slice(0, 6).map((m) => (
            <span key={m} className="text-[9px] font-mono bg-muted/60 text-muted-foreground rounded px-1">
              {m}
            </span>
          ))}
          {probe.models.length > 6 && (
            <span className="text-[9px] text-muted-foreground">+{probe.models.length - 6} more</span>
          )}
        </div>
      )}

      {/* Setup hint — not shown for auto-discovered engines (no integration yet) */}
      {!isReady && probe.credential_source !== "discovered" && setup && (
        <div className="pl-5 mt-2 space-y-1">
          <p className="text-[10px] text-muted-foreground">{setup.hint}</p>
          {setup.cmd && (
            <code className="block text-[10px] font-mono bg-muted/50 text-foreground rounded px-2 py-1">
              {setup.cmd}
            </code>
          )}
          {setup.url && (
            <a
              href={setup.url}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1 text-[10px] text-accent underline underline-offset-2"
            >
              {probe.installed ? "Documentation" : "Download"} <ExternalLink className="h-2.5 w-2.5" />
            </a>
          )}
        </div>
      )}
    </div>
  );
}

// ── ADR-0125: Detected Engines Section (primary) ──────────────────

function DetectedEnginesSection({ csrf }: { csrf: string }) {
  const qc = useQueryClient();

  const detectQ = useQuery({
    queryKey: ["engine-detect"],
    queryFn: ({ signal }) => detectEngines(signal),
    staleTime: 3 * 60_000,   // 3 min — installs / logins don't change often
    refetchOnWindowFocus: false,
  });

  const bootstrapMut = useMutation({
    mutationFn: () => bootstrapHermes(csrf),
    onSuccess: () => {
      // After bootstrap, re-detect to pick up Ollama + new model.
      qc.invalidateQueries({ queryKey: ["engine-detect"] });
      qc.invalidateQueries({ queryKey: ["os-engine-health"] });
    },
  });

  const results = detectQ.data?.results ?? [];
  const recommended = detectQ.data?.recommended_engine;
  const needsBootstrap = detectQ.data?.needs_bootstrap ?? false;

  // Only show engines that are actually installed (hide not-found probes).
  // "discovered" engines always pass this filter (installed=true by construction).
  const visibleResults = results.filter((r) => r.installed);

  // Sort: authenticated → login-needed → discovered (not yet integrated).
  const sorted = [...visibleResults].sort((a, b) => {
    const rank = (r: EngineProbeResult) =>
      r.authenticated ? 0 : r.credential_source === "discovered" ? 2 : 1;
    return rank(a) - rank(b);
  });

  const readyCount = results.filter((r) => r.authenticated).length;

  return (
    <Card className="border-2 border-accent/30">
      <CardContent className="pt-5 pb-4 space-y-4">
        {/* Header */}
        <div className="flex items-center justify-between">
          <div>
            <h2 className="font-semibold text-sm flex items-center gap-2">
              <Sparkles className="h-4 w-4 text-accent" />
              Detected Engines
              {!detectQ.isLoading && (
                <Badge
                  className={cn(
                    "text-[9px] px-1.5 py-0 h-4",
                    readyCount > 0
                      ? "bg-emerald-500/15 text-emerald-700 dark:text-emerald-400 border-emerald-400/40"
                      : "bg-amber-500/15 text-amber-700 dark:text-amber-400 border-amber-400/40",
                  )}
                >
                  {readyCount} ready
                </Badge>
              )}
            </h2>
            <p className="text-xs text-muted-foreground mt-0.5">
              Engines found on this system — subscription auth takes priority over API keys.
            </p>
          </div>
          <Button
            size="sm"
            variant="ghost"
            className="text-xs gap-1 h-7"
            onClick={() => qc.invalidateQueries({ queryKey: ["engine-detect"] })}
            disabled={detectQ.isFetching}
          >
            <RefreshCw className={cn("h-3 w-3", detectQ.isFetching && "animate-spin")} />
            Re-detect
          </Button>
        </div>

        {/* Loading */}
        {detectQ.isLoading && (
          <div className="space-y-2">
            {Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} className="h-16 w-full" />)}
          </div>
        )}

        {/* Engine cards */}
        {!detectQ.isLoading && sorted.length > 0 && (
          <div className="space-y-2">
            {sorted.map((probe) => (
              <EngineProbeCard key={probe.engine_id} probe={probe} />
            ))}
          </div>
        )}

        {/* Recommended engine callout */}
        {!detectQ.isLoading && recommended && (
          <div className="flex items-center gap-2 rounded-md bg-accent/5 border border-accent/20 px-3 py-2">
            <Check className="h-3.5 w-3.5 text-accent shrink-0" />
            <p className="text-[11px] text-muted-foreground">
              <span className="font-medium text-foreground">
                {recommended === "claude_code" ? "Claude Code"
                  : recommended === "hermes" ? "Hermes"
                  : recommended === "copilot" ? "GitHub Copilot"
                  : recommended === "opencode" ? "OpenCode"
                  : recommended === "codex_cli" ? "Codex CLI"
                  : recommended}
              </span>
              {" "}is ready — set it as OS engine below, or use{" "}
              <span className="font-mono">/engine {recommended === "claude_code" ? "claude" : recommended}</span>
              {" "}per chat.
            </p>
          </div>
        )}

        {/* Bootstrap CTA — shown when no engine is ready */}
        {!detectQ.isLoading && needsBootstrap && (
          <div className="rounded-lg border border-dashed border-amber-400/40 bg-amber-500/5 p-4 space-y-3">
            <div className="flex items-start gap-2">
              <AlertTriangle className="h-4 w-4 text-amber-500 shrink-0 mt-0.5" />
              <div>
                <p className="text-sm font-medium text-amber-700 dark:text-amber-400">
                  No engine ready
                </p>
                <p className="text-xs text-muted-foreground mt-0.5">
                  Install a model locally or authenticate a cloud engine above.
                  Hermes uses Ollama — fully local, zero cloud egress, no API key needed.
                </p>
              </div>
            </div>
            <Button
              size="sm"
              className="text-xs gap-1.5 bg-amber-600 hover:bg-amber-700 text-white"
              onClick={() => bootstrapMut.mutate()}
              disabled={bootstrapMut.isPending}
            >
              <Download className="h-3.5 w-3.5" />
              {bootstrapMut.isPending ? "Installing Hermes…" : "Bootstrap Hermes (local AI, ~4 GB)"}
            </Button>
            {bootstrapMut.data && (
              <div className="text-xs space-y-0.5">
                <p className="font-medium text-foreground">
                  {bootstrapMut.data.model_pulled ? "✅ Bootstrap complete" : "⚠ Bootstrap partial"}
                </p>
                <p className="text-muted-foreground">
                  Model: <span className="font-mono">{bootstrapMut.data.model_selected}</span>
                  {" "}· RAM: {bootstrapMut.data.ram_gb} GB
                </p>
                {bootstrapMut.data.error && (
                  <p className="text-destructive">{bootstrapMut.data.error}</p>
                )}
              </div>
            )}
            {bootstrapMut.isError && (
              <p className="text-xs text-destructive">
                {bootstrapMut.error instanceof Error ? bootstrapMut.error.message : "Bootstrap failed"}
              </p>
            )}
          </div>
        )}

        {/* Detection error */}
        {detectQ.data?.error && (
          <p className="text-xs text-muted-foreground">
            Detection partial: {detectQ.data.error}
          </p>
        )}
      </CardContent>
    </Card>
  );
}

// ── Architecture Overview ─────────────────────────────────────────

const ENGINE_ROLES: Array<{
  id: string;
  label: string;
  role: "os+worker" | "worker";
  locality: "local" | "cloud" | "github";
  cost: string;
  note?: string;
}> = [
  { id: "claude_code", label: "Claude Code",     role: "os+worker", locality: "cloud",  cost: "Per token" },
  { id: "opencode",    label: "OpenCode",         role: "os+worker", locality: "cloud",  cost: "Per token / free (Ollama)" },
  { id: "hermes",      label: "Hermes",           role: "os+worker", locality: "local",  cost: "Zero (after model download)", note: "CONFIDENTIAL-capable" },
  { id: "codex_cli",   label: "Codex CLI",        role: "worker",    locality: "cloud",  cost: "Per token" },
  { id: "copilot",     label: "GitHub Copilot",   role: "worker",    locality: "github", cost: "Zero (Copilot Business/Enterprise)", note: "Copilot Business/Enterprise plan required" },
];

function ArchitectureOverview() {
  return (
    <Card>
      <CardContent className="pt-5 pb-4">
        <div className="flex items-center justify-between mb-3">
          <div>
            <h2 className="font-semibold text-sm">Engine Architecture</h2>
            <p className="text-xs text-muted-foreground mt-0.5">
              Corvin is engine-agnostic — any of the five engines can run sub-tasks.
              OS engines also host the main conversation.
            </p>
          </div>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b text-muted-foreground">
                <th className="text-left py-2 pr-4 font-medium">Engine</th>
                <th className="text-left py-2 pr-4 font-medium">Role</th>
                <th className="text-left py-2 pr-4 font-medium">Locality</th>
                <th className="text-left py-2 font-medium">Cost model</th>
              </tr>
            </thead>
            <tbody>
              {ENGINE_ROLES.map((e) => (
                <tr key={e.id} className="border-b last:border-0 hover:bg-muted/20">
                  <td className="py-2 pr-4">
                    <div className="flex items-center gap-1.5">
                      <EngineIcon
                        engineId={e.id}
                        className={cn("h-3.5 w-3.5", e.locality === "local" ? "text-emerald-500" : "text-muted-foreground")}
                      />
                      <span className="font-medium">{e.label}</span>
                      {e.note && (
                        <span className="text-[9px] bg-muted text-muted-foreground rounded px-1">{e.note}</span>
                      )}
                    </div>
                  </td>
                  <td className="py-2 pr-4">
                    {e.role === "os+worker" ? (
                      <span className="flex items-center gap-1 text-foreground">
                        OS <ArrowRight className="h-3 w-3 text-muted-foreground" /> Worker
                      </span>
                    ) : (
                      <span className="text-muted-foreground">Worker only</span>
                    )}
                  </td>
                  <td className="py-2 pr-4">
                    {e.locality === "local" ? (
                      <span className="text-emerald-600 font-medium">On-premise</span>
                    ) : e.locality === "github" ? (
                      <span className="text-blue-600">GitHub cloud</span>
                    ) : (
                      <span className="text-muted-foreground">Cloud</span>
                    )}
                  </td>
                  <td className="py-2 text-muted-foreground">{e.cost}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </CardContent>
    </Card>
  );
}

// ── OS Engine Selector (fully catalog-driven) ─────────────────────

function OsEngineSelector({ csrf }: { csrf: string }) {
  const qc = useQueryClient();

  const catalogQ = useQuery({
    queryKey: ["engine-catalog"],
    queryFn: ({ signal }) => getEngineCatalog(signal),
    staleTime: 5 * 60_000,
  });

  const settingQ = useQuery({
    queryKey: ["os-engine-setting"],
    queryFn: ({ signal }) => getOsEngineSetting(signal),
  });

  const healthQ = useQuery({
    queryKey: ["os-engine-health"],
    queryFn: ({ signal }) => getOsEngineHealth(signal),
    refetchInterval: 15_000,
  });

  const detectQ = useQuery({
    queryKey: ["engine-detect"],
    queryFn: ({ signal }) => detectEngines(signal),
    staleTime: 3 * 60_000,
    refetchOnWindowFocus: false,
  });

  const [selected, setSelected] = React.useState<string | null>(null);
  const [modelOverride, setModelOverride] = React.useState<string>("");
  const [saved, setSaved] = React.useState(false);

  // Sync local state from server
  React.useEffect(() => {
    if (settingQ.data && selected === null) {
      setSelected(settingQ.data.default_engine ?? "claude_code");
      setModelOverride(settingQ.data.hermes_model ?? "");
    }
  }, [settingQ.data, selected]);

  const mutation = useMutation({
    mutationFn: (body: {
      default_engine: string | null;
      hermes_model: string | null;
      default_worker_engine: string | null;
      default_worker_model: string | null;
    }) => setOsEngineSetting(body, csrf),
    onSuccess: () => {
      setSaved(true);
      setTimeout(() => setSaved(false), 2500);
      qc.invalidateQueries({ queryKey: ["os-engine-setting"] });
    },
  });

  // Filter engines to those detected as installed on this system.
  // Falls back to all-engines when detect hasn't loaded yet or returned nothing.
  const allEnginesRaw = asArray<EngineCatalogEntry>(catalogQ.data) ?? [];
  const detectedOsIds = detectQ.data?.results?.length
    ? new Set(detectQ.data.results.filter((r) => r.installed).map((r) => r.engine_id))
    : null;
  const configuredOsId = settingQ.data?.default_engine ?? "claude_code";
  // Always include the currently configured engine so it doesn't disappear.
  const allEngines = detectedOsIds
    ? allEnginesRaw.filter((e) => detectedOsIds.has(e.id) || e.id === configuredOsId)
    : allEnginesRaw;
  const selectedMeta = allEngines.find((e) => e.id === selected);
  const currentEngine = settingQ.data?.default_engine ?? "claude_code";
  const hasModelAliases = (selectedMeta?.model_aliases ?? []).length > 0;

  const isDirty = selected !== null && (
    selected !== currentEngine ||
    (hasModelAliases && modelOverride !== (settingQ.data?.hermes_model ?? ""))
  );

  const handleSave = () => {
    mutation.mutate({
      default_engine: selected === "claude_code" ? null : selected,
      hermes_model: (selected === "hermes" && modelOverride) ? modelOverride : null,
      default_worker_engine: settingQ.data?.default_worker_engine ?? null,
      default_worker_model: settingQ.data?.default_worker_model ?? null,
    });
  };

  const ollama = healthQ.data;
  const isLoading = catalogQ.isLoading || settingQ.isLoading;

  return (
    <Card className="border-2 border-accent/20">
      <CardContent className="pt-5 pb-4 space-y-4">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="font-semibold text-sm">OS Engine</h2>
            <p className="text-xs text-muted-foreground mt-0.5">
              Which engine hosts Corvin itself — tenant-level default. Per-chat
              overrides via <span className="font-mono">/engine &lt;name&gt;</span>.
            </p>
          </div>
          {saved && (
            <Badge className="bg-emerald-500/10 text-emerald-600 border-emerald-500/30 text-xs gap-1">
              <Check className="h-3 w-3" /> Saved
            </Badge>
          )}
        </div>

        {isLoading && <Skeleton className="h-28 w-full" />}

        {!isLoading && (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
            {allEngines.map((eng) => {
              const isWorkerOnly = !eng.os_capable;
              const isSelected = !isWorkerOnly && (selected ?? "claude_code") === eng.id;
              const isLocal = eng.local;
              return (
                <button
                  key={eng.id}
                  disabled={isWorkerOnly}
                  onClick={() => { if (!isWorkerOnly) { setSelected(eng.id); setModelOverride(""); } }}
                  className={cn(
                    "rounded-lg border-2 p-4 text-left transition-all",
                    isWorkerOnly
                      ? "border-dashed border-border bg-muted/10 opacity-50 cursor-not-allowed"
                      : isSelected
                      ? isLocal
                        ? "border-emerald-500/50 bg-emerald-500/5"
                        : "border-accent bg-accent/5"
                      : "border-border bg-muted/20 hover:border-accent/50",
                  )}
                >
                  <div className="flex items-center gap-2 mb-2">
                    <div className={cn(
                      "rounded-md p-1.5",
                      isWorkerOnly
                        ? "bg-muted text-muted-foreground"
                        : isSelected
                        ? isLocal ? "bg-emerald-500/10 text-emerald-600" : "bg-accent/10 text-accent"
                        : "bg-muted text-muted-foreground",
                    )}>
                      <EngineIcon engineId={eng.id} className="h-4 w-4" />
                    </div>
                    <span className="font-semibold text-sm">{eng.label}</span>
                    {isLocal && !isWorkerOnly && (
                      <Badge variant="outline" className="text-[9px] px-1 py-0 border-emerald-500/40 text-emerald-600">
                        local
                      </Badge>
                    )}
                    {isWorkerOnly && (
                      <Badge variant="outline" className="text-[9px] px-1 py-0 text-muted-foreground ml-auto">
                        worker only
                      </Badge>
                    )}
                    {isSelected && !isWorkerOnly && (
                      <Check className={cn("h-3.5 w-3.5 ml-auto", isLocal ? "text-emerald-600" : "text-accent")} />
                    )}
                  </div>
                  <p className="text-xs text-muted-foreground leading-relaxed line-clamp-2">
                    {eng.description}
                  </p>
                  {/* Ollama status for Hermes */}
                  {eng.id === "hermes" && !isWorkerOnly && (
                    <div className="mt-2 flex items-center gap-1.5 text-[10px]">
                      {ollama?.ollama_reachable ? (
                        <>
                          <Wifi className="h-3 w-3 text-emerald-500" />
                          <span className="text-emerald-600">Ollama reachable</span>
                          <span className="text-muted-foreground">· {ollama.model_count} model{ollama.model_count !== 1 ? "s" : ""}</span>
                        </>
                      ) : (
                        <>
                          <WifiOff className="h-3 w-3 text-amber-500" />
                          <span className="text-amber-600">Ollama not running</span>
                        </>
                      )}
                    </div>
                  )}
                </button>
              );
            })}
          </div>
        )}

        {/* Model picker — for engines with aliases (e.g. Hermes) */}
        {!isLoading && selectedMeta && hasModelAliases && (
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
            {selectedMeta.model_examples && (
              <p className="text-[10px] text-muted-foreground font-mono">{selectedMeta.model_examples}</p>
            )}
          </div>
        )}

        {/* Capability warning for non-CC engines */}
        {!isLoading && selected && selected !== "claude_code" && (
          <div className="flex items-start gap-2 rounded-md bg-amber-500/8 border border-amber-500/20 px-3 py-2">
            <AlertTriangle className="h-3.5 w-3.5 text-amber-500 shrink-0 mt-0.5" />
            <p className="text-[11px] text-amber-700 dark:text-amber-400">
              {selected === "hermes"
                ? "Hermes lacks live /btw, forge MCP, skills injection, and hooks. Per-chat /engine claude restores full capability."
                : selected === "opencode"
                ? "OpenCode lacks live /btw and plan mode. Most features work via EAOS bridges."
                : "This engine may lack some EAOS features. Check Engine Control Center for the capability matrix."
              }
            </p>
          </div>
        )}

        {!isLoading && (
          <div className="flex items-center gap-3 pt-1">
            <Button size="sm" onClick={handleSave} disabled={!isDirty || mutation.isPending} className="text-xs">
              {mutation.isPending ? "Saving…" : "Save engine"}
            </Button>
            {mutation.isError && (
              <span className="text-xs text-destructive">
                {mutation.error instanceof Error 
                  ? mutation.error.message 
                  : typeof mutation.error === 'object' && mutation.error !== null && 'detail' in mutation.error
                  ? Array.isArray((mutation.error as { detail?: unknown }).detail)
                    ? `Validation error: ${((mutation.error as { detail: Array<{ msg?: unknown; message?: unknown }> }).detail)
                        .map((e) => String(e.msg || e.message || String(e)))
                        .join(', ')}`
                    : String((mutation.error as { detail?: unknown }).detail)
                  : "Save failed"}
              </span>
            )}
            <span className="ml-auto text-[10px] text-muted-foreground">Takes effect on next turn</span>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ── Worker Engine Selector (catalog-driven) ───────────────────────

function WorkerEngineSelector({ csrf }: { csrf: string }) {
  const qc = useQueryClient();

  const catalogQ = useQuery({
    queryKey: ["engine-catalog"],
    queryFn: ({ signal }) => getEngineCatalog(signal),
    staleTime: 5 * 60_000,
  });

  const settingQ = useQuery({
    queryKey: ["os-engine-setting"],
    queryFn: ({ signal }) => getOsEngineSetting(signal),
  });

  const detectQ = useQuery({
    queryKey: ["engine-detect"],
    queryFn: ({ signal }) => detectEngines(signal),
    staleTime: 3 * 60_000,
    refetchOnWindowFocus: false,
  });

  const [selectedWorker, setSelectedWorker] = React.useState<string | null>(null);
  const [workerModel, setWorkerModel] = React.useState("");
  const [saved, setSaved] = React.useState(false);

  React.useEffect(() => {
    if (settingQ.data && selectedWorker === null) {
      setSelectedWorker(settingQ.data.default_worker_engine ?? null);
      setWorkerModel(settingQ.data.default_worker_model ?? "");
    }
  }, [settingQ.data, selectedWorker]);

  const mutation = useMutation({
    mutationFn: (body: { default_worker_engine: string | null; default_worker_model: string | null }) =>
      setOsEngineSetting({
        default_engine: settingQ.data?.default_engine ?? null,
        hermes_model: settingQ.data?.hermes_model ?? null,
        ...body,
      }, csrf),
    onSuccess: () => {
      setSaved(true);
      setTimeout(() => setSaved(false), 2500);
      qc.invalidateQueries({ queryKey: ["os-engine-setting"] });
    },
  });

  const currentWorker = settingQ.data?.default_worker_engine ?? null;
  const isDirty = selectedWorker !== currentWorker ||
    workerModel !== (settingQ.data?.default_worker_model ?? "");
  const selectedMeta = asArray<EngineCatalogEntry>(catalogQ.data)?.find((e) => e.id === selectedWorker);
  const isLoading = catalogQ.isLoading || settingQ.isLoading;

  // Filter to installed engines only (same logic as OsEngineSelector).
  const detectedWorkerIds = detectQ.data?.results?.length
    ? new Set(detectQ.data.results.filter((r) => r.installed).map((r) => r.engine_id))
    : null;
  const visibleWorkerEngines = ((catalogQ.data ?? []) as EngineCatalogEntry[]).filter(
    (eng) => !detectedWorkerIds || detectedWorkerIds.has(eng.id) || eng.id === currentWorker,
  );

  const handleSave = () => {
    mutation.mutate({
      default_worker_engine: selectedWorker,
      default_worker_model: workerModel.trim() || null,
    });
  };

  const handleClear = () => {
    setSelectedWorker(null);
    setWorkerModel("");
    mutation.mutate({ default_worker_engine: null, default_worker_model: null });
  };

  return (
    <Card>
      <CardContent className="pt-5 pb-4 space-y-4">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="font-semibold text-sm">Worker Engine</h2>
            <p className="text-xs text-muted-foreground mt-0.5">
              Which engine Corvin delegates sub-tasks to. Tenant-level default —
              per-chat via <span className="font-mono">/engine &lt;name&gt;</span>.
              All five engines are available as workers.
            </p>
          </div>
          {saved && (
            <Badge className="bg-emerald-500/10 text-emerald-600 border-emerald-500/30 text-xs gap-1">
              <Check className="h-3 w-3" /> Saved
            </Badge>
          )}
        </div>

        {isLoading && <Skeleton className="h-28 w-full" />}

        {!isLoading && (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
            {/* Auto option */}
            <button
              onClick={() => setSelectedWorker(null)}
              className={cn(
                "rounded-lg border-2 p-3 text-left transition-all hover:border-accent/40 sm:col-span-2",
                selectedWorker === null ? "border-accent/50 bg-accent/5" : "border-border bg-muted/10",
              )}
            >
              <div className="flex items-center gap-2 mb-1">
                <Zap className={cn("h-3.5 w-3.5", selectedWorker === null ? "text-accent" : "text-muted-foreground")} />
                <span className="font-medium text-sm">Auto (OS decides)</span>
                {selectedWorker === null && <Check className="h-3 w-3 text-accent ml-auto" />}
              </div>
              <p className="text-[11px] text-muted-foreground">
                The orchestrator picks the best worker per task via model routing (L5).
              </p>
            </button>

            {visibleWorkerEngines.map((eng: EngineCatalogEntry) => (
              <button
                key={eng.id}
                onClick={() => { setSelectedWorker(eng.id); setWorkerModel(""); }}
                className={cn(
                  "rounded-lg border-2 p-3 text-left transition-all hover:border-accent/40",
                  selectedWorker === eng.id
                    ? eng.local
                      ? "border-emerald-500/50 bg-emerald-500/5"
                      : eng.id === "copilot"
                      ? "border-blue-500/50 bg-blue-500/5"
                      : "border-accent/50 bg-accent/5"
                    : "border-border bg-muted/10",
                )}
              >
                <div className="flex items-center gap-2 mb-1">
                  <EngineIcon
                    engineId={eng.id}
                    className={cn(
                      "h-3.5 w-3.5",
                      eng.local ? "text-emerald-500" : eng.id === "copilot" ? "text-blue-500" : "text-muted-foreground"
                    )}
                  />
                  <span className="font-medium text-sm">{eng.label}</span>
                  {eng.local && (
                    <span className="text-[9px] bg-emerald-500/15 text-emerald-600 rounded px-1 font-semibold uppercase tracking-wide">local</span>
                  )}
                  {eng.id === "copilot" && !eng.local && (
                    <span className="text-[9px] bg-blue-500/15 text-blue-600 rounded px-1 font-semibold uppercase tracking-wide">copilot</span>
                  )}
                  {selectedWorker === eng.id && (
                    <Check className={cn("h-3 w-3 ml-auto",
                      eng.local ? "text-emerald-600" : eng.id === "copilot" ? "text-blue-600" : "text-accent"
                    )} />
                  )}
                </div>
                <p className="text-[11px] text-muted-foreground leading-relaxed line-clamp-2">{eng.description}</p>
                {/* Task type hint for Copilot */}
                {eng.id === "copilot" && (
                  <p className="text-[10px] text-blue-500/80 mt-1.5 font-mono">
                    model: shell · git · gh · (blank = chat)
                  </p>
                )}
                {/* Alias hint for Hermes */}
                {eng.id === "hermes" && eng.model_aliases.length > 0 && (
                  <p className="text-[10px] text-muted-foreground/60 mt-1.5 font-mono">
                    {eng.model_aliases.join(" · ")}
                  </p>
                )}
              </button>
            ))}
          </div>
        )}

        {/* Model / task-type field for selected worker */}
        {selectedWorker !== null && selectedMeta && (
          <div className="space-y-1.5">
            <Label className="text-xs">
              {selectedMeta.id === "copilot" ? "Task type" : "Model"}{" "}
              <span className="text-muted-foreground font-normal">(optional)</span>
            </Label>
            {selectedMeta.model_aliases.length > 0 ? (
              <Select
                value={workerModel || ""}
                onChange={(e) => setWorkerModel(e.target.value)}
                className="h-8 text-xs"
              >
                <option value="">— default / auto —</option>
                {selectedMeta.model_aliases.map((alias) => (
                  <option key={alias} value={alias}>{alias}</option>
                ))}
              </Select>
            ) : (
              <Input
                value={workerModel}
                onChange={(e) => setWorkerModel(e.target.value)}
                placeholder={selectedMeta.model_placeholder || "default"}
                className="h-8 text-xs font-mono"
              />
            )}
            {selectedMeta.model_examples && (
              <p className="text-[10px] text-muted-foreground">
                <span className="font-mono">{selectedMeta.model_examples}</span>
              </p>
            )}
          </div>
        )}

        {!isLoading && (
          <div className="flex items-center gap-3 pt-1">
            <Button size="sm" onClick={handleSave} disabled={!isDirty || mutation.isPending} className="text-xs">
              {mutation.isPending ? "Saving…" : "Save worker"}
            </Button>
            {currentWorker !== null && (
              <Button size="sm" variant="ghost" onClick={handleClear} className="text-xs text-muted-foreground">
                Reset to auto
              </Button>
            )}
            {mutation.isError && (
              <span className="text-xs text-destructive">
                {mutation.error instanceof Error 
                  ? mutation.error.message 
                  : typeof mutation.error === 'object' && mutation.error !== null && 'detail' in mutation.error
                  ? Array.isArray((mutation.error as { detail?: unknown }).detail)
                    ? `Validation error: ${((mutation.error as { detail: Array<{ msg?: unknown; message?: unknown }> }).detail)
                        .map((e) => String(e.msg || e.message || String(e)))
                        .join(', ')}`
                    : String((mutation.error as { detail?: unknown }).detail)
                  : "Save failed"}
              </span>
            )}
            <span className="ml-auto text-[10px] text-muted-foreground">Takes effect on next turn</span>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ── Per-Engine Model Config (ADR-0119) ────────────────────────────

function PerEngineModelConfig({ csrf }: { csrf: string }) {
  const qc = useQueryClient();

  const settingQ = useQuery({
    queryKey: ["os-engine-setting"],
    queryFn: ({ signal }) => getOsEngineSetting(signal),
  });
  const registryQ = useQuery({
    queryKey: ["engine-model-registry"],
    queryFn: ({ signal }) => getEngineModelRegistry(signal),
    staleTime: 60_000,
  });
  const detectQ = useQuery({
    queryKey: ["engine-detect"],
    queryFn: ({ signal }) => detectEngines(signal),
    staleTime: 3 * 60_000,
    refetchOnWindowFocus: false,
  });
  // ADR-0181 — provider catalogue (for labels) + live-fetched model lists.
  const providersQ = useQuery({
    queryKey: ["engine-providers"],
    queryFn: ({ signal }) => getEngineProviders(signal),
    staleTime: 60_000,
  });
  const providers: Record<string, ProviderSpec> = providersQ.data ?? {};

  const [local, setLocal] = React.useState<Record<string, EngineModelConfig>>({});
  const [saved, setSaved] = React.useState(false);
  // Live models fetched per provider (ADR-0181), keyed by provider id.
  const [liveModels, setLiveModels] = React.useState<Record<string, ProviderModelsResponse>>({});
  const [fetching, setFetching] = React.useState<string | null>(null);
  const [warnings, setWarnings] = React.useState<string[]>([]);

  const fetchModelsFor = async (provider: string) => {
    if (!provider) return;
    setFetching(provider);
    try {
      const res = await getProviderModels(provider);
      setLiveModels((prev) => ({ ...prev, [provider]: res }));
    } finally {
      setFetching(null);
    }
  };

  React.useEffect(() => {
    if (settingQ.data) {
      setLocal(settingQ.data.engine_models ?? {});
    }
  }, [settingQ.data]);

  const mutation = useMutation({
    mutationFn: (engineModels: Record<string, EngineModelConfig>) =>
      setOsEngineSetting({
        default_engine: settingQ.data?.default_engine ?? null,
        hermes_model: settingQ.data?.hermes_model ?? null,
        default_worker_engine: settingQ.data?.default_worker_engine ?? null,
        default_worker_model: settingQ.data?.default_worker_model ?? null,
        engine_models: engineModels,
      }, csrf),
    onSuccess: (data) => {
      setSaved(true);
      setWarnings(data?.compliance_warnings ?? []);
      setTimeout(() => setSaved(false), 2500);
      qc.invalidateQueries({ queryKey: ["os-engine-setting"] });
    },
  });

  const registry = registryQ.data ?? {};
  
  // Normalize model arrays in case API returns objects instead of arrays
  const normalizedRegistry = Object.entries(registry).reduce((acc, [engineId, spec]) => {
    acc[engineId] = {
      ...spec,
      os_models: asArray(spec.os_models),
      worker_models: asArray(spec.worker_models),
    };
    return acc;
  }, {} as typeof registry);
  const isLoading = settingQ.isLoading || registryQ.isLoading;

  const handleModelChange = (
    engineId: string,
    role: "os_model" | "worker_model",
    value: string,
  ) => {
    setWarnings([]);
    setLocal((prev) => ({
      ...prev,
      [engineId]: {
        ...prev[engineId],
        os_model: prev[engineId]?.os_model ?? null,
        worker_model: prev[engineId]?.worker_model ?? null,
        [role]: value || null,
      },
    }));
  };

  const handleProviderChange = (engineId: string, provider: string) => {
    setWarnings([]);
    setLocal((prev) => ({
      ...prev,
      [engineId]: {
        // Clear the models: a model belongs to a provider, so keeping an id from
        // the previous provider would persist an invalid pair (review MEDIUM).
        os_model: null,
        worker_model: null,
        provider: provider || null,
      },
    }));
    if (provider) {
      const src = providers[provider]?.model_source;
      if (src && src !== "static" && !liveModels[provider]) void fetchModelsFor(provider);
    }
  };

  const handleSave = () => mutation.mutate(local);

  // Canonical, null/empty-stripped, key-sorted serialization so that touching a
  // dropdown and re-selecting the same value (or an explicit provider:null) does
  // NOT flip isDirty (review MEDIUM).
  const canonEm = (em: Record<string, EngineModelConfig> | undefined) => {
    const norm: Record<string, Record<string, string>> = {};
    for (const k of Object.keys(em ?? {}).sort()) {
      const v = (em as Record<string, EngineModelConfig>)[k];
      const e: Record<string, string> = {};
      if (v?.os_model) e.os_model = v.os_model;
      if (v?.worker_model) e.worker_model = v.worker_model;
      if (v?.provider) e.provider = v.provider;
      if (Object.keys(e).length) norm[k] = e;
    }
    return JSON.stringify(norm);
  };
  const isDirty = canonEm(local) !== canonEm(settingQ.data?.engine_models);

  // Only show engines that have configurable models AND are installed on this system.
  const detectedModelIds = detectQ.data?.results?.length
    ? new Set(detectQ.data.results.filter((r) => r.installed).map((r) => r.engine_id))
    : null;
  const configurableEngines = Object.entries(normalizedRegistry).filter(
    ([engineId, spec]) => {
      const hasModels =
        (spec.os_models.length > 0 && spec.supports_os_turn) ||
        spec.worker_models.length > 0 ||
        (spec.supported_providers?.length ?? 0) > 0;  // provider-only engines still configurable
      if (!hasModels) return false;
      return !detectedModelIds || detectedModelIds.has(engineId);
    },
  );

  return (
    <Card>
      <CardContent className="pt-5 pb-4 space-y-4">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="font-semibold text-sm flex items-center gap-2">
              <Settings2 className="h-4 w-4" />
              Per-Engine Model Configuration
            </h2>
            <p className="text-xs text-muted-foreground mt-0.5">
              Tenant-level defaults for OS-turn and worker-turn models.
              Per-persona overrides are configured on the{" "}
              <a href="/console/app/personas" className="underline underline-offset-2 hover:text-foreground">
                Personas page
              </a>{" "}
              — that is the canonical place for per-persona overrides.
            </p>
          </div>
          {saved && (
            <Badge className="bg-emerald-500/10 text-emerald-600 border-emerald-500/30 text-xs gap-1">
              <Check className="h-3 w-3" /> Saved
            </Badge>
          )}
        </div>

        {isLoading && <Skeleton className="h-40 w-full" />}

        {!isLoading && configurableEngines.length === 0 && (
          <p className="text-xs text-muted-foreground">No engine model registry loaded.</p>
        )}

        {!isLoading && configurableEngines.length > 0 && (
          <div className="space-y-3">
            {configurableEngines.map(([engineId, spec]) => {
              const current: EngineModelConfig =
                local[engineId] ?? { os_model: null, worker_model: null, provider: null };
              const supProviders = spec.supported_providers ?? [];
              const selectedProvider = current.provider ?? "";
              const live = selectedProvider ? liveModels[selectedProvider] : undefined;
              const mergeOpts = (curated: { id: string; label: string }[]) => {
                const seen = new Set<string>();
                const out: { id: string; label: string }[] = [];
                for (const m of [...curated, ...(live?.models ?? [])]) {
                  if (m.id && !seen.has(m.id)) { seen.add(m.id); out.push({ id: m.id, label: m.label }); }
                }
                return out;
              };
              return (
                <div
                  key={engineId}
                  className="rounded-lg border bg-muted/10 p-3 space-y-2"
                >
                  <div className="flex items-center gap-2">
                    <EngineIcon engineId={engineId} className="h-3.5 w-3.5 text-muted-foreground" />
                    <span className="font-medium text-sm">{spec.label}</span>
                    {spec.supports_os_turn && spec.supports_worker_turn && (
                      <span className="text-[9px] bg-accent/15 text-accent rounded px-1 font-semibold uppercase tracking-wide">OS + Worker</span>
                    )}
                    {!spec.supports_os_turn && spec.supports_worker_turn && (
                      <span className="text-[9px] bg-muted text-muted-foreground rounded px-1 font-semibold uppercase tracking-wide">Worker only</span>
                    )}
                  </div>

                  {/* ADR-0181 — provider selector + live model fetch */}
                  {supProviders.length > 0 && (
                    <div className="space-y-1">
                      <Label className="text-[10px] text-muted-foreground uppercase tracking-wide">
                        Provider
                      </Label>
                      <div className="flex flex-wrap items-center gap-2">
                        <select
                          value={selectedProvider}
                          onChange={(e) => handleProviderChange(engineId, e.target.value)}
                          className="h-8 rounded-md border border-input bg-background px-2 text-xs focus:outline-none focus:ring-1 focus:ring-ring"
                        >
                          <option value="">Native (default)</option>
                          {supProviders.map((p) => (
                            <option key={p.provider} value={p.provider}>
                              {(providers[p.provider]?.label ?? p.provider) + (p.native ? "" : " (via proxy)")}
                            </option>
                          ))}
                        </select>
                        {selectedProvider && providers[selectedProvider]?.model_source !== "static" && (
                          <Button
                            type="button" size="sm" variant="outline" className="h-8 text-[11px]"
                            onClick={() => fetchModelsFor(selectedProvider)}
                            disabled={fetching === selectedProvider}
                          >
                            {fetching === selectedProvider ? "Fetching…" : "Fetch models"}
                          </Button>
                        )}
                        {live && (
                          <span className="text-[10px] text-muted-foreground">
                            {live.reachable ? `${live.count} models` : (live.error ?? "unreachable")}
                          </span>
                        )}
                      </div>
                      {selectedProvider && providers[selectedProvider]?.kind === "cloud" && (
                        <p className="text-[10px] text-amber-600">
                          Cloud provider — turns egress data; L34/L35 apply. Set{" "}
                          <code>{providers[selectedProvider]?.credential_env || "the API key"}</code> in the vault.
                        </p>
                      )}
                      {(() => {
                        const note = supProviders.find((p) => p.provider === selectedProvider)?.note;
                        return note ? <p className="text-[10px] text-muted-foreground">{note}</p> : null;
                      })()}
                    </div>
                  )}

                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                    {(["os_model", "worker_model"] as const).map((role) => {
                      const supported = role === "os_model" ? spec.supports_os_turn : spec.supports_worker_turn;
                      const curated = role === "os_model" ? spec.os_models : spec.worker_models;
                      if (!supported || (curated.length === 0 && !selectedProvider)) return null;
                      const opts = mergeOpts(curated);
                      const value = current[role] ?? "";
                      const inList = opts.some((m) => m.id === value);
                      return (
                        <div key={role} className="space-y-1">
                          <Label className="text-[10px] text-muted-foreground uppercase tracking-wide">
                            {role === "os_model" ? "OS model" : "Worker model"}
                          </Label>
                          <select
                            value={inList ? value : "__custom__"}
                            onChange={(e) => e.target.value !== "__custom__" && handleModelChange(engineId, role, e.target.value)}
                            className="w-full h-8 rounded-md border border-input bg-background px-2 text-xs font-mono focus:outline-none focus:ring-1 focus:ring-ring"
                          >
                            {opts.map((m) => (
                              <option key={m.id} value={m.id}>{m.label}</option>
                            ))}
                            <option value="__custom__">— custom (type below) —</option>
                          </select>
                          <input
                            type="text"
                            value={value}
                            onChange={(e) => handleModelChange(engineId, role, e.target.value)}
                            placeholder="or type a model id"
                            className="w-full h-7 rounded-md border border-input bg-background px-2 text-[11px] font-mono focus:outline-none focus:ring-1 focus:ring-ring"
                          />
                        </div>
                      );
                    })}
                  </div>
                </div>
              );
            })}
          </div>
        )}

        {!isLoading && configurableEngines.length > 0 && (
          <div className="flex items-center gap-3 pt-1">
            <Button
              size="sm"
              onClick={handleSave}
              disabled={!isDirty || mutation.isPending}
              className="text-xs"
            >
              {mutation.isPending ? "Saving…" : "Save model config"}
            </Button>
            {mutation.isError && (
              <span className="text-xs text-destructive">
                {mutation.error instanceof Error 
                  ? mutation.error.message 
                  : typeof mutation.error === 'object' && mutation.error !== null && 'detail' in mutation.error
                  ? Array.isArray((mutation.error as { detail?: unknown }).detail)
                    ? `Validation error: ${((mutation.error as { detail: Array<{ msg?: unknown; message?: unknown }> }).detail)
                        .map((e) => String(e.msg || e.message || String(e)))
                        .join(', ')}`
                    : String((mutation.error as { detail?: unknown }).detail)
                  : "Save failed"}
              </span>
            )}
            <span className="ml-auto text-[10px] text-muted-foreground">
              Takes effect on next turn
            </span>
          </div>
        )}

        {warnings.length > 0 && (
          <div className="rounded-md border border-amber-500/30 bg-amber-500/10 p-2 space-y-1">
            <p className="text-[11px] font-semibold text-amber-700 flex items-center gap-1">
              <AlertTriangle className="h-3 w-3" /> Compliance advisories (L34/L35)
            </p>
            {warnings.map((w, i) => (
              <p key={i} className="text-[10px] text-amber-700/90">{w}</p>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ── API Key Card ──────────────────────────────────────────────────

function EngineCardsWithLicenseGates({
  engines, csrf, onSaved,
}: { engines: EngineInfo[]; csrf: string; onSaved: () => void }) {
  const { data: licInfo } = useQuery({
    queryKey: ["license", "info"],
    queryFn: ({ signal }) => getLicenseInfo(signal),
    staleTime: 5 * 60_000,
  });
  return (
    <div className="space-y-3">
      {engines.map((engine) => {
        const allowed = isEngineAllowed(licInfo, engine.id);
        return (
          <LicenseGate
            key={engine.id}
            allowed={allowed}
            reason={`${engine.id} requires a higher licence tier`}
          >
            <EngineCard engine={engine} csrf={csrf} onSaved={onSaved} />
          </LicenseGate>
        );
      })}
    </div>
  );
}

function EngineCard({
  engine,
  csrf,
  onSaved,
}: {
  engine: EngineInfo;
  csrf: string;
  onSaved: () => void;
}) {
  const [editing, setEditing] = React.useState(false);
  const [value, setValue] = React.useState("");
  const [show, setShow] = React.useState(false);
  const [reauthOpen, setReauthOpen] = React.useState(false);
  const [saved, setSaved] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  const doSave = async () => {
    setError(null);
    try {
      await updateEngineKey(engine.id, value, csrf);
      setSaved(true);
      setEditing(false);
      setTimeout(() => setSaved(false), 2000);
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const isOAuth = engine.kind === "oauth";
  const isCopilotBinary = engine.kind === "url";
  const isConfigurable = engine.kind !== "oauth" && engine.key;

  return (
    <Card className={cn("transition-all", engine.configured && "border-emerald-500/30")}>
      <CardContent className="pt-4 pb-3 space-y-3">
        {/* Header */}
        <div className="flex items-start justify-between gap-3">
          <div className="flex items-center gap-2">
            <div className={cn(
              "rounded-lg p-2",
              engine.configured ? "bg-emerald-500/10 text-emerald-500" : "bg-muted text-muted-foreground",
            )}>
              {engine.id === "copilot"
                ? <Github className="h-4 w-4" />
                : engine.configured
                ? <Check className="h-4 w-4" />
                : <Key className="h-4 w-4" />
              }
            </div>
            <div>
              <div className="font-semibold text-sm">{engine.label}</div>
              {engine.key && (
                <div className="font-mono text-[10px] text-muted-foreground">{engine.key}</div>
              )}
            </div>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            {saved && <Check className="h-4 w-4 text-emerald-500" />}
            {engine.configured ? (
              <Badge variant="outline" className="text-[10px] text-emerald-600 dark:text-emerald-400 border-emerald-500/40">
                {engine.value_masked ?? "connected"}
              </Badge>
            ) : (
              <Badge variant="outline" className="text-[10px] text-amber-600 border-amber-500/40">
                not configured
              </Badge>
            )}
          </div>
        </div>

        {/* OAuth info — Claude Code */}
        {isOAuth && engine.configured && (
          <p className="text-xs text-muted-foreground">
            Authenticated via <span className="font-mono">claude login</span> (OAuth). Session active.
          </p>
        )}
        {isOAuth && !engine.configured && (
          <div className="space-y-3">
            <p className="text-xs text-muted-foreground">
              Claude Code is not detected on this system.
            </p>
            <ol className="space-y-1.5 text-xs text-muted-foreground list-none">
              <li className="flex items-start gap-2">
                <span className="shrink-0 font-mono text-[10px] bg-muted rounded px-1 py-0.5 mt-0.5">1</span>
                <span>
                  <strong className="text-foreground">Install Claude Code</strong> — download from{" "}
                  <a href="https://claude.ai/code" target="_blank" rel="noreferrer" className="text-accent underline underline-offset-2">
                    claude.ai/code
                  </a>{" "}
                  or run:{" "}
                  <code className="ml-1 rounded bg-muted px-1 py-0.5 font-mono text-[10px]">
                    curl -fsSL https://claude.ai/install.sh | sh
                  </code>
                </span>
              </li>
              <li className="flex items-start gap-2">
                <span className="shrink-0 font-mono text-[10px] bg-muted rounded px-1 py-0.5 mt-0.5">2</span>
                <span>
                  <strong className="text-foreground">Log in</strong> — run{" "}
                  <code className="rounded bg-muted px-1 py-0.5 font-mono text-[10px]">claude login</code>
                  {" "}in your terminal.
                </span>
              </li>
              <li className="flex items-start gap-2">
                <span className="shrink-0 font-mono text-[10px] bg-muted rounded px-1 py-0.5 mt-0.5">3</span>
                <span><strong className="text-foreground">Refresh</strong> this page.</span>
              </li>
            </ol>
            <Button size="sm" variant="accent" className="text-xs gap-1" asChild>
              <a href="https://claude.ai/code" target="_blank" rel="noreferrer">
                Download Claude Code <ExternalLink className="h-3 w-3" />
              </a>
            </Button>
          </div>
        )}

        {/* Copilot binary info */}
        {isCopilotBinary && engine.configured && (
          <p className="text-xs text-muted-foreground">
            <span className="font-mono">copilot</span> binary available. Authenticated via{" "}
            <span className="font-mono">~/.copilot/config.json</span>.
          </p>
        )}
        {isCopilotBinary && !engine.configured && (
          <div className="space-y-3">
            <p className="text-xs text-muted-foreground">
              GitHub Copilot CLI binary not found on this system.
            </p>
            <ol className="space-y-1.5 text-xs text-muted-foreground list-none">
              <li className="flex items-start gap-2">
                <span className="shrink-0 font-mono text-[10px] bg-muted rounded px-1 py-0.5 mt-0.5">1</span>
                <span>
                  <strong className="text-foreground">Install Copilot CLI</strong> — download from{" "}
                  <a href="https://github.com/github/copilot-cli/releases" target="_blank" rel="noreferrer" className="text-accent underline underline-offset-2">
                    github.com/github/copilot-cli
                  </a>
                  {" "}and place in <code className="font-mono text-[10px] bg-muted px-1 py-0.5 rounded">~/.local/bin/copilot</code>
                </span>
              </li>
              <li className="flex items-start gap-2">
                <span className="shrink-0 font-mono text-[10px] bg-muted rounded px-1 py-0.5 mt-0.5">2</span>
                <span>
                  <strong className="text-foreground">Authenticate</strong> — run{" "}
                  <code className="rounded bg-muted px-1 py-0.5 font-mono text-[10px]">copilot auth login</code>
                  {" "}(requires GitHub Copilot Business/Enterprise subscription)
                </span>
              </li>
              <li className="flex items-start gap-2">
                <span className="shrink-0 font-mono text-[10px] bg-muted rounded px-1 py-0.5 mt-0.5">3</span>
                <span><strong className="text-foreground">Refresh</strong> this page.</span>
              </li>
            </ol>
            <Button size="sm" variant="outline" className="text-xs gap-1" asChild>
              <a href="https://github.com/github/copilot-cli/releases" target="_blank" rel="noreferrer">
                Releases <ExternalLink className="h-3 w-3" />
              </a>
            </Button>
          </div>
        )}

        {/* API key editor */}
        {isConfigurable && !editing && (
          <div className="flex gap-2">
            <Button
              size="sm"
              variant="outline"
              className="text-xs"
              onClick={() => { setValue(""); setEditing(true); setError(null); }}
            >
              {engine.configured ? "Update key" : "Set API key"}
            </Button>
            {engine.url && (
              <Button size="sm" variant="ghost" className="text-xs gap-1" asChild>
                <a href={engine.url} target="_blank" rel="noreferrer">
                  Get key <ExternalLink className="h-3 w-3" />
                </a>
              </Button>
            )}
          </div>
        )}

        {editing && (
          <div className="space-y-2">
            <Label className="text-xs">{engine.label} API Key</Label>
            <div className="flex gap-2">
              <div className="relative flex-1">
                <Input
                  type={show ? "text" : "password"}
                  value={value}
                  onChange={(e) => setValue(e.target.value)}
                  placeholder="Paste your API key…"
                  className="font-mono text-xs pr-8"
                  autoFocus
                />
                <button
                  onClick={() => setShow((v) => !v)}
                  className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                >
                  {show ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
                </button>
              </div>
            </div>
            {error && <p className="text-xs text-destructive">{error}</p>}
            <div className="flex gap-2">
              <Button variant="ghost" size="sm" onClick={() => setEditing(false)}>Cancel</Button>
              <Button size="sm" onClick={() => setReauthOpen(true)} disabled={!value.trim()}>Save</Button>
            </div>
          </div>
        )}
      </CardContent>

      <ReauthDialog
        open={reauthOpen}
        onOpenChange={setReauthOpen}
        title={`Save ${engine.label} key`}
        description="Updating API keys requires owner re-authentication."
        onConfirm={doSave}
      />
    </Card>
  );
}

// ── Custom Engines Section ────────────────────────────────────────

const EMPTY_REGISTER_FORM: CustomEngineRegisterRequest & { engine_id: string } = {
  engine_id: "",
  display_name: "",
  transport: "openai_compat",
  base_url: "",
  auth_env: "",
  locality: "local",
  data_classification: "INTERNAL",
};

function CustomEnginesSection({ csrf }: { csrf: string }) {
  const qc = useQueryClient();

  const listQ = useQuery({
    queryKey: ["custom-engines"],
    queryFn: ({ signal }) => listCustomEngines(signal),
  });

  const [dialogOpen, setDialogOpen] = React.useState(false);
  const [form, setForm] = React.useState({ ...EMPTY_REGISTER_FORM });
  const [formError, setFormError] = React.useState<string | null>(null);

  // Per-card ping state: engine_id -> { pending, reachable, error }
  const [pingState, setPingState] = React.useState<
    Record<string, { pending: boolean; reachable?: boolean; error?: string }>
  >({});

  const registerMutation = useMutation({
    mutationFn: () => {
      const { engine_id, display_name, transport, base_url, auth_env, locality, data_classification } = form;
      return registerCustomEngine(
        engine_id,
        {
          display_name,
          transport,
          base_url,
          auth_env: auth_env?.trim() || null,
          locality,
          data_classification,
        },
        csrf,
      );
    },
    onSuccess: () => {
      setDialogOpen(false);
      setForm({ ...EMPTY_REGISTER_FORM });
      setFormError(null);
      qc.invalidateQueries({ queryKey: ["custom-engines"] });
    },
    onError: (e) => {
      setFormError(e instanceof Error ? e.message : "Registration failed");
    },
  });

  const removeMutation = useMutation({
    mutationFn: (engine_id: string) => removeCustomEngine(engine_id, csrf),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["custom-engines"] });
    },
  });

  const handlePing = async (engine_id: string) => {
    setPingState((prev) => ({ ...prev, [engine_id]: { pending: true } }));
    try {
      const result = await pingCustomEngine(engine_id, csrf);
      setPingState((prev) => ({
        ...prev,
        [engine_id]: {
          pending: false,
          reachable: result.reachable,
          error: result.error,
        },
      }));
    } catch (e) {
      setPingState((prev) => ({
        ...prev,
        [engine_id]: {
          pending: false,
          reachable: false,
          error: e instanceof Error ? e.message : "Ping failed",
        },
      }));
    }
  };

  const handleOpenDialog = () => {
    setForm({ ...EMPTY_REGISTER_FORM });
    setFormError(null);
    setDialogOpen(true);
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setFormError(null);
    if (!form.engine_id.trim()) { setFormError("Engine ID is required"); return; }
    if (!form.display_name.trim()) { setFormError("Display Name is required"); return; }
    if (!form.base_url.trim()) { setFormError("Base URL is required"); return; }
    registerMutation.mutate();
  };

  const engines: CustomEngineManifest[] = listQ.data?.engines ?? [];

  const localityColor = (locality: string) => {
    if (locality === "local") return "text-emerald-600 border-emerald-500/40";
    if (locality === "eu_cloud") return "text-blue-600 border-blue-500/40";
    return "text-muted-foreground border-border";
  };

  const classificationColor = (cls: string) => {
    if (cls === "CONFIDENTIAL") return "text-red-600 border-red-500/40";
    if (cls === "INTERNAL") return "text-amber-600 border-amber-500/40";
    return "text-muted-foreground border-border";
  };

  return (
    <>
      <Card>
        <CardHeader className="pb-3">
          <div className="flex items-center justify-between">
            <CardTitle className="text-sm font-semibold">Custom Engines</CardTitle>
            <Button
              size="sm"
              variant="outline"
              className="text-xs gap-1"
              onClick={handleOpenDialog}
              data-testid="register-engine-btn"
            >
              + Register Engine
            </Button>
          </div>
          <p className="text-xs text-muted-foreground mt-0.5">
            Register third-party OpenAI-compatible, Anthropic, or Ollama endpoints as additional
            engines available for OS-turn and worker delegation.
          </p>
        </CardHeader>

        <CardContent className="pt-0 pb-4 space-y-3">
          {listQ.isLoading && (
            <div className="space-y-2">
              <Skeleton className="h-20 w-full" />
              <Skeleton className="h-20 w-full" />
            </div>
          )}

          {!listQ.isLoading && engines.length === 0 && (
            <p className="text-xs text-muted-foreground py-2">
              No custom engines registered. Use "+ Register Engine" to add one.
            </p>
          )}

          {!listQ.isLoading && engines.map((engine) => {
            const ping = pingState[engine.engine_id];
            return (
              <div
                key={engine.engine_id}
                className="rounded-lg border bg-muted/10 p-3 space-y-2"
                data-testid={`engine-card-${engine.engine_id}`}
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="flex items-center gap-2 min-w-0">
                    <Cloud className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
                    <div className="min-w-0">
                      <span className="font-medium text-sm truncate block">{engine.display_name}</span>
                      <span className="font-mono text-[10px] text-muted-foreground truncate block">{engine.engine_id}</span>
                    </div>
                  </div>
                  <div className="flex items-center gap-1.5 shrink-0 flex-wrap justify-end">
                    <Badge variant="outline" className="text-[9px] px-1 py-0 font-mono">
                      {engine.transport}
                    </Badge>
                    <Badge variant="outline" className={cn("text-[9px] px-1 py-0", localityColor(engine.locality))}>
                      {engine.locality}
                    </Badge>
                    <Badge variant="outline" className={cn("text-[9px] px-1 py-0", classificationColor(engine.data_classification))}>
                      {engine.data_classification}
                    </Badge>
                  </div>
                </div>

                <div className="flex items-center gap-2">
                  <span className="text-[10px] text-muted-foreground">
                    {engine.models.length} model{engine.models.length !== 1 ? "s" : ""}
                  </span>

                  {/* Ping result */}
                  {ping && !ping.pending && ping.reachable === true && (
                    <span className="flex items-center gap-1 text-[10px] text-emerald-600">
                      <Wifi className="h-3 w-3" /> reachable
                    </span>
                  )}
                  {ping && !ping.pending && ping.reachable === false && (
                    <span className="flex items-center gap-1 text-[10px] text-destructive">
                      <WifiOff className="h-3 w-3" />
                      {ping.error ?? "unreachable"}
                    </span>
                  )}

                  <div className="ml-auto flex items-center gap-1.5">
                    <Button
                      size="sm"
                      variant="ghost"
                      className="text-xs h-7 px-2"
                      disabled={ping?.pending}
                      onClick={() => handlePing(engine.engine_id)}
                    >
                      {ping?.pending ? (
                        <RefreshCw className="h-3 w-3 animate-spin" />
                      ) : (
                        "Ping"
                      )}
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      className="text-xs h-7 px-2 text-destructive hover:text-destructive"
                      disabled={removeMutation.isPending}
                      onClick={() => removeMutation.mutate(engine.engine_id)}
                    >
                      Remove
                    </Button>
                  </div>
                </div>
              </div>
            );
          })}

          {listQ.isError && (
            <p className="text-xs text-destructive">
              Failed to load custom engines:{" "}
              {listQ.error instanceof Error ? listQ.error.message : "Unknown error"}
            </p>
          )}
        </CardContent>
      </Card>

      {/* Register Engine Dialog */}
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle className="text-sm">Register Custom Engine</DialogTitle>
          </DialogHeader>

          <form
            onSubmit={handleSubmit}
            className="space-y-3 py-1"
            data-testid="register-engine-form"
          >
            {/* Engine ID */}
            <div className="space-y-1.5">
              <Label htmlFor="custom-engine-id" className="text-xs">
                Engine ID <span className="text-destructive">*</span>
              </Label>
              <Input
                id="custom-engine-id"
                value={form.engine_id}
                onChange={(e) => setForm((f) => ({ ...f, engine_id: e.target.value }))}
                placeholder="e.g. my-llm-endpoint"
                className="h-8 text-xs font-mono"
                required
                data-testid="engine-id-input"
              />
            </div>

            {/* Display Name */}
            <div className="space-y-1.5">
              <Label htmlFor="custom-engine-name" className="text-xs">
                Display Name <span className="text-destructive">*</span>
              </Label>
              <Input
                id="custom-engine-name"
                value={form.display_name}
                onChange={(e) => setForm((f) => ({ ...f, display_name: e.target.value }))}
                placeholder="e.g. My LLM Endpoint"
                className="h-8 text-xs"
                required
                data-testid="engine-name-input"
              />
            </div>

            {/* Transport */}
            <div className="space-y-1.5">
              <Label htmlFor="custom-engine-transport" className="text-xs">Transport</Label>
              <select
                id="custom-engine-transport"
                value={form.transport}
                onChange={(e) => setForm((f) => ({ ...f, transport: e.target.value as CustomEngineRegisterRequest["transport"] }))}
                className="w-full h-8 rounded-md border border-input bg-background px-2 text-xs focus:outline-none focus:ring-1 focus:ring-ring"
              >
                <option value="openai_compat">openai_compat</option>
                <option value="anthropic">anthropic</option>
                <option value="ollama">ollama</option>
              </select>
            </div>

            {/* Base URL */}
            <div className="space-y-1.5">
              <Label htmlFor="custom-engine-url" className="text-xs">
                Base URL <span className="text-destructive">*</span>
              </Label>
              <Input
                id="custom-engine-url"
                value={form.base_url}
                onChange={(e) => setForm((f) => ({ ...f, base_url: e.target.value }))}
                placeholder="https://api.example.com/v1"
                className="h-8 text-xs font-mono"
                required
              />
            </div>

            {/* Auth Env Var */}
            <div className="space-y-1.5">
              <Label htmlFor="custom-engine-auth-env" className="text-xs">
                Auth Env Var <span className="text-muted-foreground font-normal">(optional)</span>
              </Label>
              <Input
                id="custom-engine-auth-env"
                value={form.auth_env ?? ""}
                onChange={(e) => setForm((f) => ({ ...f, auth_env: e.target.value }))}
                placeholder="e.g. MY_ENGINE_API_KEY"
                className="h-8 text-xs font-mono"
              />
            </div>

            {/* Locality */}
            <div className="space-y-1.5">
              <Label htmlFor="custom-engine-locality" className="text-xs">Locality</Label>
              <select
                id="custom-engine-locality"
                value={form.locality}
                onChange={(e) => setForm((f) => ({ ...f, locality: e.target.value }))}
                className="w-full h-8 rounded-md border border-input bg-background px-2 text-xs focus:outline-none focus:ring-1 focus:ring-ring"
              >
                <option value="local">local</option>
                <option value="eu_cloud">eu_cloud</option>
                <option value="us_cloud">us_cloud</option>
              </select>
            </div>

            {/* Data Classification */}
            <div className="space-y-1.5">
              <Label htmlFor="custom-engine-classification" className="text-xs">Data Classification</Label>
              <select
                id="custom-engine-classification"
                value={form.data_classification}
                onChange={(e) => setForm((f) => ({ ...f, data_classification: e.target.value }))}
                className="w-full h-8 rounded-md border border-input bg-background px-2 text-xs focus:outline-none focus:ring-1 focus:ring-ring"
              >
                <option value="PUBLIC">PUBLIC</option>
                <option value="INTERNAL">INTERNAL</option>
                <option value="CONFIDENTIAL">CONFIDENTIAL</option>
              </select>
            </div>

            {formError && (
              <p className="text-xs text-destructive">{formError}</p>
            )}

            <DialogFooter className="pt-1">
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="text-xs"
                onClick={() => setDialogOpen(false)}
              >
                Cancel
              </Button>
              <Button
                type="submit"
                size="sm"
                className="text-xs"
                disabled={registerMutation.isPending}
                data-testid="register-engine-submit"
              >
                {registerMutation.isPending ? "Registering…" : "Register"}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </>
  );
}

// ── Page ──────────────────────────────────────────────────────────

export function EnginesPage() {
  const { session } = useAuth();
  const qc = useQueryClient();

  const q = useQuery({
    queryKey: ["engines"],
    queryFn: ({ signal }) => listEngines(signal),
    refetchInterval: 60_000,
  });

  const configured = q.data?.engines.filter((e) => e.configured).length ?? 0;

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      {/* Page header */}
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="font-serif text-3xl font-light tracking-tight">Engines</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Choose which AI powers your assistant and configure its credentials.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Badge variant="outline" className="text-xs">
            {configured} key{configured !== 1 ? "s" : ""} configured
          </Badge>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => {
              qc.invalidateQueries({ queryKey: ["engines"] });
              qc.invalidateQueries({ queryKey: ["os-engine-setting"] });
              qc.invalidateQueries({ queryKey: ["os-engine-health"] });
              qc.invalidateQueries({ queryKey: ["engine-catalog"] });
            }}
          >
            <RefreshCw className="h-3.5 w-3.5" />
          </Button>
        </div>
      </div>

      <Tabs defaultValue="setup">
        <TabsList>
          <TabsTrigger value="setup">Setup</TabsTrigger>
          <TabsTrigger value="control">Control</TabsTrigger>
          <TabsTrigger value="reference">Reference</TabsTrigger>
        </TabsList>

        {/* ── Setup: detected engines, defaults, models & keys (mirrors onboarding) ── */}
        <TabsContent value="setup" className="mt-4 space-y-6">
          {/* Detected Engines — primary section (ADR-0125) */}
          <DetectedEnginesSection csrf={session!.csrf_token} />

          {/* OS Engine selector — catalog-driven, tenant-level default */}
          <OsEngineSelector csrf={session!.csrf_token} />

          {/* Worker Engine selector */}
          <WorkerEngineSelector csrf={session!.csrf_token} />

          {/* Per-engine model configuration */}
          <PerEngineModelConfig csrf={session!.csrf_token} />

          {/* Divider */}
          <div className="flex items-center gap-3">
            <div className="flex-1 border-t border-border" />
            <span className="text-xs text-muted-foreground">Cloud engine credentials</span>
            <div className="flex-1 border-t border-border" />
          </div>

          {/* API key cards */}
          {q.isLoading && (
            <div className="space-y-3">
              {Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} className="h-24 w-full" />)}
            </div>
          )}

          <EngineCardsWithLicenseGates
            engines={q.data?.engines ?? []}
            csrf={session!.csrf_token}
            onSaved={() => qc.invalidateQueries({ queryKey: ["engines"] })}
          />

          {q.data && (
            <div className="rounded-lg border border-border bg-muted/20 px-4 py-3 text-xs text-muted-foreground">
              <Zap className="inline h-3.5 w-3.5 mr-1 text-accent" />
              Cloud engine keys are stored in{" "}
              <span className="font-mono">{q.data.env_path}</span>.
              Per-chat overrides via <span className="font-mono">/engine &lt;name&gt;</span> always take precedence.
              Available aliases: <span className="font-mono">claude · hermes · opencode · codex · copilot · copilot-shell · copilot-git</span>
            </div>
          )}

          {/* Custom Engines */}
          <CustomEnginesSection csrf={session!.csrf_token} />
        </TabsContent>

        {/* ── Control: capability matrix + Claude-local backend (merged from the
            former separate "Engine Control" page) ── */}
        <TabsContent value="control" className="mt-4">
          <EngineControlPage embedded />
        </TabsContent>

        {/* ── Reference: engine architecture (de-emphasised) ── */}
        <TabsContent value="reference" className="mt-4">
          <ArchitectureOverview />
        </TabsContent>
      </Tabs>
    </div>
  );
}
