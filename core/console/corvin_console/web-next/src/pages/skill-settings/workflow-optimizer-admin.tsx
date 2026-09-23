/**
 * Stream 1: Workflow Optimizer Admin Panel (Phase 3b)
 * Displays: Confidence chart, routing distribution, learned weights, versioning
 * Wires to: GET /v1/console/learning/workflow-optimizer/admin + WS /v1/console/learning/stream
 *
 * Real-time updates: Confidence score, config version, error notifications
 */

import React, { useState, useEffect } from 'react';
import {
  LineChart,
  Line,
  PieChart,
  Pie,
  Cell,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from 'recharts';
import { AlertCircle, Download, RotateCcw, Loader, CheckCircle } from 'lucide-react';
import { useWorkflowOptimizerAdmin } from '@/hooks/useSkillAdminData';
import { useSkillStream } from '@/hooks/useSkillWebSocket';
import type { WebSocketEvent } from '@/types/websocket-events';
import { isConfidenceUpdate, isConfigUpdate, isErrorEvent } from '@/types/websocket-events';

const COLORS = ['#f59e0b', '#06b6d4', '#8b5cf6']; // Amber, cyan, purple

export function WorkflowOptimizerAdminPanel() {
  const { data, loading, error, refetch } = useWorkflowOptimizerAdmin();
  const [selectedVersion, setSelectedVersion] = useState<number | null>(null);

  // Phase 3b: WebSocket real-time updates
  const { isConnected, updates } = useSkillStream('workflow-optimizer');
  const [localData, setLocalData] = useState(data);
  const [wsStatus, setWsStatus] = useState<'connected' | 'disconnected' | 'updating'>('disconnected');
  const [lastUpdate, setLastUpdate] = useState<WebSocketEvent | null>(null);

  // Handle WebSocket updates
  useEffect(() => {
    setWsStatus(isConnected ? 'connected' : 'disconnected');
  }, [isConnected]);

  useEffect(() => {
    if (updates.length === 0) return;

    const event = updates[updates.length - 1];
    setLastUpdate(event);
    setWsStatus('updating');

    setTimeout(() => setWsStatus(isConnected ? 'connected' : 'disconnected'), 2000);

    // Update local data based on event type
    if (isConfidenceUpdate(event) && data) {
      setLocalData(prev => prev ? {
        ...prev,
        current_confidence: event.data.new_confidence,
        version: event.data.version,
        confidence_timeline: [
          ...(prev.confidence_timeline || []),
          {
            timestamp: new Date(event.data.timestamp).toLocaleTimeString(),
            confidence_score: event.data.new_confidence,
          }
        ].slice(-50), // Keep last 50 points
      } : null);
    }

    if (isConfigUpdate(event) && data) {
      setLocalData(prev => prev ? {
        ...prev,
        version: event.data.config_version,
      } : null);
    }
  }, [updates, isConnected, data]);

  if (loading) {
    return (
      <div className="flex items-center justify-center p-8">
        <Loader className="w-6 h-6 animate-spin text-amber-600" />
        <span className="ml-2 text-neutral-600 dark:text-neutral-400">Loading admin data...</span>
      </div>
    );
  }

  if (error) {
    return (
      <div className="p-6 rounded-lg border border-red-200 dark:border-red-900 bg-red-50 dark:bg-red-950/30">
        <div className="flex gap-3">
          <AlertCircle className="w-5 h-5 text-red-600 dark:text-red-400 flex-shrink-0" />
          <div>
            <p className="font-semibold text-red-700 dark:text-red-300">Failed to load admin data</p>
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
      {/* WebSocket Status Toast */}
      {wsStatus === 'updating' && (
        <div className="flex items-center gap-2 px-4 py-2 rounded-lg bg-amber-50 dark:bg-amber-950/30 border border-amber-200 dark:border-amber-800 text-amber-900 dark:text-amber-100 text-sm">
          <div className="w-2 h-2 bg-amber-600 dark:bg-amber-400 rounded-full animate-pulse" />
          <span>Updating in real-time...</span>
        </div>
      )}

      {wsStatus === 'connected' && (
        <div className="flex items-center gap-2 px-4 py-2 rounded-lg bg-green-50 dark:bg-green-950/30 border border-green-200 dark:border-green-800 text-green-900 dark:text-green-100 text-sm">
          <CheckCircle className="w-4 h-4" />
          <span>Real-time updates active</span>
        </div>
      )}

      {/* Header */}
      <div className="flex justify-between items-start">
        <div>
          <h1 className="text-2xl font-bold">Workflow Optimizer Admin</h1>
          <p className="text-sm text-neutral-600 dark:text-neutral-400 mt-1">
            Current confidence: <span className="font-semibold">{((localData?.current_confidence || data?.current_confidence || 0) * 100).toFixed(1)}%</span>
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
          <button
            onClick={() => {
              const json = JSON.stringify(data, null, 2);
              const blob = new Blob([json], { type: 'application/json' });
              const url = URL.createObjectURL(blob);
              const a = document.createElement('a');
              a.href = url;
              a.download = 'workflow-optimizer-weights.json';
              a.click();
            }}
            className="px-4 py-2 rounded bg-amber-600 hover:bg-amber-700 text-white text-sm font-medium flex items-center gap-2"
          >
            <Download className="w-4 h-4" />
            Export Weights
          </button>
        </div>
      </div>

      {/* Confidence Timeline Chart */}
      <div className="rounded-lg border border-neutral-200 dark:border-neutral-800 p-6 bg-white dark:bg-neutral-950">
        <h2 className="font-semibold mb-4">Confidence Timeline (Real-time)</h2>
        <ResponsiveContainer width="100%" height={300}>
          <LineChart data={localData?.confidence_timeline || data?.confidence_timeline}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis
              dataKey="timestamp"
              tick={{ fontSize: 12 }}
              angle={-45}
              textAnchor="end"
              height={60}
            />
            <YAxis domain={[0, 1]} label={{ value: 'Confidence (0–1)', angle: -90, position: 'insideLeft' }} />
            <Tooltip
              formatter={(value: number) => [(value * 100).toFixed(1) + '%', 'Confidence']}
              contentStyle={{
                backgroundColor: '#1f2937',
                border: '1px solid #374151',
                borderRadius: '8px',
              }}
            />
            <Legend />
            <Line
              type="monotone"
              dataKey="confidence_score"
              stroke="#f59e0b"
              dot={false}
              name="Confidence Score"
            />
          </LineChart>
        </ResponsiveContainer>
        <p className="text-xs text-neutral-500 dark:text-neutral-400 mt-3">
          Last updated: {(localData || data)?.confidence_timeline[(localData || data)?.confidence_timeline.length - 1]?.timestamp}
        </p>
      </div>

      {/* Routing Distribution Pie Chart */}
      <div className="rounded-lg border border-neutral-200 dark:border-neutral-800 p-6 bg-white dark:bg-neutral-950">
        <h2 className="font-semibold mb-4">Agent Routing Distribution</h2>
        <div className="flex gap-6">
          <ResponsiveContainer width="50%" height={250}>
            <PieChart>
              <Pie
                data={data.routing_distribution}
                dataKey="percentage"
                nameKey="agent"
                cx="50%"
                cy="50%"
                outerRadius={80}
                label={({ agent, percentage }) => `${agent}: ${percentage}%`}
              >
                {data.routing_distribution.map((_, index) => (
                  <Cell key={`cell-${index}`} fill={COLORS[index % COLORS.length]} />
                ))}
              </Pie>
              <Tooltip formatter={(value: number) => value.toFixed(1) + '%'} />
            </PieChart>
          </ResponsiveContainer>

          {/* Distribution Stats */}
          <div className="flex-1 space-y-3">
            {data.routing_distribution.map((dist, idx) => (
              <div key={dist.agent} className="p-3 rounded border border-neutral-200 dark:border-neutral-800">
                <div className="flex items-center gap-2 mb-1">
                  <div
                    className="w-3 h-3 rounded-full"
                    style={{ backgroundColor: COLORS[idx % COLORS.length] }}
                  />
                  <span className="font-medium capitalize">{dist.agent}</span>
                </div>
                <div className="text-2xl font-bold">{dist.percentage.toFixed(1)}%</div>
                <p className="text-xs text-neutral-500 dark:text-neutral-400">
                  {dist.task_count.toLocaleString()} tasks
                </p>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Learned Weights Table */}
      <div className="rounded-lg border border-neutral-200 dark:border-neutral-800 p-6 bg-white dark:bg-neutral-950">
        <h2 className="font-semibold mb-4">Learned Weights (Current Config)</h2>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-neutral-200 dark:border-neutral-800">
                <th className="text-left py-2 px-3 font-semibold">Task Type</th>
                <th className="text-right py-2 px-3 font-semibold">Haiku</th>
                <th className="text-right py-2 px-3 font-semibold">Sonnet</th>
                <th className="text-right py-2 px-3 font-semibold">Opus</th>
              </tr>
            </thead>
            <tbody>
              {data.learned_weights.map((weight) => (
                <tr key={weight.task_type} className="border-b border-neutral-200 dark:border-neutral-800">
                  <td className="py-2 px-3">{weight.task_type}</td>
                  <td className="text-right py-2 px-3">
                    <span className="px-2 py-1 rounded bg-amber-50 dark:bg-amber-950/30 text-amber-900 dark:text-amber-100">
                      {(weight.haiku_probability * 100).toFixed(1)}%
                    </span>
                  </td>
                  <td className="text-right py-2 px-3">
                    <span className="px-2 py-1 rounded bg-cyan-50 dark:bg-cyan-950/30 text-cyan-900 dark:text-cyan-100">
                      {(weight.sonnet_probability * 100).toFixed(1)}%
                    </span>
                  </td>
                  <td className="text-right py-2 px-3">
                    <span className="px-2 py-1 rounded bg-purple-50 dark:bg-purple-950/30 text-purple-900 dark:text-purple-100">
                      {(weight.opus_probability * 100).toFixed(1)}%
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Version Control */}
      <div className="rounded-lg border border-neutral-200 dark:border-neutral-800 p-6 bg-white dark:bg-neutral-950">
        <h2 className="font-semibold mb-4">Configuration Versioning</h2>
        <div className="space-y-3">
          <div className="p-3 rounded border border-amber-200 dark:border-amber-800 bg-amber-50 dark:bg-amber-950/30">
            <div className="flex items-center justify-between">
              <p className="font-semibold text-amber-900 dark:text-amber-100">
                Current Version: {localData?.version || data?.version}
              </p>
              {wsStatus === 'updating' && <span className="text-xs text-amber-700 animate-pulse">● Syncing...</span>}
            </div>
            <p className="text-sm text-amber-800 dark:text-amber-200 mt-1">
              This config is currently active. All new requests use these weights.
            </p>
          </div>

          <div>
            <label className="block text-sm font-medium mb-2">Load Prior Version</label>
            <select
              value={selectedVersion ?? ''}
              onChange={(e) => setSelectedVersion(e.target.value ? parseInt(e.target.value) : null)}
              className="w-full px-3 py-2 rounded border border-neutral-300 dark:border-neutral-700 bg-white dark:bg-neutral-900 text-sm"
            >
              <option value="">Select version to load...</option>
              {data.available_versions.map((v) => (
                <option key={v.version} value={v.version}>
                  Version {v.version} — {new Date(v.timestamp).toLocaleString()}
                </option>
              ))}
            </select>
          </div>

          {selectedVersion && selectedVersion !== data.version && (
            <button className="w-full px-4 py-2 rounded bg-amber-600 hover:bg-amber-700 text-white font-medium text-sm">
              Rollback to Version {selectedVersion}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
