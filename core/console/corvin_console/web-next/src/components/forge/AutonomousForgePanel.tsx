import React, { useState, useEffect, useCallback } from 'react';
import { Loader2, RefreshCw } from 'lucide-react';
import { Button } from '@/components/ui/button';
import StatusDisplay, { CanaryState } from './StatusDisplay';
import MetricsChart, { MetricsDataPoint } from './MetricsChart';
import OperatorControls from './OperatorControls';
import AuditHistoryDisplay, { ForkAttempt } from './AuditHistoryDisplay';

interface ToastNotification {
  id: string;
  type: 'success' | 'error' | 'info';
  message: string;
}

/**
 * AutonomousForgePanel: Main component for autonomous skill forge monitoring
 * Auto-refreshes every 5 seconds during active canary
 */
export const AutonomousForgePanel: React.FC = () => {
  const [canaryState, setCanaryState] = useState<CanaryState | null>(null);
  const [metricsData, setMetricsData] = useState<MetricsDataPoint[]>([]);
  const [auditHistory, setAuditHistory] = useState<ForkAttempt[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [toasts, setToasts] = useState<ToastNotification[]>([]);

  const showToast = useCallback(
    (type: 'success' | 'error' | 'info', message: string) => {
      const id = String(Date.now());
      setToasts((prev) => [...prev, { id, type, message }]);

      if (type !== 'error') {
        setTimeout(() => {
          setToasts((prev) => prev.filter((t) => t.id !== id));
        }, 5000);
      }
    },
    []
  );

  const fetchData = useCallback(async () => {
    try {
      setLoading(true);

      // Fetch canary state
      const stateRes = await fetch('/v1/console/forge/autonomous/canary-state');
      const stateData = stateRes.ok ? await stateRes.json() : null;

      // Fetch metrics
      const metricsRes = await fetch('/v1/console/forge/autonomous/metrics?window=1h');
      const metricsData = metricsRes.ok ? await metricsRes.json() : null;

      // Fetch audit history
      const auditRes = await fetch('/v1/console/forge/autonomous/audit-history?limit=10');
      const auditData = auditRes.ok ? await auditRes.json() : null;

      setCanaryState(stateData?.canary_state || null);
      setMetricsData(metricsData?.metrics || []);
      setAuditHistory(auditData?.attempts || []);
    } catch (err) {
      console.error('Failed to fetch autonomous forge data:', err);
      showToast('error', 'Failed to load canary data');
    } finally {
      setLoading(false);
    }
  }, [showToast]);

  // Initial load
  useEffect(() => {
    fetchData();
  }, [fetchData]);

  // Auto-refresh every 5 seconds if canary is active
  useEffect(() => {
    if (!canaryState || canaryState.status !== 'canary') {
      return;
    }

    const interval = setInterval(() => {
      setRefreshing(true);
      fetchData().then(() => setRefreshing(false));
    }, 5000);

    return () => clearInterval(interval);
  }, [canaryState, fetchData]);

  const handleApprove = async () => {
    if (!canaryState) return;

    try {
      const res = await fetch('/v1/console/forge/autonomous/approve', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ skill_id: canaryState.skill_id }),
      });

      if (!res.ok) {
        throw new Error('Failed to approve canary');
      }

      showToast('success', 'Canary approved and rolled out to 100%');
      await fetchData();
    } catch (err) {
      showToast(
        'error',
        err instanceof Error ? err.message : 'Failed to approve canary'
      );
      throw err;
    }
  };

  const handleDefer = async (reason: string) => {
    if (!canaryState) return;

    try {
      const res = await fetch('/v1/console/forge/autonomous/defer', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          skill_id: canaryState.skill_id,
          reason: reason || 'Deferred by operator',
        }),
      });

      if (!res.ok) {
        throw new Error('Failed to defer canary');
      }

      showToast(
        'success',
        `Canary deferred. Keeping previous version active.`
      );
      await fetchData();
    } catch (err) {
      showToast(
        'error',
        err instanceof Error ? err.message : 'Failed to defer canary'
      );
      throw err;
    }
  };

  const handlePause = async () => {
    if (!canaryState) return;

    try {
      const res = await fetch('/v1/console/forge/autonomous/pause', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ skill_id: canaryState.skill_id }),
      });

      if (!res.ok) {
        throw new Error('Failed to pause canary');
      }

      showToast('success', 'Canary paused');
      await fetchData();
    } catch (err) {
      showToast(
        'error',
        err instanceof Error ? err.message : 'Failed to pause canary'
      );
      throw err;
    }
  };

  const handleResume = async () => {
    if (!canaryState) return;

    try {
      const res = await fetch('/v1/console/forge/autonomous/resume', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ skill_id: canaryState.skill_id }),
      });

      if (!res.ok) {
        throw new Error('Failed to resume canary');
      }

      showToast('success', 'Canary resumed');
      await fetchData();
    } catch (err) {
      showToast(
        'error',
        err instanceof Error ? err.message : 'Failed to resume canary'
      );
      throw err;
    }
  };

  const handleRollback = async () => {
    if (!canaryState) return;

    try {
      const res = await fetch('/v1/console/forge/autonomous/rollback', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ skill_id: canaryState.skill_id }),
      });

      if (!res.ok) {
        throw new Error('Failed to rollback canary');
      }

      showToast('success', 'Emergency rollback completed');
      await fetchData();
    } catch (err) {
      showToast(
        'error',
        err instanceof Error ? err.message : 'Failed to rollback canary'
      );
      throw err;
    }
  };

  const handleRefresh = async () => {
    setRefreshing(true);
    try {
      await fetchData();
      showToast('success', 'Data refreshed');
    } finally {
      setRefreshing(false);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-96">
        <Loader2 className="w-8 h-8 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Toast Notifications */}
      <div className="fixed top-4 right-4 space-y-2 z-50">
        {toasts.map((toast) => (
          <div
            key={toast.id}
            className={`px-4 py-3 rounded-lg text-sm font-medium ${
              toast.type === 'success'
                ? 'bg-green-600 text-white'
                : toast.type === 'error'
                  ? 'bg-red-600 text-white'
                  : 'bg-blue-600 text-white'
            }`}
          >
            {toast.message}
          </div>
        ))}
      </div>

      {/* Header with refresh button */}
      <div className="flex items-center justify-between">
        <h2 className="text-2xl font-bold">Autonomous Skill Forge</h2>
        <Button
          onClick={handleRefresh}
          disabled={refreshing}
          variant="outline"
          size="sm"
        >
          <RefreshCw className={`w-4 h-4 mr-2 ${refreshing ? 'animate-spin' : ''}`} />
          Refresh
        </Button>
      </div>

      {/* Main layout: 2-column grid */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Left column: Status + Metrics */}
        <div className="lg:col-span-2 space-y-6">
          <StatusDisplay canaryState={canaryState} loading={loading} />
          <MetricsChart data={metricsData} loading={loading} />
        </div>

        {/* Right column: Controls */}
        <div>
          {canaryState ? (
            <OperatorControls
              skillId={canaryState.skill_id}
              currentVersion={canaryState.version}
              canaryActive={canaryState.status === 'canary'}
              onApprove={handleApprove}
              onDefer={handleDefer}
              onPause={handlePause}
              onResume={handleResume}
              onRollback={handleRollback}
            />
          ) : (
            <div className="rounded-lg border border-dashed p-6 text-center text-muted-foreground">
              <p className="mb-2">No active canary deployment</p>
              <p className="text-sm">
                A canary will appear here when autonomous skill forging is in progress.
              </p>
            </div>
          )}
        </div>
      </div>

      {/* Audit History */}
      <AuditHistoryDisplay attempts={auditHistory} loading={loading} />
    </div>
  );
};

export default AutonomousForgePanel;
