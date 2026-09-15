/**
 * Model Cost Optimizer Dashboard — ADR-0377 Phase 2b, renamed ADR-0696
 *
 * Displays:
 * - Convergence status per task type (simple/medium/complex)
 * - Learned threshold values (compared to base 0.5)
 * - Cost efficiency metrics (real token usage x real per-model pricing)
 * - Quality maintenance (accuracy trends)
 * - Operator controls (reset, export/import)
 * - Real-time updates via polling
 */

import React, { useState, useEffect } from 'react';
import {
  BarChart, Bar, Cell, LabelList,
  XAxis, YAxis, CartesianGrid, Tooltip, Legend,
  ResponsiveContainer, Area, AreaChart,
} from 'recharts';
import {
  AlertCircle, CheckCircle, Clock, Download, Upload, RotateCcw, TrendingDown,
  Gauge, DollarSign, Activity, Loader2,
} from 'lucide-react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import {
  shortModel, tierOf, TIER_VAR, usd, savingLabel,
  toModelRows, sharedDomainMax,
  type ModelCostRow,
} from '@/panels/cost-viz';
import { Button } from '@/components/ui/button';

interface ThresholdData {
  task_type: string;
  subsystem: string;
  learned_threshold: number;
  base_threshold: number;
  sample_count: number;
  converged: boolean;
  success_rate: number;
  timestamp: string;
  /** What this tier actually costs. The threshold above is an internal
   *  parameter no live router reads; volume, reliability and spend are what an
   *  operator acts on. Optional so an older backend still type-checks. */
  dominant_model?: string;
  actual_usd?: number;
  baseline_usd?: number;
  /** Turns whose cost was computable — diverges from sample_count when token
   *  counts are missing, and a $ figure without that ratio reads as the whole
   *  bill for the tier. */
  priced_turns?: number;
}

interface CostDayPoint {
  date: string;
  actual_usd: number;
  baseline_usd: number;
  counted_turns: number;
  total_turns: number;
  // ACS-delegated worker spend — a separate cost source (full tool-access
  // agentic runs), never blended into actual_usd/baseline_usd above.
  acs_actual_usd: number;
  acs_baseline_usd: number;
  acs_counted_turns: number;
  acs_total_turns: number;
}

interface DashboardStatus {
  converged_count: number;
  total_count: number;
  thresholds: ThresholdData[];
  cost_savings_percent: number;
  cost_counted_turns?: number;
  cost_total_turns?: number;
  cost_baseline_usd: number;
  cost_current_usd: number;
  cost_data_available: boolean;
  cost_history: CostDayPoint[];
  cost_model_mix: Record<string, number>;
  cost_os_model_pin: string | null;
  acs_cost_actual_usd: number;
  acs_cost_baseline_usd: number;
  acs_model_mix: Record<string, number>;
  // False = no acs.engine_completed event with usable token data exists. The
  // per-day acs_*_usd values are then all 0.0, which must NOT be drawn — a
  // flat zero line reads as "delegated workers are free".
  acs_data_available: boolean;
  // ADR-0760 — the worker half, in the same shape as the OS half above.
  acs_counted_turns?: number;
  acs_total_turns?: number;
  acs_savings_percent?: number;
  acs_worker_model_pin?: string | null;
  /** ADR-0761 — {model_id: {actual_usd, baseline_usd, turns}} per source. */
  cost_model_cost?: Record<string, { actual_usd: number; baseline_usd: number; turns: number }>;
  acs_model_cost?: Record<string, { actual_usd: number; baseline_usd: number; turns: number }>;
  combined_actual_usd?: number;
  combined_baseline_usd?: number;
  combined_savings_percent?: number;
  combined_data_available?: boolean;
  /** Counting window. {active:false} = all-time. Every total on this page is
   *  computed over it, so it is rendered next to them, not in a settings menu. */
  window?: {
    active: boolean;
    epoch_ts: number | null;
    since_iso: string | null;
    reason: string;
  };
  accuracy_percent: number;
  last_updated: string;
}

interface ThresholdHistory {
  timestamp: string;
  task_type: string;
  value: number;
}

/** Per-model cost: real spend vs the same tokens on the reference model.
 *
 *  A floating bar per model, spanning real -> hypothetical. The BAR IS THE
 *  SAVING. A model that already is the reference (Opus) has a zero-length bar
 *  and is direct-labelled "Referenz" — the honest rendering of "saved nothing",
 *  which a grouped two-bar chart would instead show as two equal bars the
 *  reader has to compare by eye. */
