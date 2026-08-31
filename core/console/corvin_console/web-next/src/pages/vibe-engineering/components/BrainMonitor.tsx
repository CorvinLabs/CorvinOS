/**
 * Brain Monitor — the subsystem-detail view of the Vibe Engineering group.
 *
 * "Subsystem" here means a CEL pipeline stage: the units the brain actually
 * runs per turn. Everything on this page comes from a real, mounted endpoint —
 * GET /pipeline (configured stages + palette + active flag), GET /grades
 * (accrued operator/auto grades) and GET /traces (the hash-chained Decision
 * Record, which carries per-stage status, duration and source counts). Nothing
 * is synthesised: a metric the runtime does not record is rendered as "—", not
 * as a plausible-looking number.
 *
 * Operator grading lives here too (POST /grades/{stage}). It used to sit in the
 * standalone TreeOfThoughts page; stages are subsystems, so the grader belongs
 * next to the subsystem it grades.
 */
import { useMemo, useState } from "react";
import {
  Loader2, AlertCircle, Cpu, Star, ShieldCheck, Zap, Wrench, CircleSlash,
} from "lucide-react";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  usePipeline, useTraces, useStageGrades, useGradeStage, type Turn,
} from "@/adapters/vibe";

type StageStats = {
  runs: number;
  ok: number;
  failed: number;
  notRun: number;
  deferred: number;
  durMs: number;
  durN: number;
  lastTs?: number;
  sources: number;
};

const EMPTY: StageStats = {
  runs: 0, ok: 0, failed: 0, notRun: 0, deferred: 0,
  durMs: 0, durN: 0, sources: 0,
};

/** Fold every recorded turn into per-stage counters. The Decision Record is the
 *  only durable per-stage telemetry the platform keeps, so it is the source. */
function aggregate(turns: Turn[]): Map<string, StageStats> {
  const m = new Map<string, StageStats>();
  for (const t of turns) {
    for (const s of t.stages) {
      const e = m.get(s.stage) ?? { ...EMPTY };
      e.runs += 1;
      if (s.status === "ok") e.ok += 1;
      else if (s.status === "failed") e.failed += 1;
      else if (s.status === "not_run") e.notRun += 1;
      else e.deferred += 1;
      if (typeof s.duration_ms === "number") {
        e.durMs += s.duration_ms;
        e.durN += 1;
      }
      if (typeof t.ts === "number" && (e.lastTs === undefined || t.ts > e.lastTs)) {
        e.lastTs = t.ts;
      }
      e.sources += s.sources?.length ?? 0;
      m.set(s.stage, e);
    }
  }
  return m;
}

