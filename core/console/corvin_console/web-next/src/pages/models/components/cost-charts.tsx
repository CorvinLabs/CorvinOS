/**
 * Cost-optimizer payload types and chart components — moved VERBATIM from
 * panels/ModelCostOptimizer.tsx on 2026-09-18 (ADR-0885 step 2). The ADR-0761
 * encodings (cost-viz.ts) are imported unchanged. The old panel imports from
 * here until step 3 deletes it.
 */
import React from 'react';
import {
  BarChart, Bar, Cell, LabelList,
  XAxis, YAxis, CartesianGrid, Tooltip, Legend,
  ResponsiveContainer, Area, AreaChart,
} from 'recharts';
import {
  
  
} from 'lucide-react';
import {
  TIER_VAR, usd, savingLabel,
  dailyFacetShape, dayGapNote,
  type ModelCostRow,
} from '@/panels/cost-viz';


export interface ThresholdData {
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

export interface CostDayPoint {
  date: string;
  /** null = this series has no PRICED turn on this date (the route never
   *  zero-fills a series; an unmeasured day is a gap, not $0 — 2026-09-19).
   *  `counted_turns`/`total_turns` say why: 0/0 nothing recorded, 0/N recorded
   *  without token data. */
  actual_usd: number | null;
  baseline_usd: number | null;
  counted_turns: number;
  total_turns: number;
  // ACS-delegated worker spend — a separate cost source (full tool-access
  // agentic runs), never blended into actual_usd/baseline_usd above.
  acs_actual_usd: number | null;
  acs_baseline_usd: number | null;
  acs_counted_turns: number;
  acs_total_turns: number;
}

export interface DashboardStatus {
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
  // False = no worker span / acs.engine_completed with usable token data
  // exists in the window. The per-day acs_*_usd values are then all null and
  // the worker facet is not drawn at all — a flat zero line reads as
  // "delegated workers are free".
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

export interface ThresholdHistory {
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
export const ModelCostChart: React.FC<{
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

export const ModelCostTooltip = ({ active, payload }: { active?: boolean; payload?: Array<{ payload: ModelCostRow }> }) => {
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
export const CoverageMeter: React.FC<{
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
 *  layer is free" rather than "the OS layer is small". The two facets DO share
 *  one domain (ADR-0761): a facet that is genuinely small looks small, and the
 *  caption and tooltip carry its numbers so small never reads as zero.
 *
 *  Below two PRICED days in THIS facet it draws bars: a single point has
 *  nothing to connect, and an area chart over one day is a trend line asserting
 *  a trend nobody measured. The form degrades per facet — the OS series can
 *  span a week while the worker series has one priced day (live, 2026-09-19).
 *
 *  A day on which this series has no priced turn is a GAP (null), never $0:
 *  the area does not connect across it, the bar is not drawn, and the tooltip
 *  says whether nothing was recorded or something was recorded unpriced.
 *
 *  The baseline is a recessive grey, not a second series colour — it is the
 *  thing the real number is measured against, not a peer of it. */
export const DailySource: React.FC<{
  data: CostDayPoint[];
  /** '' for the OS series, 'acs_' for the worker series — selects the four
   *  columns (actual, baseline, counted, total) of ONE source. */
  prefix: '' | 'acs_';
  color: string;
  title: string;
  /** What one row of this source is called in the gap note. */
  noun: 'turn' | 'run';
  /** Shared across both facets — see ModelCostChart.domainMax. */
  domainMax: number;
}> = ({ data, prefix, color, title, noun, domainMax }) => {
  const actualKey = `${prefix}actual_usd`;
  const baselineKey = `${prefix}baseline_usd`;
  const shape = dailyFacetShape(data, actualKey);
  const tip = (
    <Tooltip
      content={<DailyTooltip prefix={prefix} noun={noun} />}
      cursor={{ fill: 'hsl(var(--muted) / 0.4)' }}
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
  const coverage =
    shape.pricedDays === shape.totalDays
      ? `priced on ${shape.totalDays === 1 ? 'the one day' : `all ${shape.totalDays} days`}`
      : `priced on ${shape.pricedDays} of ${shape.totalDays} days — the others are gaps, not $0`;
  return (
    <div>
      <div className="flex items-baseline justify-between gap-3 flex-wrap mb-2">
        <div className="flex items-center gap-2 text-xs font-medium">
          <span className="inline-block w-2.5 h-2.5 rounded-sm" style={{ background: color }} />
          {title}
        </div>
        <span className="text-xs text-muted-foreground" data-testid={`daily-coverage-${prefix || 'os'}`}>
          {coverage}{shape.mode === 'bars' && shape.totalDays > 1 ? ' · bars, not a trend' : ''}
        </span>
      </div>
      <ResponsiveContainer width="100%" height={190}>
        {shape.mode === 'bars' ? (
          <BarChart data={data} margin={{ top: 4, right: 8, bottom: 4, left: 0 }}>
            {axes}
            {tip}
            <Legend wrapperStyle={{ fontSize: 11 }} />
            {/* minPointSize: on the shared domain a $3 worker bar next to a
                $113 OS peak is under 3 % of the axis; 2px keeps the mark
                present so "small" never renders as "absent". */}
            <Bar dataKey={baselineKey} name="on Opus" fill="var(--viz-baseline)"
                 radius={[4, 4, 0, 0]} barSize={26} minPointSize={2} isAnimationActive={false} />
            <Bar dataKey={actualKey} name="actual" fill={color}
                 radius={[4, 4, 0, 0]} barSize={26} minPointSize={2} isAnimationActive={false} />
          </BarChart>
        ) : (
          <AreaChart data={data} margin={{ top: 4, right: 8, bottom: 4, left: 0 }}>
            {axes}
            {tip}
            <Legend wrapperStyle={{ fontSize: 11 }} />
            <Area type="monotone" dataKey={baselineKey} name="on Opus" connectNulls={false}
                  stroke="var(--viz-baseline)" fill="var(--viz-baseline)" fillOpacity={0.18}
                  strokeWidth={2} isAnimationActive={false} />
            <Area type="monotone" dataKey={actualKey} name="actual" connectNulls={false}
                  stroke={color} fill={color} fillOpacity={0.28}
                  strokeWidth={2} isAnimationActive={false} />
          </AreaChart>
        )}
      </ResponsiveContainer>
    </div>
  );
};

/** The daily tooltip reads the ROW, not the series payload: a null day has no
 *  series entry at all, and the reason for the gap lives in the counts. */
export const DailyTooltip = ({ active, payload, prefix, noun }: {
  active?: boolean; payload?: Array<{ payload: CostDayPoint }>;
  prefix: '' | 'acs_'; noun: 'turn' | 'run';
}) => {
  if (!active || !payload?.length) return null;
  const r = payload[0]?.payload;
  if (!r) return null;
  const actual = r[`${prefix}actual_usd` as keyof CostDayPoint] as number | null;
  const baseline = r[`${prefix}baseline_usd` as keyof CostDayPoint] as number | null;
  const counted = r[`${prefix}counted_turns` as keyof CostDayPoint] as number;
  const total = r[`${prefix}total_turns` as keyof CostDayPoint] as number;
  const gap = dayGapNote(counted, total, noun);
  return (
    <div className="rounded-md border border-border bg-background p-3 text-xs shadow-md">
      <div className="font-semibold mb-1">{r.date}</div>
      {gap ? (
        <div className="text-muted-foreground">{gap}</div>
      ) : (
        <>
          <div className="flex justify-between gap-4">
            <span className="text-muted-foreground">actual:</span>
            <span className="font-mono tabular-nums">{usd(actual ?? 0)}</span>
          </div>
          <div className="flex justify-between gap-4">
            <span className="text-muted-foreground">on Opus:</span>
            <span className="font-mono tabular-nums">{usd(baseline ?? 0)}</span>
          </div>
          <div className="text-muted-foreground mt-1">
            {counted}/{total} {noun}s with token data
          </div>
        </>
      )}
    </div>
  );
};
