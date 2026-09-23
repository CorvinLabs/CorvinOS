/**
 * Stream 3: Flow Guard Admin Panel (Phase 3b)
 * Displays: Policy matrix heatmap, classification accuracy, threshold editor, override queue
 * Wires to: GET /v1/console/learning/flow-guard/admin + WS /v1/console/learning/stream
 *
 * Real-time updates: Threshold changes, classification updates, heatmap refreshes
 */

import React, { useState, useEffect } from 'react';
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from 'recharts';
import { AlertCircle, Loader, RotateCcw, CheckCircle, XCircle } from 'lucide-react';
import { useFlowGuardAdmin } from '@/hooks/useSkillAdminData';
import { useSkillStream } from '@/hooks/useSkillWebSocket';
import type { WebSocketEvent } from '@/types/websocket-events';
import { isConfigUpdate } from '@/types/websocket-events';

export function FlowGuardAdminPanel() {
  const { data, loading, error, refetch } = useFlowGuardAdmin();
  const [selectedVersion, setSelectedVersion] = useState<number | null>(null);
  const [expandedCell, setExpandedCell] = useState<string | null>(null);

  // Phase 3b: WebSocket real-time updates
  const { isConnected, updates } = useSkillStream('flow-guard');
  const [localData, setLocalData] = useState(data);
  const [wsStatus, setWsStatus] = useState<'connected' | 'disconnected' | 'updating'>('disconnected');

  useEffect(() => {
    setWsStatus(isConnected ? 'connected' : 'disconnected');
  }, [isConnected]);

  useEffect(() => {
    if (updates.length === 0 || !data) return;

    const event = updates[updates.length - 1];
    setWsStatus('updating');

    setTimeout(() => setWsStatus(isConnected ? 'connected' : 'disconnected'), 2000);

    // Update local data based on event type
    if (isConfigUpdate(event) && data) {
      setLocalData(prev => prev ? {
        ...prev,
        current_policy_version: event.data.config_version,
      } : null);
    }
  }, [updates, isConnected, data]);

  if (loading) {
    return (
      <div className="flex items-center justify-center p-8">
        <Loader className="w-6 h-6 animate-spin text-amber-600" />
        <span className="ml-2 text-neutral-600 dark:text-neutral-400">Loading flow guard data...</span>
      </div>
    );
  }

  if (error) {
    return (
      <div className="p-6 rounded-lg border border-red-200 dark:border-red-900 bg-red-50 dark:bg-red-950/30">
        <div className="flex gap-3">
          <AlertCircle className="w-5 h-5 text-red-600 dark:text-red-400 flex-shrink-0" />
          <div>
            <p className="font-semibold text-red-700 dark:text-red-300">Failed to load flow guard data</p>
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

  // Get unique data classes and engines for matrix
  const dataClasses = [...new Set(data.policy_matrix.map((m) => m.data_class))];
  const engines = [...new Set(data.policy_matrix.map((m) => m.engine))];

  const getCellColor = (score: number) => {
    if (score >= 0.8) return 'bg-green-100 dark:bg-green-900/30 text-green-900 dark:text-green-100';
    if (score >= 0.5) return 'bg-yellow-100 dark:bg-yellow-900/30 text-yellow-900 dark:text-yellow-100';
    return 'bg-red-100 dark:bg-red-900/30 text-red-900 dark:text-red-100';
  };

  return (
    <div className="space-y-6">
      {/* WebSocket Status Toast */}
      {wsStatus === 'updating' && (
        <div className="flex items-center gap-2 px-4 py-2 rounded-lg bg-blue-50 dark:bg-blue-950/30 border border-blue-200 dark:border-blue-800 text-blue-900 dark:text-blue-100 text-sm">
          <div className="w-2 h-2 bg-blue-600 dark:bg-blue-400 rounded-full animate-pulse" />
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
          <h1 className="text-2xl font-bold">Flow Guard Admin</h1>
          <p className="text-sm text-neutral-600 dark:text-neutral-400 mt-1">
            Policy Version: <span className="font-semibold">{(localData || data)?.current_policy_version}</span>
          </p>
        </div>
        <button
          onClick={refetch}
          className="px-4 py-2 rounded border border-amber-300 dark:border-amber-700 hover:bg-amber-50 dark:hover:bg-amber-950/30 text-sm font-medium flex items-center gap-2"
        >
          <RotateCcw className="w-4 h-4" />
          Refresh
        </button>
      </div>

      {/* Classification Accuracy Chart (Real-time) */}
      <div className="rounded-lg border border-neutral-200 dark:border-neutral-800 p-6 bg-white dark:bg-neutral-950">
        <h2 className="font-semibold mb-4">Classification Accuracy by Data Class (Real-time)</h2>
        <ResponsiveContainer width="100%" height={300}>
          <BarChart data={(localData || data)?.classification_accuracy}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis dataKey="data_class" />
            <YAxis domain={[0, 100]} />
            <Tooltip formatter={(value: number) => value.toFixed(1) + '%'} />
            <Bar dataKey="safe_percentage" fill="#10b981" name="Safe Confidence (%)" />
          </BarChart>
        </ResponsiveContainer>
      </div>

      {/* Policy Matrix */}
      <div className="rounded-lg border border-neutral-200 dark:border-neutral-800 p-6 bg-white dark:bg-neutral-950">
        <h2 className="font-semibold mb-4">Policy Matrix (Data Class × Engine)</h2>
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead>
              <tr className="border-b border-neutral-200 dark:border-neutral-800">
                <th className="text-left py-2 px-3 font-semibold">Data Class</th>
                {engines.map((engine) => (
                  <th key={engine} className="text-center py-2 px-3 font-semibold text-sm">
                    {engine}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {dataClasses.map((dc) => (
                <tr key={dc} className="border-b border-neutral-200 dark:border-neutral-800">
                  <td className="py-2 px-3 font-medium">{dc}</td>
                  {engines.map((engine) => {
                    const cell = data.policy_matrix.find(
                      (m) => m.data_class === dc && m.engine === engine
                    );
                    const cellKey = `${dc}-${engine}`;
                    return (
                      <td
                        key={cellKey}
                        className="text-center py-2 px-3"
                        onClick={() =>
                          setExpandedCell(expandedCell === cellKey ? null : cellKey)
                        }
                      >
                        {cell ? (
                          <div
                            className={`p-2 rounded cursor-pointer font-medium text-sm ${getCellColor(cell.safe_score)}`}
                          >
                            {(cell.safe_score * 100).toFixed(0)}%
                          </div>
                        ) : (
                          <div className="p-2 rounded bg-neutral-100 dark:bg-neutral-900 text-neutral-500">
                            —
                          </div>
                        )}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="text-xs text-neutral-500 dark:text-neutral-400 mt-4">
          Green: Safe (≥80%) | Yellow: Medium (50–80%) | Red: Risky (&lt;50%)
        </p>
      </div>

      {/* Threshold Editor */}
      <div className="rounded-lg border border-neutral-200 dark:border-neutral-800 p-6 bg-white dark:bg-neutral-950">
        <h2 className="font-semibold mb-4">Threshold Configuration</h2>
        <div className="space-y-4">
          {data.thresholds.map((thresh) => (
            <div key={thresh.data_class} className="p-4 rounded border border-neutral-200 dark:border-neutral-800">
              <div className="flex justify-between items-start mb-2">
                <div>
                  <p className="font-medium capitalize">{thresh.data_class}</p>
                  <p className="text-xs text-neutral-600 dark:text-neutral-400 mt-1">
                    Confidence: {(thresh.confidence * 100).toFixed(0)}%
                  </p>
                </div>
                <div className="text-right">
                  <p className="font-semibold">{(thresh.current_threshold * 100).toFixed(0)}%</p>
                  <p className="text-xs text-neutral-500 dark:text-neutral-400">Current</p>
                </div>
              </div>
              <div className="space-y-2">
                <label className="flex items-center gap-2">
                  <span className="text-sm">Adjust Threshold:</span>
                  <input
                    type="range"
                    min="0"
                    max="100"
                    step="1"
                    defaultValue={thresh.current_threshold * 100}
                    className="flex-1"
                  />
                </label>
                <p className="text-xs text-neutral-600 dark:text-neutral-400">
                  Recommended: {(thresh.recommended_threshold * 100).toFixed(0)}%
                </p>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Override Requests Queue */}
      <div className="rounded-lg border border-neutral-200 dark:border-neutral-800 p-6 bg-white dark:bg-neutral-950">
        <h2 className="font-semibold mb-4">
          Override Requests ({data.override_requests.length})
        </h2>
        <div className="space-y-3">
          {data.override_requests.length === 0 ? (
            <p className="text-sm text-neutral-600 dark:text-neutral-400">No pending requests</p>
          ) : (
            data.override_requests.map((req) => (
              <div
                key={req.request_id}
                className={`p-4 rounded border ${
                  req.status === 'pending'
                    ? 'border-yellow-200 dark:border-yellow-800 bg-yellow-50 dark:bg-yellow-950/30'
                    : req.status === 'approved'
                      ? 'border-green-200 dark:border-green-800 bg-green-50 dark:bg-green-950/30'
                      : 'border-red-200 dark:border-red-800 bg-red-50 dark:bg-red-950/30'
                }`}
              >
                <div className="flex items-start justify-between gap-4">
                  <div className="flex-1">
                    <div className="flex items-center gap-2">
                      <span className="font-medium">
                        {req.data_class} → {req.engine}
                      </span>
                      <span
                        className={`px-2 py-0.5 rounded text-xs font-medium ${
                          req.status === 'pending'
                            ? 'bg-yellow-100 dark:bg-yellow-900/50 text-yellow-800 dark:text-yellow-200'
                            : req.status === 'approved'
                              ? 'bg-green-100 dark:bg-green-900/50 text-green-800 dark:text-green-200'
                              : 'bg-red-100 dark:bg-red-900/50 text-red-800 dark:text-red-200'
                        }`}
                      >
                        {req.status.toUpperCase()}
                      </span>
                    </div>
                    <p className="text-sm text-neutral-700 dark:text-neutral-300 mt-2">
                      {req.destination}
                    </p>
                    <p className="text-xs text-neutral-600 dark:text-neutral-400 mt-1">
                      {req.justification}
                    </p>
                    <p className="text-xs text-neutral-500 dark:text-neutral-400 mt-2">
                      Requested: {new Date(req.requested_at).toLocaleString()} • TTL: {req.ttl_hours}h
                    </p>
                  </div>
                  {req.status === 'pending' && (
                    <div className="flex gap-2">
                      <button className="px-3 py-1 rounded bg-green-600 hover:bg-green-700 text-white text-sm font-medium flex items-center gap-1">
                        <CheckCircle className="w-4 h-4" />
                        Approve
                      </button>
                      <button className="px-3 py-1 rounded bg-red-600 hover:bg-red-700 text-white text-sm font-medium flex items-center gap-1">
                        <XCircle className="w-4 h-4" />
                        Deny
                      </button>
                    </div>
                  )}
                </div>
              </div>
            ))
          )}
        </div>
      </div>

      {/* Policy Versioning */}
      <div className="rounded-lg border border-neutral-200 dark:border-neutral-800 p-6 bg-white dark:bg-neutral-950">
        <h2 className="font-semibold mb-4">Policy Versioning</h2>
        <div>
          <label className="block text-sm font-medium mb-2">Load Prior Policy Version</label>
          <select
            value={selectedVersion ?? ''}
            onChange={(e) => setSelectedVersion(e.target.value ? parseInt(e.target.value) : null)}
            className="w-full px-3 py-2 rounded border border-neutral-300 dark:border-neutral-700 bg-white dark:bg-neutral-900 text-sm"
          >
            <option value="">Select version to load...</option>
            {data.available_policy_versions.map((v) => (
              <option key={v.version} value={v.version}>
                Version {v.version} — {new Date(v.timestamp).toLocaleString()}
              </option>
            ))}
          </select>
          {selectedVersion && selectedVersion !== data.current_policy_version && (
            <button className="mt-3 w-full px-4 py-2 rounded bg-amber-600 hover:bg-amber-700 text-white font-medium text-sm">
              Rollback to Version {selectedVersion}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
