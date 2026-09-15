/**
 * Maturity Metrics Dashboard — 9D Learning Loops Visualization
 *
 * Shows: Hexagon Radar Chart + Score Header + Tier Breakdown Cards
 * Scoring: (Tier1_Avg * 0.40) + (Tier2_Avg * 0.35) + (Meta * 0.25)
 * Colors: Red(0-2) → Orange(2-4) → Yellow(4-6) → Lime(6-8) → Cyan(8-9) → Purple(9-10)
 *
 * All values are REAL and computed on demand by the backend from the ADR-0314
 * learning EventStore + the tenant audit chain (see routes/maturity_live.py).
 * There is no sample/fallback data: when the endpoint returns nothing, the panel
 * shows an explicit empty state.
 */

import React, { useMemo, useState } from 'react';
import { TrendingUp, TrendingDown, Minus, RefreshCw, AlertCircle, Database } from 'lucide-react';
import { ScoreHeader } from './maturity/ScoreHeader';
import { HexagonRadar } from './maturity/HexagonRadar';
import { TierBreakdown } from './maturity/TierBreakdown';
import { HoverDetails } from './maturity/HoverDetails';
import { AnomalyAlerts } from './maturity/AnomalyAlerts';
import { SummaryTab } from './maturity/SummaryTab';
import { PatternsTab } from './maturity/PatternsTab';
import { MaturityData, LoopScores } from './maturity/types';
import { useLiveMaturityData, type TimeWindow, type MaturityMeta } from '../hooks/useLiveMaturityData';

type DashboardTab = 'radar' | 'summary' | 'patterns';

const MARKER = 'vibe-maturity-live-v2'; // deploy proof marker — real on-demand data

