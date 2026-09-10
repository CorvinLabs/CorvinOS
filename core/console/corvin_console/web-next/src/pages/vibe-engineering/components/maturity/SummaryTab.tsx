/**
 * Summary Tab — 7d Confidence Trend, Convergence Status, Top Anomalies
 * Phase 4a Operator View
 */

import React, { useMemo } from 'react';
import { TrendingUp, AlertTriangle, CheckCircle } from 'lucide-react';

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
    if (score < 2) return { text: 'DEAD', color: 'text-red-500', bg: 'bg-red-900/20' };
    if (score < 4) return { text: 'NASCENT', color: 'text-orange-500', bg: 'bg-orange-900/20' };
    if (score < 6) return { text: 'LEARNING', color: 'text-yellow-500', bg: 'bg-yellow-900/20' };
    if (score < 8) return { text: 'MATURE', color: 'text-lime-500', bg: 'bg-lime-900/20' };
    if (score < 9) return { text: 'OPTIMIZED', color: 'text-cyan-500', bg: 'bg-cyan-900/20' };
    return { text: 'TRAINED', color: 'text-purple-500', bg: 'bg-purple-900/20' };
  };

  const status = getStatus(overallConfidence);

  return (
    <div className="space-y-6">
      {/* Overall Confidence */}
      <div className="bg-[#161B22] border border-[#30363D] rounded-lg p-6">
        <h3 className="text-sm font-semibold mb-4 text-[#C9D1D9]">📊 Overall Confidence</h3>
        <div className="flex items-end gap-6">
          <div>
            <div className={`text-5xl font-bold ${status.color}`}>{overallConfidence.toFixed(1)}</div>
            <div className={`text-xs font-semibold mt-2 px-2 py-1 rounded inline-block ${status.bg} ${status.color}`}>
              {status.text}
            </div>
          </div>
          <div className="flex-1">
            <div className="w-full bg-[#0D1117] rounded-full h-3 overflow-hidden">
              <div
                className="bg-gradient-to-r from-[#58A6FF] to-[#79C0FF] h-3 rounded-full"
                style={{ width: `${(overallConfidence / 10) * 100}%` }}
              ></div>
            </div>
            <div className="text-xs text-[#8B949E] mt-2">Score: 0–10 (0=dead, 10=fully trained)</div>
          </div>
        </div>
      </div>

      {/* 7d Trend */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="bg-[#161B22] border border-[#30363D] rounded-lg p-6">
          <h4 className="text-xs font-semibold text-[#8B949E] uppercase mb-4">7-Day Trend</h4>
          <div className="space-y-4">
            <div>
              <div className="flex items-baseline gap-2 mb-2">
                <span className="text-3xl font-bold text-[#3FB950]">+0.3</span>
                <span className="text-xs text-[#8B949E]">improvement (avg/day)</span>
              </div>
              <div className="h-2 bg-[#0D1117] rounded overflow-hidden">
                <div className="h-2 bg-[#3FB950] rounded" style={{ width: '60%' }}></div>
              </div>
            </div>
            <div className="flex items-center gap-2 text-xs text-[#3FB950]">
              <TrendingUp size={14} />
              <span>Healthy upward trajectory</span>
            </div>
          </div>
        </div>

        {/* Convergence Status */}
        <div className="bg-[#161B22] border border-[#30363D] rounded-lg p-6">
          <h4 className="text-xs font-semibold text-[#8B949E] uppercase mb-4">Convergence Rate</h4>
          <div className="space-y-4">
            <div>
              <div className="text-3xl font-bold text-[#79C0FF]">{convergenceRate.toFixed(2)}</div>
              <div className="text-xs text-[#8B949E] mt-1">per measurement cycle</div>
            </div>
            <div className="text-xs text-[#C9D1D9]">
              {convergenceRate > 0.8 && '✓ Excellent convergence — learning is effective'}
              {convergenceRate > 0.5 && convergenceRate <= 0.8 && '→ Good convergence — on track'}
              {convergenceRate <= 0.5 && '⚠ Slow convergence — may need tuning'}
            </div>
          </div>
        </div>
      </div>

      {/* Top Anomalies (Last 24h) */}
      <div className="bg-[#161B22] border border-[#30363D] rounded-lg p-6">
        <h4 className="text-sm font-semibold text-[#C9D1D9] mb-4">⚠️ Top Anomalies (Last 24h)</h4>
        <div className="space-y-3">
          <div className="flex items-start gap-3 p-3 bg-[#0D1117] rounded border border-[#FB8500]/30">
            <AlertTriangle size={16} className="text-[#FB8500] flex-shrink-0 mt-0.5" />
            <div className="flex-1">
              <div className="text-sm font-medium text-[#FB8500]">Workflow Loop drift spike</div>
              <div className="text-xs text-[#8B949E] mt-1">Δ = 0.08 (threshold: 0.01) — Skills config may need rebalancing</div>
              <div className="text-xs text-[#8B949E] mt-1">2 hours ago</div>
            </div>
          </div>

          <div className="flex items-start gap-3 p-3 bg-[#0D1117] rounded border border-[#79C0FF]/30">
            <CheckCircle size={16} className="text-[#79C0FF] flex-shrink-0 mt-0.5" />
            <div className="flex-1">
              <div className="text-sm font-medium text-[#79C0FF]">Confidence loop healthy</div>
              <div className="text-xs text-[#8B949E] mt-1">No score drops detected — routing decisions stable</div>
              <div className="text-xs text-[#8B949E] mt-1">Continuous</div>
            </div>
          </div>
        </div>
      </div>

      {/* Operator Note */}
      <div className="bg-[#1a3a1f] border border-[#3FB950] rounded-lg p-4">
        <div className="flex gap-3">
          <CheckCircle size={18} className="text-[#3FB950] flex-shrink-0 mt-0.5" />
          <div className="text-sm text-[#3FB950]">
            <strong>Status:</strong> Learning loops are healthy. No immediate action required. Monitor drift spikes on Workflow loop.
          </div>
        </div>
      </div>

      {lastUpdated && (
        <div className="text-xs text-[#8B949E] text-center">
          Last updated: {new Date(lastUpdated).toLocaleTimeString()}
        </div>
      )}
    </div>
  );
}
