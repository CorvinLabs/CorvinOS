/**
 * Learning Optimizer Control Panel (ADR-0629)
 *
 * Read-mostly operator interface for learning loop management:
 * - View current loop status (α, damping, loss, convergence)
 * - Inspect historical metrics and audit trail
 * - Manage checkpoints (view, rollback)
 * - Admin override (rare, audited)
 *
 * Tenant-scoped: all data filtered by authenticated session tenant_id.
 * RBAC: viewer (GET only) / admin (POST override, rollback).
 *
 * @category Console Panels
 * @author Claude Code
 * @since 2026-09-09
 */

import React, { useState, useEffect, useCallback, useMemo } from 'react';
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Badge } from '@/components/ui/badge';
import { AlertCircle, CheckCircle, Clock, TrendingDown } from 'lucide-react';
import { format, parseISO } from 'date-fns';

interface LearningStatus {
  timestamp: string;
  alpha_core: number;
  alpha_infra: number;
  damping_core: number;
  damping_infra: number;
  loss_total: number;
  loss_core: number;
  loss_infra: number;
  convergence_percent: number;
  status: 'converged' | 'converging' | 'diverging' | 'stalled';
}

interface MetricsPoint {
  timestamp: string;
  loss_total: number;
  loss_core: number;
  loss_infra: number;
  gradient_l2: number;
  alpha_core: number;
  damping_core: number;
}

interface Checkpoint {
  checkpoint_id: string;
  timestamp: string;
  loop_state: string;
  loss_at_checkpoint: number;
  created_by: string;
}

interface AuditEvent {
  event_id: string;
  event_type: 'override' | 'rollback' | 'auto_tune';
  loop_id: string;
  param: string;
  old_value: number;
  new_value: number;
  reason: string;
  operator_id: string;
  timestamp: string;
}

interface OverridePayload {
  loop: 'core' | 'infra';
  param: string;
  new_value: number;
  reason: string;
}

/**
 * LearningOptimizer — Read-mostly operator control panel for learning loops.
 *
 * **Tabs:**
 * 1. **Status** — current α, damping, loss, convergence % (live updates every 30s)
 * 2. **Metrics** — time-series loss curves (1h window, auto-refresh)
 * 3. **Checkpoints** — view and manage state snapshots
 * 4. **Audit Trail** — all operator actions (override, rollback)
 * 5. **Admin** — override α/damping (admin-only, requires reason)
 *
 * **Compliance:**
 * - Tenant isolation: all queries use authenticated session.tenant_id
 * - RBAC: admin-only endpoints POST /v1/console/learning/{override,rollback}
 * - Audit: every write → audit event with operator_id, reason, timestamp
 * - GDPR Art. 30: all actions logged and auditable
 */
