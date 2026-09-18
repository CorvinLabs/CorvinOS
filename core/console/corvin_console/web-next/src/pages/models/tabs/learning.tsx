/**
 * Learning tab (ADR-0885 D1/D2/D5/D6).
 *
 * What the selector has learned and from what: shadow classifications (in
 * window) vs. outcome samples (all time), convergence, the per-tier ranking
 * from `rank_models` (rows as the learner stored them — never merged), the
 * operator rating of recent shadow classifications, and ONE reset that clears
 * both learning stores and says so. The learned threshold stays in a collapsed
 * block, labelled as the descriptive statistic it is.
 */
import { useRef, useState } from "react";
import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, Check, Download, Info, Loader2, RotateCcw, ThumbsDown, ThumbsUp, Upload } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useAuth } from "@/lib/auth";
import { ApiError } from "@/lib/api/client";
import { getEngineConfig, getModelSelectionAnalytics } from "@/lib/api/engines";
import {
  getRecentClassifications, getTaskTypeRanking, getThresholdExport, postConfidenceReset,
  postFeedback, postThresholdImport, postThresholdReset, type RecentClassification,
} from "../api";
import { fmtInt } from "../components/engine-parts";
import { COST_STATUS_KEY, useCostOptimizerStatus } from "../hooks/use-cost-status";
import { deriveCost } from "../hooks/use-cost-derived";

const TIERS = ["SIMPLE", "MEDIUM", "COMPLEX"] as const;
const MIN_SAMPLES_TO_RECOMMEND = 5;
const RECENT_KEY = ["analytics-recent"] as const;
const perMillion = (v: number | null) => (v === null ? "—" : `$${(v * 1000).toFixed(2)}`);

