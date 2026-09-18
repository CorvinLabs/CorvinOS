/**
 * Usage & Cost tab (ADR-0885 D1) — the old Model Cost Optimizer's measured
 * half, unchanged in its rules: two separately measured sources (OS turns,
 * delegated worker runs), never merged into one plot; savings derived from
 * real dollars, never averaged percentages; every total counted over the ONE
 * window shown in the header (ADR-0760/0764); encodings from cost-viz.ts
 * (ADR-0761). "Models routed to" keeps its role dimension, and the audit-chain
 * Model Usage panel keeps its own, named denominators.
 */
import { AlertCircle, CheckCircle, DollarSign, Loader2, TrendingDown } from "lucide-react";
import {
  Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { TIER_VAR, savingLabel, usd } from "@/panels/cost-viz";
import { CoverageMeter, DailySource, ModelCostChart } from "../components/cost-charts";
import { ModelUsagePanel } from "../components/engine-parts";
import { useCostOptimizerStatus } from "../hooks/use-cost-status";
import { MIN_SAMPLES_FOR_ADVICE, deriveCost, modelMixLabel } from "../hooks/use-cost-derived";

export function UsageCostTab() {
  const q = useCostOptimizerStatus(true);
  const status = q.data;

  if (q.isLoading && !status) {
    return <div className="py-16 flex justify-center"><Loader2 className="w-8 h-8 animate-spin text-muted-foreground" /></div>;
  }
  if (!status) {
    return (
      <Card className="border-destructive/30 bg-destructive/10">
        <CardContent className="py-6 flex items-center gap-2 text-destructive text-sm">
          <AlertCircle size={18} /> The cost data could not be loaded.
        </CardContent>
      </Card>
    );
  }

  const d = deriveCost(status);
  const since = status.window?.active && status.window.since_iso
    ? `since ${new Date(status.window.since_iso).toLocaleString("en-US")}`
    : "full history";

  return (
    <div className="space-y-6">
      <p className="text-xs text-muted-foreground">
        Counting window: {since} — the same window as every other tab. Last updated{" "}
        {new Date(status.last_updated).toLocaleTimeString("en-US")}.
      </p>

      {/* KPI tiles */}
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4">
        <Card>
          <CardContent className="p-6">
            <div className="text-muted-foreground text-sm font-semibold mb-2 flex items-center gap-2">
              <DollarSign size={16} className="text-accent" /> OS turns vs. Opus reference
            </div>
            {status.cost_data_available ? (
              <>
                <div className="text-3xl font-bold text-emerald-600 dark:text-emerald-400">
                  {status.cost_savings_percent.toFixed(1)}%
                </div>
                <div className="text-xs text-muted-foreground mt-2">
                  ${status.cost_baseline_usd.toFixed(2)} on Opus → ${status.cost_current_usd.toFixed(2)} actual
                </div>
                <div className="text-xs text-muted-foreground mt-1">
                  {d.modelMixEntries.map(([id, n]) => `${Math.round((n / d.modelMixTotal) * 100)}% ${modelMixLabel(id)}`).join(" · ")}
                </div>
                {!status.acs_data_available && (
                  <div className="text-xs text-muted-foreground mt-1">OS turns only — no worker data recorded</div>
                )}
                {d.costCoverage !== null && (
                  <div className={`mt-1 text-xs ${d.costCoverage < 90 ? "text-amber-600 dark:text-amber-400" : "text-muted-foreground"}`}>
                    Based on {status.cost_counted_turns}/{status.cost_total_turns} turns with token data ({d.costCoverage}%)
                    {d.costCoverage < 90 ? " — real cost is higher" : ""}
                  </div>
                )}
                {d.isSingleModel && (
                  <div className="mt-2 flex items-start gap-1.5 text-xs text-amber-600 dark:text-amber-400">
                    <AlertCircle size={12} className="mt-0.5 shrink-0" />
                    <span>
                      {status.cost_os_model_pin ? (
                        <>Model pinned to <span className="font-mono">{modelMixLabel(status.cost_os_model_pin)}</span> (Routing tab → Turn pins) — no adaptive selection is running. Not a licence or tier limit.</>
                      ) : (
                        <>No model change observed — no adaptive selection is running. Not a licence or tier limit.</>
                      )}
                    </span>
                  </div>
                )}
              </>
            ) : (
              <>
                <div className="text-3xl font-bold text-muted-foreground">—</div>
                <div className="text-xs text-muted-foreground mt-2">No token-usage data yet</div>
              </>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardContent className="p-6">
            <div className="text-muted-foreground text-sm font-semibold mb-2 flex items-center gap-2">
              <DollarSign size={16} className="text-accent" /> Worker engine (delegated runs)
            </div>
            {status.acs_data_available ? (
              <>
                <div className="text-3xl font-bold text-emerald-600 dark:text-emerald-400">
                  {(status.acs_savings_percent ?? 0).toFixed(1)}%
                </div>
                <div className="text-xs text-muted-foreground mt-2">
                  ${status.acs_cost_baseline_usd.toFixed(4)} on Opus → ${status.acs_cost_actual_usd.toFixed(4)} actual
                </div>
                <div className="text-xs text-muted-foreground mt-1">
                  {d.acsMixEntries.map(([id, n]) => `${Math.round((n / d.acsMixTotal) * 100)}% ${modelMixLabel(id)}`).join(" · ")}
                </div>
                {d.acsCoverage !== null && (
                  <div className={`mt-1 text-xs ${d.acsCoverage < 90 ? "text-amber-600 dark:text-amber-400" : "text-muted-foreground"}`}>
                    Based on {status.acs_counted_turns}/{status.acs_total_turns} worker turns with token data ({d.acsCoverage}%)
                  </div>
                )}
                {d.acsMixEntries.length === 1 && (
                  <div className="mt-2 flex items-start gap-1.5 text-xs text-amber-600 dark:text-amber-400">
                    <AlertCircle size={12} className="mt-0.5 shrink-0" />
                    <span>
                      {status.acs_worker_model_pin ? (
                        <>Worker model pinned to <span className="font-mono">{modelMixLabel(status.acs_worker_model_pin)}</span> (Routing tab → Turn pins) — this is a price ratio, not a routing result.</>
                      ) : (
                        <>Only one worker model observed — no model change, so no routing decision is visible.</>
                      )}
                    </span>
                  </div>
                )}
              </>
            ) : (
              <>
                <div className="text-3xl font-bold text-muted-foreground">—</div>
                <div className="text-xs text-muted-foreground mt-2">
                  No delegated worker turn with token data in this window. Not “free” — <strong>not measured</strong>.
                </div>
              </>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardContent className="p-6">
            <div className="text-muted-foreground text-sm font-semibold mb-2 flex items-center gap-2">
              <TrendingDown size={16} className="text-accent" /> Combined (OS + worker)
            </div>
            {status.combined_data_available ? (
              <>
                <div className="text-3xl font-bold text-emerald-600 dark:text-emerald-400">
                  {(status.combined_savings_percent ?? 0).toFixed(1)}%
                </div>
                <div className="text-xs text-muted-foreground mt-2">
                  ${(status.combined_baseline_usd ?? 0).toFixed(2)} on Opus → ${(status.combined_actual_usd ?? 0).toFixed(2)} actual
                </div>
                <div className="text-xs text-muted-foreground mt-1">Sum of both measured sources — not an average of the two percentages.</div>
              </>
            ) : (
              <>
                <div className="text-3xl font-bold text-muted-foreground">—</div>
                <div className="text-xs text-muted-foreground mt-2">
                  Available once both sources have data in this window. A total computed from one half is not a total.
                </div>
              </>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardContent className="p-6">
            <div className="text-muted-foreground text-sm font-semibold mb-2 flex items-center gap-2">
              {status.accuracy_percent >= 85
                ? <CheckCircle size={16} className="text-emerald-600 dark:text-emerald-400" />
                : <AlertCircle size={16} className="text-amber-600 dark:text-amber-400" />}
              Turn success rate
            </div>
            <div className="text-3xl font-bold">{status.accuracy_percent.toFixed(1)}%</div>
            <div className="text-xs text-muted-foreground mt-2">
              Share of turns that finished without an error or timeout — not a content-quality score
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Cost efficiency */}
      <Card>
        <CardHeader>
          <CardTitle>Cost Efficiency</CardTitle>
          <CardDescription>
            Two separately measured sources — OS turns (lightweight orchestration) and worker
            runs (delegated agentic runs with full tool access). Never merged into one plot: they
            differ by orders of magnitude, and on a shared axis the smaller one disappears. “On
            Opus” always means the same real tokens at the Opus rate.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-8">
          <div>
            <div className="flex items-baseline justify-between gap-3 flex-wrap mb-1">
              <h3 className="text-sm font-semibold">Cost per model</h3>
              <span className="text-xs text-muted-foreground">
                Bar length = saving vs. Opus · darker = pricier model · both axes share one scale · USD over priced turns only
              </span>
            </div>
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mt-3">
              <div>
                <div className="text-xs font-medium mb-2">OS turns — over {status.cost_counted_turns ?? 0} priced OS turns</div>
                <ModelCostChart rows={d.osCostRows} domainMax={d.modelDomainMax} emptyNote="No OS turn with token data in this window." />
              </div>
              <div>
                <div className="text-xs font-medium mb-2">Worker runs — over {status.acs_counted_turns ?? 0} priced worker runs</div>
                <ModelCostChart rows={d.workerCostRows} domainMax={d.modelDomainMax} emptyNote="No delegated worker turn with token data in this window — not “free”, not measured." />
              </div>
            </div>
          </div>

          {d.routingRows.length > 0 && (
            <div>
              <div className="flex items-baseline justify-between gap-3 flex-wrap mb-1">
                <h3 className="text-sm font-semibold">Models routed to</h3>
                <span className="text-xs text-muted-foreground">
                  Turns per model by role — counts, not cost (its own axis, its own chart)
                </span>
              </div>
              <ResponsiveContainer width="100%" height={Math.max(130, d.routingRows.length * 42 + 46)}>
                <BarChart data={d.routingRows} layout="vertical" margin={{ top: 4, right: 16, bottom: 4, left: 4 }}>
                  <CartesianGrid horizontal={false} stroke="var(--viz-grid)" />
                  <XAxis type="number" allowDecimals={false} tick={{ fontSize: 11, fill: "hsl(var(--muted-foreground))" }} stroke="var(--viz-grid)" />
                  <YAxis type="category" dataKey="label" width={104} tick={{ fontSize: 12, fill: "hsl(var(--foreground))" }} stroke="var(--viz-grid)" />
                  <Tooltip cursor={{ fill: "hsl(var(--muted) / 0.4)" }}
                           contentStyle={{ background: "hsl(var(--background))", border: "1px solid hsl(var(--border))", borderRadius: 6, fontSize: 12 }} />
                  <Legend wrapperStyle={{ fontSize: 12 }} />
                  <Bar dataKey="os" stackId="r" name="OS turns" fill="var(--viz-role-os)" radius={[4, 0, 0, 4]} barSize={14} isAnimationActive={false} stroke="hsl(var(--card))" strokeWidth={2} />
                  <Bar dataKey="worker" stackId="r" name="Worker runs" fill="var(--viz-role-worker)" radius={[0, 4, 4, 0]} barSize={14} isAnimationActive={false} stroke="hsl(var(--card))" strokeWidth={2} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}

          <div>
            <div className="flex items-baseline justify-between gap-3 flex-wrap mb-1">
              <h3 className="text-sm font-semibold">{d.singleDay ? "Cost on the recorded day" : "Daily trend"}</h3>
              <span className="text-xs text-muted-foreground">
                {d.singleDay
                  ? `${d.dayCount === 0 ? "No day" : "Only one day"} in this window — shown as bars, not as a trend`
                  : `${d.dayCount} days`} · both axes share one scale · a day a source did not price is a gap, not $0
              </span>
            </div>
            {d.dayCount > 0 ? (
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mt-3">
                <DailySource data={status.cost_history} prefix="" noun="turn"
                             color="var(--viz-role-os)" title="OS turns" domainMax={d.dailyDomainMax} />
                {status.acs_data_available ? (
                  <DailySource data={status.cost_history} prefix="acs_" noun="run"
                               color="var(--viz-role-worker)" title="Worker runs" domainMax={d.dailyDomainMax} />
                ) : (
                  <div className="py-10 text-center text-xs text-muted-foreground border border-dashed border-border rounded-lg">
                    No worker data in this window. Deliberately EMPTY rather than a zero line — a flat zero claims delegated runs are free.
                  </div>
                )}
              </div>
            ) : (
              <div className="py-10 text-center text-sm text-muted-foreground border border-dashed border-border rounded-lg">
                No cost data in this window yet.
              </div>
            )}
          </div>

          <div>
            <h3 className="text-sm font-semibold mb-1">Token coverage</h3>
            <p className="text-xs text-muted-foreground mb-3">
              Share of turns whose cost could be computed at all. Whatever is missing is not cheaper — it is unmeasured.
            </p>
            <div className="space-y-2">
              <CoverageMeter label="OS turns" color="var(--viz-role-os)" counted={status.cost_counted_turns ?? 0} total={status.cost_total_turns ?? 0} />
              <CoverageMeter label="Worker runs" color="var(--viz-role-worker)" counted={status.acs_counted_turns ?? 0} total={status.acs_total_turns ?? 0} />
            </div>
          </div>

          {status.cost_data_available && d.lowCoverageDays.length > 0 && (
            <div className="flex items-start gap-2 text-xs text-amber-600 dark:text-amber-400 border border-amber-600/30 dark:border-amber-400/30 rounded-lg px-3 py-2">
              <AlertCircle size={14} className="mt-0.5 shrink-0" />
              <span>
                Low data coverage on {d.lowCoverageDays.length === 1 ? "this day" : "these days"}:{" "}
                {d.lowCoverageDays.map((p) => `${p.date} (${p.counted_turns}/${p.total_turns})`).join(", ")}
                {" "}— the cost there reflects a gap in token recording, not a real trend.
              </span>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Workload by complexity */}
      <Card>
        <CardHeader>
          <CardTitle>Workload by complexity</CardTitle>
          <CardDescription>
            Where the work lands, whether it succeeds there, and what it costs. Tiers are cut at
            the 33rd and 66th percentile of the observed tool-calls-per-turn distribution, so
            they describe this install&rsquo;s own traffic rather than a fixed scale.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-6">
          {d.tierRows.length > 0 ? (
            <>
              <div>
                <div className="flex items-baseline justify-between gap-3 flex-wrap mb-3">
                  <h3 className="text-sm font-semibold">Turns and spend per tier</h3>
                  <span className="text-xs text-muted-foreground">Bar = share of turns · darker = pricier model served the tier</span>
                </div>
                <div className="space-y-4">
                  {d.tierRows.map((t) => (
                    <div key={t.tier}>
                      <div className="flex items-baseline justify-between gap-2 text-sm">
                        <span className="font-medium capitalize">{t.tier}</span>
                        <span className="tabular-nums shrink-0">
                          {t.turns} {t.turns === 1 ? "turn" : "turns"}
                          <span className="text-muted-foreground">
                            {" "}· {t.successPct.toFixed(0)}% ok
                            {t.pricedTurns > 0 && <> · {usd(t.actual)} spent</>}
                          </span>
                        </span>
                      </div>
                      <div className="h-1.5 w-full rounded-full bg-muted overflow-hidden mt-1">
                        <div className="h-full rounded-full" style={{ width: `${t.sharePct}%`, background: TIER_VAR[t.modelTier] }} />
                      </div>
                      <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
                        {t.dominantModel && <Badge variant="secondary" className="font-normal">{modelMixLabel(t.dominantModel)}</Badge>}
                        {t.pricedTurns > 0 ? (
                          <>
                            <span className="tabular-nums">{usd(t.perTurn)}/turn</span>
                            <span className="tabular-nums">{usd(t.baseline)} on Opus</span>
                            {t.savedPct > 0.05 && <span className="tabular-nums">{savingLabel(t.savedPct)} vs. Opus</span>}
                          </>
                        ) : (
                          <span>cost not measured on these turns</span>
                        )}
                        {t.pricedTurns > 0 && t.pricedTurns < t.turns && (
                          <span className="text-amber-600 dark:text-amber-400 tabular-nums">cost from {t.pricedTurns}/{t.turns} turns</span>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
              {!d.cheapestServesHardest && d.hardest && d.hardest.turns < MIN_SAMPLES_FOR_ADVICE && (
                <p className="text-xs text-muted-foreground">
                  Too few turns in this window to say anything about model fit — a success rate over{" "}
                  {d.hardest.turns} {d.hardest.turns === 1 ? "turn" : "turns"} is not evidence.
                </p>
              )}
              {d.cheapestServesHardest && (
                <div className="flex items-start gap-2 text-xs border border-border rounded-lg px-3 py-2">
                  <CheckCircle size={14} className="mt-0.5 shrink-0 text-emerald-600 dark:text-emerald-400" />
                  <span>
                    The most complex tier is already served by{" "}
                    <span className="font-mono">{modelMixLabel(d.cheapestServesHardest.dominantModel)}</span>{" "}
                    at {d.cheapestServesHardest.successPct.toFixed(0)}% success. A more capable model would raise
                    cost without a reliability problem to solve.
                  </span>
                </div>
              )}
            </>
          ) : (
            <div className="py-10 text-center text-sm text-muted-foreground border border-dashed border-border rounded-lg">
              No turns in this window yet.
            </div>
          )}
        </CardContent>
      </Card>

      {/* Audit-chain usage: share of ALL engine spans (OS + worker), by provider and model. */}
      <ModelUsagePanel />
    </div>
  );
}
