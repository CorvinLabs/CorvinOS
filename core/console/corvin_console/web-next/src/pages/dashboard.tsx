import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  AlertCircle,
  AlertTriangle,
  CheckCircle2,
  ChevronRight,
  Cpu,
  Database,
  File,
  Fingerprint,
  Hash,
  Key,
  Network,
  ShieldCheck,
  XCircle,
  Zap,
} from "lucide-react";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import {
  api,
  dashboard,
  getLicenseStatus,
  getOsEngineSetting,
  getOsEngineHealth,
  listDataSources,
  getInstanceIdentity,
} from "@/lib/api";
import { formatBytes, formatDate } from "@/lib/utils";
import type { DSIConnection, OsEngineSetting, OsEngineHealth, InstanceIdentityStatus } from "@/lib/api";

// ── Local types ───────────────────────────────────────────────────────────────

interface SecretMeta {
  key_name: string;
  present: boolean | null;
  algorithm: string;
}
interface SecretsListResponse {
  agent_reachable: boolean;
  keys: SecretMeta[];
}

const CHANNEL_LABEL: Record<string, string> = {
  telegram: "Telegram",
  discord: "Discord",
  slack: "Slack",
  whatsapp: "WhatsApp",
  email: "Email",
  signal: "Signal",
  teams: "Teams",
};

const KEY_LABEL: Record<string, string> = {
  anthropic_api_key: "Anthropic (Claude)",
  openai_api_key: "OpenAI",
  stt_openai_api_key: "OpenAI Whisper",
  openai_whisper_key: "OpenAI Whisper",
  stt_local_whisper_api_key: "Local Whisper",
  elevenlabs_api_key: "ElevenLabs TTS",
  google_api_key: "Google",
  deepgram_api_key: "Deepgram",
};

const ADAPTER_LABEL: Record<string, string> = {
  postgresql: "PostgreSQL",
  mysql: "MySQL",
  sqlite: "SQLite",
  local_file: "Local File",
  http: "HTTP API",
  mongodb: "MongoDB",
  redis: "Redis",
};

const CLASSIFICATION_VARIANT: Record<
  string,
  "ok" | "warn" | "danger" | "outline" | "secondary"
> = {
  PUBLIC: "ok",
  INTERNAL: "secondary",
  CONFIDENTIAL: "warn",
  SECRET: "danger",
};

const ENGINE_META: Record<
  string,
  { label: string; locality: string; role: string; color: string }
> = {
  claude_code: {
    label: "Claude Code",
    locality: "cloud",
    role: "OS + Worker",
    color: "text-violet-400",
  },
  hermes: {
    label: "Hermes (Ollama)",
    locality: "local",
    role: "Worker · CONFIDENTIAL",
    color: "text-emerald-400",
  },
  opencode: {
    label: "OpenCode",
    locality: "cloud",
    role: "Worker",
    color: "text-sky-400",
  },
  codex_cli: {
    label: "Codex CLI",
    locality: "cloud",
    role: "Worker",
    color: "text-amber-400",
  },
  copilot: {
    label: "GitHub Copilot",
    locality: "cloud",
    role: "Worker",
    color: "text-blue-400",
  },
};

// ── Page ─────────────────────────────────────────────────────────────────────

