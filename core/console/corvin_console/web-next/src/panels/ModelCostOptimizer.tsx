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
        <span className="text-muted-foreground">real:</span>
        <span className="font-mono tabular-nums">{usd(r.actual)}</span>
      </div>
      <div className="flex justify-between gap-4">
        <span className="text-muted-foreground">hypothetisch (Opus):</span>
        <span className="font-mono tabular-nums">{usd(r.baseline)}</span>
      </div>
      <div className="flex justify-between gap-4 mt-1 pt-1 border-t border-border">
        <span className="text-muted-foreground">
          {r.savedPct < -0.05 ? 'mehr gekostet:' : 'gespart:'}
        </span>
        <span className="font-mono tabular-nums">
          {usd(Math.abs(r.baseline - r.actual))} ({Math.abs(r.savedPct).toFixed(1)} %)
        </span>
      </div>
      <div className="text-muted-foreground mt-1">
        {r.turns} {r.turns === 1 ? 'Turn' : 'Turns'}
      </div>
    </div>
  );
};

/** Token-coverage meter: how much of a source the dollars actually rest on.
 *  A ratio against a limit is a meter, not a chart — and it must be visible,
 *  because a $ total quoted at 39 % coverage reads as the full bill. */
const CoverageMeter: React.FC<{ counted: number; total: number; label: string }> = ({
  counted, total, label,
}) => {
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
            background: low ? 'hsl(var(--destructive))' : 'var(--viz-role-os)',
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
            <Bar dataKey={baselineKey} name="hypothetisch (Opus)" fill="var(--viz-baseline)"
                 radius={[4, 4, 0, 0]} barSize={26} isAnimationActive={false} />
            <Bar dataKey={actualKey} name="real" fill={color}
                 radius={[4, 4, 0, 0]} barSize={26} isAnimationActive={false} />
          </BarChart>
        ) : (
          <AreaChart data={data} margin={{ top: 4, right: 8, bottom: 4, left: 0 }}>
            {axes}
            {tip}
            <Legend wrapperStyle={{ fontSize: 11 }} />
            <Area type="monotone" dataKey={baselineKey} name="hypothetisch (Opus)"
                  stroke="var(--viz-baseline)" fill="var(--viz-baseline)" fillOpacity={0.18}
                  strokeWidth={2} isAnimationActive={false} />
            <Area type="monotone" dataKey={actualKey} name="real"
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
        setError(`Zählfenster konnte nicht gesetzt werden (HTTP ${response.status})`);
        return;
      }
      setWindowConfirm(false);
      await fetchData();
    } catch (err) {
      setError(`Zählfenster fehlgeschlagen: ${err}`);
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
          ADR-0377 Phase 2b • Last updated: {new Date(status.last_updated).toLocaleTimeString()}
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
                Zählfenster seit{' '}
                <span className="font-medium">
                  {new Date(win.since_iso as string).toLocaleString()}
                </span>
                <span className="text-muted-foreground">
                  {' '}— OS und Worker starten ab diesem Zeitpunkt bei null
                </span>
              </span>
            ) : (
              <span className="text-muted-foreground">
                Zählfenster: gesamte Historie (kein Reset gesetzt)
              </span>
            )}
          </div>
          <div className="flex items-center gap-2">
            <Button
              size="sm"
              variant={windowConfirm ? 'destructive' : 'outline'}
              onClick={() => handleUsageWindow(false)}
            >
              {windowConfirm ? 'Nochmal klicken zum Bestätigen' : 'Zähler auf jetzt zurücksetzen'}
            </Button>
            {windowConfirm && (
              <Button size="sm" variant="ghost" onClick={() => setWindowConfirm(false)}>
                Abbrechen
              </Button>
            )}
            {win?.active && !windowConfirm && (
              <Button size="sm" variant="ghost" onClick={() => handleUsageWindow(true)}>
                Ganze Historie zeigen
              </Button>
            )}
          </div>
          <p className="w-full text-xs text-muted-foreground">
            Setzt nur das Zählfenster. Die Audit-Chain ist append-only und
            hash-verkettet (ADR-0232) — es wird nichts gelöscht, und
            „Ganze Historie zeigen" bringt jeden Turn zurück.
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
              Haiku- vs. Opus-Referenzkosten
            </div>
            {status.cost_data_available ? (
              <>
                <div className="text-3xl font-bold text-emerald-600 dark:text-emerald-400">
                  {status.cost_savings_percent.toFixed(1)}%
                </div>
                <div className="text-xs text-muted-foreground mt-2">
                  ${status.cost_baseline_usd.toFixed(2)} hypothetisch (Opus) → ${status.cost_current_usd.toFixed(2)} real
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
                    Nur OS-Manager-Turns — keine Worker-Daten erfasst
                  </div>
                )}
                {/* The $ figures above rest on the turns that carried token
                    counts, not on every turn. Quoting a total without that
                    ratio reads as the full bill — live 2026-09-15 it was 154
                    of 391 turns (39%), the rest emitted before ADR-0696's
                    token counts existed. */}
                {costCoverage !== null && (
                  <div className={`mt-1 text-xs ${costCoverage < 90 ? 'text-amber-600 dark:text-amber-400' : 'text-muted-foreground'}`}>
                    Basis: {status.cost_counted_turns}/{status.cost_total_turns} Turns mit Token-Daten ({costCoverage}%)
                    {costCoverage < 90 ? ' — reale Kosten liegen höher' : ''}
                  </div>
                )}
                {isSingleModel && (
                  <div className="mt-2 flex items-start gap-1.5 text-xs text-amber-600 dark:text-amber-400">
                    <AlertCircle size={12} className="mt-0.5 shrink-0" />
                    <span>
                      {status.cost_os_model_pin ? (
                        <>Modell fest gepinnt auf <span className="font-mono">{modelMixLabel(status.cost_os_model_pin)}</span> (Settings → AI Engines → OS Model) — keine adaptive Auswahl aktiv. Keine Lizenz-/Tier-Beschränkung.</>
                      ) : (
                        <>Kein Modellwechsel beobachtet — keine adaptive Auswahl aktiv. Keine Lizenz-/Tier-Beschränkung.</>
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
              Worker-Engine (delegierte Runs)
            </div>
            {status.acs_data_available ? (
              <>
                <div className="text-3xl font-bold text-emerald-600 dark:text-emerald-400">
                  {(status.acs_savings_percent ?? 0).toFixed(1)}%
                </div>
                <div className="text-xs text-muted-foreground mt-2">
                  ${status.acs_cost_baseline_usd.toFixed(4)} hypothetisch (Opus) → $
                  {status.acs_cost_actual_usd.toFixed(4)} real
                </div>
                <div className="text-xs text-muted-foreground mt-1">
                  {acsMixEntries.map(([id, n]) => (
                    `${Math.round((n / acsMixTotal) * 100)}% ${modelMixLabel(id)}`
                  )).join(' · ')}
                </div>
                {acsCoverage !== null && (
                  <div className={`mt-1 text-xs ${acsCoverage < 90 ? 'text-amber-600 dark:text-amber-400' : 'text-muted-foreground'}`}>
                    Basis: {status.acs_counted_turns}/{status.acs_total_turns} Worker-Turns mit Token-Daten ({acsCoverage}%)
                  </div>
                )}
                {acsMixEntries.length === 1 && (
                  <div className="mt-2 flex items-start gap-1.5 text-xs text-amber-600 dark:text-amber-400">
                    <AlertCircle size={12} className="mt-0.5 shrink-0" />
                    <span>
                      {status.acs_worker_model_pin ? (
                        <>Worker-Modell fest gepinnt auf <span className="font-mono">{modelMixLabel(status.acs_worker_model_pin)}</span> (Engine Config → Worker Model) — kein Routing-Ergebnis, eine Preisrelation.</>
                      ) : (
                        <>Nur ein Worker-Modell beobachtet — kein Modellwechsel, also keine Routing-Entscheidung sichtbar.</>
                      )}
                    </span>
                  </div>
                )}
              </>
            ) : (
              <>
                <div className="text-3xl font-bold text-muted-foreground">—</div>
                <div className="text-xs text-muted-foreground mt-2">
                  Kein delegierter Worker-Turn mit Token-Daten im Zählfenster.
                  Nicht „kostenlos" — <strong>nicht gemessen</strong>.
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
              Gesamt (OS + Worker)
            </div>
            {status.combined_data_available ? (
              <>
                <div className="text-3xl font-bold text-emerald-600 dark:text-emerald-400">
                  {(status.combined_savings_percent ?? 0).toFixed(1)}%
                </div>
                <div className="text-xs text-muted-foreground mt-2">
                  ${(status.combined_baseline_usd ?? 0).toFixed(2)} hypothetisch (Opus) → $
                  {(status.combined_actual_usd ?? 0).toFixed(2)} real
                </div>
                <div className="text-xs text-muted-foreground mt-1">
                  Summe beider real gemessenen Quellen — kein Mittelwert der
                  beiden Prozentwerte.
                </div>
              </>
            ) : (
              <>
                <div className="text-3xl font-bold text-muted-foreground">—</div>
                <div className="text-xs text-muted-foreground mt-2">
                  Erst verfügbar, wenn BEIDE Quellen im Zählfenster Daten haben.
                  Eine Gesamtzahl aus nur einer Hälfte wäre keine Gesamtzahl.
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
              Turn-Erfolgsquote
            </div>
            <div className="text-3xl font-bold">
              {status.accuracy_percent.toFixed(1)}%
            </div>
            <div className="text-xs text-muted-foreground mt-2">
              Anteil abgeschlossener Turns ohne Fehler/Timeout — keine inhaltliche Qualitätsbewertung
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
            Zwei getrennt gemessene Quellen — OS-Turns (leichte Orchestrierung)
            und Worker-Runs (delegierte Agentic-Runs mit vollem Tool-Zugriff).
            Nie in einen Plot gemischt: sie unterscheiden sich um Größenordnungen,
            und auf einer gemeinsamen Achse verschwindet die kleinere Quelle.
            „Hypothetisch" heißt immer: dieselben real gemessenen Tokens zum
            Opus-Tarif.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-8">

          {/* ── 1. Per model: what it cost vs what Opus would have cost ──── */}
          <div>
            <div className="flex items-baseline justify-between gap-3 flex-wrap mb-1">
              <h3 className="text-sm font-semibold">Kosten je Modell</h3>
              <span className="text-xs text-muted-foreground">
                Balkenlänge = Ersparnis gegenüber Opus · dunkler = teureres Modell · beide Achsen identisch skaliert
              </span>
            </div>
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mt-3">
              <div>
                <div className="flex items-center gap-2 text-xs font-medium mb-2">
                  <span className="inline-block w-2.5 h-2.5 rounded-sm" style={{ background: 'var(--viz-role-os)' }} />
                  OS-Turns
                </div>
                <ModelCostChart
                  rows={osCostRows}
                  domainMax={modelDomainMax}
                  emptyNote="Kein OS-Turn mit Token-Daten im Zählfenster."
                />
              </div>
              <div>
                <div className="flex items-center gap-2 text-xs font-medium mb-2">
                  <span className="inline-block w-2.5 h-2.5 rounded-sm" style={{ background: 'var(--viz-role-worker)' }} />
                  Worker-Runs
                </div>
                <ModelCostChart
                  rows={workerCostRows}
                  domainMax={modelDomainMax}
                  emptyNote="Kein delegierter Worker-Turn mit Token-Daten im Zählfenster — nicht „kostenlos“, nicht gemessen."
                />
              </div>
            </div>
          </div>

          {/* ── 2. Which models were routed to, and by which role ────────── */}
          {routingRows.length > 0 && (
            <div>
              <div className="flex items-baseline justify-between gap-3 flex-wrap mb-1">
                <h3 className="text-sm font-semibold">Geroutete Modelle</h3>
                <span className="text-xs text-muted-foreground">
                  Turns je Modell — Anzahl, nicht Kosten (eigene Achse, eigener Chart)
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
                  <Bar dataKey="os" stackId="r" name="OS-Turns" fill="var(--viz-role-os)"
                       radius={[4, 0, 0, 4]} barSize={14} isAnimationActive={false}
                       stroke="hsl(var(--card))" strokeWidth={2} />
                  <Bar dataKey="worker" stackId="r" name="Worker-Runs" fill="var(--viz-role-worker)"
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
                {singleDay ? 'Kosten am erfassten Tag' : 'Täglicher Verlauf'}
              </h3>
              <span className="text-xs text-muted-foreground">
                {singleDay
                  ? `${dayCount === 0 ? 'Noch kein' : 'Erst ein'} Tag im Zählfenster — als Balken dargestellt, nicht als Trend`
                  : `${dayCount} Tage`} · beide Achsen identisch skaliert
              </span>
            </div>
            {dayCount > 0 ? (
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mt-3">
                <DailySource
                  data={status.cost_history}
                  actualKey="actual_usd"
                  baselineKey="baseline_usd"
                  color="var(--viz-role-os)"
                  title="OS-Turns"
                  singleDay={singleDay}
                  domainMax={dailyDomainMax}
                />
                {status.acs_data_available ? (
                  <DailySource
                    data={status.cost_history}
                    actualKey="acs_actual_usd"
                    baselineKey="acs_baseline_usd"
                    color="var(--viz-role-worker)"
                    title="Worker-Runs"
                    singleDay={singleDay}
                    domainMax={dailyDomainMax}
                  />
                ) : (
                  <div className="py-10 text-center text-xs text-muted-foreground border border-dashed border-border rounded-lg">
                    Keine Worker-Daten im Zählfenster. Bewusst LEER statt einer
                    Null-Linie — eine flache Null behauptet, delegierte Runs
                    seien kostenlos.
                  </div>
                )}
              </div>
            ) : (
              <div className="py-10 text-center text-sm text-muted-foreground border border-dashed border-border rounded-lg">
                Noch keine Kostendaten im Zählfenster.
              </div>
            )}
          </div>

          {/* ── 4. Coverage — what the dollars actually rest on ──────────── */}
          <div>
            <h3 className="text-sm font-semibold mb-1">Token-Abdeckung</h3>
            <p className="text-xs text-muted-foreground mb-3">
              Anteil der Turns, deren Kosten überhaupt berechenbar waren. Alles
              darunter ist nicht billiger — es ist ungemessen.
            </p>
            <div className="space-y-2">
              <CoverageMeter
                label="OS-Turns"
                counted={status.cost_counted_turns ?? 0}
                total={status.cost_total_turns ?? 0}
              />
              <CoverageMeter
                label="Worker-Runs"
                counted={status.acs_counted_turns ?? 0}
                total={status.acs_total_turns ?? 0}
              />
            </div>
          </div>

          {status.cost_data_available && lowCoverageDays.length > 0 && (
            <div className="flex items-start gap-2 text-xs text-amber-600 dark:text-amber-400 border border-amber-600/30 dark:border-amber-400/30 rounded-lg px-3 py-2">
              <AlertCircle size={14} className="mt-0.5 shrink-0" />
              <span>
                Geringe Datenabdeckung an {lowCoverageDays.length === 1 ? 'diesem Tag' : 'diesen Tagen'}:{' '}
                {lowCoverageDays.map((p) => `${p.date} (${p.counted_turns}/${p.total_turns})`).join(', ')}
                {' '}— die Kosten dort spiegeln keinen echten Trend, sondern eine Lücke in der Token-Erfassung.
              </span>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Threshold Convergence Chart */}
      <Card className="mb-6">
        <CardHeader>
          <CardTitle>Learned Thresholds vs Base</CardTitle>
          <CardDescription>
            Per-task-type routing threshold, learned value against the 0.5 base default
          </CardDescription>
        </CardHeader>
        <CardContent>
          {hasThresholds ? (
            <ResponsiveContainer width="100%" height={300}>
              <BarChart data={convergenceData}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="name" tick={{ fontSize: 12 }} />
                <YAxis domain={[0, 1]} tick={{ fontSize: 12 }} />
                <Tooltip formatter={(value) => (typeof value === 'number' ? value.toFixed(3) : value)} />
                <Legend />
                <Bar dataKey="base" fill="hsl(var(--muted-foreground))" name="Base (0.5)" />
                <Bar dataKey="learned" fill="hsl(var(--accent))" name="Learned" />
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <div className="py-16 text-center text-sm text-muted-foreground border border-dashed border-border rounded-lg">
              No learned thresholds yet — the model-selection routing path hasn't
              recorded any decisions for this tenant, so there is nothing to
              compare against the base threshold.
            </div>
          )}
        </CardContent>
      </Card>

      {/* Task Type Breakdown */}
      {hasThresholds ? (
        <div className="mb-6 grid grid-cols-1 md:grid-cols-3 gap-4">
          {status.thresholds.map((t) => (
            <Card key={`${t.task_type}:${t.subsystem}`}>
              <CardContent className="p-4">
                <div className="flex items-center justify-between mb-3">
                  <h3 className="font-semibold">{t.task_type}</h3>
                  <Badge variant={t.converged ? 'ok' : 'warn'}>
                    {t.converged ? 'Converged' : 'Learning'}
                  </Badge>
                </div>

                <div className="text-sm space-y-1 mb-3">
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">Threshold:</span>
                    <span className="font-mono font-semibold">
                      {t.learned_threshold.toFixed(3)}
                    </span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">Base:</span>
                    <span className="font-mono text-muted-foreground">
                      {t.base_threshold.toFixed(3)}
                    </span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">Samples:</span>
                    <span className="font-mono font-semibold">{t.sample_count}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">Erfolgsquote:</span>
                    <span className="font-mono font-semibold">{(t.success_rate * 100).toFixed(0)}%</span>
                  </div>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      ) : (
        <Card className="mb-6 border-dashed">
          <CardContent className="py-12 text-center text-sm text-muted-foreground">
            No task types tracked yet.
          </CardContent>
        </Card>
      )}

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
