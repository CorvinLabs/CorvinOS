/**
 * Vibe Engineering Phase 2: Operator Dashboard
 *
 * React component for:
 * - Checkpoint browser (list, preview, restore)
 * - Task execution timeline
 * - Session statistics (compression ratio, token savings, recovery success)
 * - Real-time status updates
 * - Dark mode + responsive (mobile/tablet/desktop)
 *
 * Features:
 * - List checkpoints for a task
 * - Preview checkpoint details (decisions, errors, learnings)
 * - Restore (resume) from checkpoint
 * - View system metrics
 * - Task execution timeline
 * - Dark mode toggle
 * - Responsive grid/list views
 */

import React, { useState, useEffect } from 'react';
import { AlertCircle, CheckCircle, Clock, Download, Play, RefreshCw, Settings, Zap } from 'lucide-react';

interface Checkpoint {
  checkpoint_id: string;
  iteration: number;
  timestamp: string;
  trigger: string;
  compression_pct: number;
  tokens_saved: number;
}

interface CheckpointDetail {
  checkpoint_id: string;
  task_id: string;
  iteration: number;
  phase: string;
  trigger: string;
  timestamp: string;
  compression: {
    original_tokens: number;
    reduced_tokens: number;
    reduction_pct: number;
  };
  state: {
    phase: string;
    iteration: number;
    context_tokens: number;
    tokens_burned: number;
  };
  decisions: any[];
  errors: any[];
  learnings: any[];
  strategies_tried: string[];
  recommendations: string[];
}

interface TaskStatus {
  task_id: string;
  status: string;
  phase: string;
  iteration?: number;
  context_tokens?: number;
  tokens_burned?: number;
  tokens_budget: number;
  checkpoints: number;
  latest_checkpoint?: {
    checkpoint_id: string;
    timestamp: string;
  };
  recovery_success_rate: number;
}

interface Metrics {
  checkpoints_created: number;
  total_iterations: number;
  total_splits: number;
  avg_compression_pct: number;
  tokens_saved: number;
  recovery_success_rate: number;
  uptime_seconds: number;
  splits_by_trigger: Record<string, number>;
  last_checkpoint_time?: string;
}

type View = 'checkpoints' | 'timeline' | 'metrics';

