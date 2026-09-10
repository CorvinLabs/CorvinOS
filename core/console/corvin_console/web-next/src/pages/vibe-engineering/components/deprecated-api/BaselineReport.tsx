/**
 * BaselineReport Component
 *
 * Displays the Week 5 baseline metrics report with recommendations.
 * This is fetched from /v1/console/deprecated-apis/baseline endpoint (Phase 2).
 * For now, it compiles info from the current metrics.
 */

import React, { useEffect, useState } from 'react';
import { CurrentMetricsResponse } from '../../hooks/useDeprecatedApiMetrics';
import { AlertCircle, CheckCircle2 } from 'lucide-react';

interface BaselineReportProps {
  metrics: CurrentMetricsResponse;
  lastUpdated: string | null;
}

interface BaselineData {
  week_number: number;
  timestamp: string;
  baseline_calls_per_min: number;
  baseline_error_rate_pct: number;
  baseline_skill_availability_pct: number;
  top_3_apis: Array<[string, number]>;
  total_apis_used: number;
  recommendations: string[];
}

export function BaselineReport({ metrics, lastUpdated }: BaselineReportProps) {
  const [baseline, setBaseline] = useState<BaselineData | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    const fetchBaseline = async () => {
      setLoading(true);
      try {
        const response = await fetch('/v1/console/deprecated-apis/baseline', {
          credentials: 'include',
        });
        if (response.ok) {
          const data = (await response.json()) as BaselineData;
          setBaseline(data);
        }
      } catch (err) {
        console.error('Failed to fetch baseline:', err);
        // Fall back to generated baseline from current metrics
        setBaseline(generateBaseline(metrics));
      } finally {
        setLoading(false);
      }
    };

    fetchBaseline();
  }, [metrics]);

  return (
    <div className="bg-[#161B22] border border-[#30363D] rounded-lg p-6 space-y-6">
      <div>
        <h3 className="text-sm font-semibold text-[#C9D1D9]">Week 5 Baseline Report (Phase B Cleanup)</h3>
        <p className="text-xs text-[#8B949E] mt-1">
          Established {lastUpdated ? new Date(lastUpdated).toLocaleString() : 'now'}
        </p>
      </div>

      {loading ? (
        <div className="text-[#8B949E] text-sm">Loading baseline...</div>
      ) : baseline ? (
        <>
          {/* Baseline Metrics Grid */}
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div className="bg-[#0D1117] rounded p-3 border border-[#30363D]">
              <div className="text-xs text-[#8B949E] uppercase mb-1">Baseline Call Rate</div>
              <div className="text-xl font-bold text-[#79C0FF]">
                {baseline.baseline_calls_per_min.toFixed(2)} calls/min
              </div>
              <div className="text-xs text-[#8B949E] mt-2">Target (Phase B): &lt;10/min</div>
            </div>

            <div className="bg-[#0D1117] rounded p-3 border border-[#30363D]">
              <div className="text-xs text-[#8B949E] uppercase mb-1">Baseline Error Rate</div>
              <div className="text-xl font-bold text-[#F85149]">
                {baseline.baseline_error_rate_pct.toFixed(2)}%
              </div>
              <div className="text-xs text-[#8B949E] mt-2">Target: &lt;1%</div>
            </div>

            <div className="bg-[#0D1117] rounded p-3 border border-[#30363D]">
              <div className="text-xs text-[#8B949E] uppercase mb-1">Skill Availability</div>
              <div className="text-xl font-bold text-[#3FB950]">
                {baseline.baseline_skill_availability_pct.toFixed(2)}%
              </div>
              <div className="text-xs text-[#8B949E] mt-2">Target: 99%</div>
            </div>
          </div>

          {/* Top 3 APIs */}
          {baseline.top_3_apis.length > 0 && (
            <div>
              <div className="text-xs font-semibold text-[#C9D1D9] uppercase mb-3">Top 3 Most-Called APIs</div>
              <div className="space-y-2">
                {baseline.top_3_apis.map(([api, count], i) => (
                  <div key={api} className="flex items-center justify-between bg-[#0D1117] rounded p-3 border border-[#30363D]">
                    <div className="flex items-center gap-3">
                      <span className="text-[#8B949E] font-mono text-sm">{i + 1}.</span>
                      <span className="text-[#C9D1D9] font-mono text-sm">{api}</span>
                    </div>
                    <span className="text-[#79C0FF] font-semibold">{count} calls</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Recommendations */}
          {baseline.recommendations.length > 0 && (
            <div>
              <div className="text-xs font-semibold text-[#C9D1D9] uppercase mb-3">Recommendations</div>
              <div className="space-y-2">
                {baseline.recommendations.map((rec, i) => (
                  <div key={i} className="flex items-start gap-3 bg-[#0D1117] rounded p-3 border border-[#30363D]">
                    {rec.includes('safe') || rec.includes('No deprecated') ? (
                      <CheckCircle2 size={16} className="text-[#3FB950] flex-shrink-0 mt-0.5" />
                    ) : (
                      <AlertCircle size={16} className="text-[#D29922] flex-shrink-0 mt-0.5" />
                    )}
                    <span className="text-xs text-[#8B949E]">{rec}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* SLO Status */}
          <div className="border-t border-[#30363D] pt-4">
            <div className="flex items-center gap-2 text-xs text-[#8B949E]">
              <div
                className="w-2 h-2 rounded-full"
                style={{
                  backgroundColor: baseline.baseline_calls_per_min < 10 ? '#3FB950' : '#F85149',
                }}
              ></div>
              <span>
                SLO Status: {baseline.baseline_calls_per_min < 10 ? 'ON TRACK' : 'AT RISK'} — Phase C
                deletion target is {baseline.baseline_calls_per_min < 10 ? 'achievable' : 'requires attention'}
              </span>
            </div>
          </div>
        </>
      ) : null}
    </div>
  );
}

function generateBaseline(metrics: CurrentMetricsResponse): BaselineData {
  // Generate baseline from current metrics as fallback
  const top3 = metrics.apis.slice(0, 3).map((api) => [api.api_name, api.calls_total] as [string, number]);

  const recommendations = [];
  if (metrics.total_calls_per_minute > 50) {
    recommendations.push(
      'High deprecated API usage detected (>50/min); Phase C deletion may need adjustment'
    );
  } else if (metrics.total_calls_per_minute > 10) {
    recommendations.push(
      'Moderate deprecated API usage detected (10-50/min); monitor during Phase B cleanup'
    );
  } else if (metrics.total_calls_per_minute === 0) {
    recommendations.push('No deprecated API usage detected; Phase C deletion is safe');
  } else {
    recommendations.push(`Monitor ${metrics.apis.length} active deprecated APIs during Phase B`);
  }

  if (metrics.total_error_rate_pct > 1.0) {
    recommendations.push(
      'Error rate elevated (>1%); check compat layer or Skill health'
    );
  }

  if (metrics.skill_availability_pct < 99.0) {
    recommendations.push(
      'Skill availability below SLO (99%); investigate failures'
    );
  }

  return {
    week_number: 5,
    timestamp: new Date().toISOString(),
    baseline_calls_per_min: metrics.total_calls_per_minute,
    baseline_error_rate_pct: metrics.total_error_rate_pct,
    baseline_skill_availability_pct: metrics.skill_availability_pct,
    top_3_apis: top3,
    total_apis_used: metrics.apis.length,
    recommendations,
  };
}
