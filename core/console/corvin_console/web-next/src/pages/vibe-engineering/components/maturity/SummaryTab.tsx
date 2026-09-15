/**
 * Summary Tab — Operator overview built entirely from REAL data.
 *
 * Overall confidence + convergence come from the live loop scores; the trend
 * card is the server-computed `meta.trend` / `meta.projection_30d` (real, from
 * the meta-convergence history buckets, with an explicit "not enough history"
 * state); the anomalies list is the real `/anomalies` feed. Nothing here is a
 * fixed placeholder — when a signal is absent it renders as absent.
 */

import React, { useEffect, useMemo, useState } from 'react';
import { TrendingUp, TrendingDown, Minus, AlertTriangle, CheckCircle } from 'lucide-react';
import { cn } from '@/lib/utils';
import type { MaturityMeta } from '../../hooks/useLiveMaturityData';
import type { Anomaly } from './AnomalyAlerts';

interface SummaryTabProps {
  loopScores: {
    confidence: number;
    routing: number;
    context: number;
    workflow: number;
    data_flow: number;
    security: number;
    memory: number;
    skills: number;
    plugins: number;
    audit: number;
    compliance: number;
    system: number;
    meta_convergence: number;
  };
  meta?: MaturityMeta | null;
  lastUpdated?: string;
}

