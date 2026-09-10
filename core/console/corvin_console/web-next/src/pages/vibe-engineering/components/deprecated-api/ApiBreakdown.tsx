/**
 * ApiBreakdown Component
 *
 * Shows per-API call counts, error rates, and latencies.
 */

import React from 'react';
import { DeprecatedAPICallCount } from '../../hooks/useDeprecatedApiMetrics';

interface ApiBreakdownProps {
  apis: DeprecatedAPICallCount[];
}

export function ApiBreakdown({ apis }: ApiBreakdownProps) {
  if (apis.length === 0) {
    return (
      <div className="bg-[#161B22] border border-[#30363D] rounded-lg p-6">
        <h3 className="text-sm font-semibold mb-4 text-[#C9D1D9]">Per-API Breakdown</h3>
        <div className="text-center py-8 text-[#8B949E]">
          No deprecated API calls detected (Phase B: expected)
        </div>
      </div>
    );
  }

  return (
    <div className="bg-[#161B22] border border-[#30363D] rounded-lg p-6">
      <h3 className="text-sm font-semibold mb-4 text-[#C9D1D9]">Per-API Breakdown (Top Callers)</h3>

      <div className="overflow-x-auto">
        <table className="w-full text-xs text-[#8B949E]">
          <thead>
            <tr className="border-b border-[#30363D]">
              <th className="text-left py-2 px-3 font-semibold text-[#C9D1D9]">API Name</th>
              <th className="text-right py-2 px-3 font-semibold text-[#C9D1D9]">Total Calls</th>
              <th className="text-right py-2 px-3 font-semibold text-[#C9D1D9]">Calls/Min</th>
              <th className="text-right py-2 px-3 font-semibold text-[#C9D1D9]">Error Count</th>
              <th className="text-right py-2 px-3 font-semibold text-[#C9D1D9]">Error Rate</th>
              <th className="text-right py-2 px-3 font-semibold text-[#C9D1D9]">P50 Latency</th>
              <th className="text-right py-2 px-3 font-semibold text-[#C9D1D9]">P99 Latency</th>
            </tr>
          </thead>
          <tbody>
            {apis.map((api) => (
              <tr key={api.api_name} className="border-b border-[#30363D] hover:bg-[#0D1117] transition-colors">
                <td className="py-3 px-3 text-[#C9D1D9] font-mono text-xs">{api.api_name}</td>
                <td className="text-right py-3 px-3">{api.calls_total}</td>
                <td className="text-right py-3 px-3">{api.calls_per_minute.toFixed(2)}</td>
                <td className="text-right py-3 px-3">{api.error_count}</td>
                <td className="text-right py-3 px-3">
                  <span
                    className="px-2 py-1 rounded"
                    style={{
                      backgroundColor: api.error_rate_pct > 1.0 ? '#3d1f1a' : '#1a2e1a',
                      color: api.error_rate_pct > 1.0 ? '#F85149' : '#3FB950',
                    }}
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
      <div className="mt-4 pt-4 border-t border-[#30363D] text-xs text-[#8B949E]">
        <div>Total APIs Monitored: {apis.length}</div>
        <div>SLO: Each API &lt;50ms P99 latency, &lt;1% error rate</div>
      </div>
    </div>
  );
}
