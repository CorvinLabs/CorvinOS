/**
 * Quality Gates (ADR-0688) — what the gate validators decided about the REAL
 * artifacts of the Corvin-ADR checkout (decisions, concepts, implementation
 * plans, ideas), read from the hash-chained gate_events store.
 *
 * Until 2026-09-20 this page fetched `/v1/console/quality/gates/*` — the router
 * is mounted at `/v1/console/api/quality/gates/*` — so every call 404ed and the
 * page showed zeros, "Average: NaN%" and "Trend: Up" over no data; and "Run All
 * Gates" hit a backend that wrote one hard-coded pass per gate. Now:
 *
 *  - every call goes through `api()` (session, CSRF on the run);
 *  - "Run all gates" starts a real job over the checkout and the bar below it
 *    is the job's own progress (artifacts judged / total), polled — not a timer;
 *  - a gate nobody ran shows "—", never 0 %; the trend draws only days that
 *    have events (bars below two points, ADR-0761), and says "no trend yet"
 *    instead of computing a direction from one point;
 *  - failures list the validator's own reason, with the artifact id.
 *
 * Until 2026-09-22 tiles, table and failure list read only the last 24 h, so
 * three days after a run the page showed zeros and "not run" over 2 184
 * recorded verdicts. They now show the CURRENT state — the newest verdict per
 * artifact, dated by its run; the 24 h / 7 d windows stay as columns.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertCircle, AlertTriangle, CheckCircle2, Clock, Loader2, PlayCircle, RefreshCw,
} from "lucide-react";
import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { api, ApiError } from "@/lib/api/client";
import { useAuth } from "@/lib/auth";

// ── Types (the backend's shapes, routes/quality_gates.py) ─────────────────

export interface GateWindow { pass: number; warn: number; fail: number; total: number; pass_percentage: number | null }
export interface GateSummary { current: GateWindow; last_24h: GateWindow; last_7d: GateWindow; last_verdict: string | null; last_timestamp: string | null }
export interface LastRun { run_id: string; status: string; started_at: string; completed_at: string | null; artifacts_total: number; artifacts_done: number; error: string | null }
export interface GatesStatus {
  tenant_id: string; timestamp: string; summary: Record<string, GateSummary>; gates_total: number;
  events_24h: number; events_total: number; as_of: string | null; source_root: string; last_run: LastRun | null;
}
export interface TrendPoint { date: string; pass: number; warn: number; fail: number; total: number; pass_percentage: number | null }
export interface GateFailure { gate_name: string; artifact_id: string; verdict: string; confidence: number; reason: string; timestamp: string; findings_count: number; artifact_type: string }
export interface RunResults {
  run_id: string; status: "running" | "completed" | "failed"; progress: number; artifacts_total: number; artifacts_done: number;
  gates_completed: number; gates_total: number; source_root: string | null; error: string | null;
  gates_results: Array<{ gate_name: string; artifacts: number; pass: number; warn: number; fail: number }>;
}

const BASE = "/api/quality/gates";
const KEY = ["quality", "gates"] as const;

/** Rendered caption — also the deploy marker (a string literal). */
export const MARKER_QUALITY = "What the gate validators decided about the decisions, concepts, plans and ideas of the knowledge repository.";

const GATE_LABEL: Record<string, string> = {
  ADRGate: "ADRs — frontmatter complete",
  ConceptGate: "Concepts — narrative, boundaries, evidence",
  ImplementationPlanGate: "Implementation plans — phases, criteria, estimate",
  IdeaGate: "Ideas — evidence and recurrence",
};

const pct = (v: number | null | undefined) => (v === null || v === undefined ? "—" : `${v.toFixed(1)} %`);

function verdictOf(w: GateWindow): "pass" | "warn" | "fail" | "none" {
  if (w.total === 0) return "none";
  if (w.fail > 0) return "fail";
  if (w.warn > 0) return "warn";
  return "pass";
}

function VerdictBadge({ v }: { v: "pass" | "warn" | "fail" | "none" }) {
  if (v === "none") return <Badge variant="outline">not run</Badge>;
  if (v === "pass") return <Badge variant="ok"><CheckCircle2 className="w-3 h-3 mr-1" /> pass</Badge>;
  if (v === "warn") return <Badge variant="warn"><AlertTriangle className="w-3 h-3 mr-1" /> warn</Badge>;
  return <Badge variant="danger"><AlertCircle className="w-3 h-3 mr-1" /> fail</Badge>;
}

