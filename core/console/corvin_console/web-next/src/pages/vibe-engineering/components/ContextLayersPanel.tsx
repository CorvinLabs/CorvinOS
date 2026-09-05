/**
 * Task Context Inspector v2 — Auto-load latest task from audit chain
 *
 * Features:
 * - Auto-loads available tasks on mount
 * - Auto-selects & loads latest task
 * - Shows 4-layer context breakdown (original, preserved, injected, merged)
 * - Dark/light mode support
 */

import React, { useState, useEffect } from 'react';
import { Copy, ChevronDown, ChevronUp, Search, RefreshCw } from 'lucide-react';

interface ContextLayer {
  name: string;
  version: string;
  data: Record<string, any>;
  timestamp_utc: string;
  hash: string;
  lom?: string;
  status: string;
}

interface ContextLayersData {
  task_id: string;
  original: ContextLayer;
  preserved: ContextLayer;
  injected: ContextLayer[];
  merged: ContextLayer;
}

interface TaskOption {
  task_id: string;
  timestamp: string;
  event_type: string;
}

interface LayerCardProps {
  title: string;
  layer: ContextLayer;
  color: 'green' | 'blue' | 'yellow' | 'red';
}

const LayerCard: React.FC<LayerCardProps> = ({ title, layer, color }) => {
  const [expanded, setExpanded] = useState(true);
  const [searchTerm, setSearchTerm] = useState('');
  const [, setCopied] = useState(false);

  const colorClasses = {
    green: 'border-green-500/30 bg-green-500/5',
    blue: 'border-blue-500/30 bg-blue-500/5',
    yellow: 'border-yellow-500/30 bg-yellow-500/5',
    red: 'border-red-500/30 bg-red-500/5',
  };

  const colorDots = {
    green: 'bg-green-500',
    blue: 'bg-blue-500',
    yellow: 'bg-yellow-500',
    red: 'bg-red-500',
  };

  const filteredData = Object.fromEntries(
    Object.entries(layer.data).filter(([key]) =>
      key.toLowerCase().includes(searchTerm.toLowerCase())
    )
  );

  const handleCopy = () => {
    navigator.clipboard.writeText(JSON.stringify(layer.data, null, 2));
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className={`bg-card border border-border rounded-lg overflow-hidden ${colorClasses[color]}`}>
      {/* Header */}
      <div className="bg-card border-b border-border px-4 py-3 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <span className={`h-3 w-3 rounded-full ${colorDots[color]}`} />
          <div>
            <h3 className="text-sm font-semibold text-foreground">{title}</h3>
            <p className="text-xs text-muted-foreground">v{layer.version}</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={handleCopy}
            className="p-1.5 hover:bg-muted rounded transition-colors"
            title="Copy to clipboard"
          >
            <Copy className="w-4 h-4 text-muted-foreground" />
          </button>
          <button
            onClick={() => setExpanded(!expanded)}
            className="p-1.5 hover:bg-muted rounded transition-colors"
          >
            {expanded ? (
              <ChevronUp className="w-4 h-4 text-muted-foreground" />
            ) : (
              <ChevronDown className="w-4 h-4 text-muted-foreground" />
            )}
          </button>
        </div>
      </div>

      {/* Content */}
      {expanded && (
        <div className="p-4 space-y-4">
          {/* Metadata */}
          <div className="space-y-1 pb-3 border-b border-border">
            <p className="text-xs text-muted-foreground">
              <span className="font-mono">ts:</span> {new Date(layer.timestamp_utc).toLocaleString()}
            </p>
            {layer.lom && (
              <p className="text-xs text-muted-foreground">
                <span className="font-mono">lom:</span> {layer.lom}
              </p>
            )}
            <p className="text-xs text-muted-foreground break-all">
              <span className="font-mono">hash:</span> {layer.hash.substring(0, 16)}...
            </p>
          </div>

          {/* Search (if data is large) */}
          {Object.keys(layer.data).length > 5 && (
            <div className="flex items-center gap-2">
              <Search className="w-4 h-4 text-muted-foreground" />
              <input
                type="text"
                placeholder="Search fields..."
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
                className="flex-1 px-2 py-1 text-xs bg-background border border-border rounded focus:outline-none focus:ring-1 focus:ring-primary text-foreground"
              />
            </div>
          )}

          {/* Data */}
          <div className="space-y-2">
            {Object.entries(filteredData).length === 0 ? (
              <p className="text-xs text-muted-foreground italic">No data matches search</p>
            ) : (
              Object.entries(filteredData).map(([key, value]) => (
                <div key={key} className="space-y-1 pb-2 border-b border-border/50 last:border-b-0 last:pb-0">
                  <p className="text-xs font-mono font-semibold text-foreground">{key}</p>
                  <pre className="text-xs bg-background border border-border/50 rounded p-2 overflow-x-auto max-h-32 overflow-y-auto text-foreground whitespace-pre-wrap break-words">
                    {typeof value === 'string' ? value : JSON.stringify(value, null, 2)}
                  </pre>
                </div>
              ))
            )}
          </div>
        </div>
      )}

      {/* Collapse indicator */}
      {!expanded && (
        <div className="px-4 py-2 bg-muted/20 text-xs text-muted-foreground">
          {Object.keys(layer.data).length} fields
        </div>
      )}
    </div>
  );
};