export function VibeEngineeringDashboard() {
  const [darkMode, setDarkMode] = useState(false);
  const [view, setView] = useState<View>('checkpoints');
  const [taskId, setTaskId] = useState('task_001');
  const [checkpoints, setCheckpoints] = useState<Checkpoint[]>([]);
  const [selectedCheckpoint, setSelectedCheckpoint] = useState<CheckpointDetail | null>(null);
  const [taskStatus, setTaskStatus] = useState<TaskStatus | null>(null);
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Fetch checkpoints
  const fetchCheckpoints = async (tid: string) => {
    setLoading(true);
    setError(null);
    try {
      const response = await fetch(`/vibe/checkpoints/${tid}`);
      if (!response.ok) throw new Error('Failed to fetch checkpoints');
      const data = await response.json();
      setCheckpoints(data.checkpoints);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error');
    } finally {
      setLoading(false);
    }
  };

  // Fetch checkpoint details
  const fetchCheckpointDetails = async (tid: string, cpId: string) => {
    setLoading(true);
    setError(null);
    try {
      const response = await fetch(`/vibe/checkpoint/${tid}/${cpId}`);
      if (!response.ok) throw new Error('Failed to fetch checkpoint details');
      const data = await response.json();
      setSelectedCheckpoint(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error');
    } finally {
      setLoading(false);
    }
  };

  // Fetch task status
  const fetchTaskStatus = async (tid: string) => {
    try {
      const response = await fetch(`/vibe/task-status/${tid}`);
      if (!response.ok) throw new Error('Failed to fetch task status');
      const data = await response.json();
      setTaskStatus(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error');
    }
  };

  // Fetch metrics
  const fetchMetrics = async () => {
    try {
      const response = await fetch('/vibe/metrics');
      if (!response.ok) throw new Error('Failed to fetch metrics');
      const data = await response.json();
      setMetrics(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error');
    }
  };

  // Restore checkpoint
  const restoreCheckpoint = async (tid: string, cpId: string) => {
    setLoading(true);
    setError(null);
    try {
      const response = await fetch(`/vibe/restore/${tid}/${cpId}`, { method: 'POST' });
      if (!response.ok) throw new Error('Failed to restore checkpoint');
      const data = await response.json();
      alert(`✅ ${data.message}`);
      fetchTaskStatus(tid);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error');
    } finally {
      setLoading(false);
    }
  };

  // Initial load
  useEffect(() => {
    fetchCheckpoints(taskId);
    fetchTaskStatus(taskId);
    fetchMetrics();

    const interval = setInterval(() => {
      fetchTaskStatus(taskId);
      fetchMetrics();
    }, 10000); // Refresh every 10 seconds

    return () => clearInterval(interval);
  }, [taskId]);

  const bgClass = darkMode ? 'bg-gray-900 text-white' : 'bg-white text-gray-900';
  const cardClass = darkMode
    ? 'bg-gray-800 border-gray-700'
    : 'bg-white border-gray-200';
  const hoverClass = darkMode
    ? 'hover:bg-gray-700'
    : 'hover:bg-gray-50';

  return (
    <div className={`min-h-screen ${bgClass} p-4 md:p-8`}>
      {/* Header */}
      <div className="max-w-7xl mx-auto">
        <div className="flex justify-between items-center mb-8">
          <div>
            <h1 className="text-4xl font-bold">Vibe Engineering Dashboard</h1>
            <p className="text-gray-500 text-sm mt-1">Phase 2.0 (v0.2-rc1)</p>
          </div>
          <button
            onClick={() => setDarkMode(!darkMode)}
            className={`p-2 rounded-lg border ${darkMode ? 'border-gray-700' : 'border-gray-200'}`}
          >
            {darkMode ? '☀️' : '🌙'}
          </button>
        </div>

        {/* Error alert */}
        {error && (
          <div
            className={`mb-4 p-4 rounded-lg border-l-4 ${
              darkMode
                ? 'bg-red-900 border-red-700 text-red-100'
                : 'bg-red-50 border-red-400 text-red-900'
            }`}
          >
            <div className="flex items-center">
              <AlertCircle className="w-5 h-5 mr-2" />
              <span>{error}</span>
            </div>
          </div>
        )}

        {/* Task Input */}
        <div className="mb-8 flex gap-4 items-end">
          <div>
            <label className="block text-sm font-medium mb-2">Task ID</label>
            <input
              type="text"
              value={taskId}
              onChange={(e) => setTaskId(e.target.value)}
              onKeyPress={(e) => e.key === 'Enter' && fetchCheckpoints(taskId)}
              className={`px-4 py-2 rounded-lg border ${
                darkMode
                  ? 'bg-gray-800 border-gray-700 text-white'
                  : 'bg-white border-gray-200'
              }`}
              placeholder="task_001"
            />
          </div>
          <button
            onClick={() => {
              fetchCheckpoints(taskId);
              fetchTaskStatus(taskId);
            }}
            className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg flex items-center gap-2"
          >
            <RefreshCw className="w-4 h-4" /> Refresh
          </button>
        </div>

        {/* Navigation tabs */}
        <div className="flex gap-4 mb-8 border-b border-gray-200 dark:border-gray-700">
          {(['checkpoints', 'timeline', 'metrics'] as const).map((v) => (
            <button
              key={v}
              onClick={() => setView(v)}
              className={`px-4 py-2 capitalize font-medium border-b-2 ${
                view === v
                  ? 'border-blue-600 text-blue-600'
                  : darkMode
                  ? 'border-transparent text-gray-400'
                  : 'border-transparent text-gray-600'
              }`}
            >
              {v}
            </button>
          ))}
        </div>

        {/* Task Status Card */}
        {taskStatus && (
          <div className={`mb-8 p-6 rounded-lg border ${cardClass}`}>
            <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
              <div>
                <div className="text-sm font-medium text-gray-500">Status</div>
                <div className="text-2xl font-bold flex items-center gap-2 mt-2">
                  {taskStatus.status === 'running' ? (
                    <CheckCircle className="w-6 h-6 text-green-600" />
                  ) : (
                    <Clock className="w-6 h-6 text-gray-600" />
                  )}
                  {taskStatus.status}
                </div>
              </div>
              <div>
                <div className="text-sm font-medium text-gray-500">Phase</div>
                <div className="text-2xl font-bold mt-2">{taskStatus.phase}</div>
              </div>
              <div>
                <div className="text-sm font-medium text-gray-500">Iteration</div>
                <div className="text-2xl font-bold mt-2">{taskStatus.iteration || '—'}</div>
              </div>
              <div>
                <div className="text-sm font-medium text-gray-500">Checkpoints</div>
                <div className="text-2xl font-bold mt-2">{taskStatus.checkpoints}</div>
              </div>
            </div>
          </div>
        )}

        {/* View: Checkpoints */}
        {view === 'checkpoints' && (
          <div>
            <h2 className="text-2xl font-bold mb-4">Checkpoints</h2>
            {loading ? (
              <div className="text-center py-8">Loading...</div>
            ) : checkpoints.length === 0 ? (
              <div className={`p-8 rounded-lg border text-center ${cardClass}`}>
                <Clock className="w-8 h-8 mx-auto mb-2 text-gray-400" />
                <p>No checkpoints found for this task</p>
              </div>
            ) : (
              <div className="space-y-4">
                {/* Checkpoint List */}
                <div className="space-y-3">
                  {checkpoints.map((cp) => (
                    <div
                      key={cp.checkpoint_id}
                      className={`p-4 rounded-lg border cursor-pointer transition ${cardClass} ${hoverClass}`}
                      onClick={() => fetchCheckpointDetails(taskId, cp.checkpoint_id)}
                    >
                      <div className="flex justify-between items-start">
                        <div>
                          <div className="font-mono text-sm text-blue-600">
                            {cp.checkpoint_id}
                          </div>
                          <div className="text-sm text-gray-500 mt-1">
                            Iteration {cp.iteration} • {cp.trigger}
                          </div>
                          <div className="text-xs text-gray-400 mt-2">
                            {new Date(cp.timestamp).toLocaleString()}
                          </div>
                        </div>
                        <div className="text-right">
                          <div className="text-lg font-bold text-green-600">
                            {cp.compression_pct}%
                          </div>
                          <div className="text-xs text-gray-500">compression</div>
                          <div className="text-sm text-amber-600 mt-2">
                            {cp.tokens_saved.toLocaleString()} tokens saved
                          </div>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>

                {/* Checkpoint Details */}
                {selectedCheckpoint && (
                  <div className={`mt-8 p-6 rounded-lg border ${cardClass}`}>
                    <div className="flex justify-between items-start mb-4">
                      <h3 className="text-xl font-bold">Checkpoint Details</h3>
                      <button
                        onClick={() =>
                          restoreCheckpoint(taskId, selectedCheckpoint.checkpoint_id)
                        }
                        disabled={loading}
                        className="px-4 py-2 bg-green-600 hover:bg-green-700 text-white rounded-lg flex items-center gap-2 disabled:opacity-50"
                      >
                        <Play className="w-4 h-4" /> Restore
                      </button>
                    </div>

                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-6">
                      <div>
                        <span className="text-sm text-gray-500">Compression Ratio</span>
                        <div className="text-2xl font-bold">
                          {selectedCheckpoint.compression.reduction_pct}%
                        </div>
                        <div className="text-xs text-gray-400 mt-1">
                          {selectedCheckpoint.compression.original_tokens} →{' '}
                          {selectedCheckpoint.compression.reduced_tokens} tokens
                        </div>
                      </div>
                      <div>
                        <span className="text-sm text-gray-500">Trigger</span>
                        <div className="text-2xl font-bold">{selectedCheckpoint.trigger}</div>
                      </div>
                    </div>

                    {/* Decisions, Errors, Learnings */}
                    <div className="space-y-6">
                      {selectedCheckpoint.decisions.length > 0 && (
                        <div>
                          <h4 className="font-bold mb-2">Decisions Made</h4>
                          <div className="space-y-2">
                            {selectedCheckpoint.decisions.map((d, i) => (
                              <div
                                key={i}
                                className={`p-2 rounded text-sm ${
                                  darkMode ? 'bg-gray-700' : 'bg-gray-100'
                                }`}
                              >
                                <strong>{d.decision}</strong>
                                <div className="text-xs text-gray-400 mt-1">{d.why}</div>
                              </div>
                            ))}
                          </div>
                        </div>
                      )}

                      {selectedCheckpoint.errors.length > 0 && (
                        <div>
                          <h4 className="font-bold mb-2">Errors Encountered</h4>
                          <div className="space-y-2">
                            {selectedCheckpoint.errors.map((e, i) => (
                              <div
                                key={i}
                                className={`p-2 rounded text-sm border-l-4 border-red-600 ${
                                  darkMode ? 'bg-red-900 bg-opacity-20' : 'bg-red-50'
                                }`}
                              >
                                <strong>{e.error_type || e.content}</strong>
                              </div>
                            ))}
                          </div>
                        </div>
                      )}

                      {selectedCheckpoint.recommendations.length > 0 && (
                        <div>
                          <h4 className="font-bold mb-2">Recommendations</h4>
                          <ul className="space-y-2">
                            {selectedCheckpoint.recommendations.map((r, i) => (
                              <li key={i} className="text-sm flex gap-2">
                                <span className="text-blue-600">→</span> {r}
                              </li>
                            ))}
                          </ul>
                        </div>
                      )}
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        )}

        {/* View: Timeline */}
        {view === 'timeline' && (
          <div>
            <h2 className="text-2xl font-bold mb-4">Execution Timeline</h2>
            {checkpoints.length === 0 ? (
              <div className={`p-8 rounded-lg border text-center ${cardClass}`}>
                <Clock className="w-8 h-8 mx-auto mb-2 text-gray-400" />
                <p>No checkpoints for timeline</p>
              </div>
            ) : (
              <div className="space-y-4">
                {checkpoints.map((cp, idx) => (
                  <div key={cp.checkpoint_id} className="flex gap-4">
                    <div className="flex flex-col items-center">
                      <div className="w-10 h-10 rounded-full bg-blue-600 text-white flex items-center justify-center font-bold">
                        {idx + 1}
                      </div>
                      {idx < checkpoints.length - 1 && (
                        <div className="w-1 h-12 bg-blue-200 mt-2"></div>
                      )}
                    </div>
                    <div className={`flex-1 p-4 rounded-lg border ${cardClass}`}>
                      <div className="font-bold">Iteration {cp.iteration}</div>
                      <div className="text-sm text-gray-500 mt-1">
                        {cp.trigger} • {cp.compression_pct}% compression
                      </div>
                      <div className="text-xs text-gray-400 mt-2">
                        {new Date(cp.timestamp).toLocaleString()}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* View: Metrics */}
        {view === 'metrics' && metrics && (
          <div>
            <h2 className="text-2xl font-bold mb-4">System Metrics</h2>
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
              <MetricCard
                title="Checkpoints Created"
                value={metrics.checkpoints_created}
                icon={<CheckCircle className="w-5 h-5" />}
                darkMode={darkMode}
              />
              <MetricCard
                title="Total Splits"
                value={metrics.total_splits}
                icon={<Zap className="w-5 h-5" />}
                darkMode={darkMode}
              />
              <MetricCard
                title="Avg Compression"
                value={`${metrics.avg_compression_pct}%`}
                icon={<Download className="w-5 h-5" />}
                darkMode={darkMode}
              />
              <MetricCard
                title="Tokens Saved"
                value={metrics.tokens_saved.toLocaleString()}
                icon={<Zap className="w-5 h-5 text-yellow-500" />}
                darkMode={darkMode}
              />
              <MetricCard
                title="Recovery Success Rate"
                value={`${(metrics.recovery_success_rate * 100).toFixed(0)}%`}
                icon={<CheckCircle className="w-5 h-5 text-green-600" />}
                darkMode={darkMode}
              />
              <MetricCard
                title="Uptime"
                value={`${(metrics.uptime_seconds / 3600).toFixed(1)}h`}
                icon={<Clock className="w-5 h-5" />}
                darkMode={darkMode}
              />
            </div>

            {/* Splits by trigger */}
            <div className={`mt-8 p-6 rounded-lg border ${cardClass}`}>
              <h3 className="text-lg font-bold mb-4">Splits by Trigger</h3>
              <div className="space-y-3">
                {Object.entries(metrics.splits_by_trigger).map(([trigger, count]) => (
                  <div key={trigger} className="flex justify-between items-center">
                    <span className="capitalize">{trigger.replace(/_/g, ' ')}</span>
                    <div className="flex items-center gap-2">
                      <div className="w-32 bg-gray-200 dark:bg-gray-700 rounded-full h-2">
                        <div
                          className="bg-blue-600 h-2 rounded-full"
                          style={{
                            width: `${(count / Math.max(...Object.values(metrics.splits_by_trigger))) * 100}%`,
                          }}
                        ></div>
                      </div>
                      <span className="font-bold w-8 text-right">{count}</span>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// Metric card component
function MetricCard({
  title,
  value,
  icon,
  darkMode,
}: {
  title: string;
  value: string | number;
  icon: React.ReactNode;
  darkMode: boolean;
}) {
  return (
    <div
      className={`p-6 rounded-lg border ${
        darkMode ? 'bg-gray-800 border-gray-700' : 'bg-white border-gray-200'
      }`}
    >
      <div className="flex items-start justify-between mb-2">
        <span className="text-sm font-medium text-gray-500">{title}</span>
        <div className="text-blue-600">{icon}</div>
      </div>
      <div className="text-3xl font-bold">{value}</div>
    </div>
  );
}
