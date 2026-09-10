/**
 * Maturity Metrics Dashboard — 9D Learning Loops Visualization (Phase 1)
 *
 * Shows: Hexagon Radar Chart + Score Header + Tier Breakdown Cards
 * Scoring: (Tier1_Avg * 0.40) + (Tier2_Avg * 0.35) + (Meta * 0.25)
 * Colors: Red(0-2) → Orange(2-4) → Yellow(4-6) → Lime(6-8) → Cyan(8-9) → Purple(9-10)
 *
 * Test data only (Phase 1). Phase 2 wires to live_measurements/ data source.
 */

import React, { useMemo, useState } from 'react';
import { TrendingUp, RefreshCw, AlertCircle } from 'lucide-react';
import { ScoreHeader } from './maturity/ScoreHeader';
import { HexagonRadar } from './maturity/HexagonRadar';
import { TierBreakdown } from './maturity/TierBreakdown';
import { HoverDetails } from './maturity/HoverDetails';
import { AnomalyAlerts } from './maturity/AnomalyAlerts';
import { SummaryTab } from './maturity/SummaryTab';
import { PatternsTab } from './maturity/PatternsTab';
import { DeprecatedApiTab } from './deprecated-api/DeprecatedApiTab';
import { MaturityData, LoopScores } from './maturity/types';
import { useLiveMaturityData, type TimeWindow } from '../hooks/useLiveMaturityData';

// Hardcoded test data (Phase 1)
const SAMPLE_LOOP_DATA: LoopScores = {
  // Tier 1: Core Loops
  confidence: 7.8,
  routing: 7.1,
  context: 7.4,
  workflow: 6.8,
  data_flow: 7.0,
  security: 7.3,
  // Tier 2: Infrastructure Loops
  memory: 7.2,
  skills: 6.9,
  plugins: 7.1,
  audit: 6.8,
  compliance: 7.0,
  system: 7.1,
  // Meta Loop
  meta_convergence: 8.1,
};

type DashboardTab = 'radar' | 'summary' | 'patterns' | 'deprecated-api';