export function MaturityDashboard() {
  const [windowPref, setWindow] = useState<TimeWindow>('7d');
  const [selectedLoop, setSelectedLoop] = useState<{ name: string; key: string } | null>(null);
  const [activeTab, setActiveTab] = useState<DashboardTab>('radar');
  const { loopScores, meta, loading, error, lastUpdated, refresh } = useLiveMaturityData({ window: windowPref });

  const data = useMemo(
    () => (loopScores ? computeMaturityScore(loopScores, meta) : null),
    [loopScores, meta]
  );

  return (
    <div className="space-y-6 p-6" data-marker={MARKER}>
      {/* Anomaly Alerts */}
      {loopScores && <AnomalyAlerts windowSeconds={300} />}

      {/* Time Window Controls + Refresh */}
      <div className="flex items-center justify-between">
        <div className="flex gap-2">
          {(['today', '7d', '30d', '90d'] as TimeWindow[]).map((w) => (
            <button
              key={w}
              onClick={() => setWindow(w)}
              className={`px-3 py-1 rounded text-sm font-medium transition-all ${
                windowPref === w
                  ? 'bg-accent text-accent-foreground'
                  : 'bg-muted text-muted-foreground hover:bg-muted/80'
              }`}
            >
              {w === 'today' ? '24h' : w}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-3">
          {lastUpdated && (
            <span className="text-xs text-muted-foreground">
              Updated {lastUpdated.toLocaleTimeString()}
            </span>
          )}
          <button
            onClick={refresh}
            disabled={loading}
            className="flex items-center gap-2 px-3 py-1 rounded bg-muted text-muted-foreground hover:bg-muted/80 disabled:opacity-50 transition-all"
          >
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
            Refresh
          </button>
        </div>
      </div>

      {/* Error Alert */}
      {error && (
        <div className="border border-destructive/40 bg-destructive/10 rounded p-4 flex items-center gap-3">
          <AlertCircle size={18} className="text-destructive flex-shrink-0" />
          <div className="text-sm text-destructive">{error}</div>
        </div>
      )}

      {/* Loading State */}
      {loading && !loopScores && (
        <div className="text-center py-12">
          <div className="inline-flex items-center gap-2 text-muted-foreground">
            <RefreshCw size={16} className="animate-spin" />
            <span>Loading live maturity data...</span>
          </div>
        </div>
      )}

      {/* Empty State — no synthetic fallback */}
      {!loading && !loopScores && !error && (
        <div className="border border-border bg-card rounded-lg p-8 text-center">
          <Database size={28} className="mx-auto mb-3 text-muted-foreground" />
          <div className="text-sm font-medium text-foreground mb-1">No maturity data yet</div>
          <div className="text-xs text-muted-foreground max-w-md mx-auto">
            Maturity is derived on demand from the learning EventStore and the tenant audit
            chain. Once the system records learning outcomes and audited activity, the loops
            populate automatically.
          </div>
        </div>
      )}

      {/* Score Header */}
      {loopScores && data && <ScoreHeader data={data} />}

      {/* Tab Navigation */}
      {loopScores && (
        <div className="flex gap-2 border-b border-border">
          <button
            onClick={() => setActiveTab('radar')}
            className={`px-4 py-2 text-sm font-medium transition-all ${
              activeTab === 'radar'
                ? 'border-b-2 border-accent text-accent'
                : 'text-muted-foreground hover:text-foreground'
            }`}
          >
            📊 Hexagon Radar
          </button>
          <button
            onClick={() => setActiveTab('summary')}
            className={`px-4 py-2 text-sm font-medium transition-all ${
              activeTab === 'summary'
                ? 'border-b-2 border-accent text-accent'
                : 'text-muted-foreground hover:text-foreground'
            }`}
          >
            📈 Summary
          </button>
          <button
            onClick={() => setActiveTab('patterns')}
            className={`px-4 py-2 text-sm font-medium transition-all ${
              activeTab === 'patterns'
                ? 'border-b-2 border-accent text-accent'
                : 'text-muted-foreground hover:text-foreground'
            }`}
          >
            🔍 Patterns
          </button>
        </div>
      )}

      {/* Tab Content */}
      {activeTab === 'radar' && loopScores && (
        <>
          {/* Hexagon Radar + Trend */}
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
            <div className="lg:col-span-2">
              <div className="bg-card border border-border rounded-lg p-6">
                <h3 className="text-sm font-semibold mb-4 text-foreground">
                  Learning Loops — 9D Maturity Radar (click loop for details)
                </h3>
                <HexagonRadar
                  loopScores={loopScores}
                  onLoopClick={(name, key) => setSelectedLoop({ name, key })}
                />
              </div>
            </div>

            {/* Trend Card — real, from meta.trend / meta.projection_30d */}
            <TrendCard meta={meta} />
          </div>

          <TierBreakdown loopScores={loopScores} />

          {/* Meta Loop Card — real, from meta */}
          <MetaLoopCard meta={meta} />

          {/* Recommendations — real, server-derived */}
          <RecommendationsCard meta={meta} />
        </>
      )}

      {/* Summary Tab */}
      {activeTab === 'summary' && loopScores && (
        <SummaryTab loopScores={loopScores} lastUpdated={lastUpdated?.toISOString()} />
      )}

      {/* Patterns Tab */}
      {activeTab === 'patterns' && loopScores && (
        <PatternsTab loopScores={loopScores} />
      )}

      {/* Hover Details Modal */}
      {selectedLoop && (
        <HoverDetails
          loopName={selectedLoop.name}
          loopKey={selectedLoop.key}
          onClose={() => setSelectedLoop(null)}
        />
      )}
    </div>
  );
}

function TrendCard({ meta }: { meta: MaturityMeta | null }) {
  const trend = meta?.trend;
  const TrendIcon = trend?.direction === 'up' ? TrendingUp : trend?.direction === 'down' ? TrendingDown : Minus;
  const trendColor =
    trend?.direction === 'up' ? 'text-emerald-500' : trend?.direction === 'down' ? 'text-destructive' : 'text-muted-foreground';

  return (
    <div className="bg-card border border-border rounded-lg p-6 flex flex-col justify-between">
      <div>
        <h4 className="text-xs font-semibold text-muted-foreground uppercase mb-4">Trend</h4>
        <div className="space-y-4">
          <div>
            <div className="flex items-baseline gap-2 mb-1">
              {trend?.insufficient_history ? (
                <span className="text-lg font-semibold text-muted-foreground">Not enough history</span>
              ) : (
                <>
                  <span className={`text-2xl font-bold ${trendColor}`}>
                    {trend && trend.value > 0 ? '+' : ''}
                    {trend ? trend.value.toFixed(2) : '—'}
                  </span>
                  <span className="text-xs text-muted-foreground">meta score change</span>
                </>
              )}
            </div>
          </div>
          <div>
            <div className="text-xs text-muted-foreground mb-1">Projected (30 days)</div>
            <div className="text-xl font-bold text-accent">
              {meta?.projection_30d != null ? `${meta.projection_30d.toFixed(1)}/10` : '—'}
            </div>
          </div>
        </div>
      </div>
      <div className="mt-6 pt-6 border-t border-border">
        <div className={`flex items-center gap-2 text-xs ${trendColor}`}>
          <TrendIcon size={14} />
          <span>
            {trend?.insufficient_history
              ? 'Collecting data'
              : trend?.direction === 'up'
              ? 'Improving trajectory'
              : trend?.direction === 'down'
              ? 'Declining trajectory'
              : 'Stable trajectory'}
          </span>
        </div>
      </div>
    </div>
  );
}

function MetaLoopCard({ meta }: { meta: MaturityMeta | null }) {
  const fmtPct = (v: number | null | undefined) => (v != null ? `${(v * 100).toFixed(1)}%` : '—');
  return (
    <div className="bg-card border border-border rounded-lg p-6">
      <h3 className="text-sm font-semibold mb-4 text-foreground">Meta Loop — Hyperparameter Tuning</h3>
      <div className="grid grid-cols-3 gap-4">
        <div className="space-y-2">
          <div className="text-xs text-muted-foreground uppercase">Convergence Rate</div>
          <div className="text-lg font-semibold text-accent">
            {meta ? meta.convergence_rate.toFixed(2) : '—'}
          </div>
          <div className="text-xs text-muted-foreground">
            {meta?.optimizer_active ? 'Optimizer active' : 'Optimizer idle'}
          </div>
        </div>
        <div className="space-y-2">
          <div className="text-xs text-muted-foreground uppercase">Drift (mean, active loops)</div>
          <div className="text-lg font-semibold text-accent">{meta ? meta.drift.toFixed(2) : '—'}</div>
          <div className="text-xs text-muted-foreground">
            {meta ? `${meta.threshold_updates} threshold updates` : '—'}
          </div>
        </div>
        <div className="space-y-2">
          <div className="text-xs text-muted-foreground uppercase">Prediction Accuracy</div>
          <div className="text-lg font-semibold text-accent">{fmtPct(meta?.prediction_accuracy)}</div>
          <div className="text-xs text-muted-foreground">recent outcome success</div>
        </div>
      </div>
    </div>
  );
}

function RecommendationsCard({ meta }: { meta: MaturityMeta | null }) {
  const recs = meta?.recommendations ?? [];
  const color = (sev: string) =>
    sev === 'critical' ? 'text-destructive' : sev === 'warning' ? 'text-amber-500' : 'text-emerald-500';
  return (
    <div className="bg-card border border-border rounded-lg p-6">
      <h3 className="text-sm font-semibold mb-4 text-foreground">💡 Recommendations</h3>
      {recs.length === 0 ? (
        <div className="text-sm text-muted-foreground">No recommendations — all loops within expected range.</div>
      ) : (
        <ul className="space-y-3 text-sm text-muted-foreground">
          {recs.map((r, i) => (
            <li key={i} className="flex gap-3">
              <span className={`${color(r.severity)} flex-shrink-0`}>{i + 1}.</span>
              <span>{r.text}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function computeMaturityScore(loops: LoopScores, meta: MaturityMeta | null): MaturityData {
  const tier1 = [loops.confidence, loops.routing, loops.context, loops.workflow, loops.data_flow, loops.security];
  const tier2 = [loops.memory, loops.skills, loops.plugins, loops.audit, loops.compliance, loops.system];

  const tier1Avg = tier1.reduce((a, b) => a + b, 0) / tier1.length;
  const tier2Avg = tier2.reduce((a, b) => a + b, 0) / tier2.length;
  const metaScore = loops.meta_convergence;

  const overallScore = tier1Avg * 0.4 + tier2Avg * 0.35 + metaScore * 0.25;

  return {
    overallScore: Math.round(overallScore * 10) / 10,
    status: getMaturityStatus(overallScore),
    tier1Avg,
    tier2Avg,
    metaScore,
    lastUpdated: new Date().toISOString(),
    // Real trend/projection from the server-computed meta (fallback to stable/self).
    trend: {
      direction: meta?.trend?.direction ?? 'stable',
      value: meta?.trend?.value ?? 0,
    },
    projection: meta?.projection_30d ?? Math.round(overallScore * 10) / 10,
  };
}

function getMaturityStatus(score: number): string {
  if (score < 2) return 'DEAD';
  if (score < 4) return 'NASCENT';
  if (score < 6) return 'LEARNING';
  if (score < 8) return 'MATURE';
  if (score < 9) return 'OPTIMIZED';
  return 'FULLY TRAINED';
}
