/**
 * Model Selection Learning Dashboard — ADR-0377 Phase 2b
 *
 * Displays:
 * - Convergence status per task type (simple/medium/complex)
 * - Learned threshold values (compared to base 0.5)
 * - Cost efficiency metrics (savings since Phase 2 deployed)
 * - Quality maintenance (accuracy trends)
 * - Operator controls (manual override, reset, export/import)
 * - Real-time updates via polling
 */

import React, { useState, useEffect } from 'react';
import {
  LineChart, Line, BarChart, Bar,
  XAxis, YAxis, CartesianGrid, Tooltip, Legend,
  ResponsiveContainer, Area, AreaChart,
} from 'recharts';
import { AlertCircle, CheckCircle, Clock, Download, Upload, RotateCcw } from 'lucide-react';

interface ThresholdData {
  task_type: string;
  subsystem: string;
  learned_threshold: number;
  base_threshold: number;
  sample_count: number;
  converged: boolean;
  timestamp: string;
}

interface DashboardStatus {
  converged_count: number;
  total_count: number;
  thresholds: ThresholdData[];
  cost_savings_percent: number;
  cost_baseline_usd: number;
  cost_current_usd: number;
  accuracy_percent: number;
  last_updated: string;
}

interface ThresholdHistory {
  timestamp: string;
  task_type: string;
  value: number;
}

const COLORS = {
  primary: '#3b82f6',
  success: '#10b981',
  warning: '#f59e0b',
  danger: '#ef4444',
  secondary: '#8b5cf6',
};

const TaskTypeLabels: Record<string, string> = {
  simple: '📄 Simple',
  medium: '🔧 Medium',
  complex: '🧠 Complex',
};

const TaskTypeShorthand: Record<string, string> = {
  simple: 'code_gen_simple',
  medium: 'code_gen_medium',
  complex: 'code_gen_complex',
};

