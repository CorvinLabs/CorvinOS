/**
 * Unified Learning Dashboard
 * Displays: Feedback volume, confidence trends, learning status across all streams
 * Wires to: GET /v1/console/learning/dashboard
 */

import React, { useState } from 'react';
import {
  BarChart,
  Bar,
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from 'recharts';
import { Loader, AlertCircle, Download, RotateCcw } from 'lucide-react';
import { useUnifiedLearningDashboard } from '@/hooks/useSkillAdminData';

export function UnifiedLearningDashboard() {
  const [dateRange, setDateRange] = useState<{ start: string; end: string } | undefined>();
  const { data, loading, error, refetch } = useUnifiedLearningDashboard(dateRange);

  const handleDateChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const { name, value } = e.currentTarget;
    setDateRange((prev) => ({
      start: prev?.start || '',
      end: prev?.end || '',
      [name]: value,
    }));
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center p-8">
        <Loader className="w-6 h-6 animate-spin text-amber-600" />
        <span className="ml-2 text-neutral-600 dark:text-neutral-400">Loading dashboard...</span>
      </div>
    );
  }

  if (error) {
    return (
      <div className="p-6 rounded-lg border border-red-200 dark:border-red-900 bg-red-50 dark:bg-red-950/30">
        <div className="flex gap-3">
          <AlertCircle className="w-5 h-5 text-red-600 dark:text-red-400 flex-shrink-0" />
          <div>
            <p className="font-semibold text-red-700 dark:text-red-300">Failed to load dashboard</p>
            <p className="text-sm text-red-600 dark:text-red-400 mt-1">{error}</p>
            <button
              onClick={refetch}
              className="mt-3 px-3 py-1 rounded bg-red-600 hover:bg-red-700 text-white text-sm"
            >
              Retry
            </button>
          </div>
        </div>
      </div>
    );
  }

  if (!data) {
    return <div className="p-6 text-neutral-600 dark:text-neutral-400">No data available</div>;
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex justify-between items-start">
        <div>
          <h1 className="text-2xl font-bold">Learning Dashboard</h1>
          <p className="text-sm text-neutral-600 dark:text-neutral-400 mt-1">
            {data.learning_status.total_skills} skills learning from{' '}
            <span className="font-semibold">{data.learning_status.total_feedback_received}</span> feedback
            signals
          </p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={refetch}
            className="px-4 py-2 rounded border border-amber-300 dark:border-amber-700 hover:bg-amber-50 dark:hover:bg-amber-950/30 text-sm font-medium flex items-center gap-2"
          >
            <RotateCcw className="w-4 h-4" />
            Refresh
          </button>
          <button className="px-4 py-2 rounded bg-amber-600 hover:bg-amber-700 text-white text-sm font-medium flex items-center gap-2">
            <Download className="w-4 h-4" />
            Export
          </button>
        </div>
      </div>

      {/* Learning Status Cards */}
      <div className="grid grid-cols-3 gap-4">
        <div className="rounded-lg border border-neutral-200 dark:border-neutral-800 p-4 bg-white dark:bg-neutral-950">
          <p className="text-sm text-neutral-600 dark:text-neutral-400">Total Feedback</p>
          <p className="text-3xl font-bold mt-1">
            {data.learning_status.total_feedback_received.toLocaleString()}
          </p>
          <p className="text-xs text-neutral-500 dark:text-neutral-400 mt-2">
            Over {data.learning_status.days_learning} days
          </p>
        </div>

        <div className="rounded-lg border border-neutral-200 dark:border-neutral-800 p-4 bg-white dark:bg-neutral-950">
          <p className="text-sm text-neutral-600 dark:text-neutral-400">Active Skills</p>
          <p className="text-3xl font-bold mt-1">{data.learning_status.total_skills}</p>
          <p className="text-xs text-neutral-500 dark:text-neutral-400 mt-2">
            Self-optimizing via feedback
          </p>
        </div>

        <div className="rounded-lg border border-neutral-200 dark:border-neutral-800 p-4 bg-white dark:bg-neutral-950">
          <p className="text-sm text-neutral-600 dark:text-neutral-400">Avg Confidence</p>
          <p className="text-3xl font-bold mt-1">{(data.learning_status.avg_confidence * 100).toFixed(1)}%</p>
          <p className="text-xs text-neutral-500 dark:text-neutral-400 mt-2">
            Across all streams
          </p>
        </div>
      </div>

      {/* Date Range Filter */}
      <div className="rounded-lg border border-neutral-200 dark:border-neutral-800 p-4 bg-white dark:bg-neutral-950">
        <div className="flex gap-4 items-end">
          <div>
            <label className="block text-sm font-medium mb-1">Start Date</label>
            <input
              type="date"
              name="start"
              value={dateRange?.start || ''}
              onChange={handleDateChange}
              className="px-3 py-2 rounded border border-neutral-300 dark:border-neutral-700 bg-white dark:bg-neutral-900 text-sm"
            />
          </div>
          <div>
            <label className="block text-sm font-medium mb-1">End Date</label>
            <input
              type="date"
              name="end"
              value={dateRange?.end || ''}
              onChange={handleDateChange}
              className="px-3 py-2 rounded border border-neutral-300 dark:border-neutral-700 bg-white dark:bg-neutral-900 text-sm"
            />
          </div>
          <button
            onClick={refetch}
            className="px-4 py-2 rounded bg-amber-600 hover:bg-amber-700 text-white text-sm font-medium"
          >
            Apply Filter
          </button>
        </div>
        <p className="text-xs text-neutral-500 dark:text-neutral-400 mt-2">
          Showing data from {data.date_range.start} to {data.date_range.end}
        </p>
      </div>

      {/* Feedback Volume Chart */}
      <div className="rounded-lg border border-neutral-200 dark:border-neutral-800 p-6 bg-white dark:bg-neutral-950">
        <h2 className="font-semibold mb-4">Feedback Volume Over Time</h2>
        <ResponsiveContainer width="100%" height={350}>
          <BarChart data={data.feedback_volume}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis
              dataKey="date"
              tick={{ fontSize: 12 }}
              angle={-45}
              textAnchor="end"
              height={60}
            />
            <YAxis />
            <Tooltip />
            <Legend />
            <Bar dataKey="workflow_optimizer" stackId="a" fill="#f59e0b" name="Workflow Optimizer" />
            <Bar dataKey="security_orchestrator" stackId="a" fill="#ef4444" name="Security Orchestrator" />
            <Bar dataKey="flow_guard" stackId="a" fill="#06b6d4" name="Flow Guard" />
          </BarChart>
        </ResponsiveContainer>
      </div>

      {/* Confidence Trends Chart */}
      <div className="rounded-lg border border-neutral-200 dark:border-neutral-800 p-6 bg-white dark:bg-neutral-950">
        <h2 className="font-semibold mb-4">Confidence Trends</h2>
        <ResponsiveContainer width="100%" height={350}>
          <LineChart data={data.confidence_trends}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis
              dataKey="date"
              tick={{ fontSize: 12 }}
              angle={-45}
              textAnchor="end"
              height={60}
            />
            <YAxis domain={[0, 1]} label={{ value: 'Confidence (0–1)', angle: -90, position: 'insideLeft' }} />
            <Tooltip
              formatter={(value: number) => [(value * 100).toFixed(1) + '%', 'Confidence']}
            />
            <Legend />
            <Line
              type="monotone"
              dataKey="workflow_optimizer"
              stroke="#f59e0b"
              name="Workflow Optimizer"
              dot={false}
            />
            <Line
              type="monotone"
              dataKey="security_orchestrator"
              stroke="#ef4444"
              name="Security Orchestrator"
              dot={false}
            />
            <Line
              type="monotone"
              dataKey="flow_guard"
              stroke="#06b6d4"
              name="Flow Guard"
              dot={false}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>

      {/* Quick Links */}
      <div className="rounded-lg border border-neutral-200 dark:border-neutral-800 p-6 bg-white dark:bg-neutral-950">
        <h2 className="font-semibold mb-4">Quick Links</h2>
        <div className="grid grid-cols-3 gap-4">
          <a
            href="/console/skill-settings/workflow-optimizer"
            className="p-4 rounded border border-amber-200 dark:border-amber-800 hover:bg-amber-50 dark:hover:bg-amber-950/30 text-center transition-colors"
          >
            <p className="font-medium text-amber-900 dark:text-amber-100">Workflow Optimizer</p>
            <p className="text-xs text-amber-700 dark:text-amber-300 mt-1">Routing decisions</p>
          </a>
          <a
            href="/console/skill-settings/security-orchestrator"
            className="p-4 rounded border border-red-200 dark:border-red-800 hover:bg-red-50 dark:hover:bg-red-950/30 text-center transition-colors"
          >
            <p className="font-medium text-red-900 dark:text-red-100">Security Orchestrator</p>
            <p className="text-xs text-red-700 dark:text-red-300 mt-1">Threat detection</p>
          </a>
          <a
            href="/console/skill-settings/flow-guard"
            className="p-4 rounded border border-cyan-200 dark:border-cyan-800 hover:bg-cyan-50 dark:hover:bg-cyan-950/30 text-center transition-colors"
          >
            <p className="font-medium text-cyan-900 dark:text-cyan-100">Flow Guard</p>
            <p className="text-xs text-cyan-700 dark:text-cyan-300 mt-1">Data flow policy</p>
          </a>
        </div>
      </div>
    </div>
  );
}