export const ContextLayersPanel: React.FC = () => {
  const [taskId, setTaskId] = useState('');
  const [data, setData] = useState<ContextLayersData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [availableTasks, setAvailableTasks] = useState<TaskOption[]>([]);
  const [autoLoadDone, setAutoLoadDone] = useState(false);

  // Load available tasks on mount
  useEffect(() => {
    const loadAvailableTasks = async () => {
      try {
        console.log('Loading available tasks...');
        const response = await fetch('/v1/console/vibe/tasks/list?limit=20');
        if (response.ok) {
          const result = await response.json();
          console.log('Tasks loaded:', result);
          setAvailableTasks(result.tasks || []);

          // Auto-select latest task
          if (result.latest_task_id) {
            console.log('Auto-selecting task:', result.latest_task_id);
            setTaskId(result.latest_task_id);
            setAutoLoadDone(true);
          } else {
            setLoading(false);
          }
        } else {
          console.warn('Failed to load tasks:', response.status);
          setLoading(false);
        }
      } catch (err) {
        console.error('Failed to load available tasks:', err);
        setLoading(false);
      }
    };

    loadAvailableTasks();
  }, []);

  // Auto-load context when taskId changes (only after initial setup)
  useEffect(() => {
    if (taskId && autoLoadDone) {
      fetchContextLayers();
    }
  }, [taskId, autoLoadDone]);

  const fetchContextLayers = async () => {
    if (!taskId.trim()) {
      setError('No task selected');
      setLoading(false);
      return;
    }

    setLoading(true);
    setError(null);

    try {
      console.log('Fetching context for task:', taskId);
      const response = await fetch(`/v1/console/vibe/task/${encodeURIComponent(taskId)}/context-layers`);
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      }
      const result = await response.json();
      console.log('Context loaded:', result);
      setData(result);
    } catch (err) {
      console.error('Error fetching context:', err);
      setError(`Failed to load context: ${err instanceof Error ? err.message : String(err)}`);
      setData(null);
    } finally {
      setLoading(false);
    }
  };

  const handleKeyPress = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      fetchContextLayers();
    }
  };

  return (
    <div className="min-h-screen bg-background p-6">
      <div className="max-w-7xl mx-auto space-y-6">
        {/* Header */}
        <div>
          <h2 className="text-2xl font-bold text-foreground mb-2">Task Context Inspector</h2>
          <p className="text-sm text-muted-foreground">
            Visualize the 4-layer context breakdown for a task (Original, Preserved, Injected, Merged)
          </p>
        </div>

        {/* Task ID Selection */}
        <div className="bg-card border border-border rounded-lg p-4">
          <label className="block text-sm font-semibold text-foreground mb-3">
            Select Task
            {availableTasks.length > 0 && (
              <span className="text-xs text-muted-foreground ml-2">({availableTasks.length} available)</span>
            )}
          </label>
          <div className="flex gap-2">
            {availableTasks.length > 0 ? (
              <>
                <select
                  value={taskId}
                  onChange={(e) => setTaskId(e.target.value)}
                  data-testid="task-id-input"
                  className="flex-1 px-3 py-2 bg-background border border-border rounded text-foreground focus:outline-none focus:ring-2 focus:ring-primary"
                >
                  <option value="">-- Select a task --</option>
                  {availableTasks.map((task) => (
                    <option key={task.task_id} value={task.task_id}>
                      {task.task_id} ({new Date(task.timestamp).toLocaleTimeString()})
                    </option>
                  ))}
                </select>
              </>
            ) : (
              <>
                <input
                  type="text"
                  value={taskId}
                  onChange={(e) => setTaskId(e.target.value)}
                  onKeyPress={handleKeyPress}
                  placeholder="Enter task ID..."
                  data-testid="task-id-input"
                  className="flex-1 px-3 py-2 bg-background border border-border rounded text-foreground focus:outline-none focus:ring-2 focus:ring-primary"
                />
              </>
            )}
            <button
              onClick={fetchContextLayers}
              disabled={loading || !taskId}
              data-testid="load-button"
              className="px-4 py-2 bg-primary text-primary-foreground rounded font-semibold hover:opacity-90 disabled:opacity-50 transition-opacity whitespace-nowrap flex items-center gap-2"
            >
              {loading ? (
                <>
                  <RefreshCw className="w-4 h-4 animate-spin" />
                  Loading...
                </>
              ) : (
                'Reload'
              )}
            </button>
          </div>
          {availableTasks.length === 0 && !loading && (
            <p className="text-xs text-muted-foreground mt-2">
              ℹ️ No tasks found in audit chain yet. Tasks will appear here as they are created.
            </p>
          )}
          {loading && availableTasks.length === 0 && (
            <p className="text-xs text-muted-foreground mt-2">
              ⏳ Loading available tasks...
            </p>
          )}
        </div>

        {/* Error State */}
        {error && (
          <div className="bg-red-500/10 border border-red-500/30 rounded-lg p-4">
            <p className="text-sm text-red-400">{error}</p>
          </div>
        )}

        {/* Context Layers */}
        {data && !loading && (
          <div className="space-y-6">
            {/* Original Layer */}
            <div data-testid="original-layer">
              <h3 className="text-lg font-bold text-foreground mb-3">Original Context</h3>
              <LayerCard title="Original Base" layer={data.original} color="green" />
            </div>

            {/* Preserved Layer */}
            <div data-testid="preserved-layer">
              <h3 className="text-lg font-bold text-foreground mb-3">Preserved Layers</h3>
              <LayerCard title="Preserved Fields" layer={data.preserved} color="blue" />
            </div>

            {/* Injected Layers */}
            <div data-testid="injected-layer">
              <h3 className="text-lg font-bold text-foreground mb-3">Injected Layers</h3>
              {data.injected.length === 0 ? (
                <p className="text-sm text-muted-foreground italic">No injected layers</p>
              ) : (
                <div className="space-y-3">
                  {data.injected.map((layer, idx) => (
                    <LayerCard key={idx} title={layer.name} layer={layer} color="yellow" />
                  ))}
                </div>
              )}
            </div>

            {/* Merged Layer */}
            <div data-testid="merged-layer">
              <h3 className="text-lg font-bold text-foreground mb-3">Merged Result</h3>
              <LayerCard title="Final Context (Merged)" layer={data.merged} color="red" />
            </div>
          </div>
        )}

        {/* Empty State */}
        {!data && !loading && !error && (
          <div className="bg-card border border-border rounded-lg p-8 text-center">
            <p className="text-sm text-muted-foreground">
              {availableTasks.length > 0
                ? 'Select a task from the dropdown and click "Reload" to view context layers'
                : 'Waiting for tasks to appear in audit chain...'}
            </p>
          </div>
        )}
      </div>
    </div>
  );
};