function relTime(ts?: number): string {
  if (!ts) return "—";
  const secs = Math.max(0, Math.floor(Date.now() / 1000 - ts));
  if (secs < 60) return `${secs}s ago`;
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`;
  return `${Math.floor(secs / 86400)}d ago`;
}

function effectIcon(effect: string) {
  if (effect === "egress") return <Zap className="h-3 w-3" />;
  if (effect === "forge") return <Wrench className="h-3 w-3" />;
  return <ShieldCheck className="h-3 w-3" />;
}

function healthDot(st: StageStats) {
  if (st.runs === 0) return <span className="inline-block h-2 w-2 rounded-full bg-slate-500/50" />;
  if (st.failed > 0) return <span className="inline-block h-2 w-2 rounded-full bg-red-500" />;
  if (st.ok === 0) return <span className="inline-block h-2 w-2 rounded-full bg-yellow-500" />;
  return <span className="inline-block h-2 w-2 rounded-full bg-emerald-500" />;
}

function StageGrader({ stage }: { stage: string }) {
  const grade = useGradeStage();
  const [notes, setNotes] = useState("");
  const [open, setOpen] = useState(false);

  if (!open) {
    return (
      <Button variant="ghost" size="sm" className="h-7 text-xs"
              onClick={() => setOpen(true)}>
        <Star className="mr-1 h-3 w-3" /> Grade
      </Button>
    );
  }
  return (
    <div className="flex flex-wrap items-center gap-1">
      <input
        value={notes}
        onChange={(e) => setNotes(e.target.value)}
        placeholder="note (optional)"
        aria-label={`Grading note for ${stage}`}
        className="h-7 w-32 rounded border border-border bg-background px-2 text-xs"
      />
      {[0, 0.25, 0.5, 0.75, 1].map((s) => (
        <Button
          key={s} variant="outline" size="sm" className="h-7 w-9 px-0 text-xs"
          disabled={grade.isPending}
          onClick={() => grade.mutate(
            { stage, score: s, notes },
            { onSuccess: () => { setNotes(""); setOpen(false); } },
          )}
        >
          {s}
        </Button>
      ))}
      <Button variant="ghost" size="sm" className="h-7 text-xs"
              onClick={() => setOpen(false)}>
        cancel
      </Button>
      {grade.isError && (
        <span className="text-xs text-red-500">{String(grade.error)}</span>
      )}
    </div>
  );
}

export function BrainMonitor() {
  const pipeline = usePipeline();
  const traces = useTraces(200);
  const grades = useStageGrades();

  const turns = useMemo(
    () => (traces.data?.sessions ?? []).flatMap((s) => s.turns),
    [traces.data],
  );
  const stats = useMemo(() => aggregate(turns), [turns]);

  if (pipeline.isLoading || traces.isLoading) {
    return (
      <div className="flex h-64 items-center justify-center">
        <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (pipeline.isError) {
    return (
      <div className="flex items-start gap-2 rounded-lg border border-red-500/50 bg-red-500/5 p-4">
        <AlertCircle className="mt-0.5 h-4 w-4 text-red-500" />
        <p className="text-sm text-red-700 dark:text-red-400">
          Pipeline unavailable: {String(pipeline.error)}
        </p>
      </div>
    );
  }

  const available = pipeline.data?.available ?? false;
  const current = pipeline.data?.current ?? [];
  const palette = pipeline.data?.palette ?? [];
  const activeEnabled = pipeline.data?.active_enabled ?? false;
  const configured = new Set(current.map((c) => c.stage));
  const paletteById = new Map(palette.map((p) => [p.id, p]));
  const inactive = palette.filter((p) => !configured.has(p.id));

  return (
    <div className="space-y-6" data-testid="brain-monitor">
      <header className="space-y-1">
        <h1 className="flex items-center gap-2 text-xl font-semibold">
          <Cpu className="h-5 w-5" /> Brain Monitor
        </h1>
        <p className="text-sm text-muted-foreground">
          Every subsystem the brain runs per turn, with the telemetry the
          Decision Record actually records. {turns.length} turn
          {turns.length === 1 ? "" : "s"} analysed.
        </p>
      </header>

      {!available && (
        <div className="rounded-lg border border-yellow-500/50 bg-yellow-500/5 p-3 text-sm">
          The context-engineering stage registry is not loadable on this host, so
          only recorded turns are shown.
        </div>
      )}

      {available && !activeEnabled && (
        <div className="rounded-lg border border-yellow-500/50 bg-yellow-500/5 p-3 text-sm">
          <strong>Active brain off.</strong> Stages with an <code>egress</code> or{" "}
          <code>forge</code> effect are deferred to the post-gate phase and never
          run while <code>vibe_engineering_active</code> is off. Enable it under
          Settings → Features.
        </div>
      )}

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-sm">
            Configured pipeline ({current.length} stage{current.length === 1 ? "" : "s"})
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-2">
          {current.length === 0 && (
            <p className="text-xs italic text-muted-foreground">
              No stages configured for this tenant.
            </p>
          )}
          {current.map((c, i) => {
            const spec = paletteById.get(c.stage);
            const st = stats.get(c.stage) ?? EMPTY;
            const g = grades.data?.grades?.[c.stage];
            const avgMs = st.durN > 0 ? Math.round(st.durMs / st.durN) : null;
            return (
              <div key={`${c.stage}-${i}`}
                   className="rounded-lg border border-border p-3 space-y-2">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="flex items-center gap-2">
                    {healthDot(st)}
                    <span className="font-mono text-sm">{c.stage}</span>
                    {spec && (
                      <Badge variant="outline" className="gap-1 text-xs">
                        {effectIcon(spec.effect)} {spec.effect}
                      </Badge>
                    )}
                    {spec && (
                      <Badge variant="secondary" className="text-xs">{spec.trust}</Badge>
                    )}
                  </div>
                  <StageGrader stage={c.stage} />
                </div>

                <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs sm:grid-cols-3 lg:grid-cols-6">
                  <div><dt className="text-muted-foreground">runs</dt><dd className="font-mono">{st.runs}</dd></div>
                  <div><dt className="text-muted-foreground">ok</dt><dd className="font-mono text-emerald-600">{st.ok}</dd></div>
                  <div><dt className="text-muted-foreground">failed</dt><dd className={`font-mono ${st.failed ? "text-red-600" : ""}`}>{st.failed}</dd></div>
                  <div><dt className="text-muted-foreground">avg ms</dt><dd className="font-mono">{avgMs ?? "—"}</dd></div>
                  <div><dt className="text-muted-foreground">sources</dt><dd className="font-mono">{st.sources}</dd></div>
                  <div><dt className="text-muted-foreground">last run</dt><dd className="font-mono">{relTime(st.lastTs)}</dd></div>
                </dl>

                <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
                  <span>
                    grade{" "}
                    <span className="font-mono text-foreground">
                      {g && g.n_grades > 0 ? g.mean_score.toFixed(2) : "—"}
                    </span>
                    {g ? ` (n=${g.n_grades}${g.promoting !== undefined ? `, promoting=${g.promoting}` : ""})` : ""}
                  </span>
                  {spec && spec.requires.length > 0 && (
                    <span>requires <code>{spec.requires.join(", ")}</code></span>
                  )}
                  {st.notRun > 0 && <span>not_run {st.notRun}</span>}
                  {st.deferred > 0 && <span>deferred {st.deferred}</span>}
                </div>
              </div>
            );
          })}
        </CardContent>
      </Card>

      {inactive.length > 0 && (
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="flex items-center gap-2 text-sm">
              <CircleSlash className="h-4 w-4" />
              Available but not in this pipeline ({inactive.length})
            </CardTitle>
          </CardHeader>
          <CardContent className="flex flex-wrap gap-2">
            {inactive.map((p) => (
              <Badge key={p.id} variant="secondary" className="gap-1 text-xs">
                {effectIcon(p.effect)} {p.id}
              </Badge>
            ))}
          </CardContent>
        </Card>
      )}
    </div>
  );
}

export default BrainMonitor;