export function LearningTab() {
  const qc = useQueryClient();
  const { session } = useAuth();
  const csrf = session?.csrf_token ?? "";

  const statusQ = useCostOptimizerStatus(true);
  const configQ = useQuery({ queryKey: ["engine-config"], queryFn: ({ signal }) => getEngineConfig(signal), staleTime: 5_000 });
  const analyticsQ = useQuery({ queryKey: ["model-selection-analytics"], queryFn: ({ signal }) => getModelSelectionAnalytics(signal), staleTime: 5_000 });
  const rankings = useQueries({
    queries: TIERS.map((t) => ({
      queryKey: ["task-type-ranking", t],
      queryFn: ({ signal }: { signal?: AbortSignal }) => getTaskTypeRanking(t, signal),
      staleTime: 5_000,
    })),
  });
  const recentQ = useQuery({ queryKey: [...RECENT_KEY], queryFn: ({ signal }) => getRecentClassifications(5, signal), staleTime: 10_000 });

  const invalidateLearning = () => {
    qc.invalidateQueries({ queryKey: ["model-selection-analytics"] });
    qc.invalidateQueries({ queryKey: ["task-type-ranking"] });
    qc.invalidateQueries({ queryKey: ["engine-config"] });
    qc.invalidateQueries({ queryKey: [...COST_STATUS_KEY] });
    qc.invalidateQueries({ queryKey: [...RECENT_KEY] });
  };

  // ── feedback ──
  const [rowState, setRowState] = useState<Record<string, string>>({});
  const feedback = useMutation({
    mutationFn: (args: { hash: string; rating: "good" | "poor" }) => postFeedback(args.hash, args.rating, csrf),
    onSuccess: (res) => {
      setRowState((s) => ({ ...s, [res.record_hash]: `recorded — ${res.model} now ${(res.confidence * 100).toFixed(0)}% over ${res.n_samples} samples` }));
      invalidateLearning();
    },
    onError: (err, args) => {
      const status = err instanceof ApiError ? err.status : 0;
      const copy = status === 409 ? "already rated"
        : status === 404 ? "no longer available to rate"
        : status === 503 ? "audit chain unavailable — not recorded"
        : "not recorded";
      setRowState((s) => ({ ...s, [args.hash]: copy }));
    },
  });

  // ── ONE reset: confidence store, then thresholds ──
  const [resetStep, setResetStep] = useState<"idle" | "confirm" | "running" | "half" | "done">("idle");
  const [resetError, setResetError] = useState<string | null>(null);
  const runReset = async (thresholdOnly = false) => {
    setResetStep("running"); setResetError(null);
    try {
      if (!thresholdOnly) await postConfidenceReset(csrf);
    } catch {
      setResetError("The confidence store could not be reset — nothing was changed.");
      setResetStep("idle"); return;
    }
    try {
      await postThresholdReset(csrf);
    } catch {
      setResetError(thresholdOnly
        ? "Threshold reset failed again — the threshold store is unchanged."
        : "Confidence store cleared; threshold reset failed — the threshold store is unchanged.");
      setResetStep("half"); invalidateLearning(); return;
    }
    setResetStep("done"); invalidateLearning();
  };

  // ── export / import ──
  const [exporting, setExporting] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const [ioNote, setIoNote] = useState<string | null>(null);
  const handleExport = async () => {
    setExporting(true); setIoNote(null);
    try {
      const data = await getThresholdExport();
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url; a.download = `model-thresholds-${new Date().toISOString().split("T")[0]}.json`;
      document.body.appendChild(a); a.click(); document.body.removeChild(a);
      URL.revokeObjectURL(url);
    } catch { setIoNote("Export failed."); } finally { setExporting(false); }
  };
  const importMut = useMutation({
    mutationFn: (data: unknown) => postThresholdImport(data, csrf),
    onSuccess: (res) => {
      const n = typeof res?.imported_count === "number" ? res.imported_count : null;
      setIoNote(n === null ? "Imported." : `Imported ${n} threshold${n === 1 ? "" : "s"}.`);
      invalidateLearning();
    },
    onError: () => setIoNote("Import failed — the threshold store is unchanged."),
  });

  const status = statusQ.data;
  const tierRows = status ? deriveCost(status).tierRows : [];
  const converged = status ? `${status.converged_count} / ${status.total_count}` : "—";

  return (
    <div className="space-y-6">
      {/* tiles */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <Card><CardContent className="p-6">
          <p className="text-sm text-muted-foreground">Shadow classifications — in window</p>
          <p className="text-3xl font-bold tabular-nums">{configQ.data ? fmtInt(configQ.data.total_samples) : "—"}</p>
          <p className="text-xs text-muted-foreground mt-2">Every real turn is classified in shadow mode and recorded in the audit chain; counted over the window in the header.</p>
        </CardContent></Card>
        <Card><CardContent className="p-6">
          <p className="text-sm text-muted-foreground">Outcome samples — lifetime</p>
          <p className="text-3xl font-bold tabular-nums">{analyticsQ.data ? fmtInt(analyticsQ.data.total_samples) : "—"}</p>
          <p className="text-xs text-muted-foreground mt-2">
            {analyticsQ.data && analyticsQ.data.total_samples === 0
              ? "Nothing has fed the confidence learner yet. Shown, not hidden."
              : "Samples the confidence learner accepted and chained."}
          </p>
        </CardContent></Card>
        <Card><CardContent className="p-6">
          <p className="text-sm text-muted-foreground">Converged tiers</p>
          <p className="text-3xl font-bold tabular-nums">{converged}</p>
          <p className="text-xs text-muted-foreground mt-2">Cost-variance learner: at least 10 samples and a spread below $0.01 over the last 50.</p>
        </CardContent></Card>
      </div>

      {/* per-tier ranking */}
      <Card>
        <CardHeader>
          <CardTitle>What the selector believes, per tier</CardTitle>
          <CardDescription>
            Learned confidence per (tier, model) from real outcome feedback — Beta posterior with
            EMA smoothing. Published rates are shown beside each row; a rate of “—” means the model
            is not on the rate card, never that it is free.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
            {TIERS.map((t, i) => {
              const q = rankings[i];
              const rows = q.data?.models ?? [];
              return (
                <div key={t} className="rounded-lg border p-4">
                  <div className="flex items-baseline justify-between mb-2">
                    <span className="font-semibold">{t}</span>
                    {q.isLoading && <Loader2 className="w-4 h-4 animate-spin text-muted-foreground" />}
                  </div>
                  {q.isError ? (
                    <p className="text-xs text-destructive">The ranking could not be loaded.</p>
                  ) : rows.length === 0 ? (
                    <p className="text-xs text-muted-foreground">No samples — recommendation withheld.</p>
                  ) : (
                    <div className="space-y-3">
                      {rows.map((r) => (
                        <div key={r.model} className="text-sm">
                          <div className="flex items-baseline justify-between gap-2">
                            <span className="font-mono text-xs break-all">{r.model}</span>
                            <span className="tabular-nums shrink-0">{(r.confidence * 100).toFixed(0)}%</span>
                          </div>
                          <div className="h-1.5 w-full rounded-full bg-muted overflow-hidden mt-1">
                            <div className="h-full rounded-full bg-accent/70" style={{ width: `${Math.round(r.confidence * 100)}%` }} />
                          </div>
                          <div className="text-xs text-muted-foreground mt-1 flex flex-wrap gap-x-2">
                            <span className="tabular-nums">n={r.n_samples} (lifetime)</span>
                            {r.posterior_mean !== null && <span className="tabular-nums">posterior {(r.posterior_mean * 100).toFixed(0)}%</span>}
                            {r.is_converged && <Badge variant="ok" className="font-normal">converged</Badge>}
                            <span className="tabular-nums">{perMillion(r.input_usd_per_1k)} in · {perMillion(r.output_usd_per_1k)} out / 1M</span>
                            {r.n_samples < MIN_SAMPLES_TO_RECOMMEND && (
                              <span>recommendation withheld — fewer than {MIN_SAMPLES_TO_RECOMMEND} samples</span>
                            )}
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
          <p className="text-xs text-muted-foreground mt-4">
            Ids are shown as the learner recorded them; a provider-prefixed id is a separate row.
          </p>
        </CardContent>
      </Card>

      {/* rating */}
      <Card>
        <CardHeader>
          <CardTitle>Rate the last shadow classifications</CardTitle>
          <CardDescription>
            Did the recommended tier fit? Each rating is one learner sample for that (tier, model),
            chained before it is stored; a classification can be rated once. Only the rating and
            the record id travel — never free text.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {recentQ.isLoading ? (
            <Loader2 className="w-5 h-5 animate-spin text-muted-foreground" />
          ) : recentQ.isError ? (
            <p className="text-sm text-destructive">Recent classifications could not be loaded.</p>
          ) : (recentQ.data?.items.length ?? 0) === 0 ? (
            <p className="text-sm text-muted-foreground flex items-start gap-1.5">
              <Info className="w-4 h-4 mt-0.5 shrink-0" /> No shadow classification recorded yet on this tenant.
            </p>
          ) : (
            <div className="space-y-2">
              {recentQ.data!.items.map((c: RecentClassification) => (
                <div key={c.record_hash} className="flex flex-wrap items-center justify-between gap-2 rounded-lg border px-3 py-2 text-sm">
                  <div className="min-w-0">
                    <span className="font-mono text-xs text-muted-foreground">{c.record_hash}</span>
                    <span className="mx-2">·</span>
                    <span className="font-medium">{c.task_type}</span>
                    <span className="mx-2">·</span>
                    <span className="font-mono text-xs break-all">{c.model}</span>
                    <span className="text-muted-foreground"> · {new Date(c.ts * 1000).toLocaleString("en-US")}</span>
                  </div>
                  <div className="flex items-center gap-2">
                    {rowState[c.record_hash] ? (
                      <span className="text-xs text-muted-foreground">{rowState[c.record_hash]}</span>
                    ) : (
                      <>
                        <Button size="sm" variant="outline" disabled={feedback.isPending}
                                aria-label={`Good — ${c.task_type} ${c.record_hash}`}
                                onClick={() => feedback.mutate({ hash: c.record_hash, rating: "good" })}>
                          <ThumbsUp className="w-3.5 h-3.5 mr-1" /> Good
                        </Button>
                        <Button size="sm" variant="outline" disabled={feedback.isPending}
                                aria-label={`Poor — ${c.task_type} ${c.record_hash}`}
                                onClick={() => feedback.mutate({ hash: c.record_hash, rating: "poor" })}>
                          <ThumbsDown className="w-3.5 h-3.5 mr-1" /> Poor
                        </Button>
                      </>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      {/* threshold, demoted */}
      {tierRows.length > 0 && (
        <details className="text-xs">
          <summary className="cursor-pointer text-muted-foreground hover:text-foreground">Observed complexity per tier</summary>
          <div className="mt-3 space-y-1">
            <p className="text-muted-foreground">
              Mean tool-call complexity of the turns that succeeded, per tier. A descriptive measure of
              past traffic — no routing decision reads it, so it does not steer model selection today.
            </p>
            <div className="mt-2 space-y-1">
              {tierRows.map((t) => (
                <div key={t.tier} className="flex justify-between gap-4 tabular-nums">
                  <span className="text-muted-foreground capitalize">{t.tier}</span>
                  <span className="font-mono">{t.learned.toFixed(3)} <span className="text-muted-foreground">({t.turns} {t.turns === 1 ? "sample" : "samples"})</span></span>
                </div>
              ))}
            </div>
          </div>
        </details>
      )}

      {/* operator controls */}
      <Card>
        <CardHeader><CardTitle>Operator controls</CardTitle></CardHeader>
        <CardContent className="space-y-3">
          <div className="flex flex-wrap gap-3 items-center">
            <Button variant="outline" size="sm" onClick={handleExport} disabled={exporting}>
              <Download size={16} className="mr-1.5" /> {exporting ? "Exporting…" : "Export learned state"}
            </Button>
            <Button variant="outline" size="sm" onClick={() => fileInput.current?.click()}>
              <Upload size={16} className="mr-1.5" /> Import
            </Button>
            <input ref={fileInput} type="file" accept=".json" hidden aria-label="Import learned thresholds" onChange={(e) => {
                  const file = e.target.files?.[0];
                  if (!file) return;
                  const reader = new FileReader();
                  reader.onload = (ev) => {
                    try { importMut.mutate(JSON.parse(String(ev.target?.result))); }
                    catch { setIoNote("Import failed — the file is not valid JSON."); }
                  };
                  reader.readAsText(file);
                  e.target.value = "";
                }} />
            {resetStep === "idle" || resetStep === "done" ? (
              <Button size="sm" variant="outline" onClick={() => setResetStep("confirm")}>
                <RotateCcw size={16} className="mr-1.5" /> Reset learning
              </Button>
            ) : resetStep === "confirm" ? (
              <>
                <Button size="sm" variant="destructive" onClick={() => void runReset()}>
                  Confirm: clear the confidence store and the learned thresholds
                </Button>
                <Button size="sm" variant="ghost" onClick={() => setResetStep("idle")}>Cancel</Button>
              </>
            ) : resetStep === "running" ? (
              <Loader2 className="w-4 h-4 animate-spin text-muted-foreground" />
            ) : (
              <>
                <Button size="sm" variant="destructive" onClick={() => void runReset(true)}>Retry threshold reset</Button>
                <Button size="sm" variant="ghost" onClick={() => { setResetStep("idle"); setResetError(null); }}>Cancel</Button>
              </>
            )}
            {resetStep === "done" && <span className="text-xs text-emerald-700 dark:text-emerald-300 flex items-center gap-1"><Check className="w-3.5 h-3.5" /> Both stores cleared.</span>}
          </div>
          {(resetError || ioNote) && (
            <p className="text-xs flex items-start gap-1.5 text-muted-foreground">
              <AlertCircle className="w-3.5 h-3.5 mt-0.5 shrink-0" /> {resetError ?? ioNote}
            </p>
          )}
          <div className="text-xs text-muted-foreground space-y-0.5">
            <p>• <strong>Export:</strong> the learned thresholds as JSON, for backup.</p>
            <p>• <strong>Import:</strong> restore thresholds from such a backup.</p>
            <p>• <strong>Reset learning:</strong> clears the confidence store (per-tier model confidence) and the learned thresholds — both, in that order. The audit chain and the counting window are untouched.</p>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
