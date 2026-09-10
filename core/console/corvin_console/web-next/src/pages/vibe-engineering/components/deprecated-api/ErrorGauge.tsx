/**
 * ErrorGauge Component
 *
 * Displays error rate as a gauge/progress indicator.
 * Green: <0.5%, Yellow: 0.5-1%, Red: >1%
 */

import React from 'react';
import { cn } from '@/lib/utils';

interface ErrorGaugeProps {
  errorRate: number; // percentage (0-100)
}

export function ErrorGauge({ errorRate }: ErrorGaugeProps) {
  // Determine color based on rate
  let textClass: string;
  let barClass: string;
  let status: string;
  if (errorRate < 0.5) {
    textClass = 'text-emerald-600 dark:text-emerald-400';
    barClass = 'bg-emerald-500';
    status = 'Excellent';
  } else if (errorRate < 1.0) {
    textClass = 'text-amber-600 dark:text-amber-400';
    barClass = 'bg-amber-500';
    status = 'Acceptable';
  } else {
    textClass = 'text-destructive';
    barClass = 'bg-destructive';
    status = 'High Risk';
  }

  // Clamp percentage for gauge visualization (0-2% scale for better granularity)
  const gaugePercent = Math.min((errorRate / 2) * 100, 100);

  return (
    <div className="flex flex-col gap-4">
      {/* Gauge visualization */}
      <div className="space-y-2">
        <div className={cn('text-3xl font-bold', textClass)}>
          {errorRate.toFixed(2)}%
        </div>
        <div className="text-xs text-muted-foreground">{status}</div>
      </div>

      {/* Gauge bar */}
      <div className="w-full bg-muted rounded-full h-3 overflow-hidden">
        <div
          className={cn('h-full transition-all duration-300', barClass)}
          style={{ width: `${gaugePercent}%` }}
        ></div>
      </div>

      {/* Thresholds */}
      <div className="grid grid-cols-3 gap-1 text-xs">
        <div className="text-center">
          <div className="text-muted-foreground">OK</div>
          <div className="text-emerald-600 dark:text-emerald-400">&lt;0.5%</div>
        </div>
        <div className="text-center">
          <div className="text-muted-foreground">WARN</div>
          <div className="text-amber-600 dark:text-amber-400">0.5-1%</div>
        </div>
        <div className="text-center">
          <div className="text-muted-foreground">ALERT</div>
          <div className="text-destructive">&gt;1%</div>
        </div>
      </div>

      {/* SLO info */}
      <div className="text-xs text-muted-foreground border-t border-border pt-2 mt-2">
        Week 5 Target: &lt;1% error rate (Phase B cleanup)
      </div>
    </div>
  );
}
