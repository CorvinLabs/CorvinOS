/**
 * ApiBreakdown Component
 *
 * Shows per-API call counts, error rates, and latencies.
 */

import React from 'react';
import { cn } from '@/lib/utils';
import { DeprecatedAPICallCount } from '../../hooks/useDeprecatedApiMetrics';

interface ApiBreakdownProps {
  apis: DeprecatedAPICallCount[];
}

export function ApiBreakdown({ apis }: ApiBreakdownProps) {
  if (apis.length === 0) {
    return (
      <div className="bg-card border border-border rounded-lg p-6">
        <h3 className="text-sm font-semibold mb-4 text-foreground">Per-API Breakdown</h3>
        <div className="text-center py-8 text-muted-foreground">
          No deprecated API calls detected (Phase B: expected)
        </div>
      </div>
    );
  }

  return (
    <div className="bg-card border border-border rounded-lg p-6">
      <h3 className="text-sm font-semibold mb-4 text-foreground">Per-API Breakdown (Top Callers)</h3>

      <div className="overflow-x-auto">
        <table className="w-full text-xs text-muted-foreground">
          <thead>
            <tr className="border-b border-border">
              <th className="text-left py-2 px-3 font-semibold text-foreground">API Name</th>
              <th className="text-right py-2 px-3 font-semibold text-foreground">Total Calls</th>
              <th className="text-right py-2 px-3 font-semibold text-foreground">Calls/Min</th>
              <th className="text-right py-2 px-3 font-semibold text-foreground">Error Count</th>
              <th className="text-right py-2 px-3 font-semibold text-foreground">Error Rate</th>
              <th className="text-right py-2 px-3 font-semibold text-foreground">P50 Latency</th>
              <th className="text-right py-2 px-3 font-semibold text-foreground">P99 Latency</th>
            </tr>
          </thead>
          <tbody>
            {apis.map((api) => (
              <tr key={api.api_name} className="border-b border-border hover:bg-muted/40 transition-colors">
                <td className="py-3 px-3 text-foreground font-mono text-xs">{api.api_name}</td>
                <td className="text-right py-3 px-3">{api.calls_total}</td>
                <td className="text-right py-3 px-3">{api.calls_per_minute.toFixed(2)}</td>
                <td className="text-right py-3 px-3">{api.error_count}</td>
                <td className="text-right py-3 px-3">
                  <span
                    className={cn(
                      'px-2 py-1 rounded',
                      api.error_rate_pct > 1.0
                        ? 'bg-destructive/10 text-destructive'
                        : 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400',
                    )}
                  >
                    {api.error_rate_pct.toFixed(2)}%
                  </span>
                </td>
                <td className="text-right py-3 px-3">{api.p50_latency_ms.toFixed(1)}ms</td>
                <td className="text-right py-3 px-3">{api.p99_latency_ms.toFixed(1)}ms</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Footer */}
      <div className="mt-4 pt-4 border-t border-border text-xs text-muted-foreground">
        <div>Total APIs Monitored: {apis.length}</div>
        <div>SLO: Each API &lt;50ms P99 latency, &lt;1% error rate</div>
      </div>
    </div>
  );
}
