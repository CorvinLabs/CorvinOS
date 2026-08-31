/**
 * AcsTab.tsx — Enterprise ACS (ADR-0104) dashboard for the Compute page.
 *
 * Renders the Autonomous Compute Shell as a selectable second engine
 * alongside the L25 Compute Worker.  Designed for enterprise stakeholders
 * who need full budget transparency, decision audit trails, quality signals,
 * and reproducibility data.
 */
import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Activity, AlertTriangle, Bot, CheckCircle2, ChevronDown, ChevronUp,
  Clock, Copy, Download, FolderOpen, GitBranch, Layers, ListChecks, Loader2,
  Network, Package, Shield, Terminal, TrendingUp, Workflow, Zap,
} from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { ComputeGraphView } from "@/components/ComputeGraphView";
import {
  getAcsRun, exportAcsRun, openAcsRunDir,
  type AcsManifest, type AcsIteration,
  type AcsGateResult, type AcsWorkerResult,
  type AcsRunResult,
  type AcsLossDimensions, type AcsWorkerAttribution,
} from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";

// ── Helpers ──────────────────────────────────────────────────────────────────

function fmtDuration(s: number | null | undefined): string {
  if (s == null || s <= 0) return "—";
  if (s < 60) return `${Math.round(s)}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`;
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
}