export const LearningOptimizer: React.FC = () => {
  const [status, setStatus] = useState<LearningStatus | null>(null);
  const [metrics, setMetrics] = useState<MetricsPoint[]>([]);
  const [checkpoints, setCheckpoints] = useState<Checkpoint[]>([]);
  const [auditLog, setAuditLog] = useState<AuditEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [metricsWindow, setMetricsWindow] = useState<'1h' | '6h' | '24h'>('1h');

  // Admin override state
  const [overrideLoop, setOverrideLoop] = useState<'core' | 'infra'>('core');
  const [overrideParam, setOverrideParam] = useState<string>('alpha');
  const [overrideValue, setOverrideValue] = useState<string>('0.1');
  const [overrideReason, setOverrideReason] = useState<string>('');
  const [overrideLoading, setOverrideLoading] = useState(false);

  // Rollback state
  const [selectedCheckpoint, setSelectedCheckpoint] = useState<string | null>(null);
  const [rollbackLoading, setRollbackLoading] = useState(false);

  /**
   * Fetch current learning status
   */
  const fetchStatus = useCallback(async () => {
    try {
      const response = await fetch('/v1/console/learning/status', {
        credentials: 'include',
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      setStatus(data);
      setError(null);
    } catch (err) {
      setError(`Failed to fetch status: ${err instanceof Error ? err.message : String(err)}`);
    }
  }, []);

  /**
   * Fetch metrics time-series
   */
  const fetchMetrics = useCallback(async () => {
    try {
      const response = await fetch(`/v1/console/learning/metrics?window=${metricsWindow}`, {
        credentials: 'include',
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      setMetrics(data.points || []);
      setError(null);
    } catch (err) {
      setError(`Failed to fetch metrics: ${err instanceof Error ? err.message : String(err)}`);
    }
  }, [metricsWindow]);

  /**
   * Fetch checkpoints
   */
  const fetchCheckpoints = useCallback(async () => {
    try {
      const response = await fetch('/v1/console/learning/checkpoint', {
        credentials: 'include',
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      setCheckpoints(data.checkpoints || []);
      setError(null);
    } catch (err) {
      setError(`Failed to fetch checkpoints: ${err instanceof Error ? err.message : String(err)}`);
    }
  }, []);

  /**
   * Fetch audit trail
   */
  const fetchAuditLog = useCallback(async () => {
    try {
      const response = await fetch('/v1/console/learning/audit?limit=100', {
        credentials: 'include',
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      setAuditLog(data.events || []);
      setError(null);
    } catch (err) {
      setError(`Failed to fetch audit log: ${err instanceof Error ? err.message : String(err)}`);
    }
  }, []);

  /**
   * Submit override
   */
  const handleOverride = useCallback(async () => {
    if (!overrideReason.trim()) {
      setError('Reason is required for audit trail');
      return;
    }

    setOverrideLoading(true);
    try {
      const payload: OverridePayload = {
        loop: overrideLoop,
        param: overrideParam,
        new_value: parseFloat(overrideValue),
        reason: overrideReason,
      };

      const response = await fetch('/v1/console/learning/override', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
        credentials: 'include',
      });

      if (!response.ok) throw new Error(`HTTP ${response.status}`);

      // Reset form and refresh
      setOverrideValue('0.1');
      setOverrideReason('');
      await fetchStatus();
      await fetchAuditLog();
    } catch (err) {
      setError(`Override failed: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setOverrideLoading(false);
    }
  }, [overrideLoop, overrideParam, overrideValue, overrideReason, fetchStatus, fetchAuditLog]);

  /**
   * Submit rollback
   */
  const handleRollback = useCallback(async () => {
    if (!selectedCheckpoint) {
      setError('Select a checkpoint to rollback to');
      return;
    }

    setRollbackLoading(true);
    try {
      const response = await fetch(`/v1/console/learning/rollback/${selectedCheckpoint}`, {
        method: 'POST',
        credentials: 'include',
      });

      if (!response.ok) throw new Error(`HTTP ${response.status}`);

      // Refresh status and audit log
      await fetchStatus();
      await fetchAuditLog();
      setSelectedCheckpoint(null);
    } catch (err) {
      setError(`Rollback failed: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setRollbackLoading(false);
    }
  }, [selectedCheckpoint, fetchStatus, fetchAuditLog]);

  /**
   * Initial load and setup refresh intervals
   */
  useEffect(() => {
    const load = async () => {
      setLoading(true);
      try {
        await Promise.all([
          fetchStatus(),
          fetchMetrics(),
          fetchCheckpoints(),
          fetchAuditLog(),
        ]);
      } finally {
        setLoading(false);
      }
    };

    load();

    // Refresh status every 30s
    const statusInterval = setInterval(fetchStatus, 30000);

    return () => clearInterval(statusInterval);
  }, [fetchStatus, fetchMetrics, fetchCheckpoints, fetchAuditLog]);

  /**
   * Refresh metrics when window changes
   */
  useEffect(() => {
    fetchMetrics();
  }, [metricsWindow, fetchMetrics]);

  if (loading) {
    return (
      <Card>
        <CardContent className="flex items-center justify-center py-12">
          <div className="text-center text-muted-foreground">Loading learning status...</div>
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="w-full space-y-4">
      {error && (
        <Card className="border-red-200 bg-red-50">
          <CardContent className="flex items-center gap-2 pt-6">
            <AlertCircle className="h-4 w-4 text-red-600" />
            <span className="text-sm text-red-700">{error}</span>
          </CardContent>
        </Card>
      )}

      <Tabs defaultValue="status" className="w-full">
        <TabsList className="grid w-full grid-cols-5">
          <TabsTrigger value="status">Status</TabsTrigger>
          <TabsTrigger value="metrics">Metrics</TabsTrigger>
          <TabsTrigger value="checkpoints">Checkpoints</TabsTrigger>
          <TabsTrigger value="audit">Audit</TabsTrigger>
          <TabsTrigger value="admin">Admin</TabsTrigger>
        </TabsList>

        {/* ──── STATUS TAB ──── */}
        <TabsContent value="status">
          {status && (
            <Card>
              <CardHeader>
                <CardTitle>Learning Loop Status</CardTitle>
                <CardDescription>Current state as of {format(parseISO(status.timestamp), 'PPpp')}</CardDescription>
              </CardHeader>
              <CardContent className="space-y-6">
                <div className="grid grid-cols-2 gap-4">
                  <StatusItem
                    label="Status"
                    value={status.status.toUpperCase()}
                    icon={
                      status.status === 'converged' ? (
                        <CheckCircle className="h-4 w-4 text-green-600" />
                      ) : status.status === 'diverging' ? (
                        <AlertCircle className="h-4 w-4 text-red-600" />
                      ) : (
                        <Clock className="h-4 w-4 text-blue-600" />
                      )
                    }
                  />
                  <StatusItem
                    label="Convergence"
                    value={`${status.convergence_percent.toFixed(1)}%`}
                    icon={<TrendingDown className="h-4 w-4 text-blue-600" />}
                  />
                </div>

                <div className="space-y-4 border-t pt-4">
                  <h3 className="text-sm font-semibold">Core Loop</h3>
                  <div className="grid grid-cols-2 gap-4">
                    <StatCard label="α (learning rate)" value={status.alpha_core.toFixed(4)} />
                    <StatCard label="Damping" value={status.damping_core.toFixed(4)} />
                    <StatCard label="Loss (L_core)" value={status.loss_core.toFixed(6)} />
                  </div>
                </div>

                <div className="space-y-4 border-t pt-4">
                  <h3 className="text-sm font-semibold">Infrastructure Loop</h3>
                  <div className="grid grid-cols-2 gap-4">
                    <StatCard label="α (learning rate)" value={status.alpha_infra.toFixed(4)} />
                    <StatCard label="Damping" value={status.damping_infra.toFixed(4)} />
                    <StatCard label="Loss (L_infra)" value={status.loss_infra.toFixed(6)} />
                  </div>
                </div>

                <div className="space-y-4 border-t pt-4">
                  <h3 className="text-sm font-semibold">Total Loss</h3>
                  <div>
                    <StatCard label="L_total" value={status.loss_total.toFixed(6)} />
                  </div>
                </div>
              </CardContent>
            </Card>
          )}
        </TabsContent>

        {/* ──── METRICS TAB ──── */}
        <TabsContent value="metrics">
          <Card>
            <CardHeader>
              <CardTitle>Loss Metrics Over Time</CardTitle>
              <CardDescription>Time-series data for learning loop parameters</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="flex items-center gap-2">
                <span className="text-sm font-medium">Time Window:</span>
                <Select value={metricsWindow} onValueChange={(v) => setMetricsWindow(v as '1h' | '6h' | '24h')}>
                  <SelectTrigger className="w-[150px]">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="1h">Last 1 hour</SelectItem>
                    <SelectItem value="6h">Last 6 hours</SelectItem>
                    <SelectItem value="24h">Last 24 hours</SelectItem>
                  </SelectContent>
                </Select>
              </div>

              <div className="space-y-2">
                {metrics.length === 0 ? (
                  <div className="text-sm text-muted-foreground">No metrics available yet</div>
                ) : (
                  <>
                    <div className="max-h-96 overflow-y-auto">
                      <table className="w-full text-sm">
                        <thead className="sticky top-0 bg-muted">
                          <tr>
                            <th className="text-left px-2 py-2">Timestamp</th>
                            <th className="text-right px-2 py-2">L_total</th>
                            <th className="text-right px-2 py-2">α_core</th>
                            <th className="text-right px-2 py-2">||∇||</th>
                          </tr>
                        </thead>
                        <tbody className="divide-y">
                          {metrics.map((m) => (
                            <tr key={m.timestamp} className="hover:bg-muted/50">
                              <td className="px-2 py-1">{format(parseISO(m.timestamp), 'HH:mm:ss')}</td>
                              <td className="text-right px-2 py-1">{m.loss_total.toFixed(6)}</td>
                              <td className="text-right px-2 py-1">{m.alpha_core.toFixed(4)}</td>
                              <td className="text-right px-2 py-1">{m.gradient_l2.toFixed(4)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </>
                )}
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        {/* ──── CHECKPOINTS TAB ──── */}
        <TabsContent value="checkpoints">
          <Card>
            <CardHeader>
              <CardTitle>Loop Checkpoints</CardTitle>
              <CardDescription>Saved states for recovery and testing</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              {checkpoints.length === 0 ? (
                <div className="text-sm text-muted-foreground">No checkpoints available</div>
              ) : (
                <>
                  <div className="space-y-2">
                    {checkpoints.map((cp) => (
                      <div key={cp.checkpoint_id} className="flex items-center gap-3 rounded-lg border p-3">
                        <input
                          type="radio"
                          name="checkpoint"
                          id={`cp-${cp.checkpoint_id}`}
                          value={cp.checkpoint_id}
                          checked={selectedCheckpoint === cp.checkpoint_id}
                          onChange={(e) => setSelectedCheckpoint(e.target.value)}
                          disabled={rollbackLoading}
                        />
                        <div className="flex-1">
                          <label
                            htmlFor={`cp-${cp.checkpoint_id}`}
                            className="text-sm font-medium cursor-pointer"
                          >
                            {cp.checkpoint_id}
                          </label>
                          <div className="text-xs text-muted-foreground">
                            {format(parseISO(cp.timestamp), 'PPp')} — Loss: {cp.loss_at_checkpoint.toFixed(6)}
                            {cp.created_by && ` (by ${cp.created_by})`}
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>

                  <Button
                    onClick={handleRollback}
                    disabled={!selectedCheckpoint || rollbackLoading}
                    className="w-full"
                  >
                    {rollbackLoading ? 'Rolling back...' : 'Rollback to Selected'}
                  </Button>
                </>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        {/* ──── AUDIT TAB ──── */}
        <TabsContent value="audit">
          <Card>
            <CardHeader>
              <CardTitle>Audit Trail</CardTitle>
              <CardDescription>All operator actions and system events</CardDescription>
            </CardHeader>
            <CardContent className="space-y-2">
              {auditLog.length === 0 ? (
                <div className="text-sm text-muted-foreground">No audit events</div>
              ) : (
                <div className="max-h-96 overflow-y-auto space-y-2">
                  {auditLog.map((event) => (
                    <div key={event.event_id} className="rounded-lg border p-3 text-sm">
                      <div className="flex items-center justify-between gap-2">
                        <Badge variant="outline">{event.event_type}</Badge>
                        <span className="text-xs text-muted-foreground">
                          {format(parseISO(event.timestamp), 'PPp')}
                        </span>
                      </div>
                      <div className="mt-2 space-y-1 text-xs">
                        <div>
                          <span className="font-medium">{event.param}:</span>{' '}
                          <code className="bg-muted px-1 rounded">
                            {event.old_value.toFixed(4)} → {event.new_value.toFixed(4)}
                          </code>
                        </div>
                        {event.reason && (
                          <div>
                            <span className="font-medium">Reason:</span> {event.reason}
                          </div>
                        )}
                        {event.operator_id && (
                          <div>
                            <span className="font-medium">Operator:</span> {event.operator_id}
                          </div>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        {/* ──── ADMIN TAB ──── */}
        <TabsContent value="admin">
          <Card className="border-amber-200 bg-amber-50">
            <CardHeader>
              <CardTitle className="text-amber-900">Admin Override</CardTitle>
              <CardDescription className="text-amber-800">
                Manually adjust learning parameters (admin only, audited)
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="space-y-3">
                <div>
                  <label className="text-sm font-medium">Loop</label>
                  <Select value={overrideLoop} onValueChange={(v) => setOverrideLoop(v as 'core' | 'infra')}>
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="core">Core Loop</SelectItem>
                      <SelectItem value="infra">Infrastructure Loop</SelectItem>
                    </SelectContent>
                  </Select>
                </div>

                <div>
                  <label className="text-sm font-medium">Parameter</label>
                  <Select value={overrideParam} onValueChange={setOverrideParam}>
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="alpha">Learning Rate (α)</SelectItem>
                      <SelectItem value="damping">Damping</SelectItem>
                    </SelectContent>
                  </Select>
                </div>

                <div>
                  <label className="text-sm font-medium">New Value</label>
                  <Input
                    type="number"
                    step="0.0001"
                    min="0"
                    max="1"
                    value={overrideValue}
                    onChange={(e) => setOverrideValue(e.target.value)}
                    disabled={overrideLoading}
                  />
                </div>

                <div>
                  <label className="text-sm font-medium">Reason (required)</label>
                  <Textarea
                    placeholder="Explain why you are making this change (for audit trail)..."
                    value={overrideReason}
                    onChange={(e) => setOverrideReason(e.target.value)}
                    disabled={overrideLoading}
                    rows={3}
                  />
                </div>

                <Button onClick={handleOverride} disabled={overrideLoading} className="w-full" variant="destructive">
                  {overrideLoading ? 'Applying override...' : 'Apply Override'}
                </Button>
              </div>
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
};

/**
 * Helper component: Status display item
 */
const StatusItem: React.FC<{
  label: string;
  value: string;
  icon?: React.ReactNode;
}> = ({ label, value, icon }) => (
  <div className="flex items-center gap-3 rounded-lg border p-3">
    {icon && <div className="flex-shrink-0">{icon}</div>}
    <div className="flex-1">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="text-sm font-semibold">{value}</div>
    </div>
  </div>
);

/**
 * Helper component: Stat card
 */
const StatCard: React.FC<{ label: string; value: string }> = ({ label, value }) => (
  <div className="rounded-lg bg-muted p-3">
    <div className="text-xs font-medium text-muted-foreground">{label}</div>
    <div className="text-lg font-semibold font-mono">{value}</div>
  </div>
);

export default LearningOptimizer;