/** Polls a run until it is terminal. */
function useRun(runId: string | null, onDone: (r: RunResults) => void) {
  const q = useQuery({
    queryKey: [...KEY, "run", runId],
    queryFn: ({ signal }) => api<RunResults>(`${BASE}/results/${encodeURIComponent(runId as string)}`, { signal }),
    enabled: runId !== null,
    refetchInterval: (query) => (query.state.data?.status === "running" ? 400 : false),
    retry: false,
  });
  const r = q.data;
  const reported = useRef<string | null>(null);
  useEffect(() => {
    if (r && r.status !== "running" && reported.current !== r.run_id) { reported.current = r.run_id; onDone(r); }
  }, [r, onDone]);
  return r ?? null;
}

export default function QualityGatesPanel() {
  const { session } = useAuth();
  const csrf = session?.csrf_token ?? "";
  const qc = useQueryClient();
  const status = useQuery({ queryKey: [...KEY, "status"], queryFn: ({ signal }) => api<GatesStatus>(`${BASE}/status`, { signal }), refetchInterval: 60_000, retry: false });
  const trend = useQuery({ queryKey: [...KEY, "trend"], queryFn: ({ signal }) => api<{ points: TrendPoint[] }>(`${BASE}/trend?days=14`, { signal }), refetchInterval: 60_000, retry: false });
  const failures = useQuery({ queryKey: [...KEY, "failures"], queryFn: ({ signal }) => api<{ failures: GateFailure[]; total: number }>(`${BASE}/failures?scope=current&limit=50`, { signal }), refetchInterval: 60_000, retry: false });

  const [runId, setRunId] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const invalidate = () => qc.invalidateQueries({ queryKey: [...KEY] });
  const start = useMutation({
    mutationFn: () => api<{ run_id: string; status: string; error?: string }>(`${BASE}/run/all`, { method: "POST", csrf, body: {} }),
    onSuccess: (r) => {
      if (r.status === "failed") { setMsg(`Run not started: ${r.error ?? "unknown reason"}`); return; }
      setMsg(null); setRunId(r.run_id);
    },
    onError: (e) => setMsg(e instanceof ApiError && e.status === 404 ? "Quality gates are not available on this build." : "Run not started."),
  });
  const onDone = useCallback((r: RunResults) => {
    setRunId(null);
    const judged = r.gates_results.reduce((n, g) => n + g.artifacts, 0);
    const failed = r.gates_results.reduce((n, g) => n + g.fail, 0);
    setMsg(r.status === "completed"
      ? `Run ${r.run_id} judged ${judged} artifacts — ${failed} failed. Every verdict is a hash-chained gate event.`
      : `Run ${r.run_id} failed: ${r.error ?? "unknown"}`);
    void qc.invalidateQueries({ queryKey: [...KEY] });
  }, [qc]);
  const run = useRun(runId, onDone);

  if (status.isLoading) {
    return <div className="py-16 flex justify-center"><Loader2 className="w-8 h-8 animate-spin text-muted-foreground" /></div>;
  }
  if (status.isError || !status.data) {
    const off = status.error instanceof ApiError && status.error.status === 404;
    return (
      <div className="max-w-7xl mx-auto p-6">
        <Card className="border-destructive/30 bg-destructive/10"><CardContent className="py-6 flex items-center gap-2 text-destructive text-sm">
          <AlertCircle size={18} /> {off ? "Quality gates are not available on this build." : "The gate status could not be loaded."}
        </CardContent></Card>
      </div>
    );
  }

  const s = status.data;
  const gates = Object.entries(s.summary);
  const judged = gates.reduce((n, [, g]) => n + g.current.total, 0);
  const passed = gates.reduce((n, [, g]) => n + g.current.pass, 0);
  const failed = gates.reduce((n, [, g]) => n + g.current.fail, 0);
  const warned = gates.reduce((n, [, g]) => n + g.current.warn, 0);
  const asOf = s.as_of ? new Date(s.as_of).toLocaleString("en-US") : null;
  const points = (trend.data?.points ?? []).filter((p) => p.pass_percentage !== null);
  const running = run !== null && run.status === "running";

  return (
    <div className="max-w-7xl mx-auto p-6 space-y-6" data-testid="quality-gates">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold">Quality Gates</h1>
          <p className="text-muted-foreground">{MARKER_QUALITY}</p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={invalidate}><RefreshCw className="w-4 h-4" /> Refresh</Button>
          <Button variant="accent" size="sm" disabled={start.isPending || running || !csrf} onClick={() => start.mutate()} data-testid="run-all">
            {start.isPending || running ? <Loader2 className="w-4 h-4 animate-spin" /> : <PlayCircle className="w-4 h-4" />} Run all gates
          </Button>
        </div>
      </div>

      <Card>
        <CardContent className="py-3 text-sm space-y-2">
          <div className="flex flex-wrap items-center gap-x-6 gap-y-1">
            <span><span className="text-muted-foreground">Source:</span> <span className="font-mono text-xs">{s.source_root || "no Corvin-ADR checkout found"}</span></span>
            <span><span className="text-muted-foreground">Events recorded:</span> <span className="font-mono tabular-nums">{s.events_total}</span></span>
            <span data-testid="as-of"><span className="text-muted-foreground">Newest verdict:</span> {asOf ?? "no gate run yet"}</span>
            {s.last_run && (
              <span className="text-xs text-muted-foreground">
                Last run {s.last_run.run_id}: {s.last_run.status}{s.last_run.completed_at ? ` at ${new Date(s.last_run.completed_at).toLocaleTimeString("en-US")}` : ""}
                {s.last_run.error ? ` — ${s.last_run.error}` : ""}
              </span>
            )}
          </div>
          {running && run && (
            <div className="space-y-1" data-testid="run-progress">
              <Progress value={run.progress} />
              <div className="text-xs text-muted-foreground">
                {run.artifacts_total === 0
                  ? "Loading the artifacts of the checkout…"
                  : `Judging ${run.artifacts_done} of ${run.artifacts_total} artifacts · ${run.gates_completed} of ${run.gates_total} gates · ${run.progress}%`}
              </div>
            </div>
          )}
          {msg && <p className="text-xs text-muted-foreground" data-testid="run-msg">{msg}</p>}
        </CardContent>
      </Card>

      {/* KPI tiles — current state: one verdict per artifact and gate */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        {[
          ["Artifacts judged", String(judged)],
          ["Passing", String(passed)],
          ["Warnings", String(warned)],
          ["Failing", String(failed)],
        ].map(([label, value]) => (
          <Card key={label}><CardContent className="pt-6 text-center">
            <div className="text-3xl font-bold tabular-nums">{value}</div>
            <p className="text-xs text-muted-foreground mt-1">{label}</p>
          </CardContent></Card>
        ))}
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Gate status</CardTitle>
          <CardDescription>Per gate: the newest verdict of every artifact{asOf ? ` (newest run ${asOf})` : ""}. The 24 h / 7 d columns count verdicts recorded in that window — two runs judge every artifact twice. A gate nobody ran shows no share.</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="rounded-lg border border-border overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-muted/40 border-b border-border">
                <tr>
                  <th className="text-left px-4 py-3 font-semibold">Gate</th>
                  <th className="text-center px-4 py-3 font-semibold">Status</th>
                  <th className="text-center px-4 py-3 font-semibold">Artifacts</th>
                  <th className="text-center px-4 py-3 font-semibold">Pass</th>
                  <th className="text-center px-4 py-3 font-semibold">Warn</th>
                  <th className="text-center px-4 py-3 font-semibold">Fail</th>
                  <th className="text-right px-4 py-3 font-semibold">Pass share</th>
                  <th className="text-right px-4 py-3 font-semibold">24 h</th>
                  <th className="text-right px-4 py-3 font-semibold">7 d</th>
                </tr>
              </thead>
              <tbody>
                {gates.map(([name, g]) => (
                  <tr key={name} className="border-b border-border last:border-b-0" data-testid={`gate-row-${name}`}>
                    <td className="px-4 py-3"><div className="font-medium">{name}</div><div className="text-xs text-muted-foreground">{GATE_LABEL[name] ?? ""}</div></td>
                    <td className="text-center px-4 py-3"><VerdictBadge v={verdictOf(g.current)} /></td>
                    <td className="text-center px-4 py-3 tabular-nums">{g.current.total}</td>
                    <td className="text-center px-4 py-3 tabular-nums text-emerald-600 dark:text-emerald-400">{g.current.pass}</td>
                    <td className="text-center px-4 py-3 tabular-nums text-amber-600 dark:text-amber-400">{g.current.warn}</td>
                    <td className="text-center px-4 py-3 tabular-nums text-destructive">{g.current.fail}</td>
                    <td className="text-right px-4 py-3 font-semibold tabular-nums">{pct(g.current.pass_percentage)}</td>
                    <td className="text-right px-4 py-3 tabular-nums text-muted-foreground">{pct(g.last_24h.pass_percentage)} <span className="text-xs">of {g.last_24h.total}</span></td>
                    <td className="text-right px-4 py-3 tabular-nums text-muted-foreground">{pct(g.last_7d.pass_percentage)} <span className="text-xs">of {g.last_7d.total}</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Pass share per day</CardTitle>
          <CardDescription data-testid="trend-caption">
            {points.length === 0 ? "No gate run in the last 14 days — nothing to draw."
              : points.length === 1 ? `One day with a run (${points[0].date}, UTC) — shown as a bar, not a trend.`
              : `${points.length} days with runs (UTC days); days without a run are gaps, not 0 %.`}
          </CardDescription>
        </CardHeader>
        {points.length > 0 && (
          <CardContent>
            <div className="w-full h-64">
              <ResponsiveContainer width="100%" height="100%">
                {points.length < 2 ? (
                  <BarChart data={points} margin={{ top: 4, right: 8, bottom: 4, left: 0 }}>
                    <CartesianGrid stroke="var(--viz-grid)" vertical={false} />
                    <XAxis dataKey="date" tick={{ fontSize: 11, fill: "hsl(var(--muted-foreground))" }} stroke="var(--viz-grid)" />
                    <YAxis domain={[0, 100]} tickFormatter={(v) => `${v} %`} tick={{ fontSize: 11, fill: "hsl(var(--muted-foreground))" }} stroke="var(--viz-grid)" width={56} />
                    <Tooltip formatter={(v: number | string) => `${Number(v).toFixed(1)} %`} contentStyle={{ background: "hsl(var(--background))", border: "1px solid hsl(var(--border))", borderRadius: 6, fontSize: 12 }} />
                    <Bar dataKey="pass_percentage" name="pass share" fill="var(--viz-tier-1)" radius={[4, 4, 0, 0]} barSize={26} minPointSize={2} isAnimationActive={false} />
                  </BarChart>
                ) : (
                  <AreaChart data={points} margin={{ top: 4, right: 8, bottom: 4, left: 0 }}>
                    <CartesianGrid stroke="var(--viz-grid)" vertical={false} />
                    <XAxis dataKey="date" tick={{ fontSize: 11, fill: "hsl(var(--muted-foreground))" }} stroke="var(--viz-grid)" />
                    <YAxis domain={[0, 100]} tickFormatter={(v) => `${v} %`} tick={{ fontSize: 11, fill: "hsl(var(--muted-foreground))" }} stroke="var(--viz-grid)" width={56} />
                    <Tooltip formatter={(v: number | string) => `${Number(v).toFixed(1)} %`} contentStyle={{ background: "hsl(var(--background))", border: "1px solid hsl(var(--border))", borderRadius: 6, fontSize: 12 }} />
                    <Area type="monotone" dataKey="pass_percentage" name="pass share" stroke="var(--viz-tier-1)" fill="var(--viz-tier-1)" fillOpacity={0.25} strokeWidth={2} connectNulls={false} isAnimationActive={false} />
                  </AreaChart>
                )}
              </ResponsiveContainer>
            </div>
          </CardContent>
        )}
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Failing and warning artifacts</CardTitle>
          <CardDescription data-testid="failures-caption">
            {failures.isError ? "could not be loaded" : `${failures.data?.total ?? 0} artifacts whose newest verdict is below pass${(failures.data?.total ?? 0) > (failures.data?.failures.length ?? 0) ? ` — newest ${failures.data?.failures.length} shown` : ""}`}
          </CardDescription>
        </CardHeader>
        <CardContent>
          {(failures.data?.failures.length ?? 0) === 0 ? (
            <div className="py-8 text-center text-sm text-muted-foreground">
              {judged === 0 ? "No gate has run yet." : "Every judged artifact passes."}
            </div>
          ) : (
            <div className="space-y-2">
              {failures.data!.failures.map((f) => (
                <div key={`${f.gate_name}:${f.artifact_id}:${f.timestamp}`} className="p-3 rounded-lg border border-border" data-testid="failure-row">
                  <div className="flex flex-wrap items-center gap-2 mb-1">
                    <VerdictBadge v={f.verdict === "warn" ? "warn" : "fail"} />
                    <span className="font-semibold text-sm">{f.gate_name}</span>
                    <span className="text-xs text-muted-foreground">{f.artifact_type ? `${f.artifact_type}: ` : ""}<span className="font-mono">{f.artifact_id}</span></span>
                    <span className="ml-auto text-xs text-muted-foreground flex items-center gap-1"><Clock className="w-3 h-3" />{new Date(f.timestamp).toLocaleString("en-US")}</span>
                  </div>
                  <p className="text-sm">{f.reason}{f.findings_count > 0 ? <span className="text-muted-foreground"> · {f.findings_count} finding{f.findings_count === 1 ? "" : "s"}</span> : null}</p>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      <div className="text-xs text-muted-foreground flex items-center gap-1">
        <Clock className="w-3 h-3" /> Last updated: {new Date(s.timestamp).toLocaleString("en-US")}
      </div>
    </div>
  );
}
