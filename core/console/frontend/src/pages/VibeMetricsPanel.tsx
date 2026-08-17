/**
 * VibeMetrics Dashboard Panel (Phase 2.K=3)
 *
 * Real-time token measurement dashboard with live polling.
 * Shows: summary stats, trends, subsystem breakdown, per-turn details.
 */

import React, { useState, useEffect } from 'react';
import { LineChart, Line, BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer, TabContainer, Tabs, Tab } from 'recharts';
import './VibeMetricsPanel.css';


interface MetricsSummary {
  session_id: string;
  timestamp: string;
  turn_count: number;
  total_tokens: number;
  baseline_tokens: number;
  savings_tokens: number;
  savings_percent: number;
  avg_tokens_per_turn: number;
  is_significant: boolean;
  confidence: number;
}

interface MetricsTurn {
  turn_id: string;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  savings_percent: number;
  task_type?: string;
  outcome_quality?: string;
  latency_ms?: number;
}

interface MetricsDetail {
  session_id: string;
  timestamp: string;
  summary: MetricsSummary;
  turns: MetricsTurn[];
  by_task_type: Record<string, any>;
  subsystems: Record<string, any>;
}


export const VibeMetricsPanel: React.FC<{ sessionId: string }> = ({ sessionId }) => {
  const [data, setData] = useState<MetricsDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState('summary');
  const [trendData, setTrendData] = useState<any[]>([]);

  // Poll API every 5 seconds for live updates
  useEffect(() => {
    const fetchMetrics = async () => {
      try {
        const res = await fetch(`/api/metrics/session/${sessionId}`);
        if (res.ok) {
          const detail: MetricsDetail = await res.json();
          setData(detail);
          setLoading(false);

          // Build trend data (turn #, total_tokens)
          const trend = detail.turns.map((t, idx) => ({
            turn: idx + 1,
            tokens: t.total_tokens,
            savings: t.savings_percent,
          }));
          setTrendData(trend);
        }
      } catch (err) {
        console.error('Failed to fetch metrics:', err);
      }
    };

    fetchMetrics();
    const interval = setInterval(fetchMetrics, 5000); // Poll every 5s

    return () => clearInterval(interval);
  }, [sessionId]);

  if (loading) {
    return <div className="vibe-loading">Loading metrics...</div>;
  }

  if (!data) {
    return <div className="vibe-error">No metrics available</div>;
  }

  const { summary, turns, by_task_type, subsystems } = data;

  return (
    <div className="vibe-metrics-panel">
      <header className="vibe-header">
        <h1>⚡ VibeMetrics Dashboard</h1>
        <span className="vibe-timestamp">{new Date(summary.timestamp).toLocaleTimeString()}</span>
      </header>

      {/* Summary Widget */}
      <div className="vibe-summary-grid">
        <div className="vibe-stat-card">
          <div className="stat-label">Total Tokens</div>
          <div className="stat-value">{summary.total_tokens.toLocaleString()}</div>
          <div className="stat-detail">across {summary.turn_count} turns</div>
        </div>

        <div className="vibe-stat-card highlight">
          <div className="stat-label">Savings</div>
          <div className="stat-value">{summary.savings_percent.toFixed(1)}%</div>
          <div className="stat-detail">{summary.savings_tokens.toLocaleString()} tokens saved</div>
        </div>

        <div className="vibe-stat-card">
          <div className="stat-label">Avg per Turn</div>
          <div className="stat-value">{summary.avg_tokens_per_turn}</div>
          <div className="stat-detail">baseline: {(summary.baseline_tokens / summary.turn_count).toFixed(0)}</div>
        </div>

        <div className={`vibe-stat-card confidence ${summary.is_significant ? 'significant' : 'not-significant'}`}>
          <div className="stat-label">Confidence</div>
          <div className="stat-value">{(summary.confidence * 100).toFixed(0)}%</div>
          <div className="stat-detail">{summary.is_significant ? 'Significant ✓' : 'Low confidence'}</div>
        </div>
      </div>

      {/* Tabs */}
      <div className="vibe-tabs">
        <div className="tab-buttons">
          <button
            className={`tab-btn ${activeTab === 'summary' ? 'active' : ''}`}
            onClick={() => setActiveTab('summary')}
          >
            📊 Summary
          </button>
          <button
            className={`tab-btn ${activeTab === 'trend' ? 'active' : ''}`}
            onClick={() => setActiveTab('trend')}
          >
            📈 Trend
          </button>
          <button
            className={`tab-btn ${activeTab === 'breakdown' ? 'active' : ''}`}
            onClick={() => setActiveTab('breakdown')}
          >
            🔍 Breakdown
          </button>
          <button
            className={`tab-btn ${activeTab === 'details' ? 'active' : ''}`}
            onClick={() => setActiveTab('details')}
          >
            📋 Details
          </button>
        </div>

        {/* Summary Tab */}
        {activeTab === 'summary' && (
          <div className="tab-content">
            <h3>Session Metrics</h3>
            <div className="summary-table">
              <tr>
                <td>Session ID</td>
                <td className="mono">{summary.session_id}</td>
              </tr>
              <tr>
                <td>Turns Recorded</td>
                <td>{summary.turn_count}</td>
              </tr>
              <tr>
                <td>Total Tokens (Vibe)</td>
                <td className="token-count">{summary.total_tokens.toLocaleString()}</td>
              </tr>
              <tr>
                <td>Baseline (Native)</td>
                <td className="token-count">{summary.baseline_tokens.toLocaleString()}</td>
              </tr>
              <tr>
                <td>Token Savings</td>
                <td className="savings">{summary.savings_tokens.toLocaleString()} ({summary.savings_percent.toFixed(1)}%)</td>
              </tr>
              <tr>
                <td>Confidence Level</td>
                <td className={summary.is_significant ? 'significant' : 'low-confidence'}>
                  {(summary.confidence * 100).toFixed(0)}% {summary.is_significant ? '✓ Significant' : '✗ Low'}
                </td>
              </tr>
            </div>
          </div>
        )}

        {/* Trend Tab */}
        {activeTab === 'trend' && (
          <div className="tab-content">
            <h3>Token Trend (last 24h)</h3>
            <ResponsiveContainer width="100%" height={300}>
              <LineChart data={trendData}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="turn" />
                <YAxis />
                <Tooltip />
                <Legend />
                <Line type="monotone" dataKey="tokens" stroke="#00d9ff" name="Total Tokens" />
                <Line type="monotone" dataKey="savings" stroke="#00ff41" name="Savings %" />
              </LineChart>
            </ResponsiveContainer>
          </div>
        )}

        {/* Breakdown Tab */}
        {activeTab === 'breakdown' && (
          <div className="tab-content">
            <h3>Subsystem Attribution</h3>
            <ResponsiveContainer width="100%" height={300}>
              <BarChart
                data={Object.entries(subsystems).map(([name, stats]) => ({
                  name,
                  tokens: stats.total_tokens,
                  turns: stats.count,
                }))}
              >
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="name" />
                <YAxis />
                <Tooltip />
                <Legend />
                <Bar dataKey="tokens" fill="#00d9ff" name="Token Count" />
              </BarChart>
            </ResponsiveContainer>

            <h3>By Task Type</h3>
            <div className="task-type-breakdown">
              {Object.entries(by_task_type).map(([task, stats]: [string, any]) => (
                <div key={task} className="task-card">
                  <div className="task-name">{task}</div>
                  <div className="task-stat">{stats.turns} turns</div>
                  <div className="task-stat">{stats.total_tokens.toLocaleString()} tokens</div>
                  <div className="task-savings">{stats.savings_percent.toFixed(1)}% savings</div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Details Tab */}
        {activeTab === 'details' && (
          <div className="tab-content">
            <h3>Per-Turn Metrics ({turns.length} turns)</h3>
            <div className="details-table">
              <thead>
                <tr>
                  <th>Turn</th>
                  <th>Input</th>
                  <th>Output</th>
                  <th>Total</th>
                  <th>Savings %</th>
                  <th>Task Type</th>
                  <th>Quality</th>
                </tr>
              </thead>
              <tbody>
                {turns.map((turn, idx) => (
                  <tr key={turn.turn_id}>
                    <td className="mono">{turn.turn_id.substring(0, 8)}</td>
                    <td>{turn.input_tokens}</td>
                    <td>{turn.output_tokens}</td>
                    <td className="token-count">{turn.total_tokens}</td>
                    <td className="savings">{turn.savings_percent.toFixed(1)}%</td>
                    <td>{turn.task_type || '—'}</td>
                    <td>{turn.outcome_quality || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </div>
          </div>
        )}
      </div>

      {/* Footer */}
      <div className="vibe-footer">
        <p>Last updated: {new Date(summary.timestamp).toLocaleTimeString()}</p>
        <p className="vibe-info">Polling every 5 seconds • Data from EventStore + SQLite backend</p>
      </div>
    </div>
  );
};

export default VibeMetricsPanel;