export function DashboardPage() {
  const dash = useQuery({
    queryKey: ["dashboard"],
    queryFn: ({ signal }) => dashboard(signal),
    refetchInterval: 30_000,
  });
  const license = useQuery({
    queryKey: ["license", "status"],
    queryFn: ({ signal }) => getLicenseStatus(signal),
    staleTime: 5 * 60_000,
  });
  const engineSettings = useQuery({
    queryKey: ["settings", "engine"],
    queryFn: ({ signal }) => getOsEngineSetting(signal),
    refetchInterval: 60_000,
    retry: false,
  });
  const engineHealth = useQuery({
    queryKey: ["engine", "health"],
    queryFn: ({ signal }) => getOsEngineHealth(signal),
    refetchInterval: 60_000,
    retry: false,
  });
  const dataSources = useQuery({
    queryKey: ["data-sources"],
    queryFn: ({ signal }) => listDataSources(signal),
    staleTime: 60_000,
    retry: false,
  });
  const secrets = useQuery({
    queryKey: ["byok", "secrets"],
    queryFn: ({ signal }) => api<SecretsListResponse>("/byok/secrets", { signal }),
    staleTime: 5 * 60_000,
    retry: false,
  });
  const identity = useQuery({
    queryKey: ["settings", "instance-identity"],
    queryFn: ({ signal }) => getInstanceIdentity(signal),
    staleTime: 5 * 60_000,
    retry: false,
  });

  // Normalize license mode: "invalid" without a loaded JWT = Apache free tier
  const rawMode = license.data?.mode ?? "free";
  const licenseMode = rawMode === "invalid" ? "free" : rawMode;
  const licenseTier =
    rawMode === "invalid" || license.data?.tier === "unknown"
      ? "Apache Free"
      : (license.data?.tier ?? "Free");

  const dsCount = dataSources.data?.length ?? 0;
  const activeEngine = engineSettings.data?.default_engine ?? "claude_code";
  const workerEngine = engineSettings.data?.default_worker_engine ?? null;
  const ollamaOk = engineHealth.data?.ollama_reachable ?? false;
  const ollamaModels = engineHealth.data?.model_count ?? 0;
  const engineStatus = dash.data?.engine_status ?? {};
  const activeEngineInstalled = engineStatus[activeEngine]?.installed ?? true; // optimistic if no data yet

  return (
    <div className="mx-auto max-w-6xl space-y-8">
      {/* ── Header ── */}
      <div className="flex items-end justify-between gap-4">
        <div>
          <h1 className="font-serif text-3xl font-light tracking-tight">Overview</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            System status, engines, data sources and activity at a glance.
          </p>
        </div>
        {dash.data && (
          <Badge variant="outline" className="font-mono text-xs">
            updated · {formatDate(dash.data.ts)}
          </Badge>
        )}
      </div>

      {/* ── Top stat row ── */}
      <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <StatCard
          icon={Cpu}
          title="Active Engine"
          loading={engineSettings.isLoading}
          value={ENGINE_META[activeEngine]?.label ?? activeEngine}
          hint={
            !activeEngineInstalled && !dash.isLoading
              ? "binary not found — using fallback"
              : ollamaOk
              ? `Ollama reachable · ${ollamaModels} model${ollamaModels !== 1 ? "s" : ""}`
              : "cloud engine"
          }
          status={!activeEngineInstalled && !dash.isLoading ? "warn" : "ok"}
        />
        <StatCard
          icon={Database}
          title="Data Sources"
          loading={dataSources.isLoading}
          value={
            dataSources.isError
              ? "unavailable"
              : `${dsCount} connected`
          }
          hint={
            dsCount === 0
              ? "no data sources yet"
              : dataSources.data
                  ?.map((ds) => ADAPTER_LABEL[ds.adapter] ?? ds.adapter)
                  .filter((v, i, a) => a.indexOf(v) === i)
                  .join(" · ") ?? ""
          }
          status={dataSources.isError ? "warn" : dsCount > 0 ? "ok" : undefined}
        />
        <StatCard
          icon={Zap}
          title="License"
          loading={license.isLoading}
          value={licenseTier}
          hint={
            licenseMode === "free"
              ? "open-source · Apache-2.0"
              : licenseMode === "active"
              ? license.data?.expires_at
                ? `valid until ${formatDate(license.data.expires_at)}`
                : "active"
              : licenseMode === "grace"
              ? "grace period — renew soon"
              : licenseMode === "expired"
              ? "expired — renew required"
              : licenseMode
          }
          status={
            licenseMode === "expired"
              ? "error"
              : licenseMode === "grace"
              ? "warn"
              : "ok"
          }
        />
        <StatCard
          icon={Hash}
          title="Audit Log"
          loading={dash.isLoading}
          value={
            dash.data?.audit_chain.present
              ? formatBytes(dash.data.audit_chain.size_bytes)
              : "No log yet"
          }
          hint={
            dash.data?.audit_chain.last_event_type
              ? "tamper-evident chain active"
              : "no activity recorded"
          }
          status={dash.data?.audit_chain.present ? "ok" : undefined}
        />
      </section>

      {/* ── Engine Overview ── */}
      <section>
        <Card>
          <CardHeader>
            <div className="flex items-center gap-2">
              <Cpu className="h-4 w-4 text-accent" />
              <CardTitle className="text-base">AI Engines</CardTitle>
            </div>
            <CardDescription>
              Available worker engines — OS engine runs your prompts, workers handle delegated tasks.
            </CardDescription>
          </CardHeader>
          <CardContent>
            {engineSettings.isLoading && (
              <div className="space-y-2">
                {Array.from({ length: 4 }).map((_, i) => (
                  <Skeleton key={i} className="h-10 w-full" />
                ))}
              </div>
            )}
            {engineSettings.data && (
              <EngineGrid
                settings={engineSettings.data}
                health={engineHealth.data}
                activeOs={activeEngine}
                activeWorker={workerEngine}
                detectedStatus={engineStatus}
              />
            )}
          </CardContent>
        </Card>
      </section>

      {/* ── Data Sources ── */}
      <section>
        <Card>
          <CardHeader>
            <div className="flex items-center gap-2">
              <Database className="h-4 w-4 text-accent" />
              <CardTitle className="text-base">Data Sources</CardTitle>
            </div>
            <CardDescription>
              DSI v1 connections — databases and files the AI can query.
            </CardDescription>
          </CardHeader>
          <CardContent>
            {dataSources.isLoading && (
              <div className="space-y-2">
                {Array.from({ length: 2 }).map((_, i) => (
                  <Skeleton key={i} className="h-14 w-full" />
                ))}
              </div>
            )}
            {dataSources.isError && (
              <p className="text-sm text-muted-foreground flex items-center gap-2">
                <AlertTriangle className="h-4 w-4 text-amber-500 shrink-0" />
                Data source registry temporarily unavailable.{" "}
                <a href="/console/app/data-sources" className="underline underline-offset-2">
                  Manage data sources →
                </a>
              </p>
            )}
            {dataSources.data && dataSources.data.length === 0 && (
              <div className="flex flex-col items-center gap-3 py-6 text-center">
                <Database className="h-8 w-8 text-muted-foreground/40" />
                <p className="text-sm text-muted-foreground">
                  No data sources connected yet.
                </p>
                <a
                  href="/console/app/data-sources"
                  className="text-xs text-accent underline underline-offset-2"
                >
                  Connect a database or file →
                </a>
              </div>
            )}
            {dataSources.data && dataSources.data.length > 0 && (
              <DataSourceList sources={dataSources.data} />
            )}
          </CardContent>
        </Card>
      </section>

      {/* ── Bridges + Today's events ── */}
      <section className="grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader>
            <div className="flex items-center gap-2">
              <Network className="h-4 w-4 text-accent" />
              <CardTitle className="text-base">Messaging channels</CardTitle>
            </div>
            <CardDescription>
              Ready channels have a valid credential. "Configured" means a settings file
              exists but the token is missing.
            </CardDescription>
          </CardHeader>
          <CardContent>
            {dash.isLoading && (
              <div className="space-y-2">
                {Array.from({ length: 7 }).map((_, i) => (
                  <Skeleton key={i} className="h-8 w-full" />
                ))}
              </div>
            )}
            {dash.data && (
              <div className="divide-y divide-border/60 rounded-md border border-border/60">
                {dash.data.bridges.map((b) => (
                  <div
                    key={b.channel}
                    className="flex items-center justify-between px-4 py-2.5 text-sm"
                  >
                    <div className="flex items-center gap-3">
                      {b.has_token ? (
                        <CheckCircle2 className="h-4 w-4 text-emerald-500" />
                      ) : b.configured ? (
                        <AlertTriangle className="h-4 w-4 text-amber-400" />
                      ) : (
                        <XCircle className="h-4 w-4 text-muted-foreground/50" />
                      )}
                      <span className="font-medium">
                        {CHANNEL_LABEL[b.channel] ?? b.channel}
                      </span>
                    </div>
                    <Badge variant={b.has_token ? "ok" : b.configured ? "warn" : "outline"}>
                      {b.has_token ? "ready" : b.configured ? "no token" : "not configured"}
                    </Badge>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <div className="flex items-center gap-2">
              <Activity className="h-4 w-4 text-accent" />
              <CardTitle className="text-base">Today's events</CardTitle>
            </div>
            <CardDescription>Audit events by severity since midnight.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-2">
            {dash.isLoading &&
              Array.from({ length: 3 }).map((_, i) => (
                <Skeleton key={i} className="h-6 w-full" />
              ))}
            {dash.data && Object.keys(dash.data.today_counts).length === 0 && (
              <p className="text-sm text-muted-foreground">No events recorded yet today.</p>
            )}
            {dash.data &&
              Object.entries(dash.data.today_counts)
                .sort(([a], [b]) => a.localeCompare(b))
                .map(([sev, n]) => (
                  <div
                    key={sev}
                    className="flex items-center justify-between rounded-md bg-muted/50 px-3 py-2 text-sm"
                  >
                    <span className="flex items-center gap-2">
                      <SeverityIcon sev={sev} />
                      <span className="font-medium">{sev}</span>
                    </span>
                    <span className="font-mono text-muted-foreground">{n}</span>
                  </div>
                ))}
          </CardContent>
        </Card>
      </section>

      {/* ── API Keys + Compliance ── */}
      <section className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <div className="flex items-center gap-2">
              <Key className="h-4 w-4 text-accent" />
              <CardTitle className="text-base">API key status</CardTitle>
            </div>
            <CardDescription>
              Service credentials stored in the secure vault.
            </CardDescription>
          </CardHeader>
          <CardContent>
            {secrets.isLoading && (
              <div className="space-y-2">
                {Array.from({ length: 4 }).map((_, i) => (
                  <Skeleton key={i} className="h-8 w-full" />
                ))}
              </div>
            )}
            {secrets.data && !secrets.data.agent_reachable && (
              <p className="mb-3 text-xs text-amber-500 flex items-center gap-1.5">
                <AlertTriangle className="h-3.5 w-3.5" />
                Vault agent unreachable — key presence may be stale.
              </p>
            )}
            {secrets.data && (
              <div className="divide-y divide-border/60 rounded-md border border-border/60">
                {secrets.data.keys.map((k) => (
                  <div
                    key={k.key_name}
                    className="flex items-center justify-between px-4 py-2.5 text-sm"
                  >
                    <div className="flex items-center gap-3">
                      {k.present ? (
                        <CheckCircle2 className="h-4 w-4 text-emerald-500" />
                      ) : (
                        <XCircle className="h-4 w-4 text-muted-foreground/40" />
                      )}
                      <div>
                        <span className="font-medium">
                          {KEY_LABEL[k.key_name.toLowerCase()] ?? k.key_name}
                        </span>
                        <span className="ml-2 font-mono text-[10px] text-muted-foreground/60">
                          {k.key_name.toUpperCase()}
                        </span>
                      </div>
                    </div>
                    <Badge variant={k.present ? "ok" : "outline"}>
                      {k.present ? "set" : "missing"}
                    </Badge>
                  </div>
                ))}
              </div>
            )}
            {!secrets.isLoading && !secrets.data && (
              <p className="text-sm text-muted-foreground">
                {secrets.isError
                  ? "Key vault temporarily unavailable — "
                  : "Key vault not accessible. "}
                Configure keys in{" "}
                <a href="/console/app/api-keys" className="underline underline-offset-2">
                  Settings → API Keys
                </a>
                .
              </p>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <div className="flex items-center gap-2">
              <ShieldCheck className="h-4 w-4 text-accent" />
              <CardTitle className="text-base">Privacy & security</CardTitle>
            </div>
            <CardDescription>
              Structural protections — always active, cannot be disabled.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            <ComplianceRow label="Tamper-proof activity log (hash chain)" />
            <ComplianceRow label="Users must consent before AI responds" />
            <ComplianceRow label="AI identity always disclosed to users" />
            <ComplianceRow label="API keys encrypted, never exposed to AI" />
            <ComplianceRow label="File write access gated by path-guard" />
            <ComplianceRow label="Audit chain hash-verified daily (L16)" />
          </CardContent>
        </Card>
      </section>

      {/* ── Instance identity (ADR-0145) ── */}
      <section className="grid gap-4">
        <Card>
          <CardHeader>
            <div className="flex items-center gap-2">
              <Fingerprint className="h-4 w-4 text-accent" />
              <CardTitle className="text-base">Instance identity</CardTitle>
            </div>
            <CardDescription>
              This installation's stable identifier and Instance Binding Certificate (IBC) status.
            </CardDescription>
          </CardHeader>
          <CardContent>
            {identity.isLoading && (
              <div className="space-y-2">
                <Skeleton className="h-8 w-full" />
                <Skeleton className="h-8 w-2/3" />
              </div>
            )}
            {identity.data && <InstanceIdentityCard status={identity.data} />}
            {!identity.isLoading && !identity.data && (
              <p className="text-sm text-muted-foreground">
                Instance identity unavailable — this feature requires the ADR-0145
                identity module.
              </p>
            )}
          </CardContent>
        </Card>
      </section>
    </div>
  );
}

// ── Instance Identity Card ──────────────────────────────────────────────────

function InstanceIdentityCard({ status }: { status: InstanceIdentityStatus }) {
  return (
    <div className="space-y-3 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-xs break-all">{status.instance_id || "—"}</span>
        {status.label && (
          <Badge variant="outline" className="text-[10px]">{status.label}</Badge>
        )}
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant={status.ibc_bound ? "ok" : "outline"}>
          {status.ibc_bound ? "IBC bound" : "not bound"}
        </Badge>
        {status.ibc_bound && status.plan && (
          <Badge variant="secondary" className="text-[10px]">{status.plan}</Badge>
        )}
        <Badge variant={status.hardware_bound ? "ok" : "outline"}>
          {status.hardware_bound ? "hardware tethered" : "hardware not tethered"}
        </Badge>
        {status.hardware_bound && status.hardware_matches === false && (
          <Badge variant="danger" className="text-[10px]">hardware mismatch</Badge>
        )}
        {status.revocation_status === "revoked" && (
          <Badge variant="danger">revoked</Badge>
        )}
        {status.revocation_status === "clean" && (
          <Badge variant="ok" className="text-[10px]">not revoked</Badge>
        )}
      </div>
      {!status.ibc_bound && (
        <p className="text-xs text-muted-foreground">
          Bind this instance to your Corvin Labs account with{" "}
          <code className="rounded bg-muted px-1 py-0.5 font-mono text-[10px]">corvin-id init</code>{" "}
          to turn this ID into a verifiable credential.
        </p>
      )}
    </div>
  );
}

// ── Engine Grid ───────────────────────────────────────────────────────────────

function EngineGrid({
  settings,
  health,
  activeOs,
  activeWorker,
  detectedStatus,
}: {
  settings: OsEngineSetting;
  health: OsEngineHealth | undefined;
  activeOs: string;
  activeWorker: string | null;
  detectedStatus: Record<string, { installed: boolean; has_credential: boolean }>;
}) {
  const allEngines = Array.from(
    new Set([...settings.valid_engines, ...settings.valid_worker_engines]),
  );

  return (
    <div className="divide-y divide-border/60 rounded-md border border-border/60">
      {allEngines.map((id) => {
        const meta = ENGINE_META[id] ?? {
          label: id,
          locality: "unknown",
          role: "Worker",
          color: "text-muted-foreground",
        };
        const isActiveOs = id === activeOs;
        const isActiveWorker = id === activeWorker;
        const isOsCapable = settings.valid_engines.includes(id);
        const isWorkerCapable = settings.valid_worker_engines.includes(id);
        const isHermes = id === "hermes";
        const ollamaOk = health?.ollama_reachable ?? false;
        const ollamaModels = health?.model_count ?? 0;
        const detected = detectedStatus[id];
        // If the backend hasn't returned detection data yet (first load),
        // fall back to "assume capable" so the UI doesn't flicker grey.
        const isInstalled = detected?.installed ?? true;
        const hasCred = detected?.has_credential ?? true;

        let statusDot: "ok" | "warn" | "off" = "off";
        if (!isInstalled) {
          statusDot = "off";                          // binary absent → grey
        } else if (isHermes) {
          statusDot = ollamaOk ? "ok" : "warn";      // Ollama running check
        } else if (!hasCred) {
          statusDot = "warn";                         // installed but no credential
        } else if (isActiveOs || isActiveWorker || isOsCapable || isWorkerCapable) {
          statusDot = "ok";
        }

        return (
          <div
            key={id}
            className="flex items-center justify-between gap-4 px-4 py-3 text-sm"
          >
            <div className="flex items-center gap-3 min-w-0">
              <div
                className={`h-2 w-2 shrink-0 rounded-full ${
                  statusDot === "ok"
                    ? "bg-emerald-500"
                    : statusDot === "warn"
                    ? "bg-amber-400"
                    : "bg-muted-foreground/30"
                }`}
              />
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <span className={`font-medium ${meta.color}`}>{meta.label}</span>
                  {isActiveOs && (
                    <Badge variant="ok" className="text-[10px] px-1 py-0">
                      OS engine
                    </Badge>
                  )}
                  {isActiveWorker && !isActiveOs && (
                    <Badge variant="secondary" className="text-[10px] px-1 py-0">
                      worker
                    </Badge>
                  )}
                </div>
                <p className="text-xs text-muted-foreground truncate">
                  {meta.role}
                  {!isInstalled && " · binary not found"}
                  {isInstalled && !isHermes && !hasCred && " · credential missing"}
                  {isHermes && ollamaOk && ` · ${ollamaModels} model${ollamaModels !== 1 ? "s" : ""} loaded`}
                  {isHermes && !ollamaOk && isInstalled && " · Ollama not reachable"}
                </p>
              </div>
            </div>
            <div className="flex items-center gap-2 shrink-0">
              <Badge
                variant={meta.locality === "local" ? "ok" : "outline"}
                className="text-[10px]"
              >
                {meta.locality}
              </Badge>
              {isOsCapable && !isWorkerCapable && (
                <Badge variant="outline" className="text-[10px]">OS</Badge>
              )}
              {isWorkerCapable && !isOsCapable && (
                <Badge variant="outline" className="text-[10px]">worker</Badge>
              )}
              {isOsCapable && isWorkerCapable && (
                <Badge variant="outline" className="text-[10px]">OS + worker</Badge>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

// ── Data Source List ──────────────────────────────────────────────────────────

function DataSourceList({ sources }: { sources: DSIConnection[] }) {
  return (
    <div className="divide-y divide-border/60 rounded-md border border-border/60">
      {sources.map((ds) => {
        const isDb = !["local_file", "http"].includes(ds.adapter);
        const Icon = isDb ? Database : File;
        const classVariant =
          CLASSIFICATION_VARIANT[ds.data_classification] ?? "outline";
        const host =
          typeof ds.config.host === "string"
            ? `${ds.config.host}:${ds.config.port ?? ""}`
            : typeof ds.config.path === "string"
            ? ds.config.path.split("/").pop()
            : null;

        return (
          <div key={ds.name} className="flex items-center gap-3 px-4 py-3 text-sm">
            <Icon className="h-4 w-4 shrink-0 text-accent/80" />
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2 flex-wrap">
                <span className="font-medium truncate">{ds.name}</span>
                <Badge variant="outline" className="text-[10px] shrink-0">
                  {ADAPTER_LABEL[ds.adapter] ?? ds.adapter}
                </Badge>
                <Badge
                  variant={classVariant}
                  className="text-[10px] shrink-0"
                >
                  {ds.data_classification}
                </Badge>
                {ds.read_only && (
                  <Badge variant="outline" className="text-[10px] shrink-0">
                    read-only
                  </Badge>
                )}
              </div>
              {(ds.description || host) && (
                <p className="mt-0.5 text-xs text-muted-foreground truncate">
                  {host && <span className="font-mono">{host}</span>}
                  {host && ds.description && " · "}
                  {ds.description}
                </p>
              )}
            </div>
            <a
              href="/console/app/data-sources"
              className="shrink-0 text-muted-foreground/50 hover:text-muted-foreground"
            >
              <ChevronRight className="h-4 w-4" />
            </a>
          </div>
        );
      })}
    </div>
  );
}

// ── Sub-components ────────────────────────────────────────────────────────────

function StatCard(props: {
  icon: React.ComponentType<{ className?: string }>;
  title: string;
  value: string;
  hint?: string;
  loading?: boolean;
  status?: "ok" | "warn" | "error";
}) {
  const { icon: Icon, title, value, hint, loading, status } = props;
  return (
    <Card className="overflow-hidden">
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2 text-muted-foreground">
            <Icon className="h-4 w-4 text-accent" />
            <span className="text-xs font-medium uppercase tracking-wider">{title}</span>
          </div>
          {status === "ok" && <div className="h-2 w-2 rounded-full bg-emerald-500" />}
          {status === "warn" && <div className="h-2 w-2 rounded-full bg-amber-400" />}
          {status === "error" && <div className="h-2 w-2 rounded-full bg-destructive" />}
        </div>
      </CardHeader>
      <CardContent>
        {loading ? (
          <>
            <Skeleton className="mb-2 h-6 w-24" />
            <Skeleton className="h-3 w-32" />
          </>
        ) : (
          <>
            <div className="truncate font-serif text-xl font-medium">{value}</div>
            {hint && <div className="mt-1 text-xs text-muted-foreground">{hint}</div>}
          </>
        )}
      </CardContent>
    </Card>
  );
}

function SeverityIcon({ sev }: { sev: string }) {
  if (sev === "CRITICAL" || sev === "ERROR")
    return <AlertCircle className="h-4 w-4 text-destructive" />;
  if (sev === "WARN" || sev === "WARNING")
    return <AlertTriangle className="h-4 w-4 text-amber-500" />;
  return <CheckCircle2 className="h-4 w-4 text-emerald-500" />;
}

function ComplianceRow({ label }: { label: string }) {
  return (
    <div className="flex items-center gap-2">
      <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-500" />
      <span>{label}</span>
    </div>
  );
}