export function SummaryTab({ loopScores, meta, lastUpdated }: SummaryTabProps) {
  const [anomalies, setAnomalies] = useState<Anomaly[]>([]);
  const [anomaliesLoaded, setAnomaliesLoaded] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const res = await fetch('/v1/console/vibe/maturity/anomalies?window=300', {
          headers: { 'Content-Type': 'application/json' },
        });
        if (!res.ok) throw new Error(`API error ${res.status}`);
        const json = await res.json();
        if (!cancelled) setAnomalies(json.anomalies || []);
      } catch (e) {
        console.warn('[SummaryTab] anomalies fetch failed:', e);
        if (!cancelled) setAnomalies([]);
      } finally {
        if (!cancelled) setAnomaliesLoaded(true);
      }
    };
    load();
    const interval = setInterval(load, 60 * 1000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);

  const overallConfidence = useMemo(() => {
    const tier1 = [
      loopScores.confidence,
      loopScores.routing,
      loopScores.context,
      loopScores.workflow,
      loopScores.data_flow,
      loopScores.security,
    ];
    return tier1.reduce((a, b) => a + b, 0) / tier1.length;
  }, [loopScores]);

  const convergenceRate = loopScores.meta_convergence;

  const getStatus = (score: number) => {
    if (score < 2) return { text: 'DEAD', color: 'text-destructive', bg: 'bg-destructive/15' };
    if (score < 4) return { text: 'NASCENT', color: 'text-orange-600 dark:text-orange-400', bg: 'bg-orange-500/15' };
    if (score < 6) return { text: 'LEARNING', color: 'text-amber-600 dark:text-amber-400', bg: 'bg-amber-500/15' };
    if (score < 8) return { text: 'MATURE', color: 'text-lime-600 dark:text-lime-400', bg: 'bg-lime-500/15' };
    if (score < 9) return { text: 'OPTIMIZED', color: 'text-cyan-600 dark:text-cyan-400', bg: 'bg-cyan-500/15' };
    return { text: 'TRAINED', color: 'text-accent', bg: 'bg-accent/15' };
  };

  const status = getStatus(overallConfidence);

  const trend = meta?.trend;
  const TrendIcon = trend?.direction === 'up' ? TrendingUp : trend?.direction === 'down' ? TrendingDown : Minus;
  const trendColor =
    trend?.direction === 'up'
      ? 'text-emerald-600 dark:text-emerald-400'
      : trend?.direction === 'down'
        ? 'text-destructive'
        : 'text-muted-foreground';

  return (
    <div className="space-y-6">
      {/* Overall Confidence */}
      <div className="bg-card border border-border rounded-lg p-6">
        <h3 className="text-sm font-semibold mb-4 text-foreground">📊 Overall Confidence</h3>
        <div className="flex items-end gap-6">
          <div>
            <div className={cn("text-5xl font-bold", status.color)}>{overallConfidence.toFixed(1)}</div>
            <div className={cn("text-xs font-semibold mt-2 px-2 py-1 rounded inline-block", status.bg, status.color)}>
              {status.text}
            </div>
          </div>
          <div className="flex-1">
            <div className="w-full bg-muted rounded-full h-3 overflow-hidden">
              <div
                className="bg-gradient-to-r from-accent/70 to-accent h-3 rounded-full"
                style={{ width: `${(overallConfidence / 10) * 100}%` }}
              ></div>
            </div>
            <div className="text-xs text-muted-foreground mt-2">Score: 0–10 (0=dead, 10=fully trained)</div>
          </div>
        </div>
      </div>

      {/* Trend + Convergence */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="bg-card border border-border rounded-lg p-6">
          <h4 className="text-xs font-semibold text-muted-foreground uppercase mb-4">Meta-Convergence Trend</h4>
          <div className="space-y-4">
            <div>
              <div className="flex items-baseline gap-2 mb-2">
                {trend?.insufficient_history || !trend ? (
                  <span className="text-lg font-semibold text-muted-foreground">Not enough history</span>
                ) : (
                  <>
                    <span className={cn('text-3xl font-bold', trendColor)}>
                      {trend.value > 0 ? '+' : ''}
                      {trend.value.toFixed(2)}
                    </span>
                    <span className="text-xs text-muted-foreground">meta score change (window)</span>
                  </>
                )}
              </div>
              {meta?.projection_30d != null && (
                <div className="text-xs text-muted-foreground">
                  Projected (30d): <span className="font-semibold text-accent">{meta.projection_30d.toFixed(1)}/10</span>
                </div>
              )}
            </div>
            <div className={cn('flex items-center gap-2 text-xs', trendColor)}>
              <TrendIcon size={14} />
              <span>
                {trend?.insufficient_history || !trend
                  ? 'Collecting data'
                  : trend.direction === 'up'
                    ? 'Improving trajectory'
                    : trend.direction === 'down'
                      ? 'Declining trajectory'
                      : 'Stable trajectory'}
              </span>
            </div>
          </div>
        </div>

        {/* Convergence Status */}
        <div className="bg-card border border-border rounded-lg p-6">
          <h4 className="text-xs font-semibold text-muted-foreground uppercase mb-4">Convergence Rate</h4>
          <div className="space-y-4">
            <div>
              <div className="text-3xl font-bold text-accent">{convergenceRate.toFixed(2)}</div>
              <div className="text-xs text-muted-foreground mt-1">per measurement cycle</div>
            </div>
            <div className="text-xs text-foreground">
              {convergenceRate > 0.8 && '✓ Excellent convergence — learning is effective'}
              {convergenceRate > 0.5 && convergenceRate <= 0.8 && '→ Good convergence — on track'}
              {convergenceRate <= 0.5 && '⚠ Slow convergence — may need tuning'}
            </div>
          </div>
        </div>
      </div>

      {/* Top Anomalies — real, from /anomalies */}
      <div className="bg-card border border-border rounded-lg p-6">
        <h4 className="text-sm font-semibold text-foreground mb-4">⚠️ Anomalies (current window)</h4>
        {anomaliesLoaded && anomalies.length === 0 ? (
          <div className="flex items-start gap-3 p-3 bg-muted/40 rounded border border-accent/30">
            <CheckCircle size={16} className="text-accent flex-shrink-0 mt-0.5" />
            <div className="flex-1">
              <div className="text-sm font-medium text-accent">No anomalies detected</div>
              <div className="text-xs text-muted-foreground mt-1">
                No drift spikes or convergence drops crossed threshold in the window.
              </div>
            </div>
          </div>
        ) : (
          <div className="space-y-3">
            {anomalies.map((a) => (
              <div
                key={a.id}
                className={cn(
                  'flex items-start gap-3 p-3 rounded border bg-muted/40',
                  a.severity === 'critical' ? 'border-destructive/30' : 'border-amber-500/30',
                )}
              >
                <AlertTriangle
                  size={16}
                  className={cn(
                    'flex-shrink-0 mt-0.5',
                    a.severity === 'critical' ? 'text-destructive' : 'text-amber-600 dark:text-amber-400',
                  )}
                />
                <div className="flex-1">
                  <div
                    className={cn(
                      'text-sm font-medium',
                      a.severity === 'critical' ? 'text-destructive' : 'text-amber-600 dark:text-amber-400',
                    )}
                  >
                    {a.loop.replace('_', ' ')} — {a.type.replace('-', ' ')}
                  </div>
                  <div className="text-xs text-muted-foreground mt-1">{a.message}</div>
                  <div className="text-xs text-muted-foreground mt-1">
                    {new Date(a.timestamp).toLocaleTimeString()} • Δ{a.value.toFixed(3)}
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Recommendations — real, server-derived (mirrors the radar tab) */}
      {meta?.recommendations && meta.recommendations.length > 0 && (
        <div className="border border-border bg-card rounded-lg p-4">
          <div className="text-xs font-semibold text-muted-foreground uppercase mb-3">Recommendations</div>
          <ul className="space-y-2 text-sm text-muted-foreground">
            {meta.recommendations.map((r, i) => (
              <li key={i} className="flex gap-2">
                <span
                  className={cn(
                    'flex-shrink-0',
                    r.severity === 'critical'
                      ? 'text-destructive'
                      : r.severity === 'warning'
                        ? 'text-amber-500'
                        : 'text-emerald-500',
                  )}
                >
                  {i + 1}.
                </span>
                <span>{r.text}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {lastUpdated && (
        <div className="text-xs text-muted-foreground text-center">
          Last updated: {new Date(lastUpdated).toLocaleTimeString()}
        </div>
      )}
    </div>
  );
}
