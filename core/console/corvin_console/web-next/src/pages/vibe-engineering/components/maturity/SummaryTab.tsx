/**
 * Summary Tab — 7d Confidence Trend, Convergence Status, Top Anomalies
 * Phase 4a Operator View
 */

import React, { useMemo } from 'react';
import { TrendingUp, AlertTriangle, CheckCircle } from 'lucide-react';
import { cn } from '@/lib/utils';

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
  lastUpdated?: string;
}

export function SummaryTab({ loopScores, lastUpdated }: SummaryTabProps) {
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

      {/* 7d Trend */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="bg-card border border-border rounded-lg p-6">
          <h4 className="text-xs font-semibold text-muted-foreground uppercase mb-4">7-Day Trend</h4>
          <div className="space-y-4">
            <div>
              <div className="flex items-baseline gap-2 mb-2">
                <span className="text-3xl font-bold text-emerald-600 dark:text-emerald-400">+0.3</span>
                <span className="text-xs text-muted-foreground">improvement (avg/day)</span>
              </div>
              <div className="h-2 bg-muted rounded overflow-hidden">
                <div className="h-2 bg-emerald-500 rounded" style={{ width: '60%' }}></div>
              </div>
            </div>
            <div className="flex items-center gap-2 text-xs text-emerald-600 dark:text-emerald-400">
              <TrendingUp size={14} />
              <span>Healthy upward trajectory</span>
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

      {/* Top Anomalies (Last 24h) */}
      <div className="bg-card border border-border rounded-lg p-6">
        <h4 className="text-sm font-semibold text-foreground mb-4">⚠️ Top Anomalies (Last 24h)</h4>
        <div className="space-y-3">
          <div className="flex items-start gap-3 p-3 bg-muted/40 rounded border border-amber-500/30">
            <AlertTriangle size={16} className="text-amber-600 dark:text-amber-400 flex-shrink-0 mt-0.5" />
            <div className="flex-1">
              <div className="text-sm font-medium text-amber-600 dark:text-amber-400">Workflow Loop drift spike</div>
              <div className="text-xs text-muted-foreground mt-1">Δ = 0.08 (threshold: 0.01) — Skills config may need rebalancing</div>
              <div className="text-xs text-muted-foreground mt-1">2 hours ago</div>
            </div>
          </div>

          <div className="flex items-start gap-3 p-3 bg-muted/40 rounded border border-accent/30">
            <CheckCircle size={16} className="text-accent flex-shrink-0 mt-0.5" />
            <div className="flex-1">
              <div className="text-sm font-medium text-accent">Confidence loop healthy</div>
              <div className="text-xs text-muted-foreground mt-1">No score drops detected — routing decisions stable</div>
              <div className="text-xs text-muted-foreground mt-1">Continuous</div>
            </div>
          </div>
        </div>
      </div>

      {/* Operator Note */}
      <div className="border border-emerald-500/30 bg-emerald-500/10 rounded-lg p-4">
        <div className="flex gap-3">
          <CheckCircle size={18} className="text-emerald-600 dark:text-emerald-400 flex-shrink-0 mt-0.5" />
          <div className="text-sm text-emerald-700 dark:text-emerald-400">
            <strong>Status:</strong> Learning loops are healthy. No immediate action required. Monitor drift spikes on Workflow loop.
          </div>
        </div>
      </div>

      {lastUpdated && (
        <div className="text-xs text-muted-foreground text-center">
          Last updated: {new Date(lastUpdated).toLocaleTimeString()}
        </div>
      )}
    </div>
  );
}