const ModelCostChart: React.FC<{
  rows: ModelCostRow[];
  emptyNote: string;
  /** SHARED across both facets. Small multiples with independent scales are the
   *  trap this parameter exists to close: two charts side by side, same unit,
   *  same visual bar length, silently different axes — a reader compares the
   *  pictures and draws a conclusion the numbers do not support. One domain
   *  means a facet that is genuinely small LOOKS small, which is the point. */
  domainMax: number;
}> = ({ rows, emptyNote, domainMax }) => {
  if (rows.length === 0) {
    return (
      <div className="py-10 text-center text-xs text-muted-foreground border border-dashed border-border rounded-lg">
        {emptyNote}
      </div>
    );
  }
  const max = domainMax;
  return (
    <ResponsiveContainer width="100%" height={Math.max(120, rows.length * 46 + 34)}>
      <BarChart data={rows} layout="vertical" margin={{ top: 4, right: 76, bottom: 4, left: 4 }}>
        <CartesianGrid horizontal={false} stroke="var(--viz-grid)" />
        <XAxis
          type="number"
          domain={[0, max * 1.12]}
          tickFormatter={(v) => usd(v as number)}
          tick={{ fontSize: 11, fill: 'hsl(var(--muted-foreground))' }}
          stroke="var(--viz-grid)"
        />
        <YAxis
          type="category"
          dataKey="label"
          width={104}
          tick={{ fontSize: 12, fill: 'hsl(var(--foreground))' }}
          stroke="var(--viz-grid)"
        />
        <Tooltip content={<ModelCostTooltip />} cursor={{ fill: 'hsl(var(--muted) / 0.4)' }} />
        {/* Spacer to the low end — never painted, never in the legend. */}
        <Bar dataKey="lo" stackId="cost" fill="transparent" isAnimationActive={false} />
        {/* minPointSize is load-bearing, not polish. The bar length IS the
            saving, so a model that saved nothing (Opus — it IS the reference)
            has a zero-length bar, and recharts then renders neither the mark
            NOR its LabelList: the row vanished silently from the chart, which
            is the one row an operator most needs to see. 2px keeps the row
            present and anchors the "Referenz" label. */}
        <Bar dataKey="span" stackId="cost" radius={4} isAnimationActive={false}
             barSize={12} minPointSize={2}>
          {rows.map((r) => (
            <Cell key={r.model} fill={TIER_VAR[r.tier]} />
          ))}
          {/* Three outcomes, not two. The pricing table carries families priced
              ABOVE the Opus reference (Fable, Mythos), so savedPct can be
              NEGATIVE — and the earlier two-branch formatter fell through to
              "Referenz" for exactly those, labelling the most expensive turn on
              the install as the neutral baseline. The floating bar has a
              positive length either way; only the sign says which side of the
              reference it sits on, so the sign has to be in the label. */}
          <LabelList
            dataKey="savedPct"
            position="right"
            formatter={(v: number) => savingLabel(v)}
            style={{ fontSize: 11, fill: 'hsl(var(--muted-foreground))' }}
          />
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
};

const ModelCostTooltip = ({ active, payload }: any) => {
  if (!active || !payload?.length) return null;
  const r: ModelCostRow | undefined = payload[0]?.payload;
  if (!r) return null;
  return (
    <div className="rounded-md border border-border bg-background p-3 text-xs shadow-md">
      <div className="font-semibold mb-1">{r.label}</div>
      <div className="flex justify-between gap-4">
        <span className="text-muted-foreground">actual:</span>
        <span className="font-mono tabular-nums">{usd(r.actual)}</span>
      </div>
      <div className="flex justify-between gap-4">
        <span className="text-muted-foreground">on Opus:</span>
        <span className="font-mono tabular-nums">{usd(r.baseline)}</span>
      </div>
      <div className="flex justify-between gap-4 mt-1 pt-1 border-t border-border">
        <span className="text-muted-foreground">
          {r.savedPct < -0.05 ? 'extra cost:' : 'saved:'}
        </span>
        <span className="font-mono tabular-nums">
          {usd(Math.abs(r.baseline - r.actual))} ({Math.abs(r.savedPct).toFixed(1)} %)
        </span>
      </div>
      <div className="text-muted-foreground mt-1">
        {r.turns} {r.turns === 1 ? 'turn' : 'turns'}
      </div>
    </div>
  );
};

/** Token-coverage meter: how much of a source the dollars actually rest on.
 *  A ratio against a limit is a meter, not a chart — and it must be visible,
 *  because a $ total quoted at 39 % coverage reads as the full bill. */
const CoverageMeter: React.FC<{
  counted: number; total: number; label: string; color: string;
}> = ({ counted, total, label, color }) => {
  if (!total) return null;
  const pct = Math.round((counted / total) * 100);
  const low = pct < 90;
  return (
    <div className="flex items-center gap-2 text-xs">
      <span className="text-muted-foreground w-24 shrink-0">{label}</span>
      <div className="h-1.5 flex-1 rounded-full bg-muted overflow-hidden">
        <div
          className="h-full rounded-full"
          style={{
            width: `${pct}%`,
            background: low ? 'hsl(var(--destructive))' : color,
          }}
        />
      </div>
      <span className={`tabular-nums shrink-0 ${low ? 'text-amber-600 dark:text-amber-400' : 'text-muted-foreground'}`}>
        {counted}/{total} ({pct} %)
      </span>
    </div>
  );
};

/** One source's daily cost: real against the same-token Opus counterfactual.
 *
 *  A SEPARATE chart per source, never two sources on one axis. Live measurement
 *  2026-09-15: OS $0.02 against worker $1.99 — on a shared axis the OS series
 *  occupies under 2 % of the range and is invisible, which reads as "the OS
 *  layer is free" rather than "the OS layer is small".
 *
 *  Below two days it draws bars: a single point has nothing to connect, and an
 *  area chart over one day is a trend line asserting a trend nobody measured.
 *
 *  The baseline is a recessive grey, not a second series colour — it is the
 *  thing the real number is measured against, not a peer of it. */
const DailySource: React.FC<{
  data: CostDayPoint[];
  actualKey: string;
  baselineKey: string;
  color: string;
  title: string;
  singleDay: boolean;
  /** Shared across both facets — see ModelCostChart.domainMax. */
  domainMax: number;
}> = ({ data, actualKey, baselineKey, color, title, singleDay, domainMax }) => {
  const tip = (
    <Tooltip
      formatter={(v: any) => usd(v as number)}
      cursor={{ fill: 'hsl(var(--muted) / 0.4)' }}
      contentStyle={{
        background: 'hsl(var(--background))',
        border: '1px solid hsl(var(--border))',
        borderRadius: 6,
        fontSize: 12,
      }}
    />
  );
  const axes = (
    <>
      <CartesianGrid strokeDasharray="0" stroke="var(--viz-grid)" vertical={false} />
      <XAxis dataKey="date" tick={{ fontSize: 11, fill: 'hsl(var(--muted-foreground))' }} stroke="var(--viz-grid)" />
      <YAxis
        domain={[0, domainMax]}
        tickFormatter={(v) => usd(v as number)}
        tick={{ fontSize: 11, fill: 'hsl(var(--muted-foreground))' }}
        stroke="var(--viz-grid)"
        width={72}
      />
    </>
  );
  return (
    <div>
      <div className="flex items-center gap-2 text-xs font-medium mb-2">
        <span className="inline-block w-2.5 h-2.5 rounded-sm" style={{ background: color }} />
        {title}
      </div>
      <ResponsiveContainer width="100%" height={190}>
        {singleDay ? (
          <BarChart data={data} margin={{ top: 4, right: 8, bottom: 4, left: 0 }}>
            {axes}
            {tip}
            <Legend wrapperStyle={{ fontSize: 11 }} />
            <Bar dataKey={baselineKey} name="on Opus" fill="var(--viz-baseline)"
                 radius={[4, 4, 0, 0]} barSize={26} isAnimationActive={false} />
            <Bar dataKey={actualKey} name="actual" fill={color}
                 radius={[4, 4, 0, 0]} barSize={26} isAnimationActive={false} />
          </BarChart>
        ) : (
          <AreaChart data={data} margin={{ top: 4, right: 8, bottom: 4, left: 0 }}>
            {axes}
            {tip}
            <Legend wrapperStyle={{ fontSize: 11 }} />
            <Area type="monotone" dataKey={baselineKey} name="on Opus"
                  stroke="var(--viz-baseline)" fill="var(--viz-baseline)" fillOpacity={0.18}
                  strokeWidth={2} isAnimationActive={false} />
            <Area type="monotone" dataKey={actualKey} name="actual"
                  stroke={color} fill={color} fillOpacity={0.28}
                  strokeWidth={2} isAnimationActive={false} />
          </AreaChart>
        )}
      </ResponsiveContainer>
    </div>
  );
};

export const ModelCostOptimizer: React.FC = () => {
  const [status, setStatus] = useState<DashboardStatus | null>(null);
  const [history, setHistory] = useState<ThresholdHistory[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string>('');
  const [resetConfirm, setResetConfirm] = useState(false);
  const [windowConfirm, setWindowConfirm] = useState(false);
  const [exporting, setExporting] = useState(false);

  // Fetch data on mount and periodic refresh
  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 5000); // Refresh every 5s
    return () => clearInterval(interval);
  }, []);

  const fetchData = async () => {
    try {
      const response = await fetch('/v1/console/learning/model-cost-optimizer/status');
      if (!response.ok) {
        setError(`API error: ${response.status}`);
        setLoading(false);
        return;
      }

      const data = await response.json();
      setStatus(data);
      setError('');

      // Parse threshold history from response
      if (data.history) {
        setHistory(data.history);
      }
    } catch (err) {
      setError(`Failed to fetch learning data: ${err}`);
    } finally {
      setLoading(false);
    }
  };

  const handleReset = async () => {
    if (!resetConfirm) {
      setResetConfirm(true);
      return;
    }

    try {
      const response = await fetch('/v1/console/learning/model-cost-optimizer/reset', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ reason: 'Operator manual reset via console' }),
      });

      if (response.ok) {
        setResetConfirm(false);
        setStatus(null);
        setHistory([]);
        await fetchData();
      } else {
        setError('Failed to reset learning');
      }
    } catch (err) {
      setError(`Reset failed: ${err}`);
    }
  };

  /** ADR-0760 — start counting from now, or drop the window again.
   *
   *  Deliberately NOT called "clear data": it moves one timestamp and touches
   *  no audit record. `clear` restores the full history, which is only possible
   *  because nothing was deleted in the first place. */
  const handleUsageWindow = async (clear: boolean) => {
    if (!clear && !windowConfirm) {
      setWindowConfirm(true);
      return;
    }
    try {
      const response = await fetch('/v1/console/learning/model-cost-optimizer/usage-epoch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          clear,
          reason: clear ? 'Operator cleared the counting window'
                        : 'Operator reset the counting window',
        }),
      });
      if (!response.ok) {
        setError(`Could not set the counting window (HTTP ${response.status})`);
        return;
      }
      setWindowConfirm(false);
      await fetchData();
    } catch (err) {
      setError(`Could not set the counting window: ${err}`);
    }
  };

  const handleExport = async () => {
    setExporting(true);
    try {
      const response = await fetch('/v1/console/learning/model-cost-optimizer/export');
      if (!response.ok) {
        setError('Export failed');
        return;
      }

      const data = await response.json();
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `model-cost-optimizer-thresholds-${new Date().toISOString().split('T')[0]}.json`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    } catch (err) {
      setError(`Export failed: ${err}`);
    } finally {
      setExporting(false);
    }
  };

  if (loading && !status) {
    return (
      <div className="flex items-center justify-center h-full py-24">
        <Loader2 className="w-8 h-8 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (error && !status) {
    return (
      <div className="p-6">
        <Card className="border-destructive/30 bg-destructive/10">
          <CardContent className="py-6 flex items-center gap-2 text-destructive text-sm">
            <AlertCircle size={18} />
            <span>{error}</span>
          </CardContent>
        </Card>
      </div>
    );
  }

  if (!status) {
    return (
      <div className="p-6 text-sm text-muted-foreground">No data available</div>
    );
  }

  // Prepare chart data (simple task types for visualization)
  const convergenceData = status.thresholds.map(t => ({
    name: t.task_type.split('_').pop() || t.task_type,
    learned: t.learned_threshold,
    base: t.base_threshold,
    samples: t.sample_count,
    converged: t.converged ? 1 : 0,
  }));

  const hasThresholds = status.thresholds.length > 0;

  // Model mix — a single-model mix means cost_savings_percent is just that
  // model's fixed price ratio against the baseline model, not evidence of
  // any routing decision (live finding 2026-09-13: this tenant's traffic has
  // been 100% one model since recording began).
  const modelMixEntries = Object.entries(status.cost_model_mix || {}).sort((a, b) => b[1] - a[1]);
  const modelMixTotal = modelMixEntries.reduce((sum, [, n]) => sum + n, 0);
  const modelMixLabel = (id: string) => id.replace(/^claude-/, '').replace(/-\d{8}$/, '');
  const isSingleModel = modelMixEntries.length === 1;

  // Worker model mix — the models the DELEGATED runs actually used. Present in
  // the payload since the worker spans started carrying a model id (ADR-0759)
  // and rendered nowhere until ADR-0760: the panel could draw a worker cost
  // line but could not say which models produced it, while the OS block beside
  // it named every one of its own.
  const acsMixEntries = Object.entries(status.acs_model_mix || {}).sort((a, b) => b[1] - a[1]);
  const acsMixTotal = acsMixEntries.reduce((sum, [, n]) => sum + n, 0);

  const acsCoverage =
    status.acs_total_turns && status.acs_total_turns > 0
      ? Math.round(((status.acs_counted_turns ?? 0) / status.acs_total_turns) * 100)
      : null;

  const win = status.window;

  // Workload per complexity tier: volume, reliability, spend, and which model
  // served it. Ordered cheapest-tier-first so the list reads simple -> complex.
  const TIER_RANK: Record<string, number> = { simple: 0, medium: 1, complex: 2 };
  const tierTurnTotal = status.thresholds.reduce((n, t) => n + t.sample_count, 0);
  const tierRows = status.thresholds
    .map((t) => {
      const priced = t.priced_turns ?? 0;
      const actual = t.actual_usd ?? 0;
      const baseline = t.baseline_usd ?? 0;
      return {
        tier: t.task_type,
        turns: t.sample_count,
        sharePct: tierTurnTotal > 0 ? (t.sample_count / tierTurnTotal) * 100 : 0,
        successPct: t.success_rate * 100,
        dominantModel: t.dominant_model ?? '',
        modelTier: tierOf(t.dominant_model ?? ''),
        actual,
        baseline,
        pricedTurns: priced,
        // Per PRICED turn, not per turn: dividing by turns that carried no
        // token counts would quietly understate the unit cost.
        perTurn: priced > 0 ? actual / priced : 0,
        savedPct: baseline > 0 ? (1 - actual / baseline) * 100 : 0,
        learned: t.learned_threshold,
      };
    })
    .sort((a, b) => (TIER_RANK[a.tier] ?? 9) - (TIER_RANK[b.tier] ?? 9));

  // Only claimed when the data says it: the hardest tier is served by the
  // CHEAPEST model on this install AND succeeds there. Anything less specific
  // would be advice the numbers do not support.
  // A recommendation has to rest on evidence. "100% success" over two turns is
  // one data point wearing a percentage, and a panel that turns it into "you do
  // not need a bigger model" is giving spend advice on noise. Withheld until
  // the tier has a sample worth reading.
  const MIN_SAMPLES_FOR_ADVICE = 25;
  const hardest = tierRows.find((t) => t.tier === 'complex');
  const cheapestObserved = tierRows.length
    ? Math.min(...tierRows.map((t) => t.modelTier))
    : 0;
  const cheapestServesHardest =
    hardest && hardest.turns >= MIN_SAMPLES_FOR_ADVICE && hardest.successPct >= 90 &&
    hardest.modelTier === cheapestObserved && hardest.dominantModel
      ? hardest
      : null;

  const osCostRows = toModelRows(status.cost_model_cost);
  const workerCostRows = toModelRows(status.acs_model_cost);
  // ONE domain for both model facets, 12 % headroom for the direct labels.
  const modelDomainMax = sharedDomainMax([...osCostRows, ...workerCostRows]);

  // ONE domain for both daily facets, same reason.
  const dailyDomainMax = Math.max(
    0.0001,
    ...status.cost_history.flatMap((p) => [
      p.actual_usd, p.baseline_usd, p.acs_actual_usd, p.acs_baseline_usd,
    ]),
  ) * 1.1;

  // Days with any recorded turn. With ONE day a line or area draws no line at
  // all (a single point has nothing to connect), and calling a single day a
  // "trend" is a lie of form — so the daily view switches to bars below 2 days.
  const dayCount = status.cost_history.length;
  const singleDay = dayCount < 2;

  // Routing distribution: turns per model, split by the role that ran them.
  // This is the "which models were actually routed to" question, and it is
  // counts — deliberately a different chart from the dollars above, because
  // mixing a count axis and a dollar axis into one plot is the dual-axis
  // mistake.
  const routingRows = (() => {
    const byModel = new Map<string, { label: string; os: number; worker: number; tier: number }>();
    for (const [model, n] of Object.entries(status.cost_model_mix || {})) {
      const e = byModel.get(model) || { label: shortModel(model), os: 0, worker: 0, tier: tierOf(model) };
      e.os += n;
      byModel.set(model, e);
    }
    for (const [model, n] of Object.entries(status.acs_model_mix || {})) {
      const e = byModel.get(model) || { label: shortModel(model), os: 0, worker: 0, tier: tierOf(model) };
      e.worker += n;
      byModel.set(model, e);
    }
    return [...byModel.values()].sort((a, b) => a.tier - b.tier);
  })();

  // Share of seen turns the cost totals are actually computed from.
  const costCoverage =
    status.cost_total_turns && status.cost_total_turns > 0
      ? Math.round(((status.cost_counted_turns ?? 0) / status.cost_total_turns) * 100)
      : null;

  // Coverage — days where most completed turns had no usable token data
  // (emitter gap, mid-rollout, etc.) look like a cost crash in the raw $
  // numbers alone. Flag them explicitly instead of letting a thin bar pass
  // as a real trend (live incident 2026-09-13).
  const LOW_COVERAGE_THRESHOLD = 0.5;
  const lowCoverageDays = status.cost_history.filter(
    (p) => p.total_turns > 0 && p.counted_turns / p.total_turns < LOW_COVERAGE_THRESHOLD
  );


  return (
    <div className="w-full h-full flex flex-col p-6 bg-background">
      {/* Header */}
      <div className="mb-6">
        <h1 className="text-3xl font-bold flex items-center gap-2">
          <Gauge className="w-7 h-7 text-accent" />
          Model Cost Optimizer
        </h1>
        <p className="text-muted-foreground mt-1">
          Learned thresholds &amp; cost optimization for automatic model routing
        </p>
        <p className="text-xs text-muted-foreground mt-2">
          Last updated: {new Date(status.last_updated).toLocaleTimeString()}
        </p>
      </div>

      {/* ADR-0760 — the period every number below was counted over. Rendered
          before the numbers, not after: a total read first and qualified later
          has already been misread. */}
      <Card className="mb-6">
        <CardContent className="py-3 flex flex-wrap items-center justify-between gap-3 text-sm">
          <div className="flex items-center gap-2">
            <Clock size={15} className="text-muted-foreground shrink-0" />
            {win?.active ? (
              <span>
                Counting since{' '}
                <span className="font-medium">
                  {new Date(win.since_iso as string).toLocaleString()}
                </span>
                <span className="text-muted-foreground">
                  {' '}— OS and worker both start from zero at that point
                </span>
              </span>
            ) : (
              <span className="text-muted-foreground">
                Counting window: full history (no reset set)
              </span>
            )}
          </div>
          <div className="flex items-center gap-2">
            <Button
              size="sm"
              variant={windowConfirm ? 'destructive' : 'outline'}
              onClick={() => handleUsageWindow(false)}
            >
              {windowConfirm ? 'Click again to confirm' : 'Reset counters to now'}
            </Button>
            {windowConfirm && (
              <Button size="sm" variant="ghost" onClick={() => setWindowConfirm(false)}>
                Cancel
              </Button>
            )}
            {win?.active && !windowConfirm && (
              <Button size="sm" variant="ghost" onClick={() => handleUsageWindow(true)}>
                Show full history
              </Button>
            )}
          </div>
          <p className="w-full text-xs text-muted-foreground">
            This only moves the counting window. The audit trail is append-only
            and hash-chained — nothing is deleted, and “Show full history”
            brings every turn back.
          </p>
        </CardContent>
      </Card>

      {/* Error Banner */}
      {error && (
        <Card className="mb-6 border-destructive/30 bg-destructive/10">
          <CardContent className="py-3 flex items-center gap-2 text-destructive text-sm">
            <AlertCircle size={16} />
            <span>{error}</span>
          </CardContent>
        </Card>
      )}

      {/* KPI Cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4 mb-6">
        {/* Convergence Status */}
        <Card>
          <CardContent className="p-6">
            <div className="text-muted-foreground text-sm font-semibold mb-2 flex items-center gap-2">
              {status.converged_count === status.total_count ? (
                <CheckCircle size={16} className="text-emerald-600 dark:text-emerald-400" />
              ) : (
                <Clock size={16} className="text-amber-600 dark:text-amber-400" />
              )}
              Convergence
            </div>
            <div className="text-3xl font-bold">
              {status.converged_count}/{status.total_count}
            </div>
            <div className="text-xs text-muted-foreground mt-2">
              {Math.round((status.converged_count / status.total_count) * 100)}% converged
            </div>
          </CardContent>
        </Card>

        {/* Haiku vs. Opus reference cost — NOT a routing/optimization result */}
        <Card>
          <CardContent className="p-6">
            <div className="text-muted-foreground text-sm font-semibold mb-2 flex items-center gap-2">
              <DollarSign size={16} className="text-accent" />
              OS turns vs. Opus reference
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
                  {modelMixEntries.map(([id, n]) => (
                    `${Math.round((n / modelMixTotal) * 100)}% ${modelMixLabel(id)}`
                  )).join(' · ')}
                </div>
                {/* Scope of the headline number. These $ come from
                    os_turn.completed ONLY — the OS-manager layer. Without a
                    worker series the percentage is not the system's overall
                    saving, and saying so here (not only on the chart below)
                    is what keeps the big green number from overstating. */}
                {!status.acs_data_available && (
                  <div className="text-xs text-muted-foreground mt-1">
                    OS turns only — no worker data recorded
                  </div>
                )}
                {/* The $ figures above rest on the turns that carried token
                    counts, not on every turn. Quoting a total without that
                    ratio reads as the full bill — live 2026-09-15 it was 154
                    of 391 turns (39%), the rest emitted before ADR-0696's
                    token counts existed. */}
                {costCoverage !== null && (
                  <div className={`mt-1 text-xs ${costCoverage < 90 ? 'text-amber-600 dark:text-amber-400' : 'text-muted-foreground'}`}>
                    Based on {status.cost_counted_turns}/{status.cost_total_turns} turns with token data ({costCoverage}%)
                    {costCoverage < 90 ? ' — real cost is higher' : ''}
                  </div>
                )}
                {isSingleModel && (
                  <div className="mt-2 flex items-start gap-1.5 text-xs text-amber-600 dark:text-amber-400">
                    <AlertCircle size={12} className="mt-0.5 shrink-0" />
                    <span>
                      {status.cost_os_model_pin ? (
                        <>Model pinned to <span className="font-mono">{modelMixLabel(status.cost_os_model_pin)}</span> (Engine Config → OS model) — no adaptive selection is running. Not a licence or tier limit.</>
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
                <div className="text-xs text-muted-foreground mt-2">
                  No token-usage data yet
                </div>
              </>
            )}
          </CardContent>
        </Card>

        {/* Delegated WORKER spend. Its own card, never folded into the OS
            number: two independently measured sources, different engines,
            different models, different budgets. The OS layer is the cheap
            orchestration; this is the substantive work. */}
        <Card>
          <CardContent className="p-6">
            <div className="text-muted-foreground text-sm font-semibold mb-2 flex items-center gap-2">
              <DollarSign size={16} className="text-accent" />
              Worker engine (delegated runs)
            </div>
            {status.acs_data_available ? (
              <>
                <div className="text-3xl font-bold text-emerald-600 dark:text-emerald-400">
                  {(status.acs_savings_percent ?? 0).toFixed(1)}%
                </div>
                <div className="text-xs text-muted-foreground mt-2">
                  ${status.acs_cost_baseline_usd.toFixed(4)} on Opus → $
                  {status.acs_cost_actual_usd.toFixed(4)} actual
                </div>
                <div className="text-xs text-muted-foreground mt-1">
                  {acsMixEntries.map(([id, n]) => (
                    `${Math.round((n / acsMixTotal) * 100)}% ${modelMixLabel(id)}`
                  )).join(' · ')}
                </div>
                {acsCoverage !== null && (
                  <div className={`mt-1 text-xs ${acsCoverage < 90 ? 'text-amber-600 dark:text-amber-400' : 'text-muted-foreground'}`}>
                    Based on {status.acs_counted_turns}/{status.acs_total_turns} worker turns with token data ({acsCoverage}%)
                  </div>
                )}
                {acsMixEntries.length === 1 && (
                  <div className="mt-2 flex items-start gap-1.5 text-xs text-amber-600 dark:text-amber-400">
                    <AlertCircle size={12} className="mt-0.5 shrink-0" />
                    <span>
                      {status.acs_worker_model_pin ? (
                        <>Worker model pinned to <span className="font-mono">{modelMixLabel(status.acs_worker_model_pin)}</span> (Engine Config → Worker model) — this is a price ratio, not a routing result.</>
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
                  No delegated worker turn with token data in this window.
                  Not “free” — <strong>not measured</strong>.
                </div>
              </>
            )}
          </CardContent>
        </Card>

        {/* OS + worker. Derived from the two REAL dollar totals, never from
            averaging the two percentages above — that would weight a 3-turn
            worker series like a 500-turn OS one and produce a number that
            describes no traffic that ever ran. */}
        <Card>
          <CardContent className="p-6">
            <div className="text-muted-foreground text-sm font-semibold mb-2 flex items-center gap-2">
              <TrendingDown size={16} className="text-accent" />
              Combined (OS + worker)
            </div>
            {status.combined_data_available ? (
              <>
                <div className="text-3xl font-bold text-emerald-600 dark:text-emerald-400">
                  {(status.combined_savings_percent ?? 0).toFixed(1)}%
                </div>
                <div className="text-xs text-muted-foreground mt-2">
                  ${(status.combined_baseline_usd ?? 0).toFixed(2)} on Opus → $
                  {(status.combined_actual_usd ?? 0).toFixed(2)} actual
                </div>
                <div className="text-xs text-muted-foreground mt-1">
                  Sum of both measured sources — not an average of the two
                  percentages.
                </div>
              </>
            ) : (
              <>
                <div className="text-3xl font-bold text-muted-foreground">—</div>
                <div className="text-xs text-muted-foreground mt-2">
                  Available once both sources have data in this window. A
                  total computed from one half is not a total.
                </div>
              </>
            )}
          </CardContent>
        </Card>

        {/* Turn success rate — completion reliability, NOT content quality */}
        <Card>
          <CardContent className="p-6">
            <div className="text-muted-foreground text-sm font-semibold mb-2 flex items-center gap-2">
              {status.accuracy_percent >= 85 ? (
                <CheckCircle size={16} className="text-emerald-600 dark:text-emerald-400" />
              ) : (
                <AlertCircle size={16} className="text-amber-600 dark:text-amber-400" />
              )}
              Turn success rate
            </div>
            <div className="text-3xl font-bold">
              {status.accuracy_percent.toFixed(1)}%
            </div>
            <div className="text-xs text-muted-foreground mt-2">
              Share of turns that finished without an error or timeout — not a content-quality score
            </div>
          </CardContent>
        </Card>

        {/* Last Update */}
        <Card>
          <CardContent className="p-6">
            <div className="text-muted-foreground text-sm font-semibold mb-2 flex items-center gap-2">
              <Activity size={16} className="text-accent" />
              Status
            </div>
            <div className="text-sm font-mono">
              <Badge variant={hasThresholds ? 'ok' : 'secondary'}>
                {hasThresholds ? 'Active' : 'Idle'}
              </Badge>
            </div>
            <div className="text-xs text-muted-foreground mt-2">
              {status.thresholds.length} task types tracked
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Cost Efficiency — deliberately ABOVE the threshold chart: real money
          spent is the headline, the learned routing threshold is the mechanism
          that produced it. */}
      <Card className="mb-6">
        <CardHeader>
          <CardTitle>Cost Efficiency</CardTitle>
          <CardDescription>
            Two separately measured sources — OS turns (lightweight
            orchestration) and worker runs (delegated agentic runs with full tool
            access). Never merged into one plot: they differ by orders of
            magnitude, and on a shared axis the smaller one disappears. “On Opus”
            always means the same real tokens at the Opus rate.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-8">

          {/* ── 1. Per model: what it cost vs what Opus would have cost ──── */}
          <div>
            <div className="flex items-baseline justify-between gap-3 flex-wrap mb-1">
              <h3 className="text-sm font-semibold">Cost per model</h3>
              <span className="text-xs text-muted-foreground">
                Bar length = saving vs. Opus · darker = pricier model · both axes share one scale
              </span>
            </div>
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mt-3">
              <div>
                {/* No role swatch here on purpose: in THIS chart the bars encode
                    model tier, and a second colour system beside them (teal dot
                    over amber bars) invites the reader to map one onto the
                    other. The heading is unambiguous on its own. */}
                <div className="text-xs font-medium mb-2">
                  OS turns
                </div>
                <ModelCostChart
                  rows={osCostRows}
                  domainMax={modelDomainMax}
                  emptyNote="No OS turn with token data in this window."
                />
              </div>
              <div>
                <div className="text-xs font-medium mb-2">
                  Worker runs
                </div>
                <ModelCostChart
                  rows={workerCostRows}
                  domainMax={modelDomainMax}
                  emptyNote="No delegated worker turn with token data in this window — not “free”, not measured."
                />
              </div>
            </div>
          </div>

          {/* ── 2. Which models were routed to, and by which role ────────── */}
          {routingRows.length > 0 && (
            <div>
              <div className="flex items-baseline justify-between gap-3 flex-wrap mb-1">
                <h3 className="text-sm font-semibold">Models routed to</h3>
                <span className="text-xs text-muted-foreground">
                  Turns per model — counts, not cost (its own axis, its own chart)
                </span>
              </div>
              <ResponsiveContainer width="100%" height={Math.max(130, routingRows.length * 42 + 46)}>
                <BarChart data={routingRows} layout="vertical" margin={{ top: 4, right: 16, bottom: 4, left: 4 }}>
                  <CartesianGrid horizontal={false} stroke="var(--viz-grid)" />
                  <XAxis
                    type="number"
                    allowDecimals={false}
                    tick={{ fontSize: 11, fill: 'hsl(var(--muted-foreground))' }}
                    stroke="var(--viz-grid)"
                  />
                  <YAxis
                    type="category"
                    dataKey="label"
                    width={104}
                    tick={{ fontSize: 12, fill: 'hsl(var(--foreground))' }}
                    stroke="var(--viz-grid)"
                  />
                  <Tooltip
                    cursor={{ fill: 'hsl(var(--muted) / 0.4)' }}
                    contentStyle={{
                      background: 'hsl(var(--background))',
                      border: '1px solid hsl(var(--border))',
                      borderRadius: 6,
                      fontSize: 12,
                    }}
                  />
                  <Legend wrapperStyle={{ fontSize: 12 }} />
                  {/* A 2px surface gap between the two stacked segments, not a
                      border around them. */}
                  <Bar dataKey="os" stackId="r" name="OS turns" fill="var(--viz-role-os)"
                       radius={[4, 0, 0, 4]} barSize={14} isAnimationActive={false}
                       stroke="hsl(var(--card))" strokeWidth={2} />
                  <Bar dataKey="worker" stackId="r" name="Worker runs" fill="var(--viz-role-worker)"
                       radius={[0, 4, 4, 0]} barSize={14} isAnimationActive={false}
                       stroke="hsl(var(--card))" strokeWidth={2} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}

          {/* ── 3. Per day, as small multiples — never one shared axis ───── */}
          <div>
            <div className="flex items-baseline justify-between gap-3 flex-wrap mb-1">
              <h3 className="text-sm font-semibold">
                {singleDay ? 'Cost on the recorded day' : 'Daily trend'}
              </h3>
              <span className="text-xs text-muted-foreground">
                {singleDay
                  ? `${dayCount === 0 ? 'No day' : 'Only one day'} in this window — shown as bars, not as a trend`
                  : `${dayCount} days`} · both axes share one scale
              </span>
            </div>
            {dayCount > 0 ? (
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mt-3">
                <DailySource
                  data={status.cost_history}
                  actualKey="actual_usd"
                  baselineKey="baseline_usd"
                  color="var(--viz-role-os)"
                  title="OS turns"
                  singleDay={singleDay}
                  domainMax={dailyDomainMax}
                />
                {status.acs_data_available ? (
                  <DailySource
                    data={status.cost_history}
                    actualKey="acs_actual_usd"
                    baselineKey="acs_baseline_usd"
                    color="var(--viz-role-worker)"
                    title="Worker runs"
                    singleDay={singleDay}
                    domainMax={dailyDomainMax}
                  />
                ) : (
                  <div className="py-10 text-center text-xs text-muted-foreground border border-dashed border-border rounded-lg">
                    No worker data in this window. Deliberately EMPTY rather
                    than a zero line — a flat zero claims delegated runs are
                    free.
                  </div>
                )}
              </div>
            ) : (
              <div className="py-10 text-center text-sm text-muted-foreground border border-dashed border-border rounded-lg">
                No cost data in this window yet.
              </div>
            )}
          </div>

          {/* ── 4. Coverage — what the dollars actually rest on ──────────── */}
          <div>
            <h3 className="text-sm font-semibold mb-1">Token coverage</h3>
            <p className="text-xs text-muted-foreground mb-3">
              Share of turns whose cost could be computed at all. Whatever is
              missing is not cheaper — it is unmeasured.
            </p>
            <div className="space-y-2">
              <CoverageMeter
                label="OS turns"
                color="var(--viz-role-os)"
                counted={status.cost_counted_turns ?? 0}
                total={status.cost_total_turns ?? 0}
              />
              <CoverageMeter
                label="Worker runs"
                color="var(--viz-role-worker)"
                counted={status.acs_counted_turns ?? 0}
                total={status.acs_total_turns ?? 0}
              />
            </div>
          </div>

          {status.cost_data_available && lowCoverageDays.length > 0 && (
            <div className="flex items-start gap-2 text-xs text-amber-600 dark:text-amber-400 border border-amber-600/30 dark:border-amber-400/30 rounded-lg px-3 py-2">
              <AlertCircle size={14} className="mt-0.5 shrink-0" />
              <span>
                Low data coverage on {lowCoverageDays.length === 1 ? 'this day' : 'these days'}:{' '}
                {lowCoverageDays.map((p) => `${p.date} (${p.counted_turns}/${p.total_turns})`).join(', ')}
                {' '}— the cost there reflects a gap in token recording, not a real trend.
              </span>
            </div>
          )}
        </CardContent>
      </Card>

      {/* ── Workload by complexity ──────────────────────────────────────
          Replaces a bar chart of `learned_threshold` against the 0.5 constant.
          That number is an internal parameter with no live consumer — the
          module computing it says so itself: it is a descriptive statistic over
          past turns, not a value any router reads. Charting it against an
          arbitrary constant told an operator nothing they could act on, while
          the three numbers they CAN act on — how much work lands in each tier,
          whether it succeeds, and what it costs — were computed in the same
          pass and thrown away. */}
      <Card className="mb-6">
        <CardHeader>
          <CardTitle>Workload by complexity</CardTitle>
          <CardDescription>
            Where the work lands, whether it succeeds there, and what it costs.
            Tiers are cut at the 33rd and 66th percentile of the observed
            tool-calls-per-turn distribution, so they describe this install&rsquo;s
            own traffic rather than a fixed scale.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-6">
          {tierRows.length > 0 ? (
            <>
              <div>
                <div className="flex items-baseline justify-between gap-3 flex-wrap mb-3">
                  <h3 className="text-sm font-semibold">Turns and spend per tier</h3>
                  <span className="text-xs text-muted-foreground">
                    Bar = share of turns · darker = pricier model served the tier
                  </span>
                </div>
                <div className="space-y-4">
                  {tierRows.map((t) => (
                    <div key={t.tier}>
                      <div className="flex items-baseline justify-between gap-2 text-sm">
                        <span className="font-medium capitalize">{t.tier}</span>
                        <span className="tabular-nums shrink-0">
                          {t.turns} {t.turns === 1 ? 'turn' : 'turns'}
                          <span className="text-muted-foreground">
                            {' '}· {t.successPct.toFixed(0)}% ok
                            {t.pricedTurns > 0 && <> · {usd(t.actual)} spent</>}
                          </span>
                        </span>
                      </div>
                      <div className="h-1.5 w-full rounded-full bg-muted overflow-hidden mt-1">
                        <div
                          className="h-full rounded-full"
                          style={{
                            width: `${t.sharePct}%`,
                            background: TIER_VAR[t.modelTier],
                          }}
                        />
                      </div>
                      <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
                        {t.dominantModel && (
                          <Badge variant="secondary" className="font-normal">
                            {modelMixLabel(t.dominantModel)}
                          </Badge>
                        )}
                        {t.pricedTurns > 0 ? (
                          <>
                            <span className="tabular-nums">
                              {usd(t.perTurn)}/turn
                            </span>
                            <span className="tabular-nums">
                              {usd(t.baseline)} on Opus
                            </span>
                            {t.savedPct > 0.05 && (
                              <span className="tabular-nums">
                                {savingLabel(t.savedPct)} vs. Opus
                              </span>
                            )}
                          </>
                        ) : (
                          <span>cost not measured on these turns</span>
                        )}
                        {t.pricedTurns > 0 && t.pricedTurns < t.turns && (
                          <span className="text-amber-600 dark:text-amber-400 tabular-nums">
                            cost from {t.pricedTurns}/{t.turns} turns
                          </span>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              </div>

              {/* The one reading an operator can act on, stated only when the
                  data actually supports it — never as a standing claim. */}
              {!cheapestServesHardest && hardest && hardest.turns < MIN_SAMPLES_FOR_ADVICE && (
                <p className="text-xs text-muted-foreground">
                  Too few turns in this window to say anything about model fit —
                  a success rate over {hardest.turns}{' '}
                  {hardest.turns === 1 ? 'turn' : 'turns'} is not evidence.
                </p>
              )}
              {cheapestServesHardest && (
                <div className="flex items-start gap-2 text-xs border border-border rounded-lg px-3 py-2">
                  <CheckCircle size={14} className="mt-0.5 shrink-0 text-emerald-600 dark:text-emerald-400" />
                  <span>
                    The most complex tier is already served by{' '}
                    <span className="font-mono">{modelMixLabel(cheapestServesHardest.dominantModel)}</span>{' '}
                    at {cheapestServesHardest.successPct.toFixed(0)}% success. A more
                    capable model would raise cost without a reliability problem
                    to solve.
                  </span>
                </div>
              )}
            </>
          ) : (
            <div className="py-10 text-center text-sm text-muted-foreground border border-dashed border-border rounded-lg">
              No turns in this window yet.
            </div>
          )}

          {/* The threshold, kept but demoted and labelled for what it is. */}
          {tierRows.length > 0 && (
            <details className="text-xs">
              <summary className="cursor-pointer text-muted-foreground hover:text-foreground">
                Observed complexity per tier
              </summary>
              <div className="mt-3 space-y-1">
                <p className="text-muted-foreground">
                  Mean tool-call complexity of the turns that succeeded, per
                  tier. A descriptive measure of past traffic — no routing
                  decision reads it, so it does not steer model selection today.
                </p>
                <div className="mt-2 space-y-1">
                  {tierRows.map((t) => (
                    <div key={t.tier} className="flex justify-between gap-4 tabular-nums">
                      <span className="text-muted-foreground capitalize">{t.tier}</span>
                      <span className="font-mono">
                        {t.learned.toFixed(3)}
                        <span className="text-muted-foreground">
                          {' '}({t.turns} {t.turns === 1 ? 'sample' : 'samples'})
                        </span>
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            </details>
          )}
        </CardContent>
      </Card>

      {/* Operator Controls */}
      <Card>
        <CardHeader>
          <CardTitle>Operator Controls</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex flex-wrap gap-3">
            {/* Export Button */}
            <Button variant="outline" size="sm" onClick={handleExport} disabled={exporting}>
              <Download size={16} />
              {exporting ? 'Exporting...' : 'Export'}
            </Button>

            {/* Import Button */}
            <Button variant="outline" size="sm" asChild>
              <label className="cursor-pointer">
                <Upload size={16} />
                Import
                <input type="file" accept=".json" hidden onChange={(e) => {
                  const file = e.target.files?.[0];
                  if (file) {
                    const reader = new FileReader();
                    reader.onload = async (event) => {
                      try {
                        const data = JSON.parse(event.target?.result as string);
                        const formData = new FormData();
                        formData.append('data', JSON.stringify(data));
                        const response = await fetch('/v1/console/learning/model-cost-optimizer/import', {
                          method: 'POST',
                          body: formData,
                        });
                        if (response.ok) {
                          await fetchData();
                        } else {
                          setError('Import failed');
                        }
                      } catch (err) {
                        setError(`Import parse error: ${err}`);
                      }
                    };
                    reader.readAsText(file);
                  }
                }} />
              </label>
            </Button>

            {/* Reset Button */}
            <Button
              size="sm"
              variant={resetConfirm ? 'destructive' : 'outline'}
              onClick={handleReset}
            >
              <RotateCcw size={16} />
              {resetConfirm ? 'Click again to confirm' : 'Reset Learning'}
            </Button>

            {resetConfirm && (
              <Button size="sm" variant="ghost" onClick={() => setResetConfirm(false)}>
                Cancel
              </Button>
            )}
          </div>

          <div className="mt-4 text-xs text-muted-foreground space-y-0.5">
            <p>
              • <strong>Export:</strong> Download learned thresholds as JSON for backup
            </p>
            <p>
              • <strong>Import:</strong> Restore thresholds from backup
            </p>
            <p>
              • <strong>Reset:</strong> Clear all learning and restart from base thresholds
            </p>
          </div>
        </CardContent>
      </Card>
    </div>
  );
};
