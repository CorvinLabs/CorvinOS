/**
 * Deprecated API Monitoring Tab — Week 5 Part 1 Dashboard
 *
 * Displays:
 * - Real-time call rate graph (calls/min over past 24h)
 * - Per-API breakdown
 * - Error rate gauge
 * - Status badge (RED/YELLOW/GREEN based on SLO)
 * - Top 3 most-called deprecated APIs
 * - Baseline metrics report
 */

import React, { useState } from 'react';
import { RefreshCw, AlertCircle, Loader2 } from 'lucide-react';
import { useDeprecatedApiMetrics } from '../../hooks/useDeprecatedApiMetrics';
import { StatusBadge } from './StatusBadge';
import { MetricsGraph } from './MetricsGraph';
import { ApiBreakdown } from './ApiBreakdown';
import { ErrorGauge } from './ErrorGauge';
import { BaselineReport } from './BaselineReport';

export function DeprecatedApiTab() {
  const [autoRefresh, setAutoRefresh] = useState(true);
  const { metrics, loading, error, lastUpdated, refresh } = useDeprecatedApiMetrics({
    autoRefreshInterval: autoRefresh ? 30000 : undefined, // 30s refresh
  });

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold text-[#C9D1D9]">Deprecated API Monitoring</h2>
          <p className="text-xs text-[#8B949E] mt-1">Week 5 baseline: Track legacy API usage during Phase B cleanup</p>
        </div>
        <div className="flex items-center gap-3">
          <label className="flex items-center gap-2 text-xs text-[#8B949E] cursor-pointer hover:text-[#C9D1D9]">
            <input
              type="checkbox"
              checked={autoRefresh}
              onChange={(e) => setAutoRefresh(e.target.checked)}
              className="rounded"
            />
            Auto-refresh
          </label>
          <button
            onClick={refresh}
            disabled={loading}
            className="flex items-center gap-2 px-3 py-1 rounded bg-[#30363D] text-[#C9D1D9] hover:bg-[#3d444d] disabled:opacity-50 transition-all"
          >
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
            Refresh
          </button>
        </div>
      </div>

      {/* Error Alert */}
      {error && (
        <div className="bg-[#3d1f1a] border border-[#F85149] rounded p-4 flex items-center gap-3">
          <AlertCircle size={18} className="text-[#F85149] flex-shrink-0" />
          <div className="text-sm text-[#F85149]">{error}</div>
        </div>
      )}

      {/* Loading State */}
      {loading && !metrics && (
        <div className="text-center py-12">
          <div className="inline-flex items-center gap-2 text-[#8B949E]">
            <Loader2 size={16} className="animate-spin" />
            <span>Loading deprecated API metrics...</span>
          </div>
        </div>
      )}

      {/* Metrics Dashboard */}
      {metrics && (
        <>
          {/* Top Row: Status + Summary Stats */}
          <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
            <div className="bg-[#161B22] border border-[#30363D] rounded-lg p-4">
              <div className="text-xs text-[#8B949E] uppercase mb-2">Status</div>
              <StatusBadge status={metrics.status} />
            </div>

            <div className="bg-[#161B22] border border-[#30363D] rounded-lg p-4">
              <div className="text-xs text-[#8B949E] uppercase mb-2">Calls/Min</div>
              <div className="text-2xl font-bold text-[#79C0FF]">
                {metrics.total_calls_per_minute.toFixed(2)}
              </div>
              <div className="text-xs text-[#8B949E] mt-1">SLO: &lt;10/min</div>
            </div>

            <div className="bg-[#161B22] border border-[#30363D] rounded-lg p-4">
              <div className="text-xs text-[#8B949E] uppercase mb-2">Error Rate</div>
              <div className="text-2xl font-bold text-[#F85149]">
                {metrics.total_error_rate_pct.toFixed(2)}%
              </div>
              <div className="text-xs text-[#8B949E] mt-1">SLO: &lt;1%</div>
            </div>

            <div className="bg-[#161B22] border border-[#30363D] rounded-lg p-4">
              <div className="text-xs text-[#8B949E] uppercase mb-2">Skill Availability</div>
              <div className="text-2xl font-bold text-[#3FB950]">
                {metrics.skill_availability_pct.toFixed(2)}%
              </div>
              <div className="text-xs text-[#8B949E] mt-1">Target: 99%</div>
            </div>
          </div>

          {/* Middle Row: Graph + Error Gauge */}
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
            <div className="lg:col-span-2 bg-[#161B22] border border-[#30363D] rounded-lg p-6">
              <h3 className="text-sm font-semibold mb-4 text-[#C9D1D9]">Call Rate (Past 24h)</h3>
              <MetricsGraph metrics={metrics} />
            </div>

            <div className="bg-[#161B22] border border-[#30363D] rounded-lg p-6">
              <h3 className="text-sm font-semibold mb-4 text-[#C9D1D9]">Error Rate Distribution</h3>
              <ErrorGauge errorRate={metrics.total_error_rate_pct} />
            </div>
          </div>

          {/* API Breakdown */}
          <ApiBreakdown apis={metrics.apis} />

          {/* Baseline Report */}
          <BaselineReport metrics={metrics} lastUpdated={lastUpdated} />
        </>
      )}
    </div>
  );
}