export const ModelSelectionLearning: React.FC = () => {
  const [status, setStatus] = useState<DashboardStatus | null>(null);
  const [history, setHistory] = useState<ThresholdHistory[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string>('');
  const [resetConfirm, setResetConfirm] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [overrideValues, setOverrideValues] = useState<Record<string, number>>({});

  // Fetch data on mount and periodic refresh
  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 5000); // Refresh every 5s
    return () => clearInterval(interval);
  }, []);

  const fetchData = async () => {
    try {
      const response = await fetch('/v1/console/learning/model-selection/status');
      if (!response.ok) {
        setError(`API error: ${response.status}`);
        setLoading(false);
        return;
      }

      const data = await response.json();
      setStatus(data);
      setError('');

      // Parse threshold history from response
      if (data.history) {
        setHistory(data.history);
      }
    } catch (err) {
      setError(`Failed to fetch learning data: ${err}`);
    } finally {
      setLoading(false);
    }
  };

  const handleReset = async () => {
    if (!resetConfirm) {
      setResetConfirm(true);
      return;
    }

    try {
      const response = await fetch('/v1/console/learning/model-selection/reset', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ reason: 'Operator manual reset via console' }),
      });

      if (response.ok) {
        setResetConfirm(false);
        setStatus(null);
        setHistory([]);
        await fetchData();
      } else {
        setError('Failed to reset learning');
      }
    } catch (err) {
      setError(`Reset failed: ${err}`);
    }
  };

  const handleExport = async () => {
    setExporting(true);
    try {
      const response = await fetch('/v1/console/learning/model-selection/export');
      if (!response.ok) {
        setError('Export failed');
        return;
      }

      const data = await response.json();
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `model-selection-thresholds-${new Date().toISOString().split('T')[0]}.json`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    } catch (err) {
      setError(`Export failed: ${err}`);
    } finally {
      setExporting(false);
    }
  };

  const handleOverride = async (taskType: string, newThreshold: number) => {
    try {
      const response = await fetch('/v1/console/learning/model-selection/override', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          task_type: taskType,
          new_threshold: newThreshold,
          reason: `Operator override via console: ${newThreshold.toFixed(2)}`,
        }),
      });

      if (response.ok) {
        await fetchData();
        setOverrideValues({ ...overrideValues, [taskType]: newThreshold });
      } else {
        setError('Failed to apply override');
      }
    } catch (err) {
      setError(`Override failed: ${err}`);
    }
  };

  if (loading && !status) {
    return (
      <div className="p-6 bg-slate-50 dark:bg-slate-900 rounded-lg text-center">
        <div className="animate-pulse text-slate-600 dark:text-slate-400">
          Loading Model Selection Learning dashboard...
        </div>
      </div>
    );
  }

  if (error && !status) {
    return (
      <div className="p-6 bg-red-50 dark:bg-red-900/20 rounded-lg border border-red-200 dark:border-red-800">
        <div className="flex items-center gap-2 text-red-700 dark:text-red-400">
          <AlertCircle size={20} />
          <span>{error}</span>
        </div>
      </div>
    );
  }

  if (!status) {
    return <div className="p-6 text-slate-600 dark:text-slate-400">No data available</div>;
  }

  // Prepare chart data (simple task types for visualization)
  const convergenceData = status.thresholds.map(t => ({
    name: t.task_type.split('_').pop() || t.task_type,
    learned: t.learned_threshold,
    base: t.base_threshold,
    samples: t.sample_count,
    converged: t.converged ? 1 : 0,
  }));

  const costData = [
    { label: 'Baseline', value: status.cost_baseline_usd, color: COLORS.warning },
    { label: 'Current', value: status.cost_current_usd, color: COLORS.success },
  ];

  return (
    <div className="p-6 bg-gradient-to-br from-slate-50 to-slate-100 dark:from-slate-900 dark:to-slate-800 min-h-screen rounded-lg">
      {/* Header */}
      <div className="mb-8">
        <h1 className="text-4xl font-bold text-slate-900 dark:text-white mb-2">
          📊 Model Selection Learning
        </h1>
        <p className="text-slate-600 dark:text-slate-400">
          ADR-0377 Phase 2b • Learned Thresholds & Cost Optimization
        </p>
        <p className="text-xs text-slate-500 dark:text-slate-500 mt-2">
          Last updated: {new Date(status.last_updated).toLocaleTimeString()}
        </p>
      </div>

      {/* Error Banner */}
      {error && (
        <div className="mb-6 p-4 bg-yellow-50 dark:bg-yellow-900/20 border border-yellow-200 dark:border-yellow-800 rounded-lg">
          <div className="flex items-center gap-2 text-yellow-700 dark:text-yellow-400 text-sm">
            <AlertCircle size={16} />
            <span>{error}</span>
          </div>
        </div>
      )}

      {/* KPI Cards */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4 mb-8">
        {/* Convergence Status */}
        <div className="bg-white dark:bg-slate-800 rounded-lg p-6 shadow-sm border border-slate-200 dark:border-slate-700">
          <div className="text-slate-600 dark:text-slate-400 text-sm font-semibold mb-2 flex items-center gap-2">
            {status.converged_count === status.total_count ? (
              <CheckCircle size={16} className="text-green-500" />
            ) : (
              <Clock size={16} className="text-yellow-500" />
            )}
            Convergence
          </div>
          <div className="text-3xl font-bold text-slate-900 dark:text-white">
            {status.converged_count}/{status.total_count}
          </div>
          <div className="text-xs text-slate-500 dark:text-slate-400 mt-2">
            {Math.round((status.converged_count / status.total_count) * 100)}% converged
          </div>
        </div>

        {/* Cost Savings */}
        <div className="bg-white dark:bg-slate-800 rounded-lg p-6 shadow-sm border border-slate-200 dark:border-slate-700">
          <div className="text-slate-600 dark:text-slate-400 text-sm font-semibold mb-2">
            💰 Cost Savings
          </div>
          <div className="text-3xl font-bold text-green-600 dark:text-green-400">
            {status.cost_savings_percent.toFixed(1)}%
          </div>
          <div className="text-xs text-slate-500 dark:text-slate-400 mt-2">
            ${status.cost_baseline_usd.toFixed(2)} → ${status.cost_current_usd.toFixed(2)}/day
          </div>
        </div>

        {/* Quality Maintenance */}
        <div className="bg-white dark:bg-slate-800 rounded-lg p-6 shadow-sm border border-slate-200 dark:border-slate-700">
          <div className="text-slate-600 dark:text-slate-400 text-sm font-semibold mb-2 flex items-center gap-2">
            {status.accuracy_percent >= 85 ? (
              <CheckCircle size={16} className="text-green-500" />
            ) : (
              <AlertCircle size={16} className="text-yellow-500" />
            )}
            Accuracy
          </div>
          <div className="text-3xl font-bold text-slate-900 dark:text-white">
            {status.accuracy_percent.toFixed(1)}%
          </div>
          <div className="text-xs text-slate-500 dark:text-slate-400 mt-2">
            Target: ≥85%
          </div>
        </div>

        {/* Last Update */}
        <div className="bg-white dark:bg-slate-800 rounded-lg p-6 shadow-sm border border-slate-200 dark:border-slate-700">
          <div className="text-slate-600 dark:text-slate-400 text-sm font-semibold mb-2">
            🔄 Status
          </div>
          <div className="text-sm font-mono text-slate-900 dark:text-white">
            {status.thresholds.length > 0 ? 'Active' : 'Idle'}
          </div>
          <div className="text-xs text-slate-500 dark:text-slate-400 mt-2">
            {status.thresholds.length} task types tracked
          </div>
        </div>
      </div>

      {/* Threshold Convergence Chart */}
      <div className="mb-8 bg-white dark:bg-slate-800 rounded-lg p-6 shadow-sm border border-slate-200 dark:border-slate-700">
        <h2 className="text-lg font-semibold text-slate-900 dark:text-white mb-4">
          Learned Thresholds vs Base
        </h2>
        <ResponsiveContainer width="100%" height={300}>
          <BarChart data={convergenceData}>
            <CartesianGrid strokeDasharray="3 3" stroke="rgba(100,100,100,0.1)" />
            <XAxis
              dataKey="name"
              tick={{ fontSize: 12 }}
              stroke="rgba(100,100,100,0.5)"
            />
            <YAxis
              domain={[0, 1]}
              tick={{ fontSize: 12 }}
              stroke="rgba(100,100,100,0.5)"
            />
            <Tooltip
              formatter={(value) => (typeof value === 'number' ? value.toFixed(3) : value)}
              contentStyle={{
                backgroundColor: 'rgba(0,0,0,0.8)',
                border: '1px solid rgba(255,255,255,0.2)',
              }}
            />
            <Legend />
            <Bar dataKey="base" fill={COLORS.warning} name="Base (0.5)" />
            <Bar dataKey="learned" fill={COLORS.success} name="Learned" />
          </BarChart>
        </ResponsiveContainer>
      </div>

      {/* Cost Efficiency */}
      <div className="mb-8 bg-white dark:bg-slate-800 rounded-lg p-6 shadow-sm border border-slate-200 dark:border-slate-700">
        <h2 className="text-lg font-semibold text-slate-900 dark:text-white mb-4">
          Cost Efficiency Trend
        </h2>
        <ResponsiveContainer width="100%" height={250}>
          <AreaChart
            data={[
              { name: 'Baseline', cost: status.cost_baseline_usd },
              { name: 'Current', cost: status.cost_current_usd },
            ]}
          >
            <CartesianGrid strokeDasharray="3 3" stroke="rgba(100,100,100,0.1)" />
            <XAxis dataKey="name" tick={{ fontSize: 12 }} stroke="rgba(100,100,100,0.5)" />
            <YAxis tick={{ fontSize: 12 }} stroke="rgba(100,100,100,0.5)" />
            <Tooltip
              formatter={(value) => `$${(value as number).toFixed(2)}`}
              contentStyle={{
                backgroundColor: 'rgba(0,0,0,0.8)',
                border: '1px solid rgba(255,255,255,0.2)',
              }}
            />
            <Area
              type="monotone"
              dataKey="cost"
              fill={COLORS.success}
              stroke={COLORS.success}
              fillOpacity={0.3}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>

      {/* Task Type Breakdown */}
      <div className="mb-8 grid grid-cols-1 md:grid-cols-3 gap-4">
        {status.thresholds.map((t) => (
          <div
            key={`${t.task_type}:${t.subsystem}`}
            className="bg-white dark:bg-slate-800 rounded-lg p-4 shadow-sm border border-slate-200 dark:border-slate-700"
          >
            <div className="flex items-center justify-between mb-3">
              <h3 className="font-semibold text-slate-900 dark:text-white">
                {t.task_type}
              </h3>
              {t.converged ? (
                <CheckCircle size={16} className="text-green-500" />
              ) : (
                <Clock size={16} className="text-yellow-500" />
              )}
            </div>

            <div className="text-sm space-y-1 mb-3">
              <div className="flex justify-between">
                <span className="text-slate-600 dark:text-slate-400">Threshold:</span>
                <span className="font-mono font-semibold text-slate-900 dark:text-white">
                  {t.learned_threshold.toFixed(3)}
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-slate-600 dark:text-slate-400">Base:</span>
                <span className="font-mono text-slate-600 dark:text-slate-400">
                  {t.base_threshold.toFixed(3)}
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-slate-600 dark:text-slate-400">Samples:</span>
                <span className="font-mono font-semibold text-slate-900 dark:text-white">
                  {t.sample_count}
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-slate-600 dark:text-slate-400">Status:</span>
                <span
                  className={`text-xs font-semibold ${
                    t.converged
                      ? 'text-green-600 dark:text-green-400'
                      : 'text-yellow-600 dark:text-yellow-400'
                  }`}
                >
                  {t.converged ? 'Converged' : 'Learning'}
                </span>
              </div>
            </div>

            {/* Manual Override Slider */}
            <div className="mt-3 pt-3 border-t border-slate-200 dark:border-slate-700">
              <label className="text-xs font-semibold text-slate-600 dark:text-slate-400 block mb-2">
                Manual Override
              </label>
              <input
                type="range"
                min="0.1"
                max="0.9"
                step="0.05"
                defaultValue={t.learned_threshold}
                onChange={(e) => {
                  const newValue = parseFloat(e.target.value);
                  setOverrideValues({
                    ...overrideValues,
                    [t.task_type]: newValue,
                  });
                }}
                className="w-full h-2 bg-slate-200 dark:bg-slate-600 rounded-lg appearance-none cursor-pointer"
              />
              <div className="flex justify-between text-xs mt-1">
                <span className="text-slate-500 dark:text-slate-500">
                  {(overrideValues[t.task_type] ?? t.learned_threshold).toFixed(2)}
                </span>
                <button
                  onClick={() =>
                    handleOverride(t.task_type, overrideValues[t.task_type] ?? t.learned_threshold)
                  }
                  className="px-2 py-1 bg-blue-500 text-white text-xs rounded hover:bg-blue-600 transition"
                >
                  Apply
                </button>
              </div>
            </div>
          </div>
        ))}
      </div>

      {/* Operator Controls */}
      <div className="mb-8 bg-white dark:bg-slate-800 rounded-lg p-6 shadow-sm border border-slate-200 dark:border-slate-700">
        <h2 className="text-lg font-semibold text-slate-900 dark:text-white mb-4">
          Operator Controls
        </h2>

        <div className="flex flex-wrap gap-3">
          {/* Export Button */}
          <button
            onClick={handleExport}
            disabled={exporting}
            className="px-4 py-2 bg-blue-500 text-white rounded-lg hover:bg-blue-600 transition disabled:opacity-50 flex items-center gap-2"
          >
            <Download size={16} />
            {exporting ? 'Exporting...' : 'Export'}
          </button>

          {/* Import Button */}
          <label className="px-4 py-2 bg-blue-500 text-white rounded-lg hover:bg-blue-600 transition cursor-pointer flex items-center gap-2">
            <Upload size={16} />
            Import
            <input type="file" accept=".json" hidden onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) {
                const reader = new FileReader();
                reader.onload = async (event) => {
                  try {
                    const data = JSON.parse(event.target?.result as string);
                    const formData = new FormData();
                    formData.append('data', JSON.stringify(data));
                    const response = await fetch('/v1/console/learning/model-selection/import', {
                      method: 'POST',
                      body: formData,
                    });
                    if (response.ok) {
                      await fetchData();
                    } else {
                      setError('Import failed');
                    }
                  } catch (err) {
                    setError(`Import parse error: ${err}`);
                  }
                };
                reader.readAsText(file);
              }
            }} />
          </label>

          {/* Reset Button */}
          <button
            onClick={handleReset}
            className={`px-4 py-2 rounded-lg transition flex items-center gap-2 ${
              resetConfirm
                ? 'bg-red-600 text-white hover:bg-red-700'
                : 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400 hover:bg-red-200 dark:hover:bg-red-900/50'
            }`}
          >
            <RotateCcw size={16} />
            {resetConfirm ? 'Click again to confirm' : 'Reset Learning'}
          </button>

          {resetConfirm && (
            <button
              onClick={() => setResetConfirm(false)}
              className="px-4 py-2 bg-slate-300 dark:bg-slate-600 text-slate-900 dark:text-white rounded-lg hover:bg-slate-400 dark:hover:bg-slate-500 transition"
            >
              Cancel
            </button>
          )}
        </div>

        <div className="mt-4 text-xs text-slate-600 dark:text-slate-400">
          <p>
            • <strong>Export:</strong> Download learned thresholds as JSON for backup
          </p>
          <p>
            • <strong>Import:</strong> Restore thresholds from backup
          </p>
          <p>
            • <strong>Reset:</strong> Clear all learning and restart from base thresholds
          </p>
        </div>
      </div>
    </div>
  );
};
