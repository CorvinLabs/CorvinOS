/**
 * Learning Dashboard (Stream 2: Stories 14-16)
 *
 * Three dashboard panels:
 * 1. Confidence Trend (line chart) — Story 14
 * 2. Feedback Volume (bar chart) — Story 15
 * 3. Optimizer Metrics (KPI tiles) — Story 16
 */

import React, { useState, useEffect } from 'react';
import { LineChart, Line, BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer } from 'recharts';

interface DashboardData {
  confidence_trends: Array<{
    date: string;
    confidence: number;
    success_rate: number;
    error_rate: number;
  }>;
  feedback_volume: {
    P0: number;
    P1: number;
    P2: number;
    P3: number;
  };
  optimizer_metrics: {
    total_tuning_events: number;
    config_deltas_applied: number;
    converged_skills: number;
    total_skills: number;
    convergence_percentage: number;
    last_tuning_event: string;
  };
}

export const LearningDashboard: React.FC = () => {
  const [data, setData] = useState<DashboardData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const fetchDashboard = async () => {
      try {
        const response = await fetch('/v1/console/learning/optimizer/dashboard');
        if (!response.ok) throw new Error('Failed to fetch dashboard');
        const result = await response.json();
        setData(result.dashboard);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Unknown error');
      } finally {
        setLoading(false);
      }
    };

    fetchDashboard();
    const interval = setInterval(fetchDashboard, 30000); // Refresh every 30s
    return () => clearInterval(interval);
  }, []);

  if (loading) return <div className="p-4">Loading learning dashboard...</div>;
  if (error) return <div className="p-4 text-red-600">Error: {error}</div>;
  if (!data) return <div className="p-4">No data available</div>;

  // Story 14: Confidence Trend
  const confidenceTrendData = data.confidence_trends.map(item => ({
    date: new Date(item.date).toLocaleDateString('en-US', { month: 'short', day: 'numeric' }),
    confidence: Math.round(item.confidence * 100),
    success_rate: Math.round(item.success_rate * 100),
  }));

  // Story 15: Feedback Volume
  const feedbackVolumeData = [
    { name: 'P0 (Critical)', value: data.feedback_volume.P0, fill: '#dc2626' },
    { name: 'P1 (High)', value: data.feedback_volume.P1, fill: '#ea580c' },
    { name: 'P2 (Medium)', value: data.feedback_volume.P2, fill: '#f59e0b' },
    { name: 'P3 (Low)', value: data.feedback_volume.P3, fill: '#d1d5db' },
  ];

  // Story 16: Optimizer Metrics (KPI tiles)
  const metrics = data.optimizer_metrics;

  return (
    <div className="space-y-6 p-6 bg-gray-50">
      <h1 className="text-2xl font-bold">Learning Loop Dashboard</h1>

      {/* Story 14: Confidence Trend Chart */}
      <div className="bg-white rounded-lg shadow p-6">
        <h2 className="text-xl font-semibold mb-4">Skill Confidence Trend (7 days)</h2>
        {confidenceTrendData.length > 0 ? (
          <ResponsiveContainer width="100%" height={300}>
            <LineChart data={confidenceTrendData}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="date" />
              <YAxis domain={[0, 100]} label={{ value: 'Confidence %', angle: -90, position: 'insideLeft' }} />
              <Tooltip formatter={(value) => `${value}%`} />
              <Legend />
              <Line
                type="monotone"
                dataKey="confidence"
                stroke="#10b981"
                strokeWidth={2}
                dot={{ fill: '#10b981', r: 4 }}
                activeDot={{ r: 6 }}
                name="Confidence"
              />
              <Line
                type="monotone"
                dataKey="success_rate"
                stroke="#3b82f6"
                strokeWidth={2}
                dot={{ fill: '#3b82f6', r: 4 }}
                strokeDasharray="5 5"
                name="Success Rate"
              />
            </LineChart>
          </ResponsiveContainer>
        ) : (
          <div className="text-gray-500 text-center py-12">No trend data available yet</div>
        )}
      </div>

      {/* Story 15: Feedback Volume Chart */}
      <div className="bg-white rounded-lg shadow p-6">
        <h2 className="text-xl font-semibold mb-4">Feedback Volume by Priority</h2>
        <ResponsiveContainer width="100%" height={300}>
          <BarChart data={feedbackVolumeData}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis dataKey="name" />
            <YAxis label={{ value: 'Count', angle: -90, position: 'insideLeft' }} />
            <Tooltip />
            <Bar dataKey="value" fill="#3b82f6" radius={[8, 8, 0, 0]} />
          </BarChart>
        </ResponsiveContainer>
        <div className="mt-4 grid grid-cols-4 gap-4 text-center">
          {feedbackVolumeData.map((item) => (
            <div key={item.name}>
              <div className="text-2xl font-bold" style={{ color: item.fill }}>
                {item.value}
              </div>
              <div className="text-sm text-gray-600">{item.name.split(' ')[0]}</div>
            </div>
          ))}
        </div>
      </div>

      {/* Story 16: Optimizer Metrics (KPI Tiles) */}
      <div className="bg-white rounded-lg shadow p-6">
        <h2 className="text-xl font-semibold mb-4">Optimizer Metrics</h2>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          {/* Total Tuning Events */}
          <div className="bg-gradient-to-br from-blue-50 to-blue-100 rounded-lg p-4">
            <div className="text-gray-600 text-sm font-medium">Tuning Events</div>
            <div className="text-3xl font-bold text-blue-900">{metrics.total_tuning_events}</div>
            <div className="text-xs text-gray-500 mt-1">config changes</div>
          </div>

          {/* Config Deltas Applied */}
          <div className="bg-gradient-to-br from-green-50 to-green-100 rounded-lg p-4">
            <div className="text-gray-600 text-sm font-medium">Deltas Applied</div>
            <div className="text-3xl font-bold text-green-900">{metrics.config_deltas_applied}</div>
            <div className="text-xs text-gray-500 mt-1">successful changes</div>
          </div>

          {/* Converged Skills */}
          <div className="bg-gradient-to-br from-purple-50 to-purple-100 rounded-lg p-4">
            <div className="text-gray-600 text-sm font-medium">Converged</div>
            <div className="text-3xl font-bold text-purple-900">
              {metrics.converged_skills}/{metrics.total_skills}
            </div>
            <div className="text-xs text-gray-500 mt-1">skills optimized</div>
          </div>

          {/* Convergence % */}
          <div className="bg-gradient-to-br from-amber-50 to-amber-100 rounded-lg p-4">
            <div className="text-gray-600 text-sm font-medium">Convergence</div>
            <div className="text-3xl font-bold text-amber-900">{metrics.convergence_percentage.toFixed(1)}%</div>
            <div className="text-xs text-gray-500 mt-1">optimization plateau</div>
          </div>
        </div>

        {/* Last Tuning Event */}
        <div className="mt-4 p-3 bg-gray-50 rounded text-sm">
          <span className="text-gray-600">Last tuning: </span>
          <span className="font-mono text-gray-800">
            {metrics.last_tuning_event === 'never'
              ? 'Never'
              : new Date(metrics.last_tuning_event).toLocaleString()}
          </span>
        </div>
      </div>

      {/* Footer */}
      <div className="text-xs text-gray-500 text-center">
        Dashboard auto-refreshes every 30 seconds • Last updated: {new Date().toLocaleTimeString()}
      </div>
    </div>
  );
};

export default LearningDashboard;
