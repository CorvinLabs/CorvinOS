/**
 * Compute Layer (Layer 25) — Enterprise Dashboard.
 * Tabs: Runs / Pipelines / HAC / Analytics
 */
import * as React from "react";
import { useNavigate } from "react-router-dom";
import { useQuery, useQueries, useQueryClient, useMutation } from "@tanstack/react-query";
import {
  Activity, AlertTriangle, BarChart3, Bell, CheckCircle2,
  ChevronDown, ChevronUp, Clock, Copy, Cpu, Download, ExternalLink,
  FlaskConical, FolderOpen, Globe, HardDrive, Layers, Loader2,
  MessageSquare, Package, Play, RefreshCw, Server, Settings, Shield,
  Tag, Target, TrendingDown, Trash2, Trophy, X, Zap,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { ReauthDialog } from "@/components/reauth-dialog";
import { useAuth } from "@/lib/auth";
import {
  deleteComputeRun, getComputeConfig, getComputeLicense, getComputeRunDetail,
  getComputeStatus, getPipelineDetail, getHacDetail,
  listPipelines, listHacRuns,
  openRunDir, openPipelineDir, openHacDir,
  getCorpusContext, listExperiments, getExperimentDetail,
  getArtifactStats, getArtifactPreview,
  getComputeSettings, updateComputeSettings,
  getAwpkgPreview, downloadAwpkg, promoteChampion, pipelineToWorkflow,
  artifactDownloadUrl, experimentJupyterUrl, experimentMlflowUrl, experimentReportUrl,
  computeStageImageUrl,
  getOpenBatchJobs,
  listAcsRuns,
  listComputeJobs, submitComputeJob, cancelComputeJob,
  type ComputeJob,
  type ComputeLicenseStatus, type ComputeRun, type PipelineSummary,
  type PipelineStageDetail, type HacSummary, type HacManagerDetail,
  type CorpusContext, type Experiment, type ExperimentRunDetail,
  type ComputeSettings, type AwpkgExportRequest, type OpenBatchJob,
} from "@/lib/api";
import { DataTable, type DataTableFetchParams } from "@/components/table";
import { MediaGallery, type MediaItem } from "@/components/media";
import { ComputeNarrativeDialog } from "@/components/ComputeNarrativeDialog";
import { ComputeGraphView } from "@/components/ComputeGraphView";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { AcsTab } from "@/components/AcsTab";
import { cn } from "@/lib/utils";

// ── Helpers ───────────────────────────────────────────────────────────────

function formatTimeAgo(ts: number | null): string {
  if (!ts) return "—";
  const diff = Date.now() / 1000 - ts;
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}
function formatDuration(startTs: number | null, endTs?: number | null): string {
  if (!startTs) return "—";
  const end = endTs ?? Date.now() / 1000;
  const diff = end - startTs;
  if (diff < 60) return `${Math.round(diff)}s`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ${Math.round(diff % 60)}s`;
  return `${Math.floor(diff / 3600)}h ${Math.floor((diff % 3600) / 60)}m`;
}
function pct(v: number) { return `${Math.round(v * 10) / 10}%`; }

// WA-18: the worker's flat/l25 run engine never produces a state literally
// named "complete" — its real terminal states (core/compute/corvin_compute/
// budget.py RUN_STATE_*) are "converged", "stalled", and "budget_exhausted".
// Every `r.state === "complete"` check across this page compared against a
// value that can never occur, so the top KPI strip ("Success Rate", "Best
// Loss"), every run-card "done" badge, and both Analytics tables (Strategy
// Effectiveness, Tool Performance Ranking) always read as empty/zero no
// matter how many runs actually succeeded.
const RUN_DONE_STATES = new Set(["converged", "stalled", "budget_exhausted"]);
export function isRunDone(state: string | null | undefined): boolean {
  return !!state && RUN_DONE_STATES.has(state);
}
// Pipelines (as opposed to their individual stages, which genuinely do use
// "complete" — see pipeline_detail's fallback in routes/compute.py) only
// ever reach "converged" at the top level (PipelineCoordinator.run()).
export function isPipelineDone(state: string | null | undefined): boolean {
  return state === "converged";
}

const STRATEGY_COLORS: Record<string, string> = {
  bayesian: "#8b5cf6", grid: "#3b82f6", random: "#f97316",
};
const MANAGER_COLORS = ["#3b82f6", "#8b5cf6", "#f97316", "#06b6d4", "#ec4899"];

// ── SVG Sparkline ─────────────────────────────────────────────────────────

function LossSparkline({ points, height = 48, color = "currentColor" }: {
  points: { iter: number; loss: number }[]; height?: number; color?: string;
}) {
  if (points.length < 2) return null;
  const W = 300; const H = height; const P = { x: 3, y: 4 };
  const ls = points.map((p) => p.loss);
  const mn = Math.min(...ls); const mx = Math.max(...ls);
  const rng = mx - mn || 0.01;
  const w = W - P.x * 2; const h = H - P.y * 2;
  const tx = (i: number) => P.x + (i / (points.length - 1)) * w;
  const ty = (v: number) => P.y + (1 - (v - mn) / rng) * h;
  const poly = points.map((p, i) => `${tx(i)},${ty(p.loss)}`).join(" ");
  const bestIdx = ls.indexOf(mn);
  return (
    <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} className="w-full" preserveAspectRatio="none">
      <defs>
        <linearGradient id={`sf-${bestIdx}-${H}`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.15" />
          <stop offset="100%" stopColor={color} stopOpacity="0.01" />
        </linearGradient>
      </defs>
      <polygon points={`${tx(0)},${H} ${poly} ${tx(points.length - 1)},${H}`}
        fill={`url(#sf-${bestIdx}-${H})`} />
      <polyline points={poly} fill="none" stroke={color} strokeWidth="1.5"
        strokeLinejoin="round" strokeLinecap="round" />
      <circle cx={tx(bestIdx)} cy={ty(mn)} r="3" fill="#22c55e" />
    </svg>
  );
}

// ── Open Directory Button ─────────────────────────────────────────────────

function OpenDirButton({ onOpen, path }: {
  onOpen: () => Promise<{ ok: boolean; path: string; launched: boolean }>;
  path?: string;
}) {
  const [state, setState] = React.useState<"idle" | "loading" | "done" | "error">("idle");
  const [resolvedPath, setResolvedPath] = React.useState<string | null>(path ?? null);
  const [copied, setCopied] = React.useState(false);

  const handleOpen = async () => {
    setState("loading");
    try {
      const res = await onOpen();
      setResolvedPath(res.path);
      setState(res.launched ? "done" : "error");
      if (!res.launched) {
        // No display — still show path
        setState("done");
      }
      setTimeout(() => setState("idle"), 3000);
    } catch {
      setState("error");
      setTimeout(() => setState("idle"), 3000);
    }
  };

  const handleCopy = () => {
    if (!resolvedPath) return;
    navigator.clipboard.writeText(resolvedPath).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  };

  return (
    <div className="flex items-center gap-1">
      <Button
        variant="ghost"
        size="sm"
        className={cn(
          "h-7 px-2 gap-1.5 text-xs",
          state === "done" && "text-emerald-600 dark:text-emerald-400",
          state === "error" && "text-amber-600",
        )}
        onClick={handleOpen}
        disabled={state === "loading"}
        title="Open directory in file manager"
      >
        {state === "loading"
          ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
          : <FolderOpen className="h-3.5 w-3.5" />}
        {state === "done" ? "Open" : state === "error" ? "No display" : "Open folder"}
      </Button>

      {resolvedPath && (
        <Button
          variant="ghost"
          size="sm"
          className="h-7 w-7 p-0 text-muted-foreground"
          onClick={handleCopy}
          title={copied ? "Kopiert!" : "Pfad kopieren"}
        >
          <Copy className={cn("h-3.5 w-3.5", copied && "text-emerald-500")} />
        </Button>
      )}

      {resolvedPath && state !== "idle" && (
        <span className="font-mono text-[10px] text-muted-foreground truncate max-w-64 hidden lg:block">
          {resolvedPath}
        </span>
      )}
    </div>
  );
}

// ── System Resource Bar ───────────────────────────────────────────────────

interface SystemResources {
  ram: { total_gb: number; used_gb: number; free_gb: number; used_pct: number } | null;
  cpu: { used_pct: number; core_count: number } | null;
  disk: { total_gb: number; free_gb: number; used_pct: number } | null;
}

function ResourceGauge({ label, usedPct, detail, icon: Icon, warnAt = 80, critAt = 90 }: {
  label: string; usedPct: number; detail: string;
  icon: React.ElementType; warnAt?: number; critAt?: number;
}) {
  const color =
    usedPct >= critAt ? "bg-destructive" :
    usedPct >= warnAt ? "bg-amber-500" : "bg-emerald-500";
  const textColor =
    usedPct >= critAt ? "text-destructive" :
    usedPct >= warnAt ? "text-amber-600 dark:text-amber-400" : "text-emerald-600 dark:text-emerald-400";
  return (
    <div className="flex-1 min-w-0 space-y-1.5">
      <div className="flex items-center justify-between gap-2">
        <span className="flex items-center gap-1 text-xs text-muted-foreground">
          <Icon className="h-3 w-3" />{label}
        </span>
        <span className={cn("text-xs font-mono font-medium", textColor)}>{usedPct.toFixed(0)}%</span>
      </div>
      <div className="h-1.5 bg-muted rounded-full overflow-hidden">
        <div className={cn("h-full rounded-full transition-all", color)} style={{ width: `${Math.min(100, usedPct)}%` }} />
      </div>
      <div className="text-[10px] text-muted-foreground truncate">{detail}</div>
    </div>
  );
}

function SystemResourceBar({ sys }: { sys: SystemResources | null }) {
  if (!sys) return null;
  return (
    <div className="rounded-lg border border-border bg-card/50 px-4 py-3">
      <div className="flex items-center gap-1.5 text-[10px] text-muted-foreground uppercase tracking-wide font-medium mb-3">
        <Server className="h-3 w-3" />Node resources
      </div>
      <div className="flex gap-6">
        {sys.ram && (
          <ResourceGauge
            label="RAM"
            icon={Server}
            usedPct={sys.ram.used_pct}
            detail={`${sys.ram.free_gb} GB free / ${sys.ram.total_gb} GB total`}
            warnAt={75} critAt={90}
          />
        )}
        {sys.cpu && (
          <ResourceGauge
            label={`CPU · ${sys.cpu.core_count} cores`}
            icon={Cpu}
            usedPct={sys.cpu.used_pct}
            detail={`${sys.cpu.used_pct.toFixed(1)}% used`}
            warnAt={70} critAt={90}
          />
        )}
        {sys.disk && (
          <ResourceGauge
            label="Disk"
            icon={HardDrive}
            usedPct={sys.disk.used_pct}
            detail={`${sys.disk.free_gb} GB free / ${sys.disk.total_gb} GB total`}
            warnAt={80} critAt={95}
          />
        )}
      </div>
    </div>
  );
}

// ── Compute License Bar ───────────────────────────────────────────────────

function ComputeLicenseBar({ lic }: { lic: ComputeLicenseStatus }) {
  const isLicensed = lic.mode === "licensed";
  const isTrial    = lic.mode === "trial";
  const isGrace    = lic.mode === "grace";
  const isDenied   = lic.mode === "denied";

  const modeBadgeVariant = isLicensed ? "ok" : isGrace ? "warn" : isDenied ? "danger" : "secondary";
  const modeLabel = isLicensed ? `Licensed · ${lic.tier}` :
    isGrace ? `Grace period · ${lic.tier}` :
    isDenied ? "Trial exhausted" : `Trial · ${lic.tier}`;

  return (
    <div className={cn(
      "rounded-lg border px-4 py-3 space-y-3",
      isDenied ? "border-destructive/40 bg-destructive/5" :
      isGrace ? "border-amber-300/60 bg-amber-50/40 dark:bg-amber-950/10" :
      "border-border bg-card/50"
    )}>
      {/* Header row */}
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-[10px] text-muted-foreground uppercase tracking-wide font-medium">
            Agentic Compute Licence
          </span>
          <Badge variant={modeBadgeVariant} className="text-[10px]">{modeLabel}</Badge>
          {lic.fabric_allowed && (
            <Badge variant="ok" className="text-[10px]">Compute Fabric</Badge>
          )}
        </div>
        <div className="flex items-center gap-3 shrink-0">
          {/* Daily run counter — shown against licence limit when set */}
          {lic.daily_limit !== null && lic.daily_limit !== undefined ? (
            <span className={cn(
              "text-xs font-mono font-semibold",
              (lic.runs_today ?? 0) >= lic.daily_limit
                ? "text-destructive"
                : "text-muted-foreground"
            )}>
              {lic.runs_today ?? 0}/{lic.daily_limit} today
            </span>
          ) : (
            <span className="text-xs text-muted-foreground">
              <span className="font-semibold text-foreground">{lic.runs_today ?? 0}</span> runs today
            </span>
          )}
          {(isTrial || isDenied) && (
            <a
              href={lic.upgrade_url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-xs text-accent hover:underline font-medium"
            >
              Upgrade →
            </a>
          )}
        </div>
      </div>

      {/* Daily limit bar — shown when a hard daily cap is set (Free tier) */}
      {lic.daily_limit !== null && lic.daily_limit !== undefined && (isTrial || isGrace) && (
        <div className="space-y-1.5">
          <div className="flex items-center justify-between text-xs">
            <span className="text-muted-foreground">Daily run limit</span>
            <span className={cn(
              "font-mono font-semibold",
              (lic.runs_today ?? 0) >= lic.daily_limit ? "text-destructive" :
              (lic.runs_today ?? 0) >= lic.daily_limit * 0.9 ? "text-amber-600 dark:text-amber-400" :
              "text-muted-foreground"
            )}>
              {lic.runs_today ?? 0} / {lic.daily_limit} run{lic.daily_limit !== 1 ? "s" : ""}
            </span>
          </div>
          <div className="h-2 bg-muted rounded-full overflow-hidden">
            <div
              className="h-full rounded-full transition-all"
              style={{
                width: `${Math.min(100, ((lic.runs_today ?? 0) / lic.daily_limit) * 100)}%`,
                background: (lic.runs_today ?? 0) >= lic.daily_limit
                  ? "var(--destructive)"
                  : (lic.runs_today ?? 0) >= lic.daily_limit * 0.9
                  ? "#f59e0b"
                  : "#3b82f6",
              }}
            />
          </div>
          {(lic.runs_today ?? 0) >= lic.daily_limit && (
            <p className="text-xs text-destructive">
              Daily limit reached. Resets at midnight UTC.{" "}
              <a href={lic.upgrade_url} target="_blank" rel="noopener noreferrer"
                 className="underline hover:no-underline">Upgrade for more runs →</a>
            </p>
          )}
        </div>
      )}

      {/* Licensed — feature summary */}
      {isLicensed && (
        <div className="flex items-center gap-4 text-xs text-muted-foreground flex-wrap">
          <span>Unlimited compute jobs</span>
          {lic.license_meta?.expires_at && (
            <span>
              Expires {new Date(lic.license_meta.expires_at * 1000).toLocaleDateString()}
            </span>
          )}
          <span>
            {lic.fabric_allowed ? "Compute Fabric enabled" : "Fabric not included"}
          </span>
          {lic.license_meta?.feature_flags && lic.license_meta.feature_flags.length > 0 && (
            <span className="text-accent">
              {lic.license_meta.feature_flags.join(" · ")}
            </span>
          )}
        </div>
      )}

      {/* Grace period warning */}
      {isGrace && lic.reason && (
        <div className="flex items-start gap-2 text-xs text-amber-600 dark:text-amber-400">
          <AlertTriangle className="h-3.5 w-3.5 shrink-0 mt-0.5" />
          <span>{lic.reason}</span>
        </div>
      )}

      {/* Denied */}
      {isDenied && (
        <div className="flex items-start gap-2 text-xs text-destructive">
          <AlertTriangle className="h-3.5 w-3.5 shrink-0 mt-0.5" />
          <span>
            {lic.reason ?? "Trial limit reached. Upgrade to continue submitting compute jobs."}
          </span>
        </div>
      )}
    </div>
  );
}

// ── KPI Strip ─────────────────────────────────────────────────────────────

function KpiCard({ icon: Icon, label, value, sub, color = "text-foreground", trend }: {
  icon: React.ElementType; label: string; value: string; sub?: string;
  color?: string; trend?: number[];
}) {
  const trendPts = trend ? trend.map((v, i) => ({ iter: i, loss: v })) : null;
  return (
    <div className="flex-1 min-w-0 rounded-xl border border-border bg-card px-4 py-3 space-y-1.5">
      <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
        <Icon className="h-3.5 w-3.5" />
        {label}
      </div>
      <div className={cn("text-2xl font-bold tracking-tight", color)}>{value}</div>
      {sub && <div className="text-xs text-muted-foreground">{sub}</div>}
      {trendPts && trendPts.length >= 2 && (
        <div className="pt-1">
          <LossSparkline points={trendPts} height={24} color="#b8945f" />
        </div>
      )}
    </div>
  );
}

function ComputeKpiStrip({ runs, pipelineCount, hacCount, hacRootLoss }: {
  runs: ComputeRun[]; pipelineCount: number; hacCount: number; hacRootLoss: number | null;
}) {
  const completed = runs.filter((r) => isRunDone(r.state));
  const active = runs.filter((r) => r.state === "running").length;
  const failed = runs.filter((r) => r.state === "failed").length;
  const successRate = completed.length + failed > 0
    ? (completed.length / (completed.length + failed)) * 100 : 0;
  const losses = completed.map((r) => r.best_loss).filter((v): v is number => v != null);
  const bestLoss = losses.length ? Math.min(...losses) : null;
  const avgIter = completed.length
    ? completed.reduce((a, r) => a + r.iterations, 0) / completed.length : 0;
  return (
    <div className="flex gap-3 overflow-x-auto pb-1">
      <KpiCard icon={Zap} label="Active Jobs" value={String(active)}
        sub={`${runs.length} total`}
        color={active > 0 ? "text-amber-600 dark:text-amber-400" : "text-foreground"} />
      <KpiCard icon={CheckCircle2} label="Success Rate"
        value={completed.length + failed > 0 ? `${pct(successRate)}` : "—"}
        sub={`${completed.length} completed · ${failed} failed`}
        color={successRate >= 80 ? "text-emerald-600 dark:text-emerald-400" : successRate >= 50 ? "text-amber-600" : "text-destructive"} />
      <KpiCard icon={Target} label="Best Loss"
        value={bestLoss != null ? bestLoss.toFixed(4) : "—"}
        sub={bestLoss != null ? `across ${completed.length} runs` : "no completed runs"}
        color="text-emerald-600 dark:text-emerald-400" />
      <KpiCard icon={Activity} label="Avg Convergence"
        value={completed.length ? `${Math.round(avgIter)} iter` : "—"}
        sub="mean iterations to complete"
        trend={completed.map((r) => r.iterations)} />
      <KpiCard icon={BarChart3} label="Pipelines + HAC"
        value={`${pipelineCount + hacCount}`}
        sub={`${pipelineCount} pipeline${pipelineCount !== 1 ? "s" : ""} · ${hacCount} HAC`}
        color="text-blue-600 dark:text-blue-400" />
      <KpiCard icon={TrendingDown} label="HAC Root Loss"
        value={hacRootLoss != null ? hacRootLoss.toFixed(4) : "—"}
        sub={hacRootLoss != null ? "hierarchical optimizer" : "no HAC runs active"}
        color="text-purple-600 dark:text-purple-400" />
    </div>
  );
}

// ── Worker status ─────────────────────────────────────────────────────────

function WorkerStatusBar({ enabled, socketOk }: { enabled: boolean; socketOk: boolean }) {
  return (
    <div className="flex items-center gap-3 rounded-lg border border-border bg-card/50 px-4 py-2.5 text-sm">
      <div className={cn("w-2 h-2 rounded-full shrink-0", socketOk ? "bg-emerald-500" : "bg-muted-foreground/40")} />
      <span className={cn("font-medium", socketOk ? "text-emerald-600 dark:text-emerald-400" : "text-muted-foreground")}>
        {socketOk ? "Worker running" : enabled ? "Worker not running" : "Worker disabled"}
      </span>
      {!socketOk && enabled && (
        <span className="text-xs text-muted-foreground font-mono hidden md:block">
          systemctl --user start corvin-compute@_default
        </span>
      )}
    </div>
  );
}

// ── Run card sub-components ────────────────────────────────────────────────

function BudgetBar({ used, total, state }: { used: number; total: number; state: string | null }) {
  const pctUsed = total > 0 ? Math.min(100, (used / total) * 100) : 0;
  const isEfficient = pctUsed < 60 && isRunDone(state);
  const color = isEfficient ? "bg-emerald-500" : state === "running" ? "bg-amber-500" : "bg-blue-500";
  return (
    <div className="space-y-0.5">
      <div className="flex justify-between text-[10px] text-muted-foreground">
        <span>Budget</span>
        <span className="font-mono">{used}/{total} iter ({Math.round(pctUsed)}%)</span>
      </div>
      <div className="h-1.5 bg-muted rounded-full overflow-hidden">
        <div className={cn("h-full rounded-full transition-all", color)} style={{ width: `${pctUsed}%` }} />
      </div>
    </div>
  );
}

function ConvergenceBadge({ bestIter, maxIter }: { bestIter: number | null; maxIter: number }) {
  if (bestIter == null) return null;
  const frac = bestIter / maxIter;
  if (frac < 0.4) return <Badge variant="ok" className="text-[10px]">Early convergence</Badge>;
  if (frac < 0.75) return <Badge variant="secondary" className="text-[10px]">Mid convergence</Badge>;
  return <Badge variant="warn" className="text-[10px]">Late convergence</Badge>;
}

function ImprovementChip({ first, best }: { first: number | null; best: number | null }) {
  if (first == null || best == null || first <= 0) return null;
  const imp = ((first - best) / first) * 100;
  if (imp < 0) return null;
  return (
    <span className="inline-flex items-center gap-1 text-xs text-emerald-600 dark:text-emerald-400 font-mono">
      <TrendingDown className="h-3 w-3" />▼{pct(imp)} loss
    </span>
  );
}

function AnomalyFlag({ iterations }: { iterations: { iter: number; loss: number }[] }) {
  if (iterations.length < 4) return null;
  for (let i = 0; i < iterations.length - 3; i++) {
    if (iterations[i + 1].loss > iterations[i].loss &&
      iterations[i + 2].loss > iterations[i + 1].loss &&
      iterations[i + 3].loss > iterations[i + 2].loss) {
      return (
        <span className="inline-flex items-center gap-1 text-[10px] text-amber-600 font-medium">
          <AlertTriangle className="h-3 w-3" />loss spike at iter {iterations[i + 1].iter}
        </span>
      );
    }
  }
  return null;
}

// ── RunJobCard ─────────────────────────────────────────────────────────────

function RunJobCard({ run, csrf, onDeleted }: { run: ComputeRun; csrf: string; onDeleted: () => void }) {
  const [expanded, setExpanded] = React.useState(false);
  const [deleting, setDeleting] = React.useState(false);
  const [reauthOpen, setReauthOpen] = React.useState(false);
  const [activeTab, setActiveTab] = React.useState("details");

  const detailQ = useQuery({
    queryKey: ["compute-run-detail", run.run_id],
    queryFn: ({ signal }) => getComputeRunDetail(run.run_id, signal),
    enabled: expanded, staleTime: 15_000,
  });

  const doDelete = async () => {
    setDeleting(true);
    try { await deleteComputeRun(run.run_id, csrf); onDeleted(); } finally { setDeleting(false); }
  };

  const isRunning = run.state === "running";
  const isComplete = isRunDone(run.state);
  const isFailed = run.state === "failed";
  const budget = detailQ.data?.manifest?.budget;
  const maxIter = (budget?.max_iterations as number) ?? 20;
  const iterations = detailQ.data?.iterations ?? [];
  const firstLoss = iterations[0]?.loss ?? null;
  const convergence = detailQ.data?.summary?.convergence_reason ?? null;
  const objective = detailQ.data?.manifest?.objective ?? null;
  const bestIter = detailQ.data?.summary?.best_iter ?? null;
  const submittedBy = detailQ.data?.manifest?.submitted_by ?? null;
  const stratColor = STRATEGY_COLORS[run.strategy ?? ""] ?? "#6b7280";

  return (
    <div className="rounded-lg border border-border bg-card overflow-hidden">
      <button className="w-full px-4 py-3 text-left hover:bg-muted/20 transition-colors"
        onClick={() => setExpanded((v) => !v)}>
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="font-mono text-xs text-muted-foreground">{run.run_id}</div>
            <div className="font-semibold text-sm">{run.tool_name ?? "Unknown tool"}</div>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <span className="text-xs text-muted-foreground">{formatTimeAgo(run.started_at)}</span>
            {expanded ? <ChevronUp className="h-3.5 w-3.5 text-muted-foreground" /> : <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" />}
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2 mt-1.5">
          <span className="inline-flex items-center gap-1 text-xs font-mono px-2 py-0.5 rounded-full border"
            style={{ borderColor: stratColor + "60", color: stratColor, background: stratColor + "15" }}>
            {run.strategy ?? "unknown"}
          </span>
          <Badge variant={isComplete ? "ok" : isFailed ? "danger" : isRunning ? "default" : "outline"} className="text-xs">
            {run.state ?? "pending"}
          </Badge>
          {submittedBy && <span className="text-xs text-muted-foreground">via {submittedBy}</span>}
          {isComplete && bestIter != null && <ConvergenceBadge bestIter={bestIter} maxIter={maxIter} />}
          {convergence && <Badge variant="secondary" className="text-[10px] font-mono">{convergence.replace(/_/g, " ")}</Badge>}
        </div>

        {/* Progress bar for running */}
        {isRunning && (
          <div className="mt-2 space-y-1">
            <div className="w-full bg-muted rounded-full h-1.5">
              <div className="bg-amber-500 h-1.5 rounded-full transition-all"
                style={{ width: `${Math.min(100, (run.iterations / maxIter) * 100)}%` }} />
            </div>
            <div className="flex justify-between text-xs text-muted-foreground">
              <span>{run.iterations}/{maxIter} iterations</span>
              {run.best_loss != null && <span className="font-mono">best: {run.best_loss.toFixed(4)}</span>}
            </div>
          </div>
        )}

        {/* Compact summary for complete */}
        {isComplete && !expanded && (
          <div className="mt-1.5 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
            <span>{run.iterations} iter</span>
            {run.best_loss != null && <span className="font-mono">best loss: <strong className="text-foreground">{run.best_loss.toFixed(4)}</strong></span>}
            {firstLoss != null && run.best_loss != null && <ImprovementChip first={firstLoss} best={run.best_loss} />}
          </div>
        )}
      </button>

      {/* Expanded detail */}
      {expanded && (
        <div className="border-t border-border bg-muted/10">
          {detailQ.isLoading && (
            <div className="px-4 py-4 flex items-center gap-2 text-xs text-muted-foreground">
              <Loader2 className="h-3 w-3 animate-spin" />Loading detail…
            </div>
          )}
          {detailQ.data && (
            <div className="px-4 py-4 space-y-4">
              <Tabs defaultValue="details" className="mt-3" onValueChange={setActiveTab}>
                <TabsList className="h-8">
                  <TabsTrigger value="details" className="text-xs h-7 px-3">Details</TabsTrigger>
                  <TabsTrigger value="graph" className="text-xs h-7 px-3">Graph</TabsTrigger>
                </TabsList>
                <TabsContent value="details" className="space-y-4 mt-3">
                  {/* Metrics grid */}
                  <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                    <div className="rounded-md bg-card border border-border px-3 py-2">
                      <div className="text-[10px] text-muted-foreground">Best loss</div>
                      <div className="font-mono font-bold text-base">{run.best_loss?.toFixed(6) ?? "—"}</div>
                    </div>
                    <div className="rounded-md bg-card border border-border px-3 py-2">
                      <div className="text-[10px] text-muted-foreground">Improvement</div>
                      <div className="font-mono font-bold text-base text-emerald-600 dark:text-emerald-400">
                        {firstLoss != null && run.best_loss != null
                          ? `▼ ${pct(((firstLoss - run.best_loss) / firstLoss) * 100)}`
                          : "—"}
                      </div>
                    </div>
                    <div className="rounded-md bg-card border border-border px-3 py-2">
                      <div className="text-[10px] text-muted-foreground">Wall time</div>
                      <div className="font-mono font-bold text-base flex items-center gap-1">
                        <Clock className="h-3 w-3 text-muted-foreground" />
                        {formatDuration(run.started_at)}
                      </div>
                    </div>
                    <div className="rounded-md bg-card border border-border px-3 py-2">
                      <div className="text-[10px] text-muted-foreground">Best at iter</div>
                      <div className="font-mono font-bold text-base">{bestIter ?? "—"} / {maxIter}</div>
                    </div>
                  </div>

                  {/* Budget bar */}
                  <BudgetBar used={run.iterations} total={maxIter} state={run.state} />

                  {/* Loss curve */}
                  {iterations.length >= 2 && (
                    <div className="space-y-1">
                      <div className="text-xs text-muted-foreground font-medium uppercase tracking-wide">Loss curve</div>
                      <div className="rounded-md border border-border bg-card p-2">
                        <LossSparkline points={iterations} height={64} color={stratColor} />
                        <div className="flex justify-between text-[10px] text-muted-foreground mt-1 px-1">
                          <span>iter 1 · {firstLoss?.toFixed(4) ?? "?"}</span>
                          {bestIter != null && <span className="text-emerald-600 dark:text-emerald-400">● best iter {bestIter}</span>}
                          <span>iter {iterations.length} · {iterations[iterations.length - 1]?.loss.toFixed(4)}</span>
                        </div>
                      </div>
                      <AnomalyFlag iterations={iterations} />
                    </div>
                  )}

                  {/* Objective + params */}
                  <div className="grid grid-cols-2 gap-x-6 gap-y-1.5 text-xs">
                    {objective && <><span className="text-muted-foreground">Objective</span><span className="font-mono">{objective}</span></>}
                    {budget?.max_iterations != null && <><span className="text-muted-foreground">Budget</span><span className="font-mono">{budget.max_iterations} iter · {budget.timeout_s}s max</span></>}
                    {convergence && <><span className="text-muted-foreground">Stopped by</span><span className="font-mono">{convergence.replace(/_/g, " ")}</span></>}
                    {submittedBy && <><span className="text-muted-foreground">Source</span><span>{submittedBy}</span></>}
                  </div>

                  {/* Best params */}
                  {(() => {
                    if (!iterations.length) return null;
                    const best = iterations.reduce((a, b) => a.loss < b.loss ? a : b);
                    const p = best.params ?? {};
                    if (!Object.keys(p).length) return null;
                    return (
                      <div className="space-y-1">
                        <div className="text-xs text-muted-foreground uppercase tracking-wide font-medium">Best params</div>
                        <div className="rounded-md bg-card border border-border px-3 py-2 font-mono text-xs">
                          {Object.entries(p).map(([k, v]) => (
                            <div key={k} className="flex gap-3">
                              <span className="text-muted-foreground w-32 shrink-0">{k}</span>
                              <span>{String(v)}</span>
                            </div>
                          ))}
                        </div>
                      </div>
                    );
                  })()}

                  {/* Experiment narrative + voice */}
                  <ComputeNarrativeDialog
                    runId={run.run_id}
                    enabled={isComplete}
                  />
                </TabsContent>
                <TabsContent value="graph" className="mt-2">
                  {activeTab === "graph" && <ComputeGraphView mode="l25" runId={run.run_id} />}
                </TabsContent>
              </Tabs>

              <div className="flex items-center justify-between pt-1">
                <OpenDirButton onOpen={() => openRunDir(run.run_id, csrf)} />
                <Button variant="ghost" size="sm" className="h-7 px-2 text-muted-foreground hover:text-destructive"
                  onClick={() => setReauthOpen(true)} disabled={deleting}>
                  {deleting ? <Loader2 className="h-3 w-3 animate-spin" /> : <Trash2 className="h-3 w-3" />}
                  <span className="ml-1 text-xs">Delete</span>
                </Button>
              </div>
            </div>
          )}
        </div>
      )}
      <ReauthDialog open={reauthOpen} onOpenChange={setReauthOpen}
        title={`Delete ${run.run_id}`} description="Deleting a run requires re-authentication."
        onConfirm={doDelete} />
    </div>
  );
}

// ══════════════════════════════════════════════════════════════════════════
// ── GROUPING LOGIC ────────────────────────────────────────────────────────
// ══════════════════════════════════════════════════════════════════════════

type GroupBy = "none" | "session" | "tool" | "source" | "day" | "strategy";

interface RunGroup {
  key: string;
  label: string;
  sublabel?: string;
  icon: React.ElementType;
  runs: ComputeRun[];
  bestLoss: number | null;
  color: string;
}

function dayLabel(ts: number | null): string {
  if (!ts) return "Unknown date";
  const d = new Date(ts * 1000);
  const now = new Date();
  const diff = Math.floor((now.getTime() - d.getTime()) / 86400000);
  if (diff === 0) return "Today";
  if (diff === 1) return "Yesterday";
  if (diff < 7) return `${diff} days ago`;
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function groupRuns(runs: ComputeRun[], by: GroupBy): RunGroup[] {
  if (by === "none") return [];

  const map = new Map<string, RunGroup>();

  const add = (key: string, label: string, run: ComputeRun, icon: React.ElementType, color: string, sublabel?: string) => {
    if (!map.has(key)) {
      map.set(key, { key, label, sublabel, icon, runs: [], bestLoss: null, color });
    }
    const g = map.get(key)!;
    g.runs.push(run);
    if (run.best_loss != null && (g.bestLoss === null || run.best_loss < g.bestLoss)) {
      g.bestLoss = run.best_loss;
    }
  };

  for (const run of runs) {
    if (by === "session") {
      const key = run.session_id ?? "no-session";
      const label = run.session_label ?? (run.session_id ? run.session_id.replace("discord:", "#") : "Untagged runs");
      const sub = run.session_id ? run.session_id.replace("discord:", "discord #") : undefined;
      add(key, label, run, FlaskConical, "#8b5cf6", sub);
    } else if (by === "tool") {
      const key = run.tool_name ?? "unknown";
      add(key, key, run, Target, "#3b82f6");
    } else if (by === "source") {
      const key = run.submitted_by ?? "unknown";
      const label = key === "voice" ? "Voice" : key === "chat" ? "Chat" : key === "console" ? "Console" : "Unknown";
      const col = key === "voice" ? "#f97316" : key === "chat" ? "#3b82f6" : "#6b7280";
      add(key, label, run, MessageSquare, col);
    } else if (by === "day") {
      const dayKey = run.started_at ? new Date(run.started_at * 1000).toDateString() : "unknown";
      add(dayKey, dayLabel(run.started_at), run, Clock, "#06b6d4");
    } else if (by === "strategy") {
      const key = run.strategy ?? "unknown";
      const col = STRATEGY_COLORS[key] ?? "#6b7280";
      add(key, key, run, Layers, col);
    }
  }

  return Array.from(map.values()).sort((a, b) => {
    const latestA = Math.max(...a.runs.map((r) => r.started_at ?? 0));
    const latestB = Math.max(...b.runs.map((r) => r.started_at ?? 0));
    return latestB - latestA;
  });
}

// ── GroupBy selector ──────────────────────────────────────────────────────

const GROUP_OPTIONS: { key: GroupBy; label: string; icon: React.ElementType }[] = [
  { key: "none",     label: "No grouping", icon: Layers },
  { key: "session",  label: "Session",     icon: FlaskConical },
  { key: "tool",     label: "Tool",        icon: Target },
  { key: "source",   label: "Source",      icon: MessageSquare },
  { key: "day",      label: "Day",         icon: Clock },
  { key: "strategy", label: "Strategy",    icon: Layers },
];

function GroupByBar({ value, onChange }: { value: GroupBy; onChange: (v: GroupBy) => void }) {
  return (
    <div className="flex items-center gap-2 flex-wrap">
      <span className="text-xs text-muted-foreground flex items-center gap-1 shrink-0">
        <Tag className="h-3 w-3" />Group by:
      </span>
      <div className="flex gap-1 flex-wrap">
        {GROUP_OPTIONS.map(({ key, label }) => (
          <button
            key={key}
            onClick={() => onChange(key)}
            className={cn(
              "px-2.5 py-1 rounded-md text-xs font-medium transition-colors",
              value === key
                ? "bg-accent/20 text-accent-foreground border border-accent/40"
                : "text-muted-foreground hover:text-foreground hover:bg-muted/40 border border-transparent"
            )}
          >
            {label}
          </button>
        ))}
      </div>
    </div>
  );
}

// ── Experiment group card ─────────────────────────────────────────────────

function ExperimentGroup({ group, csrf, onDeleted }: {
  group: RunGroup; csrf: string; onDeleted: () => void;
}) {
  const [open, setOpen] = React.useState(true);
  const Icon = group.icon;

  const running  = group.runs.filter((r) => r.state === "running").length;
  const complete = group.runs.filter((r) => isRunDone(r.state)).length;
  const failed   = group.runs.filter((r) => r.state === "failed").length;

  return (
    <div className="rounded-xl border border-border overflow-hidden">
      {/* Group header */}
      <button
        className="w-full px-4 py-3 text-left hover:bg-muted/20 transition-colors flex items-start justify-between gap-3"
        onClick={() => setOpen((v) => !v)}
      >
        <div className="flex items-start gap-3 min-w-0">
          <div className="rounded-lg p-2 shrink-0 mt-0.5"
            style={{ background: group.color + "18", color: group.color }}>
            <Icon className="h-4 w-4" />
          </div>
          <div className="min-w-0">
            <div className="font-semibold text-sm">{group.label}</div>
            {group.sublabel && (
              <div className="text-xs text-muted-foreground font-mono mt-0.5">{group.sublabel}</div>
            )}
            {/* State pills */}
            <div className="flex flex-wrap gap-1.5 mt-1.5">
              <Badge variant="outline" className="text-[10px]">
                {group.runs.length} run{group.runs.length !== 1 ? "s" : ""}
              </Badge>
              {running > 0 && (
                <Badge variant="default" className="text-[10px]">
                  <Zap className="h-2.5 w-2.5 mr-1" />{running} running
                </Badge>
              )}
              {complete > 0 && (
                <Badge variant="ok" className="text-[10px]">
                  ✓ {complete} done
                </Badge>
              )}
              {failed > 0 && (
                <Badge variant="danger" className="text-[10px]">
                  ✗ {failed} failed
                </Badge>
              )}
            </div>
          </div>
        </div>
        <div className="flex items-center gap-3 shrink-0">
          {group.bestLoss != null && (
            <div className="text-right">
              <div className="text-[10px] text-muted-foreground">best loss</div>
              <div className="font-mono font-bold text-sm text-emerald-600 dark:text-emerald-400">
                {group.bestLoss.toFixed(4)}
              </div>
            </div>
          )}
          {open
            ? <ChevronUp className="h-4 w-4 text-muted-foreground" />
            : <ChevronDown className="h-4 w-4 text-muted-foreground" />}
        </div>
      </button>

      {/* Runs inside group */}
      {open && (
        <div className="border-t border-border bg-muted/10 px-3 py-3 space-y-2">
          {group.runs
            .sort((a, b) => (b.started_at ?? 0) - (a.started_at ?? 0))
            .map((run) => (
              <RunJobCard key={run.run_id} run={run} csrf={csrf} onDeleted={onDeleted} />
            ))}
        </div>
      )}
    </div>
  );
}

function RunsSection({ title, runs, csrf, onDeleted, defaultExpanded }: {
  title: string; runs: ComputeRun[]; csrf: string; onDeleted: () => void; defaultExpanded: boolean;
}) {
  const [open, setOpen] = React.useState(defaultExpanded);
  if (!runs.length) return null;
  return (
    <div className="space-y-2">
      <button className="flex items-center justify-between w-full px-4 py-2.5 rounded-lg border border-border hover:bg-muted/30 transition-colors"
        onClick={() => setOpen(!open)}>
        <span className="font-semibold text-sm flex items-center gap-2">{title}
          <Badge variant="outline" className="text-xs">{runs.length}</Badge></span>
        {open ? <ChevronUp className="h-4 w-4 text-muted-foreground" /> : <ChevronDown className="h-4 w-4 text-muted-foreground" />}
      </button>
      {open && <div className="grid gap-2">{runs.map((r) => <RunJobCard key={r.run_id} run={r} csrf={csrf} onDeleted={onDeleted} />)}</div>}
    </div>
  );
}

// ── ADR-0099: Anthropic Batch Compute Job Card ────────────────────────────

function BatchJobCard({ job }: { job: OpenBatchJob }) {
  const submittedAgo = job.submitted_at
    ? Math.round((Date.now() / 1000 - job.submitted_at) / 60)
    : null;

  return (
    <Card className="overflow-hidden">
      <CardContent className="p-4 space-y-3">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0 flex-1 space-y-1">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="font-mono text-xs bg-muted px-1.5 py-0.5 rounded text-muted-foreground">
                {job.batch_id_prefix}…
              </span>
              {job.partial && (
                <Badge variant="outline" className="text-xs text-amber-600 border-amber-400">
                  partial
                </Badge>
              )}
              <Badge variant="outline" className="text-xs text-blue-600 border-blue-400">
                {job.state}
              </Badge>
            </div>
            <div className="text-xs text-muted-foreground font-mono truncate">
              {job.job_id}
            </div>
          </div>
          <div className="text-right text-xs text-muted-foreground shrink-0">
            {submittedAgo != null && (
              <div>{submittedAgo < 60 ? `${submittedAgo}m ago` : `${Math.round(submittedAgo / 60)}h ago`}</div>
            )}
          </div>
        </div>

        <div className="grid grid-cols-3 gap-3 text-xs">
          <div>
            <div className="text-muted-foreground">Candidates</div>
            <div className="font-mono font-medium">{job.candidate_count ?? "—"}</div>
          </div>
          <div>
            <div className="text-muted-foreground">Failed</div>
            <div className={cn("font-mono font-medium", (job.failed_candidate_count ?? 0) > 0 && "text-amber-600")}>
              {job.failed_candidate_count ?? 0}
            </div>
          </div>
          <div>
            <div className="text-muted-foreground">Session</div>
            <div className="font-mono truncate">{job.session_key}</div>
          </div>
        </div>

        <div className="text-xs text-muted-foreground italic">
          Batch jobs complete within 1–24 h. Results are available via CLI: <span className="font-mono">corvin-compute result {job.job_id}</span>
        </div>
      </CardContent>
    </Card>
  );
}

function BatchJobsSection({ jobs }: { jobs: OpenBatchJob[] }) {
  const [open, setOpen] = React.useState(true);
  if (!jobs.length) return null;
  return (
    <div className="space-y-2">
      <button className="flex items-center justify-between w-full px-4 py-2.5 rounded-lg border border-border hover:bg-muted/30 transition-colors"
        onClick={() => setOpen(!open)}>
        <span className="font-semibold text-sm flex items-center gap-2">
          Batch Jobs (Anthropic API)
          <Badge variant="outline" className="text-xs">{jobs.length}</Badge>
        </span>
        {open ? <ChevronUp className="h-4 w-4 text-muted-foreground" /> : <ChevronDown className="h-4 w-4 text-muted-foreground" />}
      </button>
      {open && <div className="grid gap-2">{jobs.map((j) => <BatchJobCard key={j.job_id} job={j} />)}</div>}
    </div>
  );
}

// ══════════════════════════════════════════════════════════════════════════
// ── ANALYTICS TAB ─────────────────────────────────────────────────────────
// ══════════════════════════════════════════════════════════════════════════

interface RunDetail {
  run_id: string; tool_name: string | null; strategy: string | null;
  state: string | null; best_loss: number | null; iterations: number;
  started_at: number | null; best_iter: number | null;
  convergence: string | null;
  iterData: { iter: number; loss: number }[];
  firstLoss: number | null; maxIter: number;
}

function MultiRunChart({ runs }: { runs: RunDetail[] }) {
  const W = 600; const H = 120; const PX = 40; const PY = 10;
  const w = W - PX * 2; const h = H - PY * 2;
  const allLosses = runs.flatMap((r) => r.iterData.map((p) => p.loss));
  if (!allLosses.length) return null;
  const mn = Math.min(...allLosses); const mx = Math.max(...allLosses);
  const rng = mx - mn || 0.01;
  const tx = (i: number, len: number) => PX + (i / (len - 1)) * w;
  const ty = (v: number) => PY + (1 - (v - mn) / rng) * h;

  return (
    <div className="space-y-2">
      <div className="text-xs font-medium text-muted-foreground uppercase tracking-wide">Multi-run loss comparison</div>
      <div className="rounded-md border border-border bg-card p-3 overflow-x-auto">
        <svg width="100%" height={H} viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="xMinYMin meet">
          {/* Grid lines */}
          {[0, 0.25, 0.5, 0.75, 1].map((frac) => {
            const y = PY + frac * h;
            const v = mx - frac * rng;
            return (
              <g key={frac}>
                <line x1={PX} y1={y} x2={W - PX} y2={y} stroke="currentColor" strokeOpacity="0.08" strokeWidth="1" />
                <text x={PX - 4} y={y + 3} fontSize="8" textAnchor="end" fill="currentColor" fillOpacity="0.4">{v.toFixed(2)}</text>
              </g>
            );
          })}
          {/* Lines */}
          {runs.filter((r) => r.iterData.length >= 2).map((r, i) => {
            const col = STRATEGY_COLORS[r.strategy ?? ""] ?? MANAGER_COLORS[i % MANAGER_COLORS.length];
            const pts = r.iterData.map((p, j) => `${tx(j, r.iterData.length)},${ty(p.loss)}`).join(" ");
            return (
              <g key={r.run_id}>
                <polyline points={pts} fill="none" stroke={col} strokeWidth="1.5"
                  strokeLinejoin="round" strokeLinecap="round" strokeOpacity="0.85" />
                <circle cx={tx(r.iterData.length - 1, r.iterData.length)} cy={ty(r.iterData[r.iterData.length - 1].loss)} r="2.5" fill={col} />
              </g>
            );
          })}
        </svg>
        {/* Legend */}
        <div className="flex flex-wrap gap-3 mt-2">
          {runs.filter((r) => r.iterData.length >= 2).map((r, i) => {
            const col = STRATEGY_COLORS[r.strategy ?? ""] ?? MANAGER_COLORS[i % MANAGER_COLORS.length];
            return (
              <div key={r.run_id} className="flex items-center gap-1.5 text-[11px]">
                <div className="w-4 h-0.5 rounded" style={{ background: col }} />
                <span className="text-muted-foreground font-mono truncate max-w-32">{r.run_id}</span>
                {r.best_loss != null && <span className="font-mono text-[10px]">({r.best_loss.toFixed(3)})</span>}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function StrategyTable({ runs }: { runs: RunDetail[] }) {
  const completed = runs.filter((r) => isRunDone(r.state) && r.iterData.length > 0);
  const byStrategy: Record<string, RunDetail[]> = {};
  for (const r of completed) {
    const s = r.strategy ?? "unknown";
    if (!byStrategy[s]) byStrategy[s] = [];
    byStrategy[s].push(r);
  }

  return (
    <div className="space-y-2">
      <div className="text-xs font-medium text-muted-foreground uppercase tracking-wide">Strategy effectiveness</div>
      <div className="rounded-md border border-border bg-card overflow-hidden">
        <table className="w-full text-xs">
          <thead className="bg-muted/40 border-b border-border">
            <tr>
              {["Strategy", "Runs", "Median Loss", "Best Loss", "Avg Iter", "Early Stop Rate"].map((h) => (
                <th key={h} className="px-3 py-2 text-left font-medium text-muted-foreground">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {Object.entries(byStrategy).sort((a, b) => {
              const medA = a[1].map((r) => r.best_loss ?? 1).sort((x, y) => x - y)[Math.floor(a[1].length / 2)];
              const medB = b[1].map((r) => r.best_loss ?? 1).sort((x, y) => x - y)[Math.floor(b[1].length / 2)];
              return medA - medB;
            }).map(([strat, rr], idx) => {
              const losses = rr.map((r) => r.best_loss ?? 1).sort((a, b) => a - b);
              const medLoss = losses[Math.floor(losses.length / 2)];
              const bestLoss = Math.min(...losses);
              const avgIter = rr.reduce((a, r) => a + r.iterations, 0) / rr.length;
              const earlyStop = rr.filter((r) => r.best_iter != null && r.best_iter / r.maxIter < 0.6).length;
              const col = STRATEGY_COLORS[strat] ?? "#6b7280";
              return (
                <tr key={strat} className="border-b border-border/50 last:border-0 hover:bg-muted/20">
                  <td className="px-3 py-2.5">
                    <div className="flex items-center gap-1.5">
                      <div className="w-2.5 h-2.5 rounded-full" style={{ background: col }} />
                      <span className="font-mono font-medium">{strat}</span>
                      {idx === 0 && <Badge variant="ok" className="text-[9px] px-1">best</Badge>}
                    </div>
                  </td>
                  <td className="px-3 py-2.5 font-mono">{rr.length}</td>
                  <td className="px-3 py-2.5 font-mono font-medium">{medLoss.toFixed(4)}</td>
                  <td className="px-3 py-2.5 font-mono text-emerald-600 dark:text-emerald-400">{bestLoss.toFixed(4)}</td>
                  <td className="px-3 py-2.5 font-mono">{Math.round(avgIter)}</td>
                  <td className="px-3 py-2.5">
                    <div className="flex items-center gap-2">
                      <div className="flex-1 h-1.5 bg-muted rounded-full overflow-hidden max-w-16">
                        <div className="h-full rounded-full bg-emerald-500"
                          style={{ width: `${(earlyStop / rr.length) * 100}%` }} />
                      </div>
                      <span className="font-mono">{Math.round((earlyStop / rr.length) * 100)}%</span>
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function ToolRankBars({ runs }: { runs: RunDetail[] }) {
  const completed = runs.filter((r) => isRunDone(r.state) && r.best_loss != null);
  const byTool: Record<string, RunDetail[]> = {};
  for (const r of completed) {
    const t = r.tool_name ?? "unknown";
    if (!byTool[t]) byTool[t] = [];
    byTool[t].push(r);
  }
  const ranked = Object.entries(byTool).map(([tool, rr]) => ({
    tool,
    count: rr.length,
    bestLoss: Math.min(...rr.map((r) => r.best_loss!)),
    avgLoss: rr.reduce((a, r) => a + r.best_loss!, 0) / rr.length,
  })).sort((a, b) => a.bestLoss - b.bestLoss);

  const maxLoss = Math.max(...ranked.map((r) => r.avgLoss));

  return (
    <div className="space-y-2">
      <div className="text-xs font-medium text-muted-foreground uppercase tracking-wide">Tool performance ranking</div>
      <div className="rounded-md border border-border bg-card p-3 space-y-2.5">
        {ranked.map((r, i) => (
          <div key={r.tool} className="space-y-1">
            <div className="flex items-center justify-between text-xs">
              <div className="flex items-center gap-2">
                <span className="font-mono text-muted-foreground w-5">#{i + 1}</span>
                <span className="font-mono font-medium truncate max-w-48">{r.tool}</span>
                <Badge variant="secondary" className="text-[10px]">{r.count} run{r.count > 1 ? "s" : ""}</Badge>
              </div>
              <div className="flex items-center gap-3 text-[11px]">
                <span className="text-muted-foreground">best: <span className="font-mono text-emerald-600 dark:text-emerald-400">{r.bestLoss.toFixed(4)}</span></span>
                <span className="text-muted-foreground">avg: <span className="font-mono">{r.avgLoss.toFixed(4)}</span></span>
              </div>
            </div>
            <div className="h-2 bg-muted rounded-full overflow-hidden">
              <div className="h-full rounded-full"
                style={{ width: `${(1 - r.avgLoss / maxLoss) * 100}%`, background: MANAGER_COLORS[i % MANAGER_COLORS.length] }} />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function ConvergenceHeatmap({ runs }: { runs: RunDetail[] }) {
  const completed = runs.filter((r) => isRunDone(r.state) && r.best_iter != null && r.maxIter > 0);
  const strategies = Array.from(new Set(completed.map((r) => r.strategy ?? "unknown")));
  const buckets = ["0–20%", "20–40%", "40–60%", "60–80%", "80–100%"];

  const grid: Record<string, number[]> = {};
  for (const strat of strategies) {
    grid[strat] = [0, 0, 0, 0, 0];
    for (const r of completed.filter((run) => run.strategy === strat)) {
      const frac = (r.best_iter! / r.maxIter);
      const bucket = Math.min(4, Math.floor(frac * 5));
      grid[strat][bucket]++;
    }
  }
  const maxCell = Math.max(...Object.values(grid).flat(), 1);

  return (
    <div className="space-y-2">
      <div className="text-xs font-medium text-muted-foreground uppercase tracking-wide">Convergence heatmap — when does each strategy find its best?</div>
      <div className="rounded-md border border-border bg-card p-3 overflow-x-auto">
        <div className="grid gap-1" style={{ gridTemplateColumns: `80px repeat(${buckets.length}, 1fr)` }}>
          <div />
          {buckets.map((b) => <div key={b} className="text-[10px] text-muted-foreground text-center pb-1">{b}</div>)}
          {strategies.map((strat) => {
            const col = STRATEGY_COLORS[strat] ?? "#6b7280";
            return (
              <React.Fragment key={strat}>
                <div className="text-xs font-mono flex items-center pr-2" style={{ color: col }}>{strat}</div>
                {(grid[strat] ?? [0, 0, 0, 0, 0]).map((count, bi) => {
                  const intensity = count / maxCell;
                  return (
                    <div key={bi} className="rounded-md h-8 flex items-center justify-center text-[11px] font-bold transition-all"
                      style={{
                        background: count > 0 ? col + Math.round(intensity * 200).toString(16).padStart(2, "0") : undefined,
                        color: intensity > 0.5 ? "#fff" : count > 0 ? col : undefined,
                        border: `1px solid ${count > 0 ? col + "40" : "transparent"}`,
                        opacity: count === 0 ? 0.3 : 1,
                      }}>
                      {count > 0 ? count : "·"}
                    </div>
                  );
                })}
              </React.Fragment>
            );
          })}
        </div>
        <div className="mt-2 text-[10px] text-muted-foreground">Each cell = runs that converged in that % of their iteration budget. Earlier = more efficient.</div>
      </div>
    </div>
  );
}

function AnalyticsTab({ allRuns }: { allRuns: RunDetail[] }) {
  if (allRuns.length === 0) {
    return (
      <Card><CardContent className="py-12 text-center">
        <BarChart3 className="mx-auto mb-2 h-8 w-8 text-muted-foreground/30" />
        <p className="text-sm">Run some compute jobs first to see analytics.</p>
      </CardContent></Card>
    );
  }
  return (
    <div className="space-y-6">
      <MultiRunChart runs={allRuns} />
      <StrategyTable runs={allRuns} />
      <ToolRankBars runs={allRuns} />
      <ConvergenceHeatmap runs={allRuns} />
    </div>
  );
}

// ══════════════════════════════════════════════════════════════════════════
// ── PIPELINE TAB ──────────────────────────────────────────────────────────
// ══════════════════════════════════════════════════════════════════════════

function StageBox({ stage, isActive, isBottleneck }: {
  stage: PipelineStageDetail; isActive: boolean; isBottleneck: boolean;
}) {
  const isDone = stage.state === "complete";
  const isPending = !stage.state || stage.state === "pending";
  const maxIter = 30;
  const progress = Math.min(100, (stage.iter_count / maxIter) * 100);
  const realStats = (stage.real_stats ?? {}) as Record<string, unknown>;

  return (
    <div className={cn("flex-1 rounded-lg border p-3 text-xs relative",
      isDone ? "border-emerald-300 bg-emerald-50/60 dark:bg-emerald-950/20 dark:border-emerald-800" :
        isActive ? "border-amber-300 bg-amber-50/60 dark:bg-amber-950/20 dark:border-amber-800" :
          "border-border bg-card")}>
      {isBottleneck && <div className="absolute -top-2 left-2 text-[10px] px-1.5 py-0.5 rounded bg-orange-500 text-white font-medium">bottleneck</div>}
      <div className="font-mono text-[10px] text-muted-foreground">{stage.stage_id}</div>
      <div className="font-semibold text-sm mt-0.5 truncate">{stage.tool_name ?? stage.stage_id}</div>
      {stage.best_loss != null && (
        <div className={cn("font-mono mt-1 font-medium", isDone ? "text-emerald-700 dark:text-emerald-400" : "text-amber-700 dark:text-amber-400")}>
          loss: {stage.best_loss.toFixed(4)}
        </div>
      )}
      {/* Real stats */}
      {Boolean(realStats.countries_analyzed) && (
        <div className="text-[10px] text-muted-foreground mt-1">{String(realStats.countries_analyzed)} countries · {((realStats.rows_processed ?? realStats.tracks_analyzed ?? 0) as number).toLocaleString()} rows</div>
      )}
      {isPending && !stage.best_loss && <div className="text-muted-foreground mt-1 italic text-[11px]">waiting for prev stage…</div>}
      <div className="mt-2 h-1.5 bg-muted rounded-full overflow-hidden">
        <div className={cn("h-full rounded-full", isDone ? "bg-emerald-500" : isActive ? "bg-amber-500 animate-pulse" : "bg-muted-foreground/20")}
          style={{ width: `${isDone ? 100 : progress}%` }} />
      </div>
      <div className={cn("mt-1 text-[10px]",
        isDone ? "text-emerald-600 dark:text-emerald-400" :
          isActive ? "text-amber-600 dark:text-amber-400" : "text-muted-foreground")}>
        {isDone ? `✓ ${stage.iter_count} iter · done` : isActive ? `⚡ ${stage.iter_count}/${maxIter}` : "—"}
      </div>
    </div>
  );
}

function PipelineCard({ pipeline, csrf }: { pipeline: PipelineSummary; csrf: string }) {
  const [showExportModal, setShowExportModal] = React.useState(false);
  const [expanded, setExpanded] = React.useState(true);
  const detailQ = useQuery({
    queryKey: ["pipeline-detail", pipeline.pipeline_id],
    queryFn: ({ signal }) => getPipelineDetail(pipeline.pipeline_id, signal),
    enabled: expanded, staleTime: 15_000, refetchInterval: expanded ? 10_000 : false,
  });
  const isRunning = pipeline.state === "running";
  const stages = detailQ.data?.stages ?? [];
  const bottleneckId = stages.length > 0
    ? stages.reduce((prev, cur) => ((cur.iter_count ?? 0) > (prev.iter_count ?? 0) && cur.state !== "complete") ? cur : prev, stages[0])?.stage_id
    : null;
  const totalRows = stages.reduce((s, st) => s + ((st.real_stats?.rows_processed as number | undefined) ?? 0), 0);

  return (
    <div className="rounded-xl border border-border bg-card overflow-hidden">
      <button className="w-full px-4 py-3 text-left hover:bg-muted/10 transition-colors"
        onClick={() => setExpanded((v) => !v)}>
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="font-mono text-xs text-muted-foreground">{pipeline.pipeline_id}</div>
            <div className="font-bold text-base">{pipeline.name}</div>
          </div>
          <div className="flex items-center gap-2 shrink-0 text-xs text-muted-foreground">
            {formatTimeAgo(pipeline.started_at)}
            <span className="font-mono">Stage {pipeline.completed_stages.length + (isRunning ? 1 : 0)}/{pipeline.stage_count}</span>
            {expanded ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2 mt-1.5">
          <Badge variant={isRunning ? "default" : isPipelineDone(pipeline.state) ? "ok" : "outline"}>{pipeline.state ?? "pending"}</Badge>
          {pipeline.submitted_by && <span className="text-xs text-muted-foreground">via {pipeline.submitted_by}</span>}
          {totalRows > 0 && <span className="text-xs text-muted-foreground">{(totalRows / 1_000_000).toFixed(1)}M rows processed</span>}
          {pipeline.steering_gate && <Badge variant="secondary" className="text-[10px]">steering gate</Badge>}
        </div>
      </button>

      {expanded && (
        <div className="border-t border-border">
          {detailQ.isLoading && <div className="px-4 py-6 flex gap-2 text-xs text-muted-foreground"><Loader2 className="h-3 w-3 animate-spin" />Loading stages…</div>}
          {detailQ.data && (
            <>
              {/* Stage flow */}
              <div className="px-4 pt-5 pb-3 bg-muted/20 flex items-stretch gap-0">
                {detailQ.data.stages.map((stage, i) => (
                  <React.Fragment key={stage.stage_id}>
                    <StageBox stage={stage}
                      isActive={stage.stage_id === pipeline.current_stage_id}
                      isBottleneck={stage.stage_id === bottleneckId && isRunning} />
                    {i < detailQ.data.stages.length - 1 && (
                      <div className="flex items-center px-2 text-muted-foreground text-base shrink-0 mt-4">→</div>
                    )}
                  </React.Fragment>
                ))}
              </div>

              {/* Loss curves per stage */}
              <div className="px-4 py-3 grid gap-2"
                style={{ gridTemplateColumns: `repeat(${detailQ.data.stages.length}, 1fr)` }}>
                {detailQ.data.stages.map((stage) => (
                  stage.iterations.length >= 2 ? (
                    <div key={stage.stage_id} className="rounded-md border border-border bg-card p-2">
                      <div className="text-[10px] text-muted-foreground mb-1">{stage.stage_id} · loss</div>
                      <LossSparkline points={stage.iterations} height={36}
                        color={stage.state === "complete" ? "#22c55e" : "#f59e0b"} />
                    </div>
                  ) : (
                    <div key={stage.stage_id}
                      className="rounded-md border border-dashed border-border p-2 flex items-center justify-center text-[10px] text-muted-foreground">
                      {stage.state === "pending" ? "not started" : "no data"}
                    </div>
                  )
                ))}
              </div>

              {/* Steering gate */}
              {pipeline.steering_gate && isRunning && (
                <div className="flex items-center gap-2 px-4 py-2.5 bg-violet-50 dark:bg-violet-950/20 border-t border-violet-200 dark:border-violet-800 text-xs text-violet-700 dark:text-violet-400">
                  <span>🧠</span><strong>Steering gate active</strong> — LLM reviews results before next stage.
                </div>
              )}

              {/* M4: Artifact Viewer per completed stage */}
              {detailQ.data.stages.filter((s) => s.state === "complete").map((stage) => (
                <ArtifactViewerPanel
                  key={stage.stage_id}
                  pipelineId={pipeline.pipeline_id}
                  stageId={stage.stage_id}
                  stageName={stage.tool_name ?? stage.stage_id}
                />
              ))}

              {/* Bottom action bar */}
              <div className="flex items-center justify-between px-4 py-2 border-t border-border gap-2">
                <OpenDirButton onOpen={() => openPipelineDir(pipeline.pipeline_id, csrf)} />
                <div className="flex items-center gap-1">
                  <PromoteChampionButton pipelineId={pipeline.pipeline_id} pipelineName={pipeline.name} csrf={csrf} />
                  {isPipelineDone(pipeline.state) && (
                    <Button variant="ghost" size="sm" className="h-7 px-2 gap-1.5 text-xs"
                      onClick={() => setShowExportModal(true)}>
                      <Package className="h-3.5 w-3.5" />
                      Export awpkg
                    </Button>
                  )}
                </div>
              </div>
            </>
          )}
        </div>
      )}

      {showExportModal && (
        <AwpkgExportModal
          pipelineId={pipeline.pipeline_id}
          pipelineName={pipeline.name}
          csrf={csrf}
          onClose={() => setShowExportModal(false)}
        />
      )}
    </div>
  );
}

// ══════════════════════════════════════════════════════════════════════════
// ── HAC TAB ───────────────────────────────────────────────────────────────
// ══════════════════════════════════════════════════════════════════════════

function HacRoundChart({ lossHistory, currentRound }: { lossHistory: number[]; currentRound: number }) {
  if (lossHistory.length < 2) return null;
  const points = lossHistory.map((l, i) => ({ iter: i + 1, loss: l }));
  const W = 400; const H = 60; const PX = 28; const PY = 8;
  const w = W - PX * 2; const h = H - PY * 2;
  const mn = Math.min(...lossHistory); const mx = Math.max(...lossHistory);
  const rng = mx - mn || 0.01;
  const tx = (i: number) => PX + (i / (points.length - 1)) * w;
  const ty = (v: number) => PY + (1 - (v - mn) / rng) * h;
  const poly = points.map((p, i) => `${tx(i)},${ty(p.loss)}`).join(" ");

  return (
    <div className="rounded-md border border-border bg-card p-2">
      <div className="text-[10px] text-muted-foreground mb-1">Root loss per round</div>
      <svg width="100%" height={H} viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="xMinYMin meet">
        <defs>
          <linearGradient id="hac-fill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#8b5cf6" stopOpacity="0.2" />
            <stop offset="100%" stopColor="#8b5cf6" stopOpacity="0.01" />
          </linearGradient>
        </defs>
        <polygon points={`${tx(0)},${H} ${poly} ${tx(points.length - 1)},${H}`} fill="url(#hac-fill)" />
        <polyline points={poly} fill="none" stroke="#8b5cf6" strokeWidth="2"
          strokeLinejoin="round" strokeLinecap="round" />
        {points.map((p, i) => (
          <g key={i}>
            <circle cx={tx(i)} cy={ty(p.loss)} r="3.5" fill={i === currentRound - 1 ? "#22c55e" : "#8b5cf6"}
              stroke="white" strokeWidth="1" />
            <text x={tx(i)} y={H - 1} fontSize="7" textAnchor="middle" fill="currentColor" fillOpacity="0.5">R{i + 1}</text>
          </g>
        ))}
        <text x={PX - 2} y={PY + 3} fontSize="7" textAnchor="end" fill="currentColor" fillOpacity="0.4">{mx.toFixed(2)}</text>
        <text x={PX - 2} y={H - PY + 3} fontSize="7" textAnchor="end" fill="currentColor" fillOpacity="0.4">{mn.toFixed(2)}</text>
      </svg>
    </div>
  );
}

function SubManagerCard({ manager, color, rank }: { manager: HacManagerDetail; color: string; rank: number }) {
  const stages = manager.stages ?? [];
  const activeStage = stages.find((s) => s.state === "running");
  const doneCount = stages.filter((s) => isRunDone(s.state)).length;
  const currentLoss = manager.summary?.current_loss ?? manager.summary?.best_loss ?? null;
  const effScore = currentLoss != null && manager.budget_fraction > 0
    ? ((1 - currentLoss) / manager.budget_fraction).toFixed(2) : null;

  return (
    <div className="border border-border rounded-xl overflow-hidden">
      <div className="px-3 py-2.5 border-b border-border" style={{ background: color + "12" }}>
        <div className="flex items-start justify-between">
          <div>
            <div className="font-mono text-[10px] text-muted-foreground">{manager.manager_id}</div>
            <div className="font-semibold text-sm leading-tight line-clamp-2">{manager.label ?? manager.manager_id}</div>
          </div>
          <div className="shrink-0 w-6 h-6 rounded-full flex items-center justify-center text-[10px] font-bold text-white"
            style={{ background: color }}>#{rank}</div>
        </div>
      </div>
      <div className="px-3 py-2.5 space-y-2.5">
        {/* Budget bar */}
        <div>
          <div className="flex justify-between text-[10px] text-muted-foreground mb-1">
            <span>Budget share</span>
            <span className="font-mono">{Math.round((manager.budget_fraction ?? 0.33) * 100)}%</span>
          </div>
          <div className="h-1.5 bg-muted rounded-full overflow-hidden">
            <div className="h-full rounded-full" style={{ width: `${(manager.budget_fraction ?? 0.33) * 100}%`, background: color }} />
          </div>
        </div>
        {/* Loss + efficiency */}
        <div className="grid grid-cols-2 gap-2">
          <div>
            <div className="text-[10px] text-muted-foreground">Current loss</div>
            <div className="font-mono font-bold">{currentLoss?.toFixed(4) ?? "—"}</div>
          </div>
          {effScore && (
            <div>
              <div className="text-[10px] text-muted-foreground">Efficiency</div>
              <div className="font-mono font-bold" style={{ color }}>{effScore}</div>
            </div>
          )}
        </div>
        <div className="text-[10px] text-muted-foreground">strategy: {manager.strategy}</div>
        {/* Stage list */}
        <div className="space-y-0.5">
          {stages.map((s) => {
            const isDone = isRunDone(s.state);
            const isAct = s.state === "running";
            return (
              <div key={s.stage_id} className={cn(
                "flex items-center gap-1.5 text-[11px] px-1.5 py-0.5 rounded",
                isDone ? "text-emerald-700 dark:text-emerald-400 bg-emerald-50 dark:bg-emerald-950/20" :
                  isAct ? "text-amber-700 dark:text-amber-400 bg-amber-50 dark:bg-amber-950/20" :
                    "text-muted-foreground"
              )}>
                <div className={cn("w-1.5 h-1.5 rounded-full shrink-0",
                  isDone ? "bg-emerald-500" : isAct ? "bg-amber-500 animate-pulse" : "bg-muted-foreground/30")} />
                <span className="truncate flex-1">{s.tool_name ?? s.stage_id}</span>
                {isAct && <span className="font-mono shrink-0">{s.iter_count} iter</span>}
                {isDone && s.best_loss != null && <span className="font-mono shrink-0 text-[10px]">{s.best_loss.toFixed(3)}</span>}
              </div>
            );
          })}
        </div>
        {/* Active stage sparkline */}
        {activeStage && activeStage.iterations.length >= 2 && (
          <div className="rounded border border-border bg-card p-1.5">
            <LossSparkline points={activeStage.iterations} height={28} color={color} />
          </div>
        )}
        <div className="text-[10px] text-muted-foreground">{doneCount}/{stages.length} stages done</div>
      </div>
    </div>
  );
}

function HacCard({ run, csrf }: { run: HacSummary; csrf: string }) {
  const [expanded, setExpanded] = React.useState(true);
  const detailQ = useQuery({
    queryKey: ["hac-detail", run.hac_id],
    queryFn: ({ signal }) => getHacDetail(run.hac_id, signal),
    enabled: expanded, staleTime: 15_000, refetchInterval: expanded ? 10_000 : false,
  });
  const detail = detailQ.data;
  const attributions = detail?.summary?.attributions ?? run.attributions ?? {};
  const lossHistory: number[] = detail?.loss_history ?? [];

  // Rank managers by efficiency (loss reduction per budget)
  const rankedManagers = [...(detail?.managers ?? [])].sort((a, b) => {
    const effA = a.summary?.current_loss != null ? (1 - a.summary.current_loss) / (a.budget_fraction || 0.33) : 0;
    const effB = b.summary?.current_loss != null ? (1 - b.summary.current_loss) / (b.budget_fraction || 0.33) : 0;
    return effB - effA;
  });
  const rankMap = new Map(rankedManagers.map((m, i) => [m.manager_id, i + 1]));

  return (
    <div className="rounded-xl border border-border bg-card overflow-hidden">
      <button className="w-full px-4 py-3 text-left hover:bg-muted/10 transition-colors"
        onClick={() => setExpanded((v) => !v)}>
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="font-mono text-xs text-muted-foreground">{run.hac_id}</div>
            <div className="font-bold text-base">{run.name}</div>
          </div>
          <div className="flex items-center gap-2 shrink-0 text-xs text-muted-foreground">
            {formatTimeAgo(run.started_at)}
            <span className="font-mono">Round {run.round}/{run.max_rounds}</span>
            {expanded ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2 mt-1.5">
          <Badge variant={run.state === "running" ? "default" : run.state === "converged" ? "ok" : "outline"}>{run.state ?? "pending"}</Badge>
          <Badge variant="secondary" className="font-mono text-xs">{run.aggregation_mode ?? "weighted_sum"}</Badge>
          {run.fluid_reallocation && <Badge variant="secondary" className="text-[10px]">fluid realloc</Badge>}
          {run.submitted_by && <span className="text-xs text-muted-foreground">via {run.submitted_by}</span>}
        </div>
        {run.root_loss != null && (
          <div className="mt-2 flex items-center gap-4">
            <div>
              <div className="text-[10px] text-muted-foreground">Root loss (aggregated)</div>
              <div className="font-mono font-bold text-xl">{run.root_loss.toFixed(4)}</div>
            </div>
            {lossHistory.length >= 2 && (
              <div className="flex-1 max-w-48">
                <LossSparkline points={lossHistory.map((l, i) => ({ iter: i, loss: l }))} height={30} color="#8b5cf6" />
              </div>
            )}
          </div>
        )}
      </button>

      {expanded && (
        <div className="border-t border-border">
          {detailQ.isLoading && <div className="px-4 py-6 flex gap-2 text-xs text-muted-foreground"><Loader2 className="h-3 w-3 animate-spin" />Loading…</div>}
          {detail && (
            <>
              {/* Round history + stats */}
              {lossHistory.length >= 2 && (
                <div className="px-4 pt-4 pb-2">
                  <HacRoundChart lossHistory={lossHistory} currentRound={run.round} />
                </div>
              )}

              {/* Sub-managers grid — ranked by efficiency */}
              <div className="px-4 py-3 grid gap-3"
                style={{ gridTemplateColumns: `repeat(${Math.min(detail.managers.length, 3)}, 1fr)` }}>
                {(detail.managers ?? []).map((mgr, i) => (
                  <SubManagerCard key={mgr.manager_id} manager={mgr}
                    color={MANAGER_COLORS[i % MANAGER_COLORS.length]}
                    rank={rankMap.get(mgr.manager_id) ?? i + 1} />
                ))}
              </div>

              {/* Attribution */}
              {Object.keys(attributions).length > 0 && (
                <div className="px-4 pb-4 space-y-1.5">
                  <div className="text-[10px] text-muted-foreground uppercase tracking-wide font-medium">
                    Loss attribution — Round {run.round}
                  </div>
                  {Object.entries(attributions)
                    .sort(([, a], [, b]) => b - a)
                    .map(([mid, val], i) => {
                      const pctVal = Math.round(val * 100);
                      return (
                        <div key={mid} className="flex items-center gap-2">
                          <span className="text-xs font-mono w-44 truncate">{mid}</span>
                          <div className="flex-1 h-2 bg-muted rounded-full overflow-hidden">
                            <div className="h-full rounded-full" style={{ width: `${pctVal}%`, background: MANAGER_COLORS[i % MANAGER_COLORS.length] }} />
                          </div>
                          <span className="text-xs text-muted-foreground w-8 text-right font-mono">{pctVal}%</span>
                        </div>
                      );
                    })}
                </div>
              )}

              {/* Backprop gate */}
              {detail.manifest?.backprop_gate && run.state === "running" && (
                <div className="flex items-center gap-2 px-4 py-2.5 bg-violet-50 dark:bg-violet-950/20 border-t border-violet-200 dark:border-violet-800 text-xs text-violet-700 dark:text-violet-400">
                  <div className="w-1.5 h-1.5 rounded-full bg-violet-500 animate-pulse" />
                  <strong>Backprop gate</strong> — Round {run.round} complete. LLM reviewing for budget reallocation in Round {run.round + 1}.
                </div>
              )}

              {/* Open directory */}
              <div className="flex justify-start px-4 py-2 border-t border-border">
                <OpenDirButton onOpen={() => openHacDir(run.hac_id, csrf)} />
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}

// ══════════════════════════════════════════════════════════════════════════
// ── MAIN PAGE ─────────────────────────────────────────────────────────────
// ══════════════════════════════════════════════════════════════════════════

// ══════════════════════════════════════════════════════════════════════════
// ── M1: CORPUS BANNER ─────────────────────────────────────────────────────
// ══════════════════════════════════════════════════════════════════════════

function CorpusBanner({ ctx }: { ctx: CorpusContext }) {
  if (!ctx.has_corpus) return null;
  const rs = ctx.real_stats;
  const stats: { val: string; lbl: string }[] = [
    { val: rs.total_rows ? `${(rs.total_rows / 1_000_000).toFixed(1)}M` : "—", lbl: "Chart rows" },
    { val: rs.unique_countries ? String(rs.unique_countries) : "—", lbl: "Countries" },
    { val: rs.iso_weeks ? String(rs.iso_weeks) : "—", lbl: "ISO weeks" },
    { val: [rs.date_range_start?.slice(0, 4), rs.date_range_end?.slice(0, 4)].filter(Boolean).join("–") || "—", lbl: "Date range" },
    { val: rs.file_size_mb ? `${rs.file_size_mb} MB` : "—", lbl: "Stage 1 size" },
    { val: rs.compression_factor ? `${rs.compression_factor}x` : "—", lbl: "Compression" },
  ].filter((s) => s.val !== "—");

  return (
    <div className="rounded-lg overflow-hidden" style={{ background: "linear-gradient(135deg,#1a1a18,#2d2a26)", color: "#f7f5f2" }}>
      <div className="flex flex-wrap items-center gap-0" style={{ borderColor: "#3d3a36" }}>
        {stats.map((s, i) => (
          <div key={i} className="px-4 py-3 text-center" style={{ borderRight: "1px solid #3d3a36" }}>
            <div className="font-mono font-bold text-lg" style={{ color: "#fcd34d" }}>{s.val}</div>
            <div className="text-[10px] uppercase tracking-wide mt-0.5" style={{ color: "#a8a29e" }}>{s.lbl}</div>
          </div>
        ))}
        <div className="flex-1 px-4 py-3 text-xs" style={{ color: "#a8a29e" }}>
          <div><span style={{ color: "#f7f5f2" }} className="font-medium">{ctx.pipeline_name ?? "Corpus"}</span></div>
          <div className="mt-0.5 flex gap-3 flex-wrap">
            {rs.watermark_date && <span>Watermark: <span style={{ color: "#f7f5f2" }}>{rs.watermark_date}</span></span>}
            <span className="flex items-center gap-1"><Shield className="h-3 w-3" />
              {rs.pii_detected ? "PII detected" : <span style={{ color: "#86efac" }}>No PII</span>}
            </span>
            {rs.zone && <span className="flex items-center gap-1"><Globe className="h-3 w-3" />{rs.zone.toUpperCase()}</span>}
          </div>
        </div>
      </div>
    </div>
  );
}

// ══════════════════════════════════════════════════════════════════════════
// ── M2: EXPERIMENT COMPONENTS ─────────────────────────────────────────────
// ══════════════════════════════════════════════════════════════════════════

const PARAM_DIFF_COLOR = "#fef3c7";

function ExperimentComparisonTable({ runs, baselineId, championId }: {
  runs: ExperimentRunDetail[]; baselineId: string | null; championId: string | null;
}) {
  const allParamKeys = Array.from(new Set(runs.flatMap((r) => Object.keys(r.params ?? {}))));
  const baselineRun = runs.find((r) => r.run_id === baselineId);

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs border-collapse">
        <thead>
          <tr className="bg-muted/40 border-b border-border">
            <th className="text-left px-3 py-2 text-[10px] uppercase tracking-wide text-muted-foreground">Run</th>
            <th className="text-left px-3 py-2 text-[10px] uppercase tracking-wide text-muted-foreground">Strategy</th>
            {allParamKeys.map((k) => (
              <th key={k} className="text-left px-3 py-2 text-[10px] uppercase tracking-wide text-muted-foreground font-mono">{k}</th>
            ))}
            <th className="text-left px-3 py-2 text-[10px] uppercase tracking-wide text-muted-foreground">Best Loss</th>
            <th className="text-left px-3 py-2 text-[10px] uppercase tracking-wide text-muted-foreground">Best Iter</th>
            <th className="text-left px-3 py-2 text-[10px] uppercase tracking-wide text-muted-foreground">Convergence</th>
            <th className="text-left px-3 py-2 text-[10px] uppercase tracking-wide text-muted-foreground">Source</th>
          </tr>
        </thead>
        <tbody>
          {runs.sort((a, b) => (a.best_loss ?? 99) - (b.best_loss ?? 99)).map((run) => {
            const isChampion = run.run_id === championId;
            const isBaseline = run.run_id === baselineId;
            return (
              <tr key={run.run_id}
                className={cn("border-b border-border/50 hover:bg-muted/10",
                  isChampion && "bg-emerald-50/60 dark:bg-emerald-950/20",
                  isBaseline && !isChampion && "bg-amber-50/40 dark:bg-amber-950/10")}>
                <td className="px-3 py-2">
                  <div className="flex items-center gap-1">
                    {isChampion && <span className="text-[10px]">🏆</span>}
                    {isBaseline && !isChampion && <span className="text-[10px]">📌</span>}
                    <span className="font-mono text-[10px] truncate max-w-32">{run.run_id}</span>
                  </div>
                </td>
                <td className="px-3 py-2">
                  <span className="font-mono text-[10px] px-1.5 py-0.5 rounded"
                    style={{ color: STRATEGY_COLORS[run.strategy ?? ""] ?? "#6b7280", background: (STRATEGY_COLORS[run.strategy ?? ""] ?? "#6b7280") + "18" }}>
                    {run.strategy}
                  </span>
                </td>
                {allParamKeys.map((k) => {
                  const val = run.params?.[k];
                  const baseVal = baselineRun?.params?.[k];
                  const differs = baselineRun && val !== undefined && baseVal !== undefined && String(val) !== String(baseVal);
                  return (
                    <td key={k} className="px-3 py-2 font-mono">
                      <span className={cn(differs && "rounded px-1")}
                        style={differs ? { background: PARAM_DIFF_COLOR, fontWeight: 600 } : undefined}>
                        {val != null ? String(val) : "—"}
                      </span>
                    </td>
                  );
                })}
                <td className="px-3 py-2 font-mono font-bold"
                  style={{ color: isChampion ? "#16a34a" : undefined }}>
                  {run.best_loss?.toFixed(4) ?? "—"}
                </td>
                <td className="px-3 py-2 font-mono text-muted-foreground">
                  {run.best_iter != null ? `${run.best_iter}/${run.budget_max ?? "?"}` : "—"}
                </td>
                <td className="px-3 py-2">
                  <Badge variant={run.convergence === "threshold_reached" ? "ok" : "secondary"} className="text-[10px]">
                    {run.convergence?.replace(/_/g, " ") ?? "—"}
                  </Badge>
                </td>
                <td className="px-3 py-2 text-muted-foreground">{run.submitted_by ?? "—"}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function ExperimentCard({ experiment }: { experiment: Experiment }) {
  const [expanded, setExpanded] = React.useState(true);
  const detailQ = useQuery({
    queryKey: ["experiment-detail", experiment.experiment_id],
    queryFn: ({ signal }) => getExperimentDetail(experiment.experiment_id, signal),
    enabled: expanded, staleTime: 60_000,
  });

  const improvement = React.useMemo(() => {
    if (!detailQ.data) return null;
    const base = detailQ.data.runs_detail.find((r) => r.is_baseline);
    const champ = detailQ.data.runs_detail.find((r) => r.is_champion);
    if (base?.best_loss && champ?.best_loss && base.best_loss > 0) {
      return ((1 - champ.best_loss / base.best_loss) * 100).toFixed(1);
    }
    return null;
  }, [detailQ.data]);

  return (
    <div className="rounded-xl border border-border bg-card overflow-hidden">
      <button className="w-full px-4 py-3 text-left hover:bg-muted/10 transition-colors"
        onClick={() => setExpanded((v) => !v)}>
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <FlaskConical className="h-4 w-4 text-violet-500 shrink-0" />
              <span className="font-bold text-base">{experiment.name}</span>
            </div>
            {experiment.hypothesis && (
              <div className="text-xs text-muted-foreground italic mt-1 ml-6">"{experiment.hypothesis}"</div>
            )}
          </div>
          <div className="flex items-center gap-3 shrink-0">
            {improvement && (
              <div className="text-right">
                <div className="text-[10px] text-muted-foreground">improvement</div>
                <div className="font-mono font-bold text-emerald-600 dark:text-emerald-400">▼{improvement}%</div>
              </div>
            )}
            {expanded ? <ChevronUp className="h-4 w-4 text-muted-foreground" /> : <ChevronDown className="h-4 w-4 text-muted-foreground" />}
          </div>
        </div>
        <div className="flex flex-wrap gap-2 mt-2 ml-6">
          <Badge variant="outline" className="text-[10px]">{experiment.run_ids.length} runs</Badge>
          {experiment.session_label && <Badge variant="secondary" className="text-[10px]">{experiment.session_label}</Badge>}
          {experiment.tags.map((t) => <Badge key={t} variant="secondary" className="text-[10px]">{t}</Badge>)}
          {experiment.champion_run_id && <Badge variant="ok" className="text-[10px]">🏆 {experiment.champion_run_id}</Badge>}
        </div>
      </button>

      {expanded && detailQ.data && (
        <div className="border-t border-border">
          <ExperimentComparisonTable
            runs={detailQ.data.runs_detail}
            baselineId={experiment.baseline_run_id}
            championId={experiment.champion_run_id}
          />
          {/* Export actions */}
          <div className="flex items-center gap-2 px-4 py-2.5 border-t border-border bg-muted/10">
            <span className="text-xs text-muted-foreground">Export:</span>
            <a href={experimentJupyterUrl(experiment.experiment_id)}
              className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground border border-border rounded px-2 py-1 hover:bg-muted/30">
              <Download className="h-3 w-3" />Jupyter
            </a>
            <a href={experimentMlflowUrl(experiment.experiment_id)}
              className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground border border-border rounded px-2 py-1 hover:bg-muted/30">
              <Download className="h-3 w-3" />MLflow
            </a>
            <a href={experimentReportUrl(experiment.experiment_id)} target="_blank" rel="noopener noreferrer"
              className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground border border-border rounded px-2 py-1 hover:bg-muted/30">
              <ExternalLink className="h-3 w-3" />Report
            </a>
          </div>
        </div>
      )}
      {expanded && detailQ.isLoading && (
        <div className="px-4 py-4 border-t border-border"><Skeleton className="h-20 w-full" /></div>
      )}
    </div>
  );
}

// ══════════════════════════════════════════════════════════════════════════
// ── M3: LINEAGE DAG (inside PipelineCard — enhanced) ──────────────────────
// ══════════════════════════════════════════════════════════════════════════

// LineageDAG is already handled by the existing StageBox + flow layout.
// We enhance it by adding row-count edge annotations derived from real_stats.
// This is done by passing real_stats into StageBox below.

// ══════════════════════════════════════════════════════════════════════════
// ── M4: ARTIFACT VIEWER ───────────────────────────────────────────────────
// ══════════════════════════════════════════════════════════════════════════

function ColumnHistogram({ values, color }: { values: number[]; color: string }) {
  if (!values.length) return null;
  const max = Math.max(...values);
  return (
    <div className="flex items-end gap-0.5 h-5">
      {values.map((v, i) => (
        <div key={i} className="flex-1 rounded-sm min-w-[2px]"
          style={{ height: `${max > 0 ? (v / max) * 100 : 0}%`, background: color + "cc" }} />
      ))}
    </div>
  );
}

function StageImagesSection({ pipelineId, stageId }: {
  pipelineId: string;
  stageId: string;
}) {
  const imagesQ = useQuery({
    queryKey: ["stage-images", pipelineId, stageId],
    queryFn: async ({ signal }) => {
      const r = await fetch(
        `/v1/console/compute/pipelines/${encodeURIComponent(pipelineId)}/stages/${encodeURIComponent(stageId)}/artifact-images`,
        { credentials: "include", signal },
      );
      if (!r.ok) return { images: [], count: 0 };
      return r.json() as Promise<{ images: Array<{ filename: string; mime_type: string; size_bytes: number; thumbnail_filename: string | null; stage_id: string; pipeline_id: string }>; count: number }>;
    },
    staleTime: 60_000,
  });

  if (!imagesQ.data?.count) return null;

  const items: MediaItem[] = (imagesQ.data?.images ?? []).map((img) => ({
    media_id: `${stageId}_${img.filename}`,
    stage_id: stageId,
    pipeline_id: pipelineId,
    filename: img.filename,
    mime_type: img.mime_type,
    label: null,
    size_bytes: img.size_bytes,
    src: computeStageImageUrl(pipelineId, stageId, img.filename),
    thumbnail_src: img.thumbnail_filename
      ? computeStageImageUrl(pipelineId, stageId, img.thumbnail_filename)
      : null,
    ts: 0,
  }));

  return (
    <div className="border-t border-border px-4 py-3 bg-muted/5">
      <div className="flex items-center gap-2 mb-3">
        <BarChart3 className="h-3.5 w-3.5 text-violet-500" />
        <span className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          Charts & Images ({items.length})
        </span>
      </div>
      <MediaGallery items={items} />
    </div>
  );
}

function ArtifactViewerPanel({ pipelineId, stageId, stageName }: {
  pipelineId: string; stageId: string; stageName: string;
}) {
  const [previewFile, setPreviewFile] = React.useState<string | null>(null);

  const statsQ = useQuery({
    queryKey: ["artifact-stats", pipelineId, stageId],
    queryFn: ({ signal }) => getArtifactStats(pipelineId, stageId, signal),
    staleTime: 60_000,
  });

  const rs = statsQ.data?.real_stats ?? null;
  const schema: Array<{name: string; type: string}> = rs?.schema ?? [];
  const colStats: Record<string, { unique?: number; min?: number; max?: number; p50?: number; p95?: number; p99?: number }> = rs?.column_stats ?? {};

  React.useEffect(() => {
    if (statsQ.data?.artifacts?.length && !previewFile) {
      const first = statsQ.data.artifacts.find(
        (a) => [".csv", ".parquet", ".pq", ".json"].includes(a.extension)
      );
      if (first) setPreviewFile(first.filename);
    }
  }, [statsQ.data, previewFile]);

  if (statsQ.isLoading) return <div className="px-4 py-3 flex gap-2 text-xs text-muted-foreground"><Loader2 className="h-3 w-3 animate-spin" />Loading artifacts…</div>;
  if (!statsQ.data?.artifacts?.length) return null;

  return (
    <div className="border-t border-border bg-muted/5 space-y-0">
      {/* Artifact header */}
      <div className="flex items-center justify-between px-4 py-2.5 bg-muted/20 border-b border-border">
        <div className="flex items-center gap-2">
          <span className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">📄 Artifacts — {stageName}</span>
          {statsQ.data.pii_columns.length > 0 && (
            <Badge variant="warn" className="text-[10px] flex items-center gap-1">
              <Shield className="h-2.5 w-2.5" />PII: {statsQ.data.pii_columns.join(", ")}
            </Badge>
          )}
        </div>
        <div className="flex gap-1">
          {statsQ.data.artifacts.map((art) => (
            <button key={art.filename}
              className={cn("text-[10px] px-2 py-1 rounded border font-mono",
                previewFile === art.filename ? "bg-accent/20 border-accent/40 text-foreground" : "border-border text-muted-foreground hover:bg-muted/30")}
              onClick={() => setPreviewFile(art.filename)}>
              {art.filename} ({art.size_mb}MB)
            </button>
          ))}
        </div>
      </div>

      {/* Real stats row */}
      {rs && Object.keys(rs).length > 0 && (
        <div className="flex flex-wrap gap-4 px-4 py-2.5 border-b border-border text-xs">
          {rs.output_rows != null && <div><span className="text-muted-foreground">Rows: </span><span className="font-mono font-semibold">{rs.output_rows.toLocaleString()}</span></div>}
          {rs.compression_factor && <div><span className="text-muted-foreground">Compression: </span><span className="font-mono">{rs.compression_factor}×</span></div>}
          {rs.unique_countries && <div><span className="text-muted-foreground">Countries: </span><span className="font-mono">{rs.unique_countries}</span></div>}
          {rs.iso_weeks && <div><span className="text-muted-foreground">ISO weeks: </span><span className="font-mono">{rs.iso_weeks}</span></div>}
        </div>
      )}

      {/* Column chips with histograms */}
      {schema.length > 0 && (
        <div className="flex gap-2 px-4 py-3 overflow-x-auto border-b border-border">
          {schema.map((col) => {
            const cs = colStats[col.name] ?? {};
            const histBuckets = cs.p50 != null
              ? [cs.min ?? 0, (cs.p50 ?? 0) * 0.3, cs.p50 ?? 0, (cs.p50 ?? 0) * 1.5, cs.p95 ?? 0, cs.p99 ?? cs.p95 ?? 0].map(Number)
              : [];
            return (
              <div key={col.name} className="shrink-0 border border-border rounded-md px-2.5 py-2 min-w-24 bg-card">
                <div className="font-mono text-[10px] font-semibold truncate">{col.name}</div>
                <div className="text-[9px] text-muted-foreground">{col.type}{cs.unique ? ` · ${cs.unique} uniq` : ""}</div>
                {histBuckets.length > 0 && (
                  <div className="mt-1.5">
                    <ColumnHistogram values={histBuckets} color="#b8945f" />
                    {cs.p50 != null && <div className="text-[9px] text-muted-foreground mt-0.5">p50: {cs.p50.toLocaleString()}</div>}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* Navigable DataTable (sort / filter / pagination) */}
      {previewFile && (
        <div className="px-4 py-3">
          <DataTable
            key={previewFile}
            title={previewFile}
            downloadUrl={artifactDownloadUrl(pipelineId, stageId, previewFile)}
            defaultPerPage={50}
            fetchPage={async (params: DataTableFetchParams) =>
              getArtifactPreview(pipelineId, stageId, previewFile, params.per_page, undefined, {
                page: params.page,
                per_page: params.per_page,
                sort_col: params.sort_col ?? undefined,
                sort_dir: params.sort_dir,
                filter: params.filter || undefined,
              })
            }
          />
        </div>
      )}
      <StageImagesSection pipelineId={pipelineId} stageId={stageId} />
    </div>
  );
}

// ══════════════════════════════════════════════════════════════════════════
// ── M5: ROI PANEL ─────────────────────────────────════════════════════────
// ══════════════════════════════════════════════════════════════════════════

const COUNTRY_POSITIONS: Record<string, [number, number]> = {
  US:[20,38],MX:[18,50],BR:[28,65],AR:[26,78],CL:[23,78],CO:[22,60],PE:[22,68],
  GB:[45,32],DE:[47,34],FR:[45,38],ES:[44,40],IT:[48,38],SE:[49,27],NO:[48,25],
  NL:[46,33],PL:[50,32],AT:[49,35],BE:[46,33],CH:[47,36],PT:[43,41],FI:[51,24],
  DK:[47,30],IE:[43,33],
  AU:[78,72],NZ:[85,75],JP:[78,38],KR:[76,38],SG:[74,56],HK:[74,44],TW:[76,44],
  IN:[67,48],TH:[72,52],MY:[73,55],PH:[77,51],ID:[76,60],VN:[73,50],
  ZA:[50,72],NG:[47,56],EG:[52,43],
  TR:[53,40],
};

function WorldMap({ countries }: { countries: string[] }) {
  const countrySet = new Set(countries);
  return (
    <div className="relative bg-blue-50/40 dark:bg-blue-950/10 rounded-lg overflow-hidden h-32 border border-border">
      <svg viewBox="0 0 100 100" className="w-full h-full opacity-20">
        <rect width="100" height="100" fill="none" />
        <path d="M10,30 Q30,25 50,28 Q70,30 90,28 L90,32 Q70,34 50,32 Q30,30 10,34 Z" fill="#94a3b8" />
        <path d="M5,40 Q25,38 45,40 Q65,42 85,40 L88,50 Q68,52 48,50 Q28,48 8,50 Z" fill="#94a3b8" />
        <path d="M8,55 Q30,52 55,55 Q75,58 90,55 L92,65 Q75,68 55,65 Q30,62 8,65 Z" fill="#94a3b8" />
        <path d="M20,68 Q35,65 50,68 Q65,70 75,68 L76,78 Q64,80 50,78 Q35,75 20,78 Z" fill="#94a3b8" />
        <path d="M60,35 Q70,32 80,35 Q85,38 80,45 Q75,48 60,45 Z" fill="#94a3b8" />
        <path d="M68,50 Q78,48 85,52 Q88,56 85,62 Q78,65 68,62 Z" fill="#94a3b8" />
      </svg>
      {Object.entries(COUNTRY_POSITIONS).map(([cc, [x, y]]) => (
        <div key={cc}
          className="absolute rounded-full transition-all"
          style={{
            left: `${x}%`, top: `${y}%`,
            width: countrySet.has(cc) ? 8 : 4,
            height: countrySet.has(cc) ? 8 : 4,
            background: countrySet.has(cc) ? "#22c55e" : "#94a3b8",
            opacity: countrySet.has(cc) ? 1 : 0.3,
            transform: "translate(-50%,-50%)",
          }}
          title={cc}
        />
      ))}
      <div className="absolute bottom-1 right-2 text-[9px] text-muted-foreground">
        🟢 {countries.length} markets covered
      </div>
    </div>
  );
}

function ExperimentROIPanel({ experiments, runs }: { experiments: Experiment[]; runs: ComputeRun[] }) {
  if (!experiments.length) return null;

  const completedRuns = runs.filter((r) => isRunDone(r.state) && r.best_loss != null);
  const bestLoss = completedRuns.length ? Math.min(...completedRuns.map((r) => r.best_loss!)) : null;
  const worstLoss = completedRuns.length ? Math.max(...completedRuns.map((r) => r.best_loss!)) : null;
  const improvement = bestLoss != null && worstLoss != null && worstLoss > 0
    ? ((1 - bestLoss / worstLoss) * 100).toFixed(1) : null;
  const totalIter = runs.reduce((a, r) => a + r.iterations, 0);

  return (
    <div className="rounded-xl border border-border bg-card overflow-hidden">
      <div className="px-4 py-3 border-b border-border flex items-center justify-between">
        <div className="font-semibold text-sm flex items-center gap-2">
          <BarChart3 className="h-4 w-4 text-amber-500" />
          Cross-Experiment ROI
        </div>
        <span className="text-xs text-muted-foreground">{experiments.length} experiments · {runs.length} runs total</span>
      </div>
      <div className="p-4 space-y-4">
        <div className="grid grid-cols-4 gap-3">
          {[
            { val: improvement ? `▼${improvement}%` : "—", lbl: "Loss reduction", color: "text-emerald-600 dark:text-emerald-400" },
            { val: bestLoss?.toFixed(4) ?? "—", lbl: "Champion loss", color: "text-blue-600 dark:text-blue-400" },
            { val: totalIter.toLocaleString(), lbl: "Total iterations", color: "text-amber-600 dark:text-amber-400" },
            { val: String(experiments.length), lbl: "Experiments", color: "text-violet-600 dark:text-violet-400" },
          ].map((s) => (
            <div key={s.lbl} className="rounded-lg bg-muted/30 p-3 text-center">
              <div className={cn("font-bold font-mono text-lg", s.color)}>{s.val}</div>
              <div className="text-[10px] text-muted-foreground uppercase tracking-wide mt-0.5">{s.lbl}</div>
            </div>
          ))}
        </div>
        <WorldMap countries={Object.keys(COUNTRY_POSITIONS).slice(0, 24)} />
      </div>
    </div>
  );
}

// ══════════════════════════════════════════════════════════════════════════
// ── M6: EXPORT HUB ────────────────────────────────────════════════────────
// ══════════════════════════════════════════════════════════════════════════

// ══════════════════════════════════════════════════════════════════════════
// ── ADR-0090: awpkg Export Modal ──────────────────────────────────────────
// ══════════════════════════════════════════════════════════════════════════

function AwpkgExportModal({
  pipelineId,
  pipelineName,
  csrf,
  onClose,
}: {
  pipelineId: string;
  pipelineName: string;
  csrf: string;
  onClose: () => void;
}) {
  const previewQ = useQuery({
    queryKey: ["awpkg-preview", pipelineId],
    queryFn: ({ signal }) => getAwpkgPreview(pipelineId, signal),
    staleTime: 60_000,
  });

  const [packageId, setPackageId] = React.useState(
    () => `com.myorg.${pipelineName.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "pipeline"}`,
  );
  const [version, setVersion] = React.useState("1.0.0");
  const [mode, setMode] = React.useState<"replay" | "reoptimize">("replay");
  const [includeRag, setIncludeRag] = React.useState(true);
  const [includeFabric, setIncludeFabric] = React.useState(true);
  const [includeOutput, setIncludeOutput] = React.useState(true);
  const [includeWatermarks, setIncludeWatermarks] = React.useState(false);
  const [includeAdapters] = React.useState(true);
  const [includeBackends] = React.useState(true);
  const [scheduleCron, setScheduleCron] = React.useState("");
  const [scheduleTimezone, setScheduleTimezone] = React.useState("UTC");
  const [maxLoss, setMaxLoss] = React.useState("");
  const [downloading, setDownloading] = React.useState(false);
  const [sendingToWorkflow, setSendingToWorkflow] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  const navigate = useNavigate();
  const p = previewQ.data;

  const handleDownload = async () => {
    setError(null);
    setDownloading(true);
    try {
      const body: AwpkgExportRequest = {
        package_id: packageId,
        version,
        mode,
        include_sample_data: true,
        sample_rows: 100,
        include_rag_manifests: includeRag,
        include_fabric_datasources: includeFabric,
        include_output_datasources: includeOutput,
        include_watermarks: includeWatermarks,
        include_custom_adapters: includeAdapters,
        include_ml_backends: includeBackends,
        schedule_cron: scheduleCron || null,
        schedule_timezone: scheduleTimezone,
        acceptance_criteria: maxLoss ? { max_best_loss: parseFloat(maxLoss), on_fail: "abort" } : null,
      };
      const res = await downloadAwpkg(pipelineId, body, csrf);
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${packageId}-${version}.awpkg`;
      a.click();
      URL.revokeObjectURL(url);
      onClose();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setDownloading(false);
    }
  };

  const handleSendToWorkflow = async () => {
    setError(null);
    setSendingToWorkflow(true);
    try {
      const body: AwpkgExportRequest = {
        package_id: packageId,
        version,
        mode,
        include_sample_data: true,
        sample_rows: 100,
        include_rag_manifests: includeRag,
        include_fabric_datasources: includeFabric,
        include_output_datasources: includeOutput,
        include_watermarks: includeWatermarks,
        include_custom_adapters: includeAdapters,
        include_ml_backends: includeBackends,
        schedule_cron: scheduleCron || null,
        schedule_timezone: scheduleTimezone,
        acceptance_criteria: maxLoss ? { max_best_loss: parseFloat(maxLoss), on_fail: "abort" } : null,
      };
      const result = await pipelineToWorkflow(pipelineId, body, csrf);
      onClose();
      navigate(result.redirect_url);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSendingToWorkflow(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm p-4">
      <div className="bg-card border border-border rounded-2xl shadow-2xl w-full max-w-2xl max-h-[90vh] overflow-y-auto">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-border">
          <div className="flex items-center gap-2">
            <Package className="h-5 w-5 text-violet-500" />
            <span className="font-semibold text-base">Export as awpkg</span>
          </div>
          <button onClick={onClose} className="text-muted-foreground hover:text-foreground p-1 rounded">
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="px-6 py-4 space-y-5">
          {/* Preview loading */}
          {previewQ.isLoading && (
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <Loader2 className="h-3.5 w-3.5 animate-spin" />Scanning pipeline…
            </div>
          )}

          {/* Package identity */}
          <div className="space-y-3">
            <div className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Package identity</div>
            <div className="grid grid-cols-3 gap-3">
              <div className="col-span-2">
                <label className="text-xs text-muted-foreground mb-1 block">Package ID (reverse domain)</label>
                <input
                  value={packageId}
                  onChange={(e) => setPackageId(e.target.value)}
                  className="w-full text-xs border border-border rounded px-3 py-2 bg-background font-mono focus:outline-none focus:ring-1 focus:ring-violet-500"
                  placeholder="com.myorg.my-pipeline"
                />
              </div>
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">Version (SemVer)</label>
                <input
                  value={version}
                  onChange={(e) => setVersion(e.target.value)}
                  className="w-full text-xs border border-border rounded px-3 py-2 bg-background font-mono focus:outline-none focus:ring-1 focus:ring-violet-500"
                  placeholder="1.0.0"
                />
              </div>
            </div>
          </div>

          {/* Mode */}
          <div className="space-y-2">
            <div className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Execution mode</div>
            <div className="grid grid-cols-2 gap-2">
              {(["replay", "reoptimize"] as const).map((m) => (
                <button key={m} onClick={() => setMode(m)}
                  className={cn(
                    "rounded-lg border p-3 text-left text-xs transition-all",
                    mode === m
                      ? "border-violet-500 bg-violet-50 dark:bg-violet-950/30"
                      : "border-border hover:border-muted-foreground/40",
                  )}>
                  <div className="font-semibold capitalize">{m}</div>
                  <div className="text-muted-foreground mt-0.5">
                    {m === "replay"
                      ? "Champion params hardcoded — deterministic reproduction"
                      : "Param grid preserved — re-runs optimization on new data"}
                  </div>
                </button>
              ))}
            </div>
          </div>

          {/* Data sources */}
          {p && (p.rag_providers.length > 0 || p.fabric_datasources.length > 0 || p.output_datasources.length > 0) && (
            <div className="space-y-2">
              <div className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Data sources</div>
              <div className="rounded-lg border border-border divide-y divide-border">
                {p.rag_providers.length > 0 && (
                  <label className="flex items-center justify-between px-3 py-2.5 cursor-pointer hover:bg-muted/20">
                    <div>
                      <div className="text-xs font-medium">RAG Providers ({p.rag_providers.length})</div>
                      <div className="text-[10px] text-muted-foreground">
                        {p.rag_providers.map((r) => r.provider_id).join(", ")}
                      </div>
                    </div>
                    <input type="checkbox" checked={includeRag} onChange={(e) => setIncludeRag(e.target.checked)}
                      className="rounded border-border" />
                  </label>
                )}
                {p.fabric_datasources.length > 0 && (
                  <label className="flex items-center justify-between px-3 py-2.5 cursor-pointer hover:bg-muted/20">
                    <div>
                      <div className="text-xs font-medium">Input Datasources ({p.fabric_datasources.length})</div>
                      <div className="text-[10px] text-muted-foreground">
                        {p.fabric_datasources.map((d) => `${d.name} (${d.adapter}/${d.region})`).join(", ")}
                      </div>
                    </div>
                    <input type="checkbox" checked={includeFabric} onChange={(e) => setIncludeFabric(e.target.checked)}
                      className="rounded border-border" />
                  </label>
                )}
                {p.output_datasources.length > 0 && (
                  <label className="flex items-center justify-between px-3 py-2.5 cursor-pointer hover:bg-muted/20">
                    <div>
                      <div className="text-xs font-medium">Output Sinks ({p.output_datasources.length})</div>
                      <div className="text-[10px] text-muted-foreground">
                        {p.output_datasources.map((d) => d.name).join(", ")}
                      </div>
                    </div>
                    <input type="checkbox" checked={includeOutput} onChange={(e) => setIncludeOutput(e.target.checked)}
                      className="rounded border-border" />
                  </label>
                )}
                <label className="flex items-center justify-between px-3 py-2.5 cursor-pointer hover:bg-muted/20">
                  <div>
                    <div className="text-xs font-medium">Watermark checkpoints</div>
                    <div className="text-[10px] text-muted-foreground">Continue incremental loads from last processed record</div>
                  </div>
                  <input type="checkbox" checked={includeWatermarks} onChange={(e) => setIncludeWatermarks(e.target.checked)}
                    className="rounded border-border" />
                </label>
              </div>
            </div>
          )}

          {/* Schedule */}
          <div className="space-y-2">
            <div className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Production schedule</div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">Cron expression (optional)</label>
                <input value={scheduleCron} onChange={(e) => setScheduleCron(e.target.value)}
                  className="w-full text-xs border border-border rounded px-3 py-2 bg-background font-mono focus:outline-none focus:ring-1 focus:ring-violet-500"
                  placeholder="0 6 * * 1  (Mon 06:00)" />
              </div>
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">Timezone</label>
                <select value={scheduleTimezone} onChange={(e) => setScheduleTimezone(e.target.value)}
                  className="w-full text-xs border border-border rounded px-3 py-2 bg-background focus:outline-none focus:ring-1 focus:ring-violet-500">
                  {["UTC", "Europe/Berlin", "Europe/Paris", "America/New_York", "America/Los_Angeles", "Asia/Tokyo"].map((tz) => (
                    <option key={tz} value={tz}>{tz}</option>
                  ))}
                </select>
              </div>
            </div>
          </div>

          {/* Quality gate */}
          <div className="space-y-2">
            <div className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Quality gate</div>
            <div>
              <label className="text-xs text-muted-foreground mb-1 block">Max best_loss threshold (abort run if exceeded)</label>
              <input value={maxLoss} onChange={(e) => setMaxLoss(e.target.value)}
                type="number" step="0.001" min="0" max="1"
                className="w-40 text-xs border border-border rounded px-3 py-2 bg-background font-mono focus:outline-none focus:ring-1 focus:ring-violet-500"
                placeholder="e.g. 0.150" />
            </div>
          </div>

          {/* Credentials warning */}
          {p && p.secrets_required.length > 0 && (
            <div className="rounded-lg border border-amber-200 dark:border-amber-800 bg-amber-50 dark:bg-amber-950/30 p-3">
              <div className="text-xs font-semibold text-amber-700 dark:text-amber-400 mb-1">
                Receiver must provision {p.secrets_required.length} credential{p.secrets_required.length !== 1 ? "s" : ""}
              </div>
              <div className="flex flex-wrap gap-1">
                {p.secrets_required.map((s) => (
                  <code key={s} className="text-[10px] bg-amber-100 dark:bg-amber-900/40 rounded px-1.5 py-0.5 font-mono">{s}</code>
                ))}
              </div>
            </div>
          )}

          {/* Estimated size */}
          {p && (
            <div className="flex items-center justify-between text-xs text-muted-foreground">
              <span>Estimated package size</span>
              <span className="font-mono">~{p.estimated_size_kb} KB</span>
            </div>
          )}

          {error && (
            <div className="rounded-lg border border-destructive/40 bg-destructive/10 p-3 text-xs text-destructive">{error}</div>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between px-6 py-4 border-t border-border bg-muted/10">
          <button onClick={onClose} className="text-xs text-muted-foreground hover:text-foreground px-3 py-1.5 rounded">
            Cancel
          </button>
          <div className="flex items-center gap-2">
            <Button onClick={handleDownload} disabled={downloading || sendingToWorkflow || !packageId} size="sm"
              className="gap-2 bg-violet-600 hover:bg-violet-700 text-white">
              {downloading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Download className="h-3.5 w-3.5" />}
              {downloading ? "Generating…" : "Download .awpkg"}
            </Button>
            <Button onClick={handleSendToWorkflow} disabled={downloading || sendingToWorkflow || !packageId} size="sm"
              variant="outline" className="gap-2 border-violet-300 text-violet-700 hover:bg-violet-50 dark:text-violet-400">
              {sendingToWorkflow ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Package className="h-3.5 w-3.5" />}
              {sendingToWorkflow ? "Sending…" : "Add to Workflows"}
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Promote Champion button ───────────────────────────────────────────────

function PromoteChampionButton({
  pipelineId,
  pipelineName,
  csrf,
}: {
  pipelineId: string;
  pipelineName: string;
  csrf: string;
}) {
  const [state, setState] = React.useState<"idle" | "asking" | "promoting" | "done" | "error">("idle");
  const [result, setResult] = React.useState<{ new_version: string; improvement_pct: number | null } | null>(null);
  const [runId, setRunId] = React.useState("");
  const [currentVersion, setCurrentVersion] = React.useState("1.0.0");
  const [error, setError] = React.useState<string | null>(null);

  if (state === "done" && result) {
    return (
      <div className="flex items-center gap-2 text-xs text-emerald-600 dark:text-emerald-400">
        <Trophy className="h-3.5 w-3.5" />
        Champion promoted → v{result.new_version}
        {result.improvement_pct != null && <span className="text-[10px]">({result.improvement_pct}% better)</span>}
        <button onClick={() => { setState("idle"); setResult(null); }}
          className="ml-1 text-muted-foreground hover:text-foreground"><X className="h-3 w-3" /></button>
      </div>
    );
  }

  if (state === "asking") {
    return (
      <div className="flex items-center gap-2 flex-wrap">
        <input value={runId} onChange={(e) => setRunId(e.target.value)}
          placeholder="run_id of champion" className="text-xs border border-border rounded px-2 py-1 bg-background w-36 font-mono" />
        <input value={currentVersion} onChange={(e) => setCurrentVersion(e.target.value)}
          placeholder="current version" className="text-xs border border-border rounded px-2 py-1 bg-background w-20 font-mono" />
        <Button size="sm" className="h-6 px-2 text-xs"
          disabled={!runId || state !== "asking"}
          onClick={async () => {
            setState("promoting");
            setError(null);
            try {
              const r = await promoteChampion(pipelineId, {
                run_id: runId,
                package_id: `com.myorg.${pipelineName.toLowerCase().replace(/[^a-z0-9]+/g, "-")}`,
                current_version: currentVersion,
                improvement_threshold_pct: 2.0,
              }, csrf);
              if (r.promoted) {
                setResult({ new_version: r.new_version, improvement_pct: r.improvement_pct });
                setState("done");
              } else {
                setError(r.reason ?? "Threshold not met");
                setState("asking");
              }
            } catch (e: unknown) {
              setError(e instanceof Error ? e.message : String(e));
              setState("asking");
            }
          }}>
          Promote
        </Button>
        <button onClick={() => setState("idle")} className="text-muted-foreground hover:text-foreground"><X className="h-3.5 w-3.5" /></button>
        {error && <span className="text-[10px] text-destructive">{error}</span>}
      </div>
    );
  }

  return (
    <Button variant="ghost" size="sm" className="h-7 px-2 gap-1.5 text-xs"
      disabled={state === "promoting"}
      onClick={() => setState("asking")}>
      {state === "promoting" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Trophy className="h-3.5 w-3.5" />}
      Promote Champion
    </Button>
  );
}

function ExportHubTab({
  experiments,
  pipelines,
  csrf,
}: {
  experiments: Experiment[];
  pipelines: import("@/lib/api").PipelineSummary[];
  csrf: string;
}) {
  const [exportModal, setExportModal] = React.useState<{ id: string; name: string } | null>(null);

  const formats = [
    { icon: "📓", name: "Jupyter Notebook", desc: "Pre-filled .ipynb with data loading, loss charts, top-track table", ext: "Jupyter" },
    { icon: "🧪", name: "MLflow mlruns/", desc: "params/, metrics/, artifacts/ structure — import with mlflow.load_experiment()", ext: "MLflow" },
    { icon: "📄", name: "HTML Report", desc: "Shareable, self-contained experiment report for stakeholders", ext: "Report" },
  ];

  const completedPipelines = pipelines.filter((p) => isPipelineDone(p.state));

  return (
    <div className="space-y-6">

      {/* ── Pipeline awpkg exports ── */}
      <div className="space-y-3">
        <div className="flex items-center gap-2">
          <Package className="h-4 w-4 text-violet-500" />
          <span className="font-semibold text-sm">Pipeline Packages (awpkg)</span>
        </div>
        <p className="text-xs text-muted-foreground">
          Export a complete pipeline as an installable <code className="font-mono text-[10px] bg-muted px-1 rounded">.awpkg</code> bundle —
          includes AWP workflow DAG, forge tools, RAG providers, datasource connections, ML backends, and GDPR processing record.
          Installable on any CorvinOS tenant with <code className="font-mono text-[10px] bg-muted px-1 rounded">corvin install</code>.
        </p>

        {completedPipelines.length === 0 ? (
          <Card><CardContent className="py-8 text-center">
            <Package className="mx-auto mb-2 h-7 w-7 text-muted-foreground/30" />
            <p className="text-sm font-medium">No completed pipelines yet.</p>
            <p className="text-xs text-muted-foreground mt-1">Complete a pipeline run to unlock awpkg export.</p>
          </CardContent></Card>
        ) : (
          <div className="grid gap-2">
            {completedPipelines.map((pip) => (
              <div key={pip.pipeline_id}
                className="rounded-xl border border-border bg-card flex items-center justify-between px-4 py-3">
                <div className="min-w-0">
                  <div className="font-semibold text-sm truncate">{pip.name}</div>
                  <div className="flex items-center gap-2 mt-0.5">
                    <code className="text-[10px] font-mono text-muted-foreground">{pip.pipeline_id}</code>
                    <Badge variant="ok" className="text-[10px]">{pip.stage_count} stages</Badge>
                    {pip.best_losses && Object.keys(pip.best_losses).length > 0 && (
                      <span className="text-[10px] text-muted-foreground">
                        best loss: {Math.min(...Object.values(pip.best_losses)).toFixed(4)}
                      </span>
                    )}
                  </div>
                </div>
                <Button size="sm" variant="outline"
                  className="gap-1.5 text-xs border-violet-300 text-violet-700 hover:bg-violet-50 dark:text-violet-400 dark:hover:bg-violet-950/30 shrink-0"
                  onClick={() => setExportModal({ id: pip.pipeline_id, name: pip.name })}>
                  <Package className="h-3.5 w-3.5" />
                  Export as awpkg
                </Button>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="border-t border-border" />

      {/* ── Experiment exports ── */}
      <div className="space-y-3">
        <div className="flex items-center gap-2">
          <FlaskConical className="h-4 w-4 text-violet-500" />
          <span className="font-semibold text-sm">Experiment Exports</span>
        </div>

        {!experiments.length ? (
          <Card><CardContent className="py-8 text-center">
            <FlaskConical className="mx-auto mb-2 h-7 w-7 text-muted-foreground/30" />
            <p className="text-sm font-medium">No experiments yet.</p>
            <p className="text-xs text-muted-foreground mt-1">Create an experiment to unlock Jupyter, MLflow and HTML exports.</p>
          </CardContent></Card>
        ) : (
          experiments.map((exp) => (
            <div key={exp.experiment_id} className="rounded-xl border border-border bg-card overflow-hidden">
              <div className="px-4 py-3 border-b border-border bg-muted/20 flex items-center gap-2">
                <FlaskConical className="h-4 w-4 text-violet-500" />
                <span className="font-semibold text-sm">{exp.name}</span>
                <Badge variant="secondary" className="text-[10px]">{exp.run_ids.length} runs</Badge>
              </div>
              <div className="grid grid-cols-3 gap-3 p-4">
                {formats.map((fmt) => (
                  <a key={fmt.name}
                    href={fmt.ext === "Jupyter" ? experimentJupyterUrl(exp.experiment_id)
                      : fmt.ext === "MLflow" ? experimentMlflowUrl(exp.experiment_id)
                      : experimentReportUrl(exp.experiment_id)}
                    target={fmt.ext === "Report" ? "_blank" : undefined}
                    rel="noopener noreferrer"
                    download={fmt.ext !== "Report"}
                    className="rounded-lg border border-border p-4 text-center hover:bg-muted/30 hover:border-accent/40 transition-colors group block">
                    <div className="text-2xl mb-2">{fmt.icon}</div>
                    <div className="font-semibold text-sm group-hover:text-foreground">{fmt.name}</div>
                    <div className="text-[10px] text-muted-foreground mt-1">{fmt.desc}</div>
                    <div className="mt-3 flex items-center justify-center gap-1 text-[10px] text-muted-foreground">
                      {fmt.ext === "Report" ? <><ExternalLink className="h-3 w-3" />Open</>
                        : <><Download className="h-3 w-3" />Download</>}
                    </div>
                  </a>
                ))}
              </div>
            </div>
          ))
        )}
      </div>

      {exportModal && (
        <AwpkgExportModal
          pipelineId={exportModal.id}
          pipelineName={exportModal.name}
          csrf={csrf}
          onClose={() => setExportModal(null)}
        />
      )}
    </div>
  );
}

// ══════════════════════════════════════════════════════════════════════════
// ── COMPUTE SETTINGS PANEL ────────────────────────────────────────────────
// ══════════════════════════════════════════════════════════════════════════

function SettingsRow({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-4 py-3 border-b border-border/60 last:border-0">
      <div className="min-w-0">
        <div className="text-sm font-medium">{label}</div>
        {hint && <div className="text-xs text-muted-foreground mt-0.5">{hint}</div>}
      </div>
      <div className="shrink-0">{children}</div>
    </div>
  );
}

function StrategyPicker({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const opts = [
    { key: "bayesian", label: "Bayesian", desc: "Smart — converges fast, best for unknowns" },
    { key: "grid",     label: "Grid",     desc: "Exhaustive — tests all combinations" },
    { key: "random",   label: "Random",   desc: "Exploratory — broad coverage, no model" },
  ];
  return (
    <div className="flex gap-1.5">
      {opts.map((o) => (
        <button key={o.key} title={o.desc}
          onClick={() => onChange(o.key)}
          className={cn(
            "px-3 py-1.5 rounded-md text-xs font-medium border transition-colors",
            value === o.key
              ? "border-transparent text-white"
              : "border-border text-muted-foreground hover:text-foreground hover:bg-muted/30"
          )}
          style={value === o.key ? { background: STRATEGY_COLORS[o.key] ?? "#6b7280" } : undefined}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

function Toggle({ checked, onChange }: { checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <button
      role="switch" aria-checked={checked}
      onClick={() => onChange(!checked)}
      className={cn(
        "relative inline-flex h-5 w-9 shrink-0 rounded-full border-2 border-transparent transition-colors",
        checked ? "bg-emerald-500" : "bg-muted"
      )}>
      <span className={cn(
        "pointer-events-none inline-block h-4 w-4 rounded-full bg-white shadow-sm ring-0 transition-transform",
        checked ? "translate-x-4" : "translate-x-0"
      )} />
    </button>
  );
}

function NumericInput({ value, onChange, min, max, step = 1, suffix }: {
  value: number; onChange: (v: number) => void; min: number; max: number; step?: number; suffix?: string;
}) {
  return (
    <div className="flex items-center gap-1">
      <input type="number" value={value} min={min} max={max} step={step}
        onChange={(e) => {
          const v = parseFloat(e.target.value);
          if (!isNaN(v) && v >= min && v <= max) onChange(v);
        }}
        className="w-20 text-right text-xs font-mono border border-border rounded px-2 py-1 bg-background focus:outline-none focus:ring-1 focus:ring-accent" />
      {suffix && <span className="text-xs text-muted-foreground">{suffix}</span>}
    </div>
  );
}

function NullableFloat({ value, onChange, min, max, placeholder, nullLabel = "Disabled" }: {
  value: number | null; onChange: (v: number | null) => void;
  min: number; max: number; placeholder?: string; nullLabel?: string;
}) {
  const [enabled, setEnabled] = React.useState(value !== null);
  const [local, setLocal] = React.useState(value ?? min);

  const toggle = (on: boolean) => {
    setEnabled(on);
    onChange(on ? local : null);
  };

  return (
    <div className="flex items-center gap-2">
      <Toggle checked={enabled} onChange={toggle} />
      {enabled ? (
        <input type="number" value={local} min={min} max={max} step={0.01}
          onChange={(e) => {
            const v = parseFloat(e.target.value);
            if (!isNaN(v)) { setLocal(v); onChange(v); }
          }}
          placeholder={placeholder}
          className="w-20 text-right text-xs font-mono border border-border rounded px-2 py-1 bg-background focus:outline-none focus:ring-1 focus:ring-accent" />
      ) : (
        <span className="text-xs text-muted-foreground">{nullLabel}</span>
      )}
    </div>
  );
}

function GroupByPicker({ value, onChange }: {
  value: ComputeSettings["default_group_by"];
  onChange: (v: ComputeSettings["default_group_by"]) => void;
}) {
  const opts: ComputeSettings["default_group_by"][] = ["none", "session", "tool", "source", "day", "strategy"];
  return (
    <select value={value} onChange={(e) => onChange(e.target.value as ComputeSettings["default_group_by"])}
      className="text-xs border border-border rounded px-2 py-1.5 bg-background focus:outline-none focus:ring-1 focus:ring-accent">
      {opts.map((o) => <option key={o} value={o}>{o === "none" ? "No grouping" : o.charAt(0).toUpperCase() + o.slice(1)}</option>)}
    </select>
  );
}

function ComputeSettingsPanel({ onClose, onSaved, csrf }: { onClose: () => void; onSaved: (s: ComputeSettings) => void; csrf: string }) {
  const settingsQ = useQuery({
    queryKey: ["compute-settings"],
    queryFn: ({ signal }) => getComputeSettings(signal),
    staleTime: 60_000,
  });

  const [draft, setDraft] = React.useState<ComputeSettings | null>(null);
  const [saving, setSaving] = React.useState(false);
  const [saved, setSaved] = React.useState(false);

  React.useEffect(() => {
    if (settingsQ.data && !draft) setDraft(settingsQ.data.settings);
  }, [settingsQ.data, draft]);

  const set = <K extends keyof ComputeSettings>(k: K, v: ComputeSettings[K]) => {
    setDraft((d) => d ? { ...d, [k]: v } : d);
    setSaved(false);
  };

  const save = async () => {
    if (!draft) return;
    setSaving(true);
    try {
      const res = await updateComputeSettings(draft, csrf);
      onSaved(res.settings);
      setSaved(true);
      setTimeout(() => setSaved(false), 2500);
    } finally {
      setSaving(false);
    }
  };

  if (settingsQ.isLoading || !draft) {
    return (
      <div className="fixed inset-0 z-50 bg-background/60 backdrop-blur-sm flex items-start justify-end p-4" onClick={onClose}>
        <div className="bg-card border border-border rounded-xl shadow-2xl w-96 p-6 flex items-center gap-2 text-muted-foreground text-sm mt-16">
          <Loader2 className="h-4 w-4 animate-spin" />Loading settings…
        </div>
      </div>
    );
  }

  return (
    <div className="fixed inset-0 z-50 bg-background/60 backdrop-blur-sm flex items-start justify-end p-4"
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="bg-card border border-border rounded-xl shadow-2xl w-[420px] max-h-[calc(100vh-2rem)] overflow-y-auto mt-16"
        onClick={(e) => e.stopPropagation()}>

        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-border sticky top-0 bg-card z-10">
          <div className="flex items-center gap-2">
            <Settings className="h-4 w-4 text-accent" />
            <span className="font-semibold text-sm">Agentic Compute Settings</span>
          </div>
          <button onClick={onClose} className="text-muted-foreground hover:text-foreground">
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="px-5 py-1">
          {/* Section: Optimization */}
          <div className="pt-4 pb-1">
            <div className="text-[10px] text-muted-foreground uppercase tracking-widest font-medium mb-1">Optimization defaults</div>
            <div className="text-[10px] text-muted-foreground">Applied when starting jobs from chat or voice without explicit parameters.</div>
          </div>

          <SettingsRow label="Default strategy"
            hint="Bayesian converges fast; Grid is exhaustive; Random explores broadly">
            <StrategyPicker value={draft.default_strategy} onChange={(v) => set("default_strategy", v as ComputeSettings["default_strategy"])} />
          </SettingsRow>

          <SettingsRow label="Max iterations" hint="Budget ceiling per run">
            <NumericInput value={draft.default_max_iterations} onChange={(v) => set("default_max_iterations", v)} min={5} max={1000} />
          </SettingsRow>

          <SettingsRow label="Run timeout" hint="Hard time limit per run">
            <NumericInput value={draft.default_timeout_s} onChange={(v) => set("default_timeout_s", v)} min={60} max={7200} suffix="s" />
          </SettingsRow>

          <SettingsRow label="Auto-stop threshold"
            hint="Stop early when best loss drops below this value (e.g. 0.05 = stop at 5%)">
            <NullableFloat value={draft.convergence_threshold} onChange={(v) => set("convergence_threshold", v)}
              min={0.001} max={1.0} placeholder="0.05" nullLabel="Off" />
          </SettingsRow>

          {/* Section: Experiments */}
          <div className="pt-5 pb-1">
            <div className="text-[10px] text-muted-foreground uppercase tracking-widest font-medium mb-1">Experiments</div>
          </div>

          <SettingsRow label="Auto-champion"
            hint="Automatically mark the run with lowest loss as champion in its experiment">
            <Toggle checked={draft.auto_champion} onChange={(v) => set("auto_champion", v)} />
          </SettingsRow>

          <SettingsRow label="Default grouping" hint="How runs are grouped when you open the Runs tab">
            <GroupByPicker value={draft.default_group_by} onChange={(v) => set("default_group_by", v)} />
          </SettingsRow>

          {/* Section: Display */}
          <div className="pt-5 pb-1">
            <div className="text-[10px] text-muted-foreground uppercase tracking-widest font-medium mb-1">Display</div>
          </div>

          <SettingsRow label="Show corpus banner"
            hint="Dark banner above KPIs showing dataset size, countries, PII status">
            <Toggle checked={draft.show_corpus_banner} onChange={(v) => set("show_corpus_banner", v)} />
          </SettingsRow>

          <SettingsRow label="Artifact preview rows" hint="Number of rows shown in the in-UI CSV/Parquet preview">
            <NumericInput value={draft.artifact_preview_rows} onChange={(v) => set("artifact_preview_rows", v)} min={10} max={500} suffix="rows" />
          </SettingsRow>

          {/* Section: Alerts */}
          <div className="pt-5 pb-1">
            <div className="text-[10px] text-muted-foreground uppercase tracking-widest font-medium mb-1 flex items-center gap-1.5">
              <Bell className="h-3 w-3" />Alerts
            </div>
          </div>

          <SettingsRow label="Loss alert threshold"
            hint="Show a warning badge when any run's best loss drops below this value">
            <NullableFloat value={draft.alert_loss_threshold} onChange={(v) => set("alert_loss_threshold", v)}
              min={0.001} max={1.0} placeholder="0.10" nullLabel="Off" />
          </SettingsRow>
        </div>

        {/* Footer */}
        <div className="sticky bottom-0 bg-card border-t border-border px-5 py-3 flex items-center justify-between gap-3">
          <span className={cn("text-xs transition-all", saved ? "text-emerald-600 dark:text-emerald-400" : "text-muted-foreground opacity-0")}>
            ✓ Saved
          </span>
          <div className="flex gap-2">
            <Button variant="outline" size="sm" onClick={onClose}>Cancel</Button>
            <Button size="sm" onClick={save} disabled={saving}>
              {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin mr-1" /> : null}
              Save settings
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}

// ══════════════════════════════════════════════════════════════════════════
// ── COMPUTE JOBS SECTION (ADR-0124 M3) ────────────────────────────────────
// ══════════════════════════════════════════════════════════════════════════

const JOB_STATUS_STYLE: Record<string, { label: string; className: string }> = {
  queued:    { label: "Queued",    className: "bg-yellow-100 text-yellow-800 dark:bg-yellow-900/40 dark:text-yellow-300 border border-yellow-300 dark:border-yellow-700" },
  running:   { label: "Running",   className: "bg-blue-100 text-blue-800 dark:bg-blue-900/40 dark:text-blue-300 border border-blue-300 dark:border-blue-700" },
  completed: { label: "Completed", className: "bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-300 border border-green-300 dark:border-green-700" },
  failed:    { label: "Failed",    className: "bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300 border border-red-300 dark:border-red-700" },
};

function ComputeJobStatusBadge({ status }: { status: string }) {
  const style = JOB_STATUS_STYLE[status] ?? { label: status, className: "bg-muted text-muted-foreground border border-border" };
  return (
    <span className={cn("inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-semibold", style.className)}>
      {style.label}
    </span>
  );
}

function SubmitJobDialog({ csrf, onSubmitted, onClose }: {
  csrf: string;
  onSubmitted: () => void;
  onClose: () => void;
}) {
  const [name, setName] = React.useState("");
  const [jobType, setJobType] = React.useState<"grid" | "pipeline" | "batch">("grid");
  const [strategy, setStrategy] = React.useState<"grid" | "random" | "bayesian">("bayesian");
  const [maxTrials, setMaxTrials] = React.useState(10);
  const [description, setDescription] = React.useState("");
  const [error, setError] = React.useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: () =>
      submitComputeJob(
        { name: name.trim(), job_type: jobType, strategy, max_trials: maxTrials, description: description.trim() || undefined },
        csrf,
      ),
    onSuccess: () => {
      onSubmitted();
      onClose();
    },
    onError: (e: unknown) => {
      setError(e instanceof Error ? e.message : String(e));
    },
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return;
    setError(null);
    mutation.mutate();
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm p-4">
      <div className="bg-card border border-border rounded-2xl shadow-2xl w-full max-w-md">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-border">
          <div className="flex items-center gap-2">
            <Cpu className="h-4 w-4 text-accent" />
            <span className="font-semibold text-sm">Submit Agentic Compute Job</span>
          </div>
          <button onClick={onClose} className="text-muted-foreground hover:text-foreground p-1 rounded" data-testid="submit-job-dialog-close">
            <X className="h-4 w-4" />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="px-6 py-4 space-y-4">
          {/* Job Name */}
          <div className="space-y-1">
            <label className="text-xs font-medium text-muted-foreground block">Job Name <span className="text-destructive">*</span></label>
            <input
              data-testid="job-name-input"
              type="text"
              required
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. Bayesian sweep — model v3"
              className="w-full text-sm border border-border rounded px-3 py-2 bg-background focus:outline-none focus:ring-1 focus:ring-accent"
            />
          </div>

          {/* Job Type */}
          <div className="space-y-1">
            <label className="text-xs font-medium text-muted-foreground block">Job Type</label>
            <select
              data-testid="job-type-select"
              value={jobType}
              onChange={(e) => setJobType(e.target.value as "grid" | "pipeline" | "batch")}
              className="w-full text-sm border border-border rounded px-3 py-2 bg-background focus:outline-none focus:ring-1 focus:ring-accent"
            >
              <option value="grid">Grid</option>
              <option value="pipeline">Pipeline</option>
              <option value="batch">Batch</option>
            </select>
          </div>

          {/* Strategy */}
          <div className="space-y-1">
            <label className="text-xs font-medium text-muted-foreground block">Strategy</label>
            <select
              data-testid="job-strategy-select"
              value={strategy}
              onChange={(e) => setStrategy(e.target.value as "grid" | "random" | "bayesian")}
              className="w-full text-sm border border-border rounded px-3 py-2 bg-background focus:outline-none focus:ring-1 focus:ring-accent"
            >
              <option value="grid">Grid</option>
              <option value="random">Random</option>
              <option value="bayesian">Bayesian</option>
            </select>
          </div>

          {/* Max Trials */}
          <div className="space-y-1">
            <label className="text-xs font-medium text-muted-foreground block">Max Trials</label>
            <input
              data-testid="job-max-trials-input"
              type="number"
              min={1}
              max={10000}
              value={maxTrials}
              onChange={(e) => {
                const v = parseInt(e.target.value, 10);
                if (!isNaN(v) && v >= 1 && v <= 10000) setMaxTrials(v);
              }}
              className="w-full text-sm border border-border rounded px-3 py-2 bg-background font-mono focus:outline-none focus:ring-1 focus:ring-accent"
            />
          </div>

          {/* Description */}
          <div className="space-y-1">
            <label className="text-xs font-medium text-muted-foreground block">Description <span className="text-muted-foreground/60">(optional)</span></label>
            <input
              data-testid="job-description-input"
              type="text"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Short description of this job"
              className="w-full text-sm border border-border rounded px-3 py-2 bg-background focus:outline-none focus:ring-1 focus:ring-accent"
            />
          </div>

          {error && (
            <div className="rounded-lg border border-destructive/40 bg-destructive/10 p-3 text-xs text-destructive">{error}</div>
          )}

          <div className="flex items-center justify-end gap-2 pt-1">
            <Button type="button" variant="outline" size="sm" onClick={onClose} data-testid="submit-job-cancel-btn">
              Cancel
            </Button>
            <Button
              type="submit"
              size="sm"
              disabled={!name.trim() || mutation.isPending}
              data-testid="submit-job-confirm-btn"
              className="gap-1.5"
            >
              {mutation.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5" />}
              {mutation.isPending ? "Submitting…" : "Submit Job"}
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}

function ComputeJobCard({ job, csrf, onCancelled }: { job: ComputeJob; csrf: string; onCancelled: () => void }) {
  const cancelMutation = useMutation({
    mutationFn: () => cancelComputeJob(job.job_id, csrf),
    onSuccess: onCancelled,
  });

  const createdAt = job.created_at
    ? new Date(typeof job.created_at === "number" ? job.created_at * 1000 : job.created_at).toLocaleString()
    : "—";

  return (
    <div className="rounded-lg border border-border bg-card px-4 py-3 flex items-start gap-3" data-testid={`compute-job-card-${job.job_id}`}>
      <div className="flex-1 min-w-0 space-y-1.5">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-semibold text-sm truncate">{job.name}</span>
          <span className="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-mono border border-border bg-muted text-muted-foreground">
            {job.job_type}
          </span>
          <ComputeJobStatusBadge status={job.status} />
        </div>
        <div className="text-xs font-mono text-muted-foreground">{job.job_id}</div>
        <div className="text-[11px] text-muted-foreground">{createdAt}</div>
      </div>
      <Button
        variant="ghost"
        size="sm"
        className="h-7 px-2 text-muted-foreground hover:text-destructive shrink-0"
        onClick={() => cancelMutation.mutate()}
        disabled={cancelMutation.isPending || job.status === "completed" || job.status === "failed"}
        data-testid={`cancel-job-btn-${job.job_id}`}
        title="Cancel job"
      >
        {cancelMutation.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <X className="h-3.5 w-3.5" />}
        <span className="ml-1 text-xs">Cancel</span>
      </Button>
    </div>
  );
}

function ComputeJobsSection({ csrf }: { csrf: string }) {
  const qc = useQueryClient();
  const [dialogOpen, setDialogOpen] = React.useState(false);

  const jobsQ = useQuery({
    queryKey: ["compute-jobs"],
    queryFn: ({ signal }) => listComputeJobs(signal),
    refetchInterval: 30_000,
  });

  const jobs: ComputeJob[] = jobsQ.data?.jobs ?? [];

  const handleSubmitted = () => {
    qc.invalidateQueries({ queryKey: ["compute-jobs"] });
  };

  const handleCancelled = () => {
    qc.invalidateQueries({ queryKey: ["compute-jobs"] });
  };

  return (
    <div className="space-y-3">
      {/* Section header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Cpu className="h-4 w-4 text-accent" />
          <span className="font-semibold text-sm">Agentic Compute Jobs</span>
          {jobs.length > 0 && (
            <Badge variant="outline" className="text-xs">{jobs.length}</Badge>
          )}
        </div>
        <Button
          size="sm"
          variant="outline"
          className="gap-1.5 text-xs"
          onClick={() => setDialogOpen(true)}
          data-testid="submit-job-btn"
        >
          <Play className="h-3.5 w-3.5" />
          + Submit Job
        </Button>
      </div>

      {/* Job list */}
      {jobsQ.isLoading ? (
        <Skeleton className="h-24 w-full" />
      ) : jobs.length === 0 ? (
        <Card>
          <CardContent className="py-10 text-center">
            <Cpu className="mx-auto mb-2 h-7 w-7 text-muted-foreground/30" />
            <p className="text-sm text-muted-foreground">No jobs submitted yet.</p>
          </CardContent>
        </Card>
      ) : (
        <div className="grid gap-2">
          {jobs.map((job) => (
            <ComputeJobCard
              key={job.job_id}
              job={job}
              csrf={csrf}
              onCancelled={handleCancelled}
            />
          ))}
        </div>
      )}

      {/* Submit dialog */}
      {dialogOpen && (
        <SubmitJobDialog
          csrf={csrf}
          onSubmitted={handleSubmitted}
          onClose={() => setDialogOpen(false)}
        />
      )}
    </div>
  );
}

// ══════════════════════════════════════════════════════════════════════════

type Tab = "runs" | "pipelines" | "hac" | "analytics" | "experiments" | "export" | "acs";

export function ComputePage() {
  const { session } = useAuth();
  const qc = useQueryClient();
  const [tab, setTab] = React.useState<Tab>("acs");
  const [groupBy, setGroupBy] = React.useState<GroupBy>("none");
  const [settingsOpen, setSettingsOpen] = React.useState(false);

  const statusQ = useQuery({
    queryKey: ["compute-status"],
    queryFn: ({ signal }) => getComputeStatus(signal),
    refetchInterval: 10_000,
  });
  useQuery({ queryKey: ["compute-config"], queryFn: ({ signal }) => getComputeConfig(signal) });
  const corpusQ = useQuery({
    queryKey: ["compute-corpus"],
    queryFn: ({ signal }) => getCorpusContext(signal),
    staleTime: 120_000,
  });
  const experimentsQ = useQuery({
    queryKey: ["compute-experiments"],
    queryFn: ({ signal }) => listExperiments(signal),
    refetchInterval: tab === "experiments" || tab === "export" ? 30_000 : false,
  });
  const licenseQ = useQuery({
    queryKey: ["compute-license"],
    queryFn: ({ signal }) => getComputeLicense(signal),
    refetchInterval: 30_000,
    staleTime: 20_000,
  });
  // ADR-0099 — open Anthropic Batch API jobs (poll every 5 min; results take 1-24 h)
  const batchQ = useQuery({
    queryKey: ["compute-batch-open"],
    queryFn: ({ signal }) => getOpenBatchJobs(signal),
    refetchInterval: tab === "runs" ? 300_000 : false,
    staleTime: 240_000,
  });
  const openBatchJobs = batchQ.data?.jobs ?? [];
  const pipelinesQ = useQuery({
    queryKey: ["compute-pipelines"],
    queryFn: ({ signal }) => listPipelines(signal),
    refetchInterval: 30_000,
  });
  const hacQ = useQuery({
    queryKey: ["compute-hac"],
    queryFn: ({ signal }) => listHacRuns(signal),
    refetchInterval: 30_000,
  });
  const acsQ = useQuery({
    queryKey: ["compute-acs"],
    queryFn: ({ signal }) => listAcsRuns(signal),
    refetchInterval: tab === "acs" ? 30_000 : false,
    staleTime: 20_000,
  });
  const acsRuns = acsQ.data?.runs ?? [];
  const acsAvailable = acsQ.data?.available !== false;

  // Batch-fetch all run details for analytics
  const allRunIds = statusQ.data?.runs.map((r) => r.run_id) ?? [];
  const detailQueries = useQueries({
    queries: allRunIds.map((rid) => ({
      queryKey: ["compute-run-detail", rid],
      queryFn: ({ signal }: { signal: AbortSignal }) => getComputeRunDetail(rid, signal),
      enabled: tab === "analytics",
      staleTime: 60_000,
    })),
  });

  const runs = statusQ.data?.runs ?? [];
  const activeRuns = runs.filter((r) => r.state === "running" || r.state === "pending");
  const completedRuns = runs.filter((r) => isRunDone(r.state));
  const pipelineCount = statusQ.data?.pipeline_count ?? 0;
  const hacCount = statusQ.data?.hac_count ?? 0;
  const hacRootLoss = hacQ.data?.runs[0]?.root_loss ?? null;

  // Build analytics RunDetail objects
  const allRunDetails: RunDetail[] = detailQueries
    .map((q, i) => {
      const d = q.data;
      const r = runs[i];
      if (!d || !r) return null as unknown as RunDetail;
      const iters = d.iterations ?? [];
      const detail: RunDetail = {
        run_id: r.run_id,
        tool_name: r.tool_name,
        strategy: r.strategy,
        state: r.state,
        best_loss: r.best_loss,
        iterations: r.iterations,
        started_at: r.started_at,
        convergence: r.convergence,
        best_iter: d.summary?.best_iter ?? null,
        iterData: iters,
        firstLoss: iters[0]?.loss ?? null,
        maxIter: d.manifest?.budget?.max_iterations ?? 20,
      };
      return detail;
    })
    .filter((v): v is RunDetail => v != null && typeof v === "object" && "run_id" in v);

  const enabled = true;
  const socketOk = statusQ.data?.worker_socket.reachable ?? false;

  const experiments = experimentsQ.data?.experiments ?? [];

  // Load settings from backend (drive groupBy default, corpus banner visibility)
  const settingsQ = useQuery({
    queryKey: ["compute-settings"],
    queryFn: ({ signal }) => getComputeSettings(signal),
    staleTime: 120_000,
  });
  const computeSettings = settingsQ.data?.settings;
  // Apply default_group_by on first load
  React.useEffect(() => {
    if (computeSettings?.default_group_by && groupBy === "none") {
      setGroupBy(computeSettings.default_group_by as GroupBy);
    }
  }, [computeSettings?.default_group_by, groupBy]);

  const TABS: { key: Tab; label: string; count?: number }[] = [
    { key: "acs", label: "Agent Shell", count: acsRuns.length > 0 ? acsRuns.length : undefined },
    { key: "runs", label: "Runs", count: runs.length },
    { key: "pipelines", label: "Pipelines", count: pipelineCount },
    { key: "experiments", label: "Experiments", count: experiments.length },
    { key: "analytics", label: "Analytics" },
    { key: "hac", label: "HAC", count: hacCount },
    { key: "export", label: "Export Hub" },
  ];

  return (
    <div className="mx-auto max-w-5xl space-y-4">
      {/* Header */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between sm:gap-4">
        <div className="min-w-0">
          <h1 className="font-serif text-3xl font-light tracking-tight">Agentic Compute</h1>
          <p className="mt-1 text-sm text-muted-foreground">Run batch jobs, optimization experiments, and long-running AI tasks.</p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <WorkerStatusBar enabled={enabled} socketOk={socketOk} />
          <Button size="sm" variant="outline" onClick={() => setSettingsOpen(true)}
            className="gap-1.5">
            <Settings className="h-3.5 w-3.5" />Settings
          </Button>
          <Button size="sm" variant="ghost" onClick={() => {
            qc.invalidateQueries({ queryKey: ["compute-status"] });
            qc.invalidateQueries({ queryKey: ["compute-pipelines"] });
            qc.invalidateQueries({ queryKey: ["compute-hac"] });
          }}>
            <RefreshCw className="h-3.5 w-3.5" />
          </Button>
        </div>
      </div>

      {/* M1: Corpus Banner (respects show_corpus_banner setting) */}
      {corpusQ.data?.has_corpus && computeSettings?.show_corpus_banner !== false && (
        <CorpusBanner ctx={corpusQ.data} />
      )}

      {/* KPI Strip */}
      {statusQ.isLoading ? (
        <Skeleton className="h-20 w-full" />
      ) : (
        <ComputeKpiStrip
          runs={runs} pipelineCount={pipelineCount}
          hacCount={hacCount} hacRootLoss={hacRootLoss}
        />
      )}

      {/* System resource bar */}
      {statusQ.data && (
        <SystemResourceBar sys={statusQ.data.system ?? null} />
      )}

      {/* License / quota bar */}
      {licenseQ.data && <ComputeLicenseBar lic={licenseQ.data} />}

      {/* Tabs */}
      <div className="flex overflow-x-auto border-b border-border">
        {TABS.map(({ key, label, count }) => (
          <button key={key}
            className={cn("shrink-0 px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors",
              tab === key ? "border-accent text-foreground" : "border-transparent text-muted-foreground hover:text-foreground")}
            onClick={() => setTab(key)}>
            {label}
            {count != null && count > 0 && (
              <span className={cn("ml-1.5 text-xs px-1.5 py-0.5 rounded-full",
                tab === key ? "bg-accent/20 text-accent-foreground" : "bg-muted text-muted-foreground")}>
                {count}
              </span>
            )}
          </button>
        ))}
      </div>

      {/* ── RUNS ── */}
      {tab === "runs" && (
        <div className="space-y-4">
          {/* Group-by bar */}
          {runs.length > 0 && (
            <GroupByBar value={groupBy} onChange={setGroupBy} />
          )}

          {statusQ.isLoading ? <Skeleton className="h-32 w-full" /> :
            runs.length === 0 ? (
              <Card><CardContent className="py-8 px-6 space-y-4">
                <div className="text-center">
                  <Cpu className="mx-auto mb-2 h-8 w-8 text-muted-foreground/30" />
                  <p className="text-sm font-semibold">No compute runs yet</p>
                  <p className="text-xs text-muted-foreground mt-1">
                    Layer 25 runs grid, random, or Bayesian hyperparameter search out-of-LLM-loop.
                  </p>
                </div>
                <div className="grid gap-2 sm:grid-cols-3">
                  <div className="rounded-lg border border-border bg-muted/40 p-3 space-y-1">
                    <p className="text-xs font-semibold text-foreground flex items-center gap-1">
                      <MessageSquare className="h-3 w-3 flex-shrink-0" /> Via chat
                    </p>
                    <code className="block text-[11px] font-mono text-muted-foreground leading-relaxed">
                      /compute run --strategy bayesian --trials 20
                    </code>
                  </div>
                  <div className="rounded-lg border border-border bg-muted/40 p-3 space-y-1">
                    <p className="text-xs font-semibold text-foreground flex items-center gap-1">
                      <Activity className="h-3 w-3 flex-shrink-0" /> Via pipeline
                    </p>
                    <code className="block text-[11px] font-mono text-muted-foreground leading-relaxed">
                      compute_run in a workflow step
                    </code>
                  </div>
                  <div className="rounded-lg border border-border bg-muted/40 p-3 space-y-1">
                    <p className="text-xs font-semibold text-foreground flex items-center gap-1">
                      <Server className="h-3 w-3 flex-shrink-0" /> Via API
                    </p>
                    <code className="block text-[11px] font-mono text-muted-foreground leading-relaxed">
                      POST /v1/console/compute/runs
                    </code>
                  </div>
                </div>
              </CardContent></Card>
            ) : session && groupBy !== "none" ? (
              <div className="space-y-3">
                {groupRuns(runs, groupBy).map((group) => (
                  <ExperimentGroup
                    key={group.key}
                    group={group}
                    csrf={session.csrf_token}
                    onDeleted={() => qc.invalidateQueries({ queryKey: ["compute-status"] })}
                  />
                ))}
              </div>
            ) : session ? (
              <>
                <RunsSection title="Running" runs={activeRuns} csrf={session.csrf_token}
                  onDeleted={() => qc.invalidateQueries({ queryKey: ["compute-status"] })}
                  defaultExpanded={true} />
                <BatchJobsSection jobs={openBatchJobs} />
                <RunsSection title="Completed" runs={completedRuns} csrf={session.csrf_token}
                  onDeleted={() => qc.invalidateQueries({ queryKey: ["compute-status"] })}
                  defaultExpanded={true} />
              </>
            ) : null}
        </div>
      )}

      {/* ── PIPELINES ── */}
      {tab === "pipelines" && (
        <div className="space-y-4">
          {pipelinesQ.isLoading ? <Skeleton className="h-40 w-full" /> :
            (pipelinesQ.data?.pipelines ?? []).length === 0 ? (
              <Card><CardContent className="py-12 text-center">
                <Activity className="mx-auto mb-2 h-8 w-8 text-muted-foreground/30" />
                <p className="text-sm font-medium">No pipeline runs yet.</p>
                <p className="text-xs text-muted-foreground mt-1">Start a pipeline from chat or voice.</p>
              </CardContent></Card>
            ) : (pipelinesQ.data?.pipelines ?? []).map((p) => <PipelineCard key={p.pipeline_id} pipeline={p} csrf={session?.csrf_token ?? ""} />)}
        </div>
      )}

      {/* ── HAC ── */}
      {tab === "hac" && (
        <div className="space-y-4">
          {hacQ.isLoading ? <Skeleton className="h-40 w-full" /> :
            (hacQ.data?.runs ?? []).length === 0 ? (
              <Card><CardContent className="py-12 text-center">
                <Cpu className="mx-auto mb-2 h-8 w-8 text-muted-foreground/30" />
                <p className="text-sm font-medium">No HAC runs yet.</p>
                <p className="text-xs text-muted-foreground mt-1">HAC coordinates multiple pipelines with adaptive budget allocation.</p>
              </CardContent></Card>
            ) : (hacQ.data?.runs ?? []).map((r) => <HacCard key={r.hac_id} run={r} csrf={session?.csrf_token ?? ""} />)}
        </div>
      )}

      {/* ── ANALYTICS ── */}
      {tab === "analytics" && (
        detailQueries.some((q) => q.isLoading) && allRunDetails.length === 0 ? (
          <div className="space-y-4">
            <Skeleton className="h-32 w-full" />
            <Skeleton className="h-48 w-full" />
          </div>
        ) : <AnalyticsTab allRuns={allRunDetails} />
      )}

      {/* ── M5 + M2: EXPERIMENTS TAB ── */}
      {tab === "experiments" && (
        <div className="space-y-4">
          {/* ROI Panel */}
          {experiments.length > 0 && <ExperimentROIPanel experiments={experiments} runs={runs} />}
          {/* Experiment cards */}
          {experimentsQ.isLoading ? (
            <Skeleton className="h-40 w-full" />
          ) : experiments.length === 0 ? (
            <Card><CardContent className="py-12 text-center">
              <FlaskConical className="mx-auto mb-2 h-8 w-8 text-muted-foreground/30" />
              <p className="text-sm font-medium">No experiments yet.</p>
              <p className="text-xs text-muted-foreground mt-1">Experiments group related runs with a shared hypothesis and comparison table.</p>
            </CardContent></Card>
          ) : (
            experiments.map((exp) => <ExperimentCard key={exp.experiment_id} experiment={exp} />)
          )}
        </div>
      )}

      {/* ── M6: EXPORT HUB ── */}
      {tab === "export" && (
        <ExportHubTab
          experiments={experiments}
          pipelines={pipelinesQ.data?.pipelines ?? []}
          csrf={session?.csrf_token ?? ""}
        />
      )}

      {/* ── ACS — Autonomous Compute Shell ── */}
      {tab === "acs" && (
        <AcsTab
          runs={acsRuns}
          loading={acsQ.isLoading}
          available={acsAvailable}
        />
      )}

      {/* ── COMPUTE JOBS ── */}
      <div className="border-t border-border pt-4">
        <ComputeJobsSection csrf={session?.csrf_token ?? ""} />
      </div>

      {/* Settings drawer */}
      {settingsOpen && (
        <ComputeSettingsPanel
          onClose={() => setSettingsOpen(false)}
          onSaved={(s) => {
            qc.setQueryData(["compute-settings"], { settings: s });
            setGroupBy(s.default_group_by as GroupBy);
          }}
          csrf={session?.csrf_token ?? ""}
        />
      )}
    </div>
  );
}