function fmtAgo(ts: number | null | undefined): string {
  if (!ts) return "—";
  const diff = Date.now() / 1000 - ts;
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

const GATE_LABELS: Record<string, string> = {
  gate1_length: "Length",
  gate2_near_duplicate: "Dedup",
  gate3_novelty: "Novelty",
  gate4_quality_score: "Quality",
  gate5_goal_alignment: "Alignment",
};

function statusBg(status: string): string {
  if (status === "success") return "bg-emerald-50 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300 border-emerald-200 dark:border-emerald-800";
  if (status === "budget_exhausted") return "bg-amber-50 text-amber-700 dark:bg-amber-950/40 dark:text-amber-300 border-amber-200 dark:border-amber-800";
  return "bg-red-50 text-red-700 dark:bg-red-950/40 dark:text-red-300 border-red-200 dark:border-red-800";
}

function StatusIcon({ status, className }: { status: string; className?: string }) {
  if (status === "success") return <CheckCircle2 className={cn("h-4 w-4 text-emerald-500", className)} />;
  if (status === "budget_exhausted") return <AlertTriangle className={cn("h-4 w-4 text-amber-500", className)} />;
  return <AlertTriangle className={cn("h-4 w-4 text-red-500", className)} />;
}

// ── Open-Dir Button ───────────────────────────────────────────────────────────

function AcsOpenDirButton({ runId, csrf }: { runId: string; csrf: string }) {
  const [state, setState] = React.useState<"idle" | "loading" | "done" | "error">("idle");
  const [path, setPath] = React.useState<string | null>(null);
  const [copied, setCopied] = React.useState(false);

  const handleOpen = async () => {
    setState("loading");
    try {
      const res = await openAcsRunDir(runId, csrf);
      setPath(res.path);
      setState("done");
      setTimeout(() => setState("idle"), 3000);
    } catch {
      setState("error");
      setTimeout(() => setState("idle"), 3000);
    }
  };

  const handleCopy = () => {
    if (!path) return;
    navigator.clipboard.writeText(path).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  };

  return (
    <div className="flex items-center gap-1">
      <button
        onClick={(e) => { e.stopPropagation(); handleOpen(); }}
        disabled={state === "loading"}
        title="Open workspace in file manager"
        className={cn(
          "flex items-center gap-1 rounded px-2 py-0.5 text-xs transition-colors",
          "text-muted-foreground hover:text-foreground hover:bg-muted/60",
          state === "done" && "text-emerald-600 dark:text-emerald-400",
          state === "error" && "text-amber-600",
        )}
      >
        {state === "loading"
          ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
          : <FolderOpen className="h-3.5 w-3.5" />}
        {state === "done" ? "Open" : state === "error" ? "No display" : "Folder"}
      </button>
      {path && (
        <button
          onClick={(e) => { e.stopPropagation(); handleCopy(); }}
          title={copied ? "Kopiert!" : "Pfad kopieren"}
          className="rounded p-0.5 text-muted-foreground hover:text-foreground transition-colors"
        >
          <Copy className={cn("h-3 w-3", copied && "text-emerald-500")} />
        </button>
      )}
    </div>
  );
}

// ── KPI Strip ────────────────────────────────────────────────────────────────

function AcsKpiStrip({ runs }: { runs: AcsManifest[] }) {
  const total = runs.length;
  const successful = runs.filter((r) => r.status === "success").length;
  const successRate = total > 0 ? Math.round((successful / total) * 100) : 0;
  const avgIterations = total > 0
    ? Math.round(runs.reduce((s, r) => s + (r.iterations ?? 0), 0) / total)
    : 0;
  const totalWorkers = runs.reduce((s, r) => s + (r.workers_spawned ?? 0), 0);

  const kpis = [
    { icon: Activity, label: "Total Runs", value: total, sub: `${successful} succeeded` },
    { icon: TrendingUp, label: "Success Rate", value: `${successRate}%`, sub: `${total - successful} failed` },
    { icon: GitBranch, label: "Avg Iterations", value: avgIterations, sub: "per completed run" },
    { icon: Network, label: "Total Workers", value: totalWorkers, sub: "agents dispatched" },
  ];

  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
      {kpis.map(({ icon: Icon, label, value, sub }) => (
        <Card key={label} className="bg-card/60">
          <CardContent className="p-4">
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0">
                <p className="text-xs text-muted-foreground truncate">{label}</p>
                <p className="mt-0.5 text-2xl font-semibold tabular-nums leading-none">{value}</p>
                <p className="mt-1 text-xs text-muted-foreground">{sub}</p>
              </div>
              <Icon className="h-4 w-4 shrink-0 text-muted-foreground/50 mt-0.5" />
            </div>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}

// ── Budget Bar ────────────────────────────────────────────────────────────────

function BudgetBar({
  label, used, max, color = "bg-accent",
}: { label: string; used: number; max: number; color?: string }) {
  const pct = max > 0 ? Math.min(100, Math.round((used / max) * 100)) : 0;
  const overrun = pct >= 90;
  return (
    <div className="flex items-center gap-2 min-w-0">
      <span className="text-xs text-muted-foreground w-16 shrink-0">{label}</span>
      <div className="flex-1 h-1.5 rounded-full bg-muted overflow-hidden">
        <div
          className={cn("h-full rounded-full transition-all", overrun ? "bg-amber-500" : color)}
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="text-xs tabular-nums text-muted-foreground shrink-0 w-16 text-right">
        {used}{max > 0 ? ` / ${max}` : ""}
      </span>
    </div>
  );
}

// ── Decision Timeline ────────────────────────────────────────────────────────

const DECISION_STYLES: Record<string, string> = {
  DELEGATE: "bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300 ring-1 ring-blue-200 dark:ring-blue-700",
  COMPLETE: "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300 ring-1 ring-emerald-200 dark:ring-emerald-700",
  FAIL: "bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300 ring-1 ring-red-200 dark:ring-red-700",
};

function DecisionTimeline({ iterations }: { iterations: AcsIteration[] }) {
  if (iterations.length === 0) return null;
  return (
    <div className="space-y-1.5">
      <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">Decision Timeline</p>
      <div className="flex flex-wrap gap-1.5 items-center">
        {iterations.map((it, idx) => (
          <React.Fragment key={it.iteration}>
            <div
              className={cn(
                "inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-xs font-medium",
                DECISION_STYLES[it.decision?.toUpperCase()] ?? "bg-muted text-muted-foreground ring-1 ring-border",
              )}
              title={`Iteration ${it.iteration}: ${it.decision} (${it.reasoning_len} chars reasoning)`}
            >
              <span className="text-[10px] opacity-60">#{it.iteration}</span>
              {it.decision?.toUpperCase()}
            </div>
            {idx < iterations.length - 1 && (
              <span className="text-muted-foreground/40 text-xs">→</span>
            )}
          </React.Fragment>
        ))}
      </div>
    </div>
  );
}

// ── Gate Chain ────────────────────────────────────────────────────────────────

function GateChainDots({ gateResults }: { gateResults: AcsGateResult[] }) {
  if (gateResults.length === 0) return null;
  const last = gateResults[gateResults.length - 1];
  const GATE_IDS = [
    "gate1_length", "gate2_near_duplicate", "gate3_novelty",
    "gate4_quality_score", "gate5_goal_alignment",
  ];

  const gateMap = new Map(last.gates.map((g) => [g.gate_id, g]));

  return (
    <div className="space-y-1.5">
      <div className="flex items-center gap-2">
        <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">Gate Chain</p>
        <span className={cn("text-xs font-medium", last.passed ? "text-emerald-600 dark:text-emerald-400" : "text-red-500")}>
          {last.passed ? "Passed" : "Rejected"} — score {(last.aggregate_score * 100).toFixed(0)}%
        </span>
        {gateResults.length > 1 && (
          <span className="text-xs text-muted-foreground">({gateResults.length} evaluations)</span>
        )}
      </div>
      <div className="flex gap-2 flex-wrap">
        {GATE_IDS.map((gid) => {
          const g = gateMap.get(gid);
          return (
            <div key={gid} className="flex items-center gap-1.5" title={g?.reason ?? gid}>
              <div className={cn(
                "h-2.5 w-2.5 rounded-full shrink-0",
                !g ? "bg-muted-foreground/20" :
                g.passed ? "bg-emerald-500" : "bg-red-500",
              )} />
              <span className="text-xs text-muted-foreground">
                {GATE_LABELS[gid] ?? gid}
                {g ? ` (${(g.score * 100).toFixed(0)}%)` : ""}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ── Worker Grid ───────────────────────────────────────────────────────────────

function WorkerGrid({ workers }: { workers: AcsWorkerResult[] }) {
  if (workers.length === 0) return null;
  const visible = workers.slice(0, 24);
  const more = workers.length - visible.length;
  return (
    <div className="space-y-1.5">
      <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
        Worker Dispatch <span className="normal-case font-normal">({workers.length} total)</span>
      </p>
      <div className="flex flex-wrap gap-1.5">
        {visible.map((w) => (
          <div
            key={w.worker_id}
            title={`${w.worker_id} · iter ${w.iteration} · depth ${w.depth} · conf ${(w.confidence * 100).toFixed(0)}%`}
            className={cn(
              "inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px] font-mono border",
              w.status === "success" ? "bg-emerald-50 border-emerald-200 text-emerald-700 dark:bg-emerald-950/40 dark:border-emerald-800 dark:text-emerald-300" :
              w.status === "partial"  ? "bg-amber-50 border-amber-200 text-amber-700 dark:bg-amber-950/40 dark:border-amber-800 dark:text-amber-300" :
                                       "bg-red-50 border-red-200 text-red-700 dark:bg-red-950/40 dark:border-red-800 dark:text-red-300",
            )}
          >
            <span className="opacity-70">{w.worker_id.slice(0, 6)}</span>
            <span className="opacity-50">·</span>
            <span>{(w.confidence * 100).toFixed(0)}%</span>
            {w.depth > 0 && <span className="opacity-50">+{w.depth}</span>}
          </div>
        ))}
        {more > 0 && (
          <span className="text-xs text-muted-foreground self-center">+{more} more</span>
        )}
      </div>
    </div>
  );
}

// ── Loss Curve Sparkline ──────────────────────────────────────────────────────

function LossCurveSparkline({ gateResults }: { gateResults: AcsGateResult[] }) {
  const points = gateResults
    .filter((g) => g.loss_total != null)
    .map((g) => ({ iteration: g.iteration, total: g.loss_total as number, passed: g.passed }));
  if (points.length < 1) return null;

  const W = 200, H = 40, PAD = 4;
  const minV = Math.min(0, ...points.map((p) => p.total));
  const maxV = Math.max(1, ...points.map((p) => p.total));
  const scaleX = (i: number) => PAD + (i / Math.max(points.length - 1, 1)) * (W - 2 * PAD);
  const scaleY = (v: number) => H - PAD - ((v - minV) / (maxV - minV + 1e-9)) * (H - 2 * PAD);

  const polyline = points.map((p, i) => `${scaleX(i)},${scaleY(p.total)}`).join(" ");
  const area = `M ${scaleX(0)},${H} ` +
    points.map((p, i) => `L ${scaleX(i)},${scaleY(p.total)}`).join(" ") +
    ` L ${scaleX(points.length - 1)},${H} Z`;

  const latest = points[points.length - 1];
  const delta = points.length >= 2 ? latest.total - points[points.length - 2].total : null;

  return (
    <div className="space-y-1.5">
      <div className="flex items-center gap-2">
        <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">Loss Trajectory</p>
        <span className="text-xs font-semibold tabular-nums">
          L={latest.total.toFixed(3)}
        </span>
        {delta != null && (
          <span className={cn(
            "text-xs tabular-nums",
            delta > 0.005 ? "text-emerald-600 dark:text-emerald-400" :
            delta < -0.005 ? "text-red-500" : "text-muted-foreground",
          )}>
            {delta >= 0 ? "+" : ""}{delta.toFixed(3)}
          </span>
        )}
        <span className="text-xs text-muted-foreground">({points.length} evals)</span>
      </div>
      <svg width={W} height={H} className="overflow-visible">
        <defs>
          <linearGradient id="loss-fill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="hsl(var(--accent))" stopOpacity="0.25" />
            <stop offset="100%" stopColor="hsl(var(--accent))" stopOpacity="0.02" />
          </linearGradient>
        </defs>
        <path d={area} fill="url(#loss-fill)" />
        <polyline
          points={polyline}
          fill="none"
          stroke="hsl(var(--accent))"
          strokeWidth="1.5"
          strokeLinejoin="round"
          strokeLinecap="round"
        />
        {points.map((p, i) => (
          <circle
            key={i}
            cx={scaleX(i)}
            cy={scaleY(p.total)}
            r={i === points.length - 1 ? 2.5 : 1.5}
            fill={p.passed ? "hsl(var(--accent))" : "hsl(var(--destructive))"}
            className="cursor-pointer"
          >
            <title>Iter {p.iteration}: L={p.total.toFixed(3)} {p.passed ? "✓" : "✗"}</title>
          </circle>
        ))}
      </svg>
    </div>
  );
}

// ── Loss Dimensions Bar ───────────────────────────────────────────────────────

const DIM_META: { key: keyof AcsLossDimensions; label: string; color: string }[] = [
  { key: "quality",      label: "Quality",      color: "bg-violet-500" },
  { key: "completeness", label: "Complete",     color: "bg-emerald-500" },
  { key: "metrics",      label: "Metrics",      color: "bg-cyan-500" },
  { key: "novelty",      label: "Novelty",      color: "bg-amber-500" },
  { key: "confidence",   label: "Confidence",   color: "bg-blue-500" },
];

function LossDimensionsBar({ dims }: { dims: AcsLossDimensions }) {
  return (
    <div className="space-y-1.5">
      <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">Loss Dimensions</p>
      <div className="grid grid-cols-5 gap-1.5">
        {DIM_META.map(({ key, label, color }) => {
          const val = dims[key];
          const pct = Math.round(val * 100);
          return (
            <div key={key} className="space-y-1" title={`${label}: ${pct}%`}>
              <div className="h-10 rounded bg-muted overflow-hidden flex flex-col-reverse">
                <div
                  className={cn("w-full rounded transition-all", color)}
                  style={{ height: `${pct}%` }}
                />
              </div>
              <p className="text-[10px] text-center text-muted-foreground truncate">{label}</p>
              <p className="text-[10px] text-center font-semibold tabular-nums">{pct}%</p>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ── Worker Attribution Panel ──────────────────────────────────────────────────

function WorkerAttributionPanel({ attrs }: { attrs: AcsWorkerAttribution[] }) {
  if (attrs.length === 0) return null;
  const maxAbs = Math.max(...attrs.map((a) => Math.abs(a.attribution)), 1e-9);
  return (
    <div className="space-y-1.5">
      <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">Worker Attribution</p>
      <div className="space-y-1">
        {attrs.slice(0, 5).map((w) => {
          const pct = Math.round((Math.abs(w.attribution) / maxAbs) * 100);
          const positive = w.attribution >= 0;
          return (
            <div key={w.worker_id} className="flex items-center gap-2 min-w-0">
              <span className="text-[11px] font-mono text-muted-foreground w-24 shrink-0 truncate">
                {w.worker_id.slice(0, 12)}
              </span>
              <div className="flex-1 h-1.5 rounded-full bg-muted overflow-hidden">
                <div
                  className={cn("h-full rounded-full", positive ? "bg-emerald-500" : "bg-red-500")}
                  style={{ width: `${pct}%` }}
                />
              </div>
              <span className={cn(
                "text-[11px] tabular-nums w-14 text-right shrink-0",
                positive ? "text-emerald-600 dark:text-emerald-400" : "text-red-500",
              )}>
                {positive ? "+" : ""}{w.attribution.toFixed(4)}
              </span>
              <span className={cn(
                "text-[10px] shrink-0 px-1 rounded",
                w.status === "success" ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300" :
                w.status === "partial"  ? "bg-amber-100 text-amber-700 dark:bg-amber-950/40 dark:text-amber-300" :
                                          "bg-red-100 text-red-700 dark:bg-red-950/40 dark:text-red-300",
              )}>
                {w.status}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ── CLI Hint ──────────────────────────────────────────────────────────────────

function CliHint({ manifest }: { manifest: AcsManifest }) {
  const [copied, setCopied] = React.useState(false);
  const cmd = `corvin-workflow run <your-spec.awp.yaml>  # run_id: ${manifest.run_id}`;
  const copy = () => {
    navigator.clipboard.writeText(cmd).catch(() => null);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };
  return (
    <div className="flex items-center gap-2 rounded-md bg-muted/50 px-3 py-2 border border-border">
      <Terminal className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
      <code className="flex-1 text-xs font-mono text-muted-foreground truncate">{cmd}</code>
      <button
        onClick={copy}
        className="shrink-0 text-xs text-muted-foreground hover:text-foreground transition-colors"
      >
        <Copy className={cn("h-3.5 w-3.5", copied ? "text-emerald-500" : "")} />
      </button>
    </div>
  );
}

// ── Run Card Detail ───────────────────────────────────────────────────────────

// ── Export Panel ──────────────────────────────────────────────────────────────

function AcsExportPanel({ runId, csrf }: { runId: string; csrf: string }) {
  const [mode, setMode] = React.useState<"dag" | "template">("dag");
  const [desc, setDesc] = React.useState("");
  const [exporting, setExporting] = React.useState(false);
  const [status, setStatus] = React.useState<{ ok: boolean; msg: string } | null>(null);

  const handleExport = async () => {
    setExporting(true);
    setStatus(null);
    try {
      const blob = await exportAcsRun(runId, mode, desc, csrf);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      const short = runId.slice(0, 8);
      a.href = url;
      a.download = `acs-${mode === "dag" ? "discovered" : "template"}-${short}.awpkg`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
      setStatus({ ok: true, msg: "AWPKG downloaded" });
    } catch (e: unknown) {
      setStatus({ ok: false, msg: e instanceof Error ? e.message : "Export failed" });
    } finally {
      setExporting(false);
    }
  };

  return (
    <div className="rounded-md border border-border bg-muted/30 p-3 space-y-3">
      <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide flex items-center gap-1.5">
        <Package className="h-3.5 w-3.5" />
        Export Discovered Graph as AWPKG
      </p>

      {/* Mode toggle */}
      <div className="grid grid-cols-2 gap-2">
        {(["dag", "template"] as const).map((m) => (
          <button
            key={m}
            onClick={() => setMode(m)}
            className={cn(
              "rounded-md border px-3 py-2 text-left transition-colors",
              mode === m
                ? "border-accent bg-accent/10 text-accent-foreground"
                : "border-border bg-transparent text-muted-foreground hover:text-foreground",
            )}
          >
            <span className="text-xs font-medium block">
              {m === "dag" ? "DAG Replay" : "AWP Template"}
            </span>
            <span className="text-[10px] opacity-70 block mt-0.5">
              {m === "dag"
                ? "Deterministic replay — identical node structure"
                : "Adaptive seed — manager can still deviate"}
            </span>
          </button>
        ))}
      </div>

      {/* Optional description */}
      <input
        type="text"
        placeholder="Optional: describe this workflow…"
        value={desc}
        onChange={(e) => setDesc(e.target.value)}
        maxLength={200}
        className="w-full rounded-md border border-border bg-background px-3 py-1.5 text-xs placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-accent"
      />

      {/* Download button */}
      <button
        onClick={handleExport}
        disabled={exporting}
        className="w-full flex items-center justify-center gap-1.5 rounded-md bg-accent px-3 py-1.5 text-xs font-medium text-accent-foreground hover:bg-accent/90 disabled:opacity-50 transition-colors"
      >
        {exporting
          ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
          : <Download className="h-3.5 w-3.5" />}
        {exporting ? "Building AWPKG…" : "Download AWPKG"}
      </button>

      {status && (
        <p className={cn("text-xs", status.ok ? "text-emerald-600 dark:text-emerald-400" : "text-red-500")}>
          {status.ok ? "✓ " : "✗ "}{status.msg}
        </p>
      )}
    </div>
  );
}

// ── Run Card Detail ───────────────────────────────────────────────────────────

// ── Results Tab Content ───────────────────────────────────────────────────────

function AcsResultsPanel({
  result,
  gate_results,
}: {
  result: AcsRunResult;
  gate_results: AcsGateResult[];
}) {
  const [summaryExpanded, setSummaryExpanded] = React.useState(false);
  const hasSummary = !!result.summary;
  const hasFinalOutput = result.final_output && Object.keys(result.final_output).length > 0;
  const hasGates = gate_results.length > 0;
  const hasAnyContent = hasSummary || hasFinalOutput || hasGates || !!result.error || !!result.budget_breach;

  if (!hasAnyContent) {
    const isActive = result.status === "running" || result.status === "queued";
    return (
      <p className="text-sm text-muted-foreground py-4 text-center">
        {isActive ? "Run in progress…" : "No output data available for this run."}
      </p>
    );
  }

  const GATE_IDS = [
    "gate1_length", "gate2_near_duplicate", "gate3_novelty",
    "gate4_quality_score", "gate5_goal_alignment",
  ];

  return (
    <div className="space-y-5">
      {/* Summary */}
      {hasSummary && (() => {
        const PREVIEW_LEN = 600;
        const isLong = (result.summary?.length ?? 0) > PREVIEW_LEN;
        const displayed = isLong && !summaryExpanded
          ? result.summary.slice(0, PREVIEW_LEN) + "…"
          : result.summary;
        return (
          <div className="space-y-2">
            <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">Summary</p>
            <div className="rounded-lg border border-border bg-muted/30 px-4 py-3">
              <p className="text-sm text-foreground leading-relaxed whitespace-pre-wrap">{displayed}</p>
              {isLong && (
                <button
                  onClick={() => setSummaryExpanded((v) => !v)}
                  className="mt-2 text-xs text-accent hover:underline focus:outline-none"
                >
                  {summaryExpanded ? "Show less" : `Show full summary (${result.summary.length} chars)`}
                </button>
              )}
            </div>
          </div>
        );
      })()}

      {/* Final Output */}
      {hasFinalOutput && (
        <div className="space-y-2">
          <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">Final Output</p>
          <div className="space-y-2">
            {Object.entries(result.final_output).map(([key, val]) => (
              <div key={key} className="rounded-md border border-border bg-card/60 px-3 py-2 space-y-1">
                <p className="text-xs font-medium text-muted-foreground">{key}</p>
                {typeof val === "string" ? (
                  <p className="text-sm text-foreground leading-relaxed whitespace-pre-wrap">{val}</p>
                ) : typeof val === "number" ? (
                  <p className="text-lg font-semibold tabular-nums">{val}</p>
                ) : (
                  <pre className="text-xs font-mono text-foreground/80 overflow-x-auto whitespace-pre-wrap break-all">
                    {JSON.stringify(val, null, 2)}
                  </pre>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Gate Chain Summary — larger, more prominent */}
      {hasGates && (() => {
        const last = gate_results[gate_results.length - 1];
        const gateMap = new Map((last.gates ?? []).map((g) => [g.gate_id, g]));
        return (
          <div className="space-y-2">
            <div className="flex items-center gap-2">
              <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">Gate Chain</p>
              <span className={cn(
                "text-xs font-semibold px-2 py-0.5 rounded-full",
                last.passed
                  ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300"
                  : "bg-red-100 text-red-700 dark:bg-red-950/40 dark:text-red-300",
              )}>
                {last.passed ? "Passed" : "Rejected"} — {(last.aggregate_score * 100).toFixed(0)}%
              </span>
              {gate_results.length > 1 && (
                <span className="text-xs text-muted-foreground">({gate_results.length} evals)</span>
              )}
            </div>
            <div className="grid grid-cols-5 gap-2">
              {GATE_IDS.map((gid) => {
                const g = gateMap.get(gid);
                return (
                  <div key={gid} className="flex flex-col items-center gap-1.5" title={g?.reason ?? gid}>
                    <div className={cn(
                      "h-5 w-5 rounded-full shrink-0 ring-2",
                      !g ? "bg-muted-foreground/20 ring-muted-foreground/10" :
                      g.passed
                        ? "bg-emerald-500 ring-emerald-200 dark:ring-emerald-800"
                        : "bg-red-500 ring-red-200 dark:ring-red-800",
                    )} />
                    <span className="text-[10px] text-center text-muted-foreground leading-tight">
                      {GATE_LABELS[gid] ?? gid}
                    </span>
                    {g && (
                      <span className="text-[10px] font-semibold tabular-nums text-center">
                        {(g.score * 100).toFixed(0)}%
                      </span>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        );
      })()}

      {/* Error */}
      {result.error && (
        <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 dark:border-red-800 dark:bg-red-950/30">
          <p className="text-xs font-medium text-red-700 dark:text-red-300">Error</p>
          <p className="text-xs text-red-600 dark:text-red-400 mt-0.5 font-mono">{result.error}</p>
        </div>
      )}

      {/* Budget breach */}
      {result.budget_breach && (
        <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 dark:border-amber-800 dark:bg-amber-950/30">
          <p className="text-xs font-medium text-amber-700 dark:text-amber-300">Budget Breach</p>
          <p className="text-xs text-amber-600 dark:text-amber-400 mt-0.5">{result.budget_breach}</p>
        </div>
      )}
    </div>
  );
}

function AcsRunCardDetail({ runId, manifest }: { runId: string; manifest: AcsManifest }) {
  const { session } = useAuth();
  const [activeTab, setActiveTab] = React.useState("results");
  const detailQ = useQuery({
    queryKey: ["acs-run-detail", runId],
    queryFn: ({ signal }) => getAcsRun(runId, signal),
    staleTime: 60_000,
  });

  if (detailQ.isLoading) {
    return <Skeleton className="h-32 w-full" />;
  }
  if (detailQ.isError || !detailQ.data) {
    return (
      <p className="text-xs text-muted-foreground">Could not load iteration detail.</p>
    );
  }

  const { result, iterations, gate_results, workers } = detailQ.data;
  const graphExportable = detailQ.data.graph_exportable === true;

  if (!result) {
    return (
      <p className="text-xs text-muted-foreground py-4">
        Result data could not be loaded for this run.
      </p>
    );
  }

  return (
    <div className="space-y-4 pt-2">
      <Tabs defaultValue="results" className="mt-1" onValueChange={(v) => setActiveTab(v)}>
        <TabsList className="h-8">
          <TabsTrigger value="results" className="text-xs h-7 px-3">Results</TabsTrigger>
          <TabsTrigger value="details" className="text-xs h-7 px-3">Details</TabsTrigger>
          <TabsTrigger value="graph" className="text-xs h-7 px-3">Graph</TabsTrigger>
        </TabsList>

        <TabsContent value="results" className="mt-3">
          <AcsResultsPanel result={result} gate_results={gate_results} />
        </TabsContent>

        <TabsContent value="details" className="space-y-4 mt-3">
      {/* Budget bars */}
      <div className="space-y-2">
        <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">Budget Utilization</p>
        <BudgetBar label="Iterations" used={result.iterations ?? 0} max={manifest.max_loops ?? 0} color="bg-blue-500" />
        <BudgetBar label="Workers" used={result.workers_spawned ?? 0} max={manifest.max_workers_per_iteration ?? 0} color="bg-violet-500" />
        {result.elapsed_s ? (
          <BudgetBar label="Wall-time" used={Math.round(result.elapsed_s)} max={manifest.max_wall_time ?? 0} color="bg-cyan-500" />
        ) : null}
      </div>

      {/* Decision Timeline */}
      <DecisionTimeline iterations={iterations} />

      {/* Gate Chain */}
      {gate_results.length > 0 && <GateChainDots gateResults={gate_results} />}

      {/* ADR-0105: Loss Trajectory sparkline */}
      {gate_results.some((g) => g.loss_total != null) && (
        <LossCurveSparkline gateResults={gate_results} />
      )}

      {/* ADR-0105: Loss Dimensions (latest evaluation) */}
      {gate_results.length > 0 &&
        gate_results[gate_results.length - 1].loss_dimensions && (
        <LossDimensionsBar dims={gate_results[gate_results.length - 1].loss_dimensions!} />
      )}

      {/* ADR-0105: Worker Attribution */}
      {gate_results.length > 0 &&
        (gate_results[gate_results.length - 1].worker_attributions?.length ?? 0) > 0 && (
        <WorkerAttributionPanel
          attrs={gate_results[gate_results.length - 1].worker_attributions!}
        />
      )}

      {/* Worker Grid */}
      <WorkerGrid workers={workers} />

      {/* AWPKG Graph Export — only for succeeded runs with subtask data (M9) */}
      {result.status === "success" && graphExportable && session && (
        <AcsExportPanel runId={runId} csrf={session.csrf_token} />
      )}
      {result.status === "success" && !graphExportable && (
        <div className="rounded-md border border-dashed border-border px-3 py-2">
          <p className="text-xs text-muted-foreground flex items-center gap-1.5">
            <Package className="h-3.5 w-3.5 shrink-0" />
            Graph export available for runs started after ACS M9 upgrade.
          </p>
        </div>
      )}

      {/* CLI hint */}
      <CliHint manifest={manifest} />
        </TabsContent>

        <TabsContent value="graph" className="mt-2">
          {activeTab === "graph" && <ComputeGraphView mode="acs" runId={runId} />}
        </TabsContent>
      </Tabs>
    </div>
  );
}

// ── Run Card ──────────────────────────────────────────────────────────────────

function AcsRunCard({ manifest, defaultExpanded = false }: { manifest: AcsManifest; defaultExpanded?: boolean }) {
  const [expanded, setExpanded] = React.useState(defaultExpanded);
  const { session } = useAuth();
  const csrf = session?.csrf_token ?? "";

  const iterPct = manifest.max_loops && manifest.max_loops > 0
    ? Math.min(100, Math.round(((manifest.iterations ?? 0) / manifest.max_loops) * 100))
    : null;

  return (
    <Card className="bg-card/60 hover:bg-card transition-colors">
      <CardContent className="p-4 space-y-3">
        {/* Header row */}
        <div className="flex items-start gap-3">
          <div className="mt-0.5 shrink-0">
            <StatusIcon status={manifest.status} />
          </div>
          <div className="flex-1 min-w-0">
            {/* Workflow ID as primary title */}
            <p className="text-sm font-medium truncate text-foreground/90">
              {manifest.workflow_id || "Unknown workflow"}
            </p>
            <div className="flex items-center gap-2 flex-wrap mt-0.5">
              <span
                className={cn(
                  "inline-flex items-center rounded-md border px-2 py-0.5 text-xs font-medium",
                  statusBg(manifest.status),
                )}
              >
                {manifest.status === "budget_exhausted" ? "Budget Exhausted" :
                 manifest.status.charAt(0).toUpperCase() + manifest.status.slice(1)}
              </span>
              <span className="text-xs text-muted-foreground font-mono truncate">
                {manifest.run_id}
              </span>
            </div>
          </div>
          <button
            onClick={() => setExpanded((v) => !v)}
            className="shrink-0 text-muted-foreground hover:text-foreground transition-colors p-1 -m-1 rounded"
          >
            {expanded
              ? <ChevronUp className="h-4 w-4" />
              : <ChevronDown className="h-4 w-4" />}
          </button>
        </div>

        {/* Stat row */}
        <div className="flex flex-wrap items-center gap-4 text-xs text-muted-foreground">
          <span className="flex items-center gap-1">
            <Clock className="h-3.5 w-3.5" />
            {fmtAgo(manifest.started_at)} · {fmtDuration(manifest.duration_s)}
          </span>
          <span className="flex items-center gap-1">
            <GitBranch className="h-3.5 w-3.5" />
            {manifest.iterations ?? 0} iterations
          </span>
          <span className="flex items-center gap-1">
            <Network className="h-3.5 w-3.5" />
            {manifest.workers_spawned ?? 0} workers
          </span>
          {manifest.budget_breach && (
            <span className="flex items-center gap-1 text-amber-600 dark:text-amber-400">
              <AlertTriangle className="h-3.5 w-3.5" />
              {manifest.budget_breach}
            </span>
          )}
          <AcsOpenDirButton runId={manifest.run_id} csrf={csrf} />
        </div>

        {/* Iteration progress bar (collapsed only) */}
        {!expanded && iterPct !== null && (
          <div className="flex items-center gap-2 min-w-0">
            <span className="text-[10px] text-muted-foreground shrink-0">Progress</span>
            <div className="flex-1 h-1 rounded-full bg-muted overflow-hidden">
              <div
                className={cn(
                  "h-full rounded-full transition-all",
                  iterPct >= 90 ? "bg-amber-500" : "bg-blue-500",
                )}
                style={{ width: `${iterPct}%` }}
              />
            </div>
            <span className="text-[10px] tabular-nums text-muted-foreground shrink-0">
              {manifest.iterations ?? 0}/{manifest.max_loops}
            </span>
          </div>
        )}

        {/* Expanded detail */}
        {expanded && (
          <AcsRunCardDetail runId={manifest.run_id} manifest={manifest} />
        )}
      </CardContent>
    </Card>
  );
}

// ── Empty State ───────────────────────────────────────────────────────────────

function AcsEmptyState() {
  return (
    <Card>
      <CardContent className="py-14 text-center space-y-3">
        <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-muted">
          <Bot className="h-6 w-6 text-muted-foreground/50" />
        </div>
        <div>
          <p className="text-sm font-medium">No ACS runs yet</p>
          <p className="text-xs text-muted-foreground mt-1 max-w-xs mx-auto">
            The Autonomous Compute Shell executes agentic delegation loops.
            Start a run with a <code className="font-mono text-[11px]">.awp.yaml</code> workflow
            specifying <code className="font-mono text-[11px]">orchestration.engine: delegation_loop</code>.
          </p>
        </div>
        <div className="flex items-center justify-center gap-2 pt-1">
          <div className="rounded-md bg-muted/70 px-3 py-2 text-left max-w-sm">
            <div className="flex items-center gap-2 text-muted-foreground mb-1">
              <Terminal className="h-3.5 w-3.5" />
              <span className="text-xs font-medium">Quick Start</span>
            </div>
            <code className="text-[11px] font-mono text-muted-foreground block">
              corvin-workflow run my-workflow.awp.yaml
            </code>
          </div>
        </div>
        <div className="flex flex-wrap justify-center gap-3 pt-2 text-xs text-muted-foreground">
          <div className="flex items-center gap-1.5">
            <Shield className="h-3.5 w-3.5 text-blue-500" />
            L34 data-classification gated
          </div>
          <div className="flex items-center gap-1.5">
            <ListChecks className="h-3.5 w-3.5 text-violet-500" />
            5-gate quality chain
          </div>
          <div className="flex items-center gap-1.5">
            <Layers className="h-3.5 w-3.5 text-cyan-500" />
            Recursive delegation
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

// ── How-it-works explainer ────────────────────────────────────────────────────

function AcsExplainer() {
  return (
    <Card className="bg-muted/30 border-dashed">
      <CardContent className="p-4">
        <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-3">
          How ACS Works
        </p>
        <div className="grid grid-cols-1 sm:grid-cols-5 gap-2 text-xs text-muted-foreground">
          {[
            { icon: Workflow, label: "Spec", desc: "AWP YAML defines workflow, budget, and goal" },
            { icon: Bot, label: "Manager", desc: "LLM decides DELEGATE / COMPLETE / FAIL each loop" },
            { icon: Network, label: "Workers", desc: "Parallel sub-agents execute subtasks" },
            { icon: ListChecks, label: "Gates", desc: "5-gate quality chain validates completions" },
            { icon: Zap, label: "Output", desc: "Accepted result written to run directory" },
          ].map(({ icon: Icon, label, desc }, i) => (
            <div key={label} className="flex items-start gap-2">
              {i > 0 && <span className="hidden sm:block text-muted-foreground/30 text-base self-center">→</span>}
              <div className="flex items-start gap-2 sm:flex-col sm:gap-1">
                <div className="flex items-center gap-1.5">
                  <Icon className="h-3.5 w-3.5 shrink-0 text-muted-foreground/60" />
                  <span className="font-medium text-foreground/70">{label}</span>
                </div>
                <p className="hidden sm:block">{desc}</p>
              </div>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}

// ── Main export ───────────────────────────────────────────────────────────────

export interface AcsTabProps {
  runs: AcsManifest[];
  loading: boolean;
  available?: boolean;
}

export function AcsTab({ runs, loading, available = true }: AcsTabProps) {
  if (loading) {
    return (
      <div className="space-y-3">
        <Skeleton className="h-20 w-full" />
        <Skeleton className="h-40 w-full" />
      </div>
    );
  }

  if (!available) {
    return (
      <Card>
        <CardContent className="py-12 text-center">
          <AlertTriangle className="mx-auto mb-2 h-8 w-8 text-amber-500/50" />
          <p className="text-sm font-medium">ACS engine not available</p>
          <p className="text-xs text-muted-foreground mt-1">
            The ACS runtime is not installed on this CorvinOS instance.
          </p>
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="space-y-4">
      {runs.length > 0 && <AcsKpiStrip runs={runs} />}
      <AcsExplainer />

      {runs.length === 0 ? (
        <AcsEmptyState />
      ) : (
        <div className="space-y-3">
          <p className="text-xs text-muted-foreground">
            {runs.length} run{runs.length !== 1 ? "s" : ""} — click a card to expand full detail
          </p>
          {runs.map((r, i) => (
            <AcsRunCard key={r.run_id} manifest={r} defaultExpanded={i === 0} />
          ))}
        </div>
      )}
    </div>
  );
}
