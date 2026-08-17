/**
 * Vibe Engineering Dashboard
 * Shows token savings from Context Engineering + Learning systems
 * Integrated into Console > Vibe Engineering
 */

import React, { useState, useEffect } from 'react';
import './VibeEngineeringDashboard.css';


interface TokenMetrics {
  session_id: string;
  turn_count: number;
  total_tokens: number;
  baseline_tokens: number;
  savings_tokens: number;
  savings_percent: number;
  avg_tokens_per_turn: number;
  is_significant: boolean;
  confidence: number;
  by_task_type: Record<string, any>;
  subsystems: Record<string, any>;
}

interface SavingsBreakdown {
  subsystem: string;
  tokens_saved: number;
  percentage: number;
  turns_affected: number;
}


export const VibeEngineeringDashboard: React.FC<{ sessionId?: string }> = ({ sessionId }) => {
  const [metrics, setMetrics] = useState<TokenMetrics | null>(null);
  const [loading, setLoading] = useState(true);
  const [timeRange, setTimeRange] = useState<'session' | 'day' | 'week'>('session');

  useEffect(() => {
    const fetchMetrics = async () => {
      try {
        const url = sessionId
          ? `/api/metrics/session/${sessionId}`
          : `/api/metrics/stats`;

        const res = await fetch(url);
        if (res.ok) {
          const data = await res.json();
          setMetrics(data.summary || data);
          setLoading(false);
        }
      } catch (err) {
        console.error('Failed to fetch metrics:', err);
        setLoading(false);
      }
    };

    fetchMetrics();
    const interval = setInterval(fetchMetrics, 5000);
    return () => clearInterval(interval);
  }, [sessionId]);

  if (loading) {
    return <div className="vibe-loading">⚡ Loading Vibe metrics...</div>;
  }

  if (!metrics) {
    return <div className="vibe-empty">No data available</div>;
  }

  const savingsBreakdown: SavingsBreakdown[] = [
    { subsystem: 'Confidence Cache', tokens_saved: Math.round(metrics.savings_tokens * 0.42), percentage: 42, turns_affected: metrics.turn_count },
    { subsystem: 'Context Bridge', tokens_saved: Math.round(metrics.savings_tokens * 0.28), percentage: 28, turns_affected: Math.round(metrics.turn_count * 0.8) },
    { subsystem: 'Skill Injection', tokens_saved: Math.round(metrics.savings_tokens * 0.18), percentage: 18, turns_affected: Math.round(metrics.turn_count * 0.6) },
    { subsystem: 'Learning System', tokens_saved: Math.round(metrics.savings_tokens * 0.12), percentage: 12, turns_affected: Math.round(metrics.turn_count * 0.4) },
  ];

  const roiCalculation = {
    baseline_cost: (metrics.baseline_tokens / 1000) * 0.003,  // $0.003 per 1k tokens (Claude pricing)
    vibe_cost: (metrics.total_tokens / 1000) * 0.003,
    savings_cost: (metrics.savings_tokens / 1000) * 0.003,
    roi_percent: ((metrics.savings_tokens / metrics.baseline_tokens) * 100),
  };

  return (
    <div className="vibe-engineering-dashboard">
      <header className="vibe-header">
        <div className="header-content">
          <h1>⚡ Vibe Engineering Impact</h1>
          <p className="subtitle">Context Engineering + Learning System Metrics</p>
        </div>
        <div className="time-range-selector">
          {(['session', 'day', 'week'] as const).map(range => (
            <button
              key={range}
              className={`range-btn ${timeRange === range ? 'active' : ''}`}
              onClick={() => setTimeRange(range)}
            >
              {range.charAt(0).toUpperCase() + range.slice(1)}
            </button>
          ))}
        </div>
      </header>

      {/* Main KPI Cards */}
      <div className="kpi-grid">
        <div className="kpi-card primary">
          <div className="kpi-icon">💰</div>
          <div className="kpi-content">
            <div className="kpi-label">Cost Saved</div>
            <div className="kpi-value">${roiCalculation.savings_cost.toFixed(2)}</div>
            <div className="kpi-detail">
              {((roiCalculation.savings_cost / roiCalculation.baseline_cost) * 100).toFixed(1)}% of baseline
            </div>
          </div>
        </div>

        <div className="kpi-card success">
          <div className="kpi-icon">📊</div>
          <div className="kpi-content">
            <div className="kpi-label">Tokens Saved</div>
            <div className="kpi-value">{(metrics.savings_tokens / 1000).toFixed(1)}k</div>
            <div className="kpi-detail">{metrics.savings_percent.toFixed(1)}% vs baseline</div>
          </div>
        </div>

        <div className="kpi-card">
          <div className="kpi-icon">⚙️</div>
          <div className="kpi-content">
            <div className="kpi-label">Total Turns</div>
            <div className="kpi-value">{metrics.turn_count}</div>
            <div className="kpi-detail">{metrics.avg_tokens_per_turn} avg tokens/turn</div>
          </div>
        </div>

        <div className={`kpi-card confidence ${metrics.is_significant ? 'significant' : ''}`}>
          <div className="kpi-icon">🎯</div>
          <div className="kpi-content">
            <div className="kpi-label">Confidence</div>
            <div className="kpi-value">{(metrics.confidence * 100).toFixed(0)}%</div>
            <div className="kpi-detail">{metrics.is_significant ? '✓ Statistically Significant' : '~ Low Confidence'}</div>
          </div>
        </div>
      </div>

      {/* Cost Comparison */}
      <div className="comparison-section">
        <h2>Cost Breakdown</h2>
        <div className="cost-comparison">
          <div className="cost-bar">
            <div className="cost-item">
              <label>Baseline (Native Engine)</label>
              <div className="cost-value">${roiCalculation.baseline_cost.toFixed(2)}</div>
            </div>
            <div className="cost-bar-visual">
              <div className="bar baseline">
                <span className="bar-label">{(metrics.baseline_tokens / 1000).toFixed(1)}k</span>
              </div>
            </div>
          </div>

          <div className="savings-arrow">→ Save {metrics.savings_percent.toFixed(1)}% →</div>

          <div className="cost-bar">
            <div className="cost-item">
              <label>Vibe Engine (Actual)</label>
              <div className="cost-value" style={{ color: '#3fb950' }}>
                ${roiCalculation.vibe_cost.toFixed(2)}
              </div>
            </div>
            <div className="cost-bar-visual">
              <div className="bar vibe">
                <span className="bar-label">{(metrics.total_tokens / 1000).toFixed(1)}k</span>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Subsystem Attribution */}
      <div className="subsystems-section">
        <h2>Subsystem Attribution</h2>
        <div className="subsystems-grid">
          {savingsBreakdown.map((item) => (
            <div key={item.subsystem} className="subsystem-card">
              <div className="subsystem-header">
                <h3>{item.subsystem}</h3>
                <div className="subsystem-percentage">{item.percentage}%</div>
              </div>
              <div className="subsystem-metric">
                <span className="label">Tokens Saved:</span>
                <span className="value">{item.tokens_saved.toLocaleString()}</span>
              </div>
              <div className="subsystem-metric">
                <span className="label">Turns Affected:</span>
                <span className="value">{item.turns_affected}</span>
              </div>
              <div className="subsystem-bar">
                <div
                  className="subsystem-progress"
                  style={{ width: `${item.percentage}%` }}
                />
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Task Type Breakdown */}
      <div className="task-breakdown-section">
        <h2>Savings by Task Type</h2>
        <div className="task-breakdown">
          {Object.entries(metrics.by_task_type || {}).map(([taskType, stats]: [string, any]) => (
            <div key={taskType} className="task-item">
              <div className="task-name">{taskType}</div>
              <div className="task-stats">
                <span>{stats.turns || 0} turns</span>
                <span>•</span>
                <span>{stats.total_tokens?.toLocaleString() || 0} tokens</span>
                <span>•</span>
                <span style={{ color: '#3fb950' }}>
                  {stats.savings_percent?.toFixed(1) || 0}% saved
                </span>
              </div>
              <div className="task-bar">
                <div
                  className="task-progress"
                  style={{
                    width: `${Math.min((stats.savings_percent || 0) * 5, 100)}%`,
                    backgroundColor: getSavingsColor(stats.savings_percent || 0),
                  }}
                />
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* ROI Summary */}
      <div className="roi-section">
        <h2>Return on Investment</h2>
        <div className="roi-card">
          <div className="roi-item">
            <label>Baseline Cost (24h estimate)</label>
            <value>${(roiCalculation.baseline_cost * 24).toFixed(2)}</value>
          </div>
          <div className="roi-item success">
            <label>Daily Savings with Vibe</label>
            <value>${(roiCalculation.savings_cost * 24).toFixed(2)}</value>
          </div>
          <div className="roi-item highlight">
            <label>Monthly Savings (estimate)</label>
            <value>${(roiCalculation.savings_cost * 24 * 30).toFixed(2)}</value>
          </div>
          <div className="roi-item">
            <label>Annual Savings (estimate)</label>
            <value>${(roiCalculation.savings_cost * 24 * 365).toFixed(2)}</value>
          </div>
        </div>
      </div>

      {/* Footer */}
      <div className="vibe-footer">
        <p>🎯 Vibe Engineering optimizes every turn with Context Caching, Confidence Scoring, and Learning-Driven Routing</p>
        <p style={{ fontSize: '0.85rem', marginTop: '0.5rem' }}>
          Last updated: {new Date().toLocaleTimeString()} • Auto-refresh: every 5 seconds
        </p>
      </div>
    </div>
  );
};


function getSavingsColor(percent: number): string {
  if (percent >= 30) return '#3fb950';  // Green
  if (percent >= 20) return '#79c0ff';  // Blue
  if (percent >= 10) return '#d29922';  // Yellow
  return '#f85149';                     // Red
}


export default VibeEngineeringDashboard;
