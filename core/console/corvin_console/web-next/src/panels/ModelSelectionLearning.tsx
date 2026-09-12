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
  BarChart, Bar,
  XAxis, YAxis, CartesianGrid, Tooltip, Legend,
  ResponsiveContainer, Area, AreaChart,
} from 'recharts';
import {
  AlertCircle, CheckCircle, Clock, Download, Upload, RotateCcw,
  Gauge, DollarSign, Activity, Loader2,
} from 'lucide-react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';

interface ThresholdData {
  task_type: string;
  subsystem: string;
  learned_threshold: number;
  base_threshold: number;
  sample_count: number;
  converged: boolean;
  timestamp: string;
}

interface CostDayPoint {
  date: string;
  actual_usd: number;
  baseline_usd: number;
}

interface DashboardStatus {
  converged_count: number;
  total_count: number;
  thresholds: ThresholdData[];
  cost_savings_percent: number;
  cost_baseline_usd: number;
  cost_current_usd: number;
  cost_data_available: boolean;
  cost_history: CostDayPoint[];
  accuracy_percent: number;
  last_updated: string;
}

interface ThresholdHistory {
  timestamp: string;
  task_type: string;
  value: number;
}

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
      <div className="flex items-center justify-center h-full py-24">
        <Loader2 className="w-8 h-8 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (error && !status) {
    return (
      <div className="p-6">
        <Card className="border-destructive/30 bg-destructive/10">
          <CardContent className="py-6 flex items-center gap-2 text-destructive text-sm">
            <AlertCircle size={18} />
            <span>{error}</span>
          </CardContent>
        </Card>
      </div>
    );
  }

  if (!status) {
    return (
      <div className="p-6 text-sm text-muted-foreground">No data available</div>
    );
  }

  // Prepare chart data (simple task types for visualization)
  const convergenceData = status.thresholds.map(t => ({
    name: t.task_type.split('_').pop() || t.task_type,
    learned: t.learned_threshold,
    base: t.base_threshold,
    samples: t.sample_count,
    converged: t.converged ? 1 : 0,
  }));

  const hasThresholds = status.thresholds.length > 0;

  return (
    <div className="w-full h-full flex flex-col p-6 bg-background">
      {/* Header */}
      <div className="mb-6">
        <h1 className="text-3xl font-bold flex items-center gap-2">
          <Gauge className="w-7 h-7 text-accent" />
          Model Selection Learning
        </h1>
        <p className="text-muted-foreground mt-1">
          Learned thresholds &amp; cost optimization for automatic model routing
        </p>
        <p className="text-xs text-muted-foreground mt-2">
          ADR-0377 Phase 2b • Last updated: {new Date(status.last_updated).toLocaleTimeString()}
        </p>
      </div>

      {/* Error Banner */}
      {error && (
        <Card className="mb-6 border-destructive/30 bg-destructive/10">
          <CardContent className="py-3 flex items-center gap-2 text-destructive text-sm">
            <AlertCircle size={16} />
            <span>{error}</span>
          </CardContent>
        </Card>
      )}

      {/* KPI Cards */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4 mb-6">
        {/* Convergence Status */}
        <Card>
          <CardContent className="p-6">
            <div className="text-muted-foreground text-sm font-semibold mb-2 flex items-center gap-2">
              {status.converged_count === status.total_count ? (
                <CheckCircle size={16} className="text-emerald-600 dark:text-emerald-400" />
              ) : (
                <Clock size={16} className="text-amber-600 dark:text-amber-400" />
              )}
              Convergence
            </div>
            <div className="text-3xl font-bold">
              {status.converged_count}/{status.total_count}
            </div>
            <div className="text-xs text-muted-foreground mt-2">
              {Math.round((status.converged_count / status.total_count) * 100)}% converged
            </div>
          </CardContent>
        </Card>

        {/* Cost Savings */}
        <Card>
          <CardContent className="p-6">
            <div className="text-muted-foreground text-sm font-semibold mb-2 flex items-center gap-2">
              <DollarSign size={16} className="text-accent" />
              Cost Savings
            </div>
            {status.cost_data_available ? (
              <>
                <div className="text-3xl font-bold text-emerald-600 dark:text-emerald-400">
                  {status.cost_savings_percent.toFixed(1)}%
                </div>
                <div className="text-xs text-muted-foreground mt-2">
                  ${status.cost_baseline_usd.toFixed(2)} baseline → ${status.cost_current_usd.toFixed(2)} actual
                </div>
              </>
            ) : (
              <>
                <div className="text-3xl font-bold text-muted-foreground">—</div>
                <div className="text-xs text-muted-foreground mt-2">
                  No token-usage data yet
                </div>
              </>
            )}
          </CardContent>
        </Card>

        {/* Quality Maintenance */}
        <Card>
          <CardContent className="p-6">
            <div className="text-muted-foreground text-sm font-semibold mb-2 flex items-center gap-2">
              {status.accuracy_percent >= 85 ? (
                <CheckCircle size={16} className="text-emerald-600 dark:text-emerald-400" />
              ) : (
                <AlertCircle size={16} className="text-amber-600 dark:text-amber-400" />
              )}
              Accuracy
            </div>
            <div className="text-3xl font-bold">
              {status.accuracy_percent.toFixed(1)}%
            </div>
            <div className="text-xs text-muted-foreground mt-2">
              Target: ≥85%
            </div>
          </CardContent>
        </Card>

        {/* Last Update */}
        <Card>
          <CardContent className="p-6">
            <div className="text-muted-foreground text-sm font-semibold mb-2 flex items-center gap-2">
              <Activity size={16} className="text-accent" />
              Status
            </div>
            <div className="text-sm font-mono">
              <Badge variant={hasThresholds ? 'ok' : 'secondary'}>
                {hasThresholds ? 'Active' : 'Idle'}
              </Badge>
            </div>
            <div className="text-xs text-muted-foreground mt-2">
              {status.thresholds.length} task types tracked
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Threshold Convergence Chart */}
      <Card className="mb-6">
        <CardHeader>
          <CardTitle>Learned Thresholds vs Base</CardTitle>
          <CardDescription>
            Per-task-type routing threshold, learned value against the 0.5 base default
          </CardDescription>
        </CardHeader>
        <CardContent>
          {hasThresholds ? (
            <ResponsiveContainer width="100%" height={300}>
              <BarChart data={convergenceData}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="name" tick={{ fontSize: 12 }} />
                <YAxis domain={[0, 1]} tick={{ fontSize: 12 }} />
                <Tooltip formatter={(value) => (typeof value === 'number' ? value.toFixed(3) : value)} />
                <Legend />
                <Bar dataKey="base" fill="hsl(var(--muted-foreground))" name="Base (0.5)" />
                <Bar dataKey="learned" fill="hsl(var(--accent))" name="Learned" />
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <div className="py-16 text-center text-sm text-muted-foreground border border-dashed border-border rounded-lg">
              No learned thresholds yet — the model-selection routing path hasn't
              recorded any decisions for this tenant, so there is nothing to
              compare against the base threshold.
            </div>
          )}
        </CardContent>
      </Card>

      {/* Cost Efficiency */}
      <Card className="mb-6">
        <CardHeader>
          <CardTitle>Cost Efficiency Trend</CardTitle>
          <CardDescription>
            Real daily cost from actual token usage — actual model mix vs. an
            always-Opus baseline on the same tokens
          </CardDescription>
        </CardHeader>
        <CardContent>
          {status.cost_data_available && status.cost_history.length > 0 ? (
            <ResponsiveContainer width="100%" height={250}>
              <AreaChart data={status.cost_history}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="date" tick={{ fontSize: 12 }} />
                <YAxis tick={{ fontSize: 12 }} />
                <Tooltip formatter={(value) => `$${(value as number).toFixed(4)}`} />
                <Legend />
                <Area
                  type="monotone"
                  dataKey="baseline_usd"
                  name="Baseline (always Opus)"
                  fill="hsl(var(--muted-foreground))"
                  stroke="hsl(var(--muted-foreground))"
                  fillOpacity={0.15}
                />
                <Area
                  type="monotone"
                  dataKey="actual_usd"
                  name="Actual"
                  fill="hsl(var(--accent))"
                  stroke="hsl(var(--accent))"
                  fillOpacity={0.25}
                />
              </AreaChart>
            </ResponsiveContainer>
          ) : (
            <div className="py-16 text-center text-sm text-muted-foreground border border-dashed border-border rounded-lg">
              No cost data yet — token usage is only recorded on turns
              completed after this feature shipped (ADR-0696). Once new
              turns complete, real daily cost will appear here.
            </div>
          )}
        </CardContent>
      </Card>

      {/* Task Type Breakdown */}
      {hasThresholds ? (
        <div className="mb-6 grid grid-cols-1 md:grid-cols-3 gap-4">
          {status.thresholds.map((t) => (
            <Card key={`${t.task_type}:${t.subsystem}`}>
              <CardContent className="p-4">
                <div className="flex items-center justify-between mb-3">
                  <h3 className="font-semibold">{t.task_type}</h3>
                  <Badge variant={t.converged ? 'ok' : 'warn'}>
                    {t.converged ? 'Converged' : 'Learning'}
                  </Badge>
                </div>

                <div className="text-sm space-y-1 mb-3">
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">Threshold:</span>
                    <span className="font-mono font-semibold">
                      {t.learned_threshold.toFixed(3)}
                    </span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">Base:</span>
                    <span className="font-mono text-muted-foreground">
                      {t.base_threshold.toFixed(3)}
                    </span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">Samples:</span>
                    <span className="font-mono font-semibold">{t.sample_count}</span>
                  </div>
                </div>

                {/* Manual Override Slider */}
                <div className="mt-3 pt-3 border-t border-border">
                  <label className="text-xs font-semibold text-muted-foreground block mb-2">
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
                    className="w-full h-2 bg-muted rounded-lg appearance-none cursor-pointer accent-accent"
                  />
                  <div className="flex justify-between items-center text-xs mt-1">
                    <span className="text-muted-foreground">
                      {(overrideValues[t.task_type] ?? t.learned_threshold).toFixed(2)}
                    </span>
                    <Button
                      size="sm"
                      variant="accent"
                      onClick={() =>
                        handleOverride(t.task_type, overrideValues[t.task_type] ?? t.learned_threshold)
                      }
                    >
                      Apply
                    </Button>
                  </div>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      ) : (
        <Card className="mb-6 border-dashed">
          <CardContent className="py-12 text-center text-sm text-muted-foreground">
            No task types tracked yet.
          </CardContent>
        </Card>
      )}

      {/* Operator Controls */}
      <Card>
        <CardHeader>
          <CardTitle>Operator Controls</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex flex-wrap gap-3">
            {/* Export Button */}
            <Button variant="outline" size="sm" onClick={handleExport} disabled={exporting}>
              <Download size={16} />
              {exporting ? 'Exporting...' : 'Export'}
            </Button>

            {/* Import Button */}
            <Button variant="outline" size="sm" asChild>
              <label className="cursor-pointer">
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
            </Button>

            {/* Reset Button */}
            <Button
              size="sm"
              variant={resetConfirm ? 'destructive' : 'outline'}
              onClick={handleReset}
            >
              <RotateCcw size={16} />
              {resetConfirm ? 'Click again to confirm' : 'Reset Learning'}
            </Button>

            {resetConfirm && (
              <Button size="sm" variant="ghost" onClick={() => setResetConfirm(false)}>
                Cancel
              </Button>
            )}
          </div>

          <div className="mt-4 text-xs text-muted-foreground space-y-0.5">
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
        </CardContent>
      </Card>
    </div>
  );
};