export function MaturityDashboard() {
  const [windowPref, setWindow] = useState<TimeWindow>('7d');
  const [selectedLoop, setSelectedLoop] = useState<{ name: string; key: string } | null>(null);
  const [activeTab, setActiveTab] = useState<DashboardTab>('radar');
  const { loopScores, loading, error, lastUpdated, refresh } = useLiveMaturityData({ window: windowPref });

  // Use live data if available, fallback to sample data
  const loopData = loopScores || SAMPLE_LOOP_DATA;
  const data = useMemo(() => computeMaturityScore(loopData), [loopData]);

  return (
    <div className="space-y-6 p-6">
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
                  ? 'bg-[#58A6FF] text-[#0D1117]'
                  : 'bg-[#30363D] text-[#C9D1D9] hover:bg-[#3d444d]'
              }`}
            >
              {w === 'today' ? '24h' : w}
            </button>
          ))}
        </div>
        <button
          onClick={refresh}
          disabled={loading}
          className="flex items-center gap-2 px-3 py-1 rounded bg-[#30363D] text-[#C9D1D9] hover:bg-[#3d444d] disabled:opacity-50 transition-all"
        >
          <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
          Refresh
        </button>
      </div>

      {/* Error Alert */}
      {error && (
        <div className="bg-[#3d1f1a] border border-[#F85149] rounded p-4 flex items-center gap-3">
          <AlertCircle size={18} className="text-[#F85149] flex-shrink-0" />
          <div className="text-sm text-[#F85149]">{error}</div>
        </div>
      )}

      {/* Loading State */}
      {loading && !loopScores && (
        <div className="text-center py-12">
          <div className="inline-flex items-center gap-2 text-[#8B949E]">
            <RefreshCw size={16} className="animate-spin" />
            <span>Loading live maturity data...</span>
          </div>
        </div>
      )}

      {/* Score Header */}
      {loopScores && <ScoreHeader data={data} />}

      {/* Tab Navigation */}
      {loopScores && (
        <div className="flex gap-2 border-b border-[#30363D]">
          <button
            onClick={() => setActiveTab('radar')}
            className={`px-4 py-2 text-sm font-medium transition-all ${
              activeTab === 'radar'
                ? 'border-b-2 border-[#58A6FF] text-[#58A6FF]'
                : 'text-[#8B949E] hover:text-[#C9D1D9]'
            }`}
          >
            📊 Hexagon Radar
          </button>
          <button
            onClick={() => setActiveTab('summary')}
            className={`px-4 py-2 text-sm font-medium transition-all ${
              activeTab === 'summary'
                ? 'border-b-2 border-[#58A6FF] text-[#58A6FF]'
                : 'text-[#8B949E] hover:text-[#C9D1D9]'
            }`}
          >
            📈 Summary
          </button>
          <button
            onClick={() => setActiveTab('patterns')}
            className={`px-4 py-2 text-sm font-medium transition-all ${
              activeTab === 'patterns'
                ? 'border-b-2 border-[#58A6FF] text-[#58A6FF]'
                : 'text-[#8B949E] hover:text-[#C9D1D9]'
            }`}
          >
            🔍 Patterns
          </button>
          <button
            onClick={() => setActiveTab('deprecated-api')}
            className={`px-4 py-2 text-sm font-medium transition-all ${
              activeTab === 'deprecated-api'
                ? 'border-b-2 border-[#58A6FF] text-[#58A6FF]'
                : 'text-[#8B949E] hover:text-[#C9D1D9]'
            }`}
          >
            ⚠️ Deprecated APIs
          </button>
        </div>
      )}

      {/* Tab Content */}
      {activeTab === 'radar' && loopScores && (
        <>
          {/* Hexagon Radar + Tier Breakdown + Meta + Recommendations */}
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
            <div className="lg:col-span-2">
              <div className="bg-[#161B22] border border-[#30363D] rounded-lg p-6">
                <h3 className="text-sm font-semibold mb-4 text-[#C9D1D9]">
                  Learning Loops — 9D Maturity Radar (click loop for details)
                </h3>
                <HexagonRadar
                  loopScores={loopData}
                  onLoopClick={(name, key) => setSelectedLoop({ name, key })}
                />
              </div>
            </div>

            {/* Trend Card */}
            <div className="bg-[#161B22] border border-[#30363D] rounded-lg p-6 flex flex-col justify-between">
              <div>
                <h4 className="text-xs font-semibold text-[#8B949E] uppercase mb-4">Trend</h4>
                <div className="space-y-4">
                  <div>
                    <div className="flex items-baseline gap-2 mb-1">
                      <span className="text-2xl font-bold text-[#58A6FF]">+0.3</span>
                      <span className="text-xs text-[#8B949E]">last 7 days</span>
                    </div>
                    <div className="w-full bg-[#0D1117] rounded h-2">
                      <div className="bg-gradient-to-r from-[#58A6FF] to-[#79C0FF] h-2 rounded" style={{ width: '75%' }}></div>
                    </div>
                  </div>
                  <div>
                    <div className="text-xs text-[#8B949E] mb-1">Projected (30 days)</div>
                    <div className="text-xl font-bold text-[#79C0FF]">8.2/10</div>
                  </div>
                </div>
              </div>
              <div className="mt-6 pt-6 border-t border-[#30363D]">
                <div className="flex items-center gap-2 text-xs text-[#8B949E]">
                  <TrendingUp size={14} />
                  <span>Stable trajectory</span>
                </div>
              </div>
            </div>
          </div>

          <TierBreakdown loopScores={loopScores} />

          <div className="bg-[#161B22] border border-[#30363D] rounded-lg p-6">
            <h3 className="text-sm font-semibold mb-4 text-[#C9D1D9]">
              Meta Loop — Hyperparameter Tuning
            </h3>
            <div className="grid grid-cols-3 gap-4">
              <div className="space-y-2">
                <div className="text-xs text-[#8B949E] uppercase">Convergence Rate</div>
                <div className="text-lg font-semibold text-[#79C0FF]">0.87</div>
                <div className="text-xs text-[#3FB950]">Excellent</div>
              </div>
              <div className="space-y-2">
                <div className="text-xs text-[#8B949E] uppercase">Drift</div>
                <div className="text-lg font-semibold text-[#79C0FF]">0.12</div>
                <div className="text-xs text-[#3FB950]">Minimal</div>
              </div>
              <div className="space-y-2">
                <div className="text-xs text-[#8B949E] uppercase">Prediction Accuracy</div>
                <div className="text-lg font-semibold text-[#79C0FF]">92.3%</div>
                <div className="text-xs text-[#3FB950]">Highly accurate</div>
              </div>
            </div>
          </div>

          <div className="bg-[#161B22] border border-[#30363D] rounded-lg p-6">
            <h3 className="text-sm font-semibold mb-4 text-[#C9D1D9]">💡 Recommendations</h3>
            <ul className="space-y-3 text-sm text-[#8B949E]">
              <li className="flex gap-3">
                <span className="text-[#F85149] flex-shrink-0">1.</span>
                <span>Workflow Loop ({loopScores.workflow.toFixed(1)}) is bottleneck — focus on this</span>
              </li>
              <li className="flex gap-3">
                <span className="text-[#F85149] flex-shrink-0">2.</span>
                <span>Skills config drift detected — rebalance weights</span>
              </li>
              <li className="flex gap-3">
                <span className="text-[#3FB950] flex-shrink-0">3.</span>
                <span>Meta-loop converging well — stable trajectory</span>
              </li>
            </ul>
          </div>
        </>
      )}

      {/* Summary Tab */}
      {activeTab === 'summary' && loopScores && (
        <SummaryTab loopScores={loopData} lastUpdated={lastUpdated} />
      )}

      {/* Patterns Tab */}
      {activeTab === 'patterns' && loopScores && (
        <PatternsTab loopScores={loopData} />
      )}

      {/* Deprecated API Tab */}
      {activeTab === 'deprecated-api' && (
        <DeprecatedApiTab />
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

function computeMaturityScore(loops: LoopScores): MaturityData {
  const tier1 = [
    loops.confidence,
    loops.routing,
    loops.context,
    loops.workflow,
    loops.data_flow,
    loops.security,
  ];

  const tier2 = [
    loops.memory,
    loops.skills,
    loops.plugins,
    loops.audit,
    loops.compliance,
    loops.system,
  ];

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
    trend: { direction: 'up', value: 0.3 },
    projection: 8.2,
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
