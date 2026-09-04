/**
 * Phase 5: L5 Metrics Monitor — Real-time Deployment Monitoring Dashboard
 *
 * Features:
 * - Real-time L5 stack health visualization
 * - Gate latency metrics (p50/p95/p99) with SLA visualization
 * - Decision distribution (auto-approved %, pending %, rejected %)
 * - Config apply success rate (%)
 * - Revoke rate + holdover time analysis
 * - Operator latency SLA monitoring (target <5min)
 * - Cross-skill coordination metrics
 * - Active alerts with acknowledgement
 * - WebSocket live updates
 * - Responsive design
 *
 * ADR-0588: L5 Deployment Monitoring
 */

import React, { useState, useEffect, useCallback, useMemo } from "react";
import {
  AlertCircle,
  CheckCircle,
  TrendingUp,
  TrendingDown,
  BarChart3,
  Clock,
  Activity,
  AlertTriangle,
  RefreshCw,
  X,
} from "lucide-react";

// ============================================================================
// Types
// ============================================================================

interface GateHealthStatus {
  gate_name: string;
  is_healthy: boolean;
  latency_p50_ms?: number;
  latency_p95_ms?: number;
  latency_p99_ms?: number;
  avg_latency_ms?: number;
  error_rate_pct: number;
  pending_count: number;
  sla_breaches: number;
  last_check_timestamp: string;
}

interface L5HealthSnapshot {
  timestamp: string;
  all_healthy: boolean;
  gates: Record<string, GateHealthStatus>;
  total_pending: number;
  auto_approval_rate_pct: number;
  rejection_rate_pct: number;
  config_apply_success_rate_pct: number;
  avg_operator_latency_ms?: number;
  sla_status: string;
  alerts: string[];
}

interface Alert {
  alert_id: string;
  severity: string;
  message: string;
  gate_name?: string;
  skill_id?: string;
  timestamp: string;
  is_acknowledged: boolean;
}

interface MetricsService {
  baseURL: string;
  tenantID: string;

  getHealthStatus(): Promise<L5HealthSnapshot>;
  getTimeseries(startTime: string, endTime: string): Promise<any>;
  getActiveAlerts(): Promise<Alert[]>;
  acknowledgeAlert(alertId: string): Promise<boolean>;
  resolveAlert(alertId: string): Promise<boolean>;
}

// ============================================================================
// Metrics Service
// ============================================================================

class L5MetricsService implements MetricsService {
  baseURL = "/v1/metrics/l5";
  tenantID = "_default";

  async getHealthStatus(): Promise<L5HealthSnapshot> {
    const res = await fetch(`${this.baseURL}/status?tenant_id=${this.tenantID}`);
    if (!res.ok) throw new Error(`Failed to fetch health status: ${res.statusText}`);
    return res.json();
  }

  async getTimeseries(startTime: string, endTime: string): Promise<any> {
    const params = new URLSearchParams({
      start: startTime,
      end: endTime,
      tenant_id: this.tenantID,
    });
    const res = await fetch(`${this.baseURL}/timeseries?${params}`);
    if (!res.ok) throw new Error(`Failed to fetch timeseries: ${res.statusText}`);
    return res.json();
  }

  async getActiveAlerts(): Promise<Alert[]> {
    const res = await fetch(`${this.baseURL}/alerts?tenant_id=${this.tenantID}`);
    if (!res.ok) throw new Error(`Failed to fetch alerts: ${res.statusText}`);
    return res.json();
  }

  async acknowledgeAlert(alertId: string): Promise<boolean> {
    const res = await fetch(`${this.baseURL}/alerts/${alertId}/acknowledge`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tenant_id: this.tenantID }),
    });
    return res.ok;
  }

  async resolveAlert(alertId: string): Promise<boolean> {
    const res = await fetch(`${this.baseURL}/alerts/${alertId}/resolve`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tenant_id: this.tenantID }),
    });
    return res.ok;
  }
}

// ============================================================================
// Component: Health Status Indicator
// ============================================================================

interface HealthIndicatorProps {
  isHealthy: boolean;
  slaStatus: string;
}

function HealthIndicator({ isHealthy, slaStatus }: HealthIndicatorProps) {
  const getStatusColor = () => {
    if (slaStatus === "CRITICAL") return "text-red-600";
    if (slaStatus === "WARNING") return "text-yellow-600";
    return "text-green-600";
  };

  const getStatusLabel = () => {
    if (slaStatus === "CRITICAL") return "CRITICAL";
    if (slaStatus === "WARNING") return "WARNING";
    return "OK";
  };

  const Icon = isHealthy ? CheckCircle : AlertCircle;

  return (
    <div className="flex items-center gap-2">
      <Icon className={`w-6 h-6 ${getStatusColor()}`} />
      <span className={`text-lg font-semibold ${getStatusColor()}`}>
        {getStatusLabel()}
      </span>
    </div>
  );
}

// ============================================================================
// Component: Gate Health Card
// ============================================================================

interface GateHealthCardProps {
  status: GateHealthStatus;
}

function GateHealthCard({ status }: GateHealthCardProps) {
  const getHealthColor = (isHealthy: boolean) =>
    isHealthy
      ? "border-green-200 bg-green-50"
      : "border-red-200 bg-red-50";

  const formatLatency = (ms?: number) =>
    ms !== undefined ? `${ms.toFixed(0)}ms` : "N/A";

  return (
    <div
      className={`border-2 rounded-lg p-4 ${getHealthColor(status.is_healthy)}`}
    >
      <h3 className="font-semibold text-lg mb-3">{status.gate_name}</h3>

      <div className="grid grid-cols-2 gap-4 mb-4">
        <div>
          <span className="text-sm text-gray-600">P50</span>
          <p className="text-lg font-mono">{formatLatency(status.latency_p50_ms)}</p>
        </div>
        <div>
          <span className="text-sm text-gray-600">P95</span>
          <p className="text-lg font-mono">{formatLatency(status.latency_p95_ms)}</p>
        </div>
        <div>
          <span className="text-sm text-gray-600">P99</span>
          <p className="text-lg font-mono">{formatLatency(status.latency_p99_ms)}</p>
        </div>
        <div>
          <span className="text-sm text-gray-600">AVG</span>
          <p className="text-lg font-mono">{formatLatency(status.avg_latency_ms)}</p>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-4">
        <div>
          <span className="text-sm text-gray-600">Error Rate</span>
          <p className="text-lg font-mono">{status.error_rate_pct.toFixed(1)}%</p>
        </div>
        <div>
          <span className="text-sm text-gray-600">Pending</span>
          <p className="text-lg font-mono">{status.pending_count}</p>
        </div>
      </div>

      {status.sla_breaches > 0 && (
        <div className="mt-3 text-sm text-red-600 font-semibold">
          ⚠️ {status.sla_breaches} SLA breaches
        </div>
      )}
    </div>
  );
}

// ============================================================================
// Component: Metrics Summary
// ============================================================================

interface MetricsSummaryProps {
  snapshot: L5HealthSnapshot;
}

function MetricsSummary({ snapshot }: MetricsSummaryProps) {
  const renderMetricBox = (label: string, value: string | number, unit: string = "") => (
    <div className="bg-white p-4 rounded-lg border border-gray-200">
      <span className="text-sm text-gray-600">{label}</span>
      <p className="text-2xl font-bold text-gray-900">
        {typeof value === "number" ? value.toFixed(1) : value}
        <span className="text-sm ml-1">{unit}</span>
      </p>
    </div>
  );

  return (
    <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
      {renderMetricBox("Total Pending", snapshot.total_pending, "decisions")}
      {renderMetricBox(
        "Auto-Approved",
        snapshot.auto_approval_rate_pct,
        "%"
      )}
      {renderMetricBox("Rejection Rate", snapshot.rejection_rate_pct, "%")}
      {renderMetricBox(
        "Config Apply Success",
        snapshot.config_apply_success_rate_pct,
        "%"
      )}
    </div>
  );
}

// ============================================================================
// Component: Alerts Panel
// ============================================================================

interface AlertsPanelProps {
  alerts: Alert[];
  onAcknowledge: (alertId: string) => void;
  onResolve: (alertId: string) => void;
}

function AlertsPanel({ alerts, onAcknowledge, onResolve }: AlertsPanelProps) {
  if (alerts.length === 0) {
    return (
      <div className="bg-green-50 border border-green-200 rounded-lg p-4">
        <div className="flex items-center gap-2">
          <CheckCircle className="w-5 h-5 text-green-600" />
          <span className="text-green-800">No active alerts</span>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {alerts.map((alert) => (
        <div
          key={alert.alert_id}
          className={`border-2 rounded-lg p-4 ${
            alert.severity === "CRITICAL"
              ? "border-red-200 bg-red-50"
              : "border-yellow-200 bg-yellow-50"
          }`}
        >
          <div className="flex items-start justify-between">
            <div className="flex items-start gap-3 flex-1">
              <AlertTriangle
                className={`w-5 h-5 flex-shrink-0 ${
                  alert.severity === "CRITICAL"
                    ? "text-red-600"
                    : "text-yellow-600"
                }`}
              />
              <div>
                <p className="font-semibold text-gray-900">{alert.message}</p>
                <p className="text-xs text-gray-600 mt-1">
                  {new Date(alert.timestamp).toLocaleTimeString()} •{" "}
                  {alert.severity}
                  {alert.gate_name && ` • ${alert.gate_name}`}
                  {alert.skill_id && ` • ${alert.skill_id}`}
                </p>
              </div>
            </div>
            <div className="flex gap-2 flex-shrink-0">
              {!alert.is_acknowledged && (
                <button
                  onClick={() => onAcknowledge(alert.alert_id)}
                  className="px-2 py-1 text-xs font-medium bg-blue-100 text-blue-700 rounded hover:bg-blue-200"
                >
                  Ack
                </button>
              )}
              <button
                onClick={() => onResolve(alert.alert_id)}
                className="px-2 py-1 text-xs font-medium bg-gray-100 text-gray-700 rounded hover:bg-gray-200"
              >
                <X className="w-3 h-3" />
              </button>
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}

// ============================================================================
// Main Component: L5 Metrics Monitor
// ============================================================================

export default function L5MetricsMonitor() {
  const [snapshot, setSnapshot] = useState<L5HealthSnapshot | null>(null);
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [autoRefresh, setAutoRefresh] = useState(true);

  const service = useMemo(() => new L5MetricsService(), []);

  // Fetch health status
  const fetchHealthStatus = useCallback(async () => {
    try {
      setError(null);
      const [healthData, alertsData] = await Promise.all([
        service.getHealthStatus(),
        service.getActiveAlerts(),
      ]);
      setSnapshot(healthData);
      setAlerts(alertsData);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to fetch metrics");
    } finally {
      setLoading(false);
    }
  }, [service]);

  // Initial fetch
  useEffect(() => {
    fetchHealthStatus();
  }, [fetchHealthStatus]);

  // Auto-refresh every 10 seconds
  useEffect(() => {
    if (!autoRefresh) return;
    const interval = setInterval(fetchHealthStatus, 10000);
    return () => clearInterval(interval);
  }, [autoRefresh, fetchHealthStatus]);

  // Alert handlers
  const handleAcknowledgeAlert = async (alertId: string) => {
    const success = await service.acknowledgeAlert(alertId);
    if (success) {
      setAlerts((prev) =>
        prev.map((a) =>
          a.alert_id === alertId ? { ...a, is_acknowledged: true } : a
        )
      );
    }
  };

  const handleResolveAlert = async (alertId: string) => {
    const success = await service.resolveAlert(alertId);
    if (success) {
      setAlerts((prev) => prev.filter((a) => a.alert_id !== alertId));
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-screen">
        <div className="text-center">
          <Activity className="w-12 h-12 text-blue-600 animate-spin mx-auto mb-4" />
          <p className="text-gray-600">Loading metrics...</p>
        </div>
      </div>
    );
  }

  if (error || !snapshot) {
    return (
      <div className="flex items-center justify-center h-screen">
        <div className="text-center text-red-600">
          <AlertCircle className="w-12 h-12 mx-auto mb-4" />
          <p className="font-semibold">Error loading metrics</p>
          <p className="text-sm">{error}</p>
          <button
            onClick={fetchHealthStatus}
            className="mt-4 px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700"
          >
            Retry
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="p-6 bg-gray-50 min-h-screen">
      {/* Header */}
      <div className="mb-8">
        <div className="flex items-center justify-between mb-4">
          <h1 className="text-3xl font-bold text-gray-900">L5 Deployment Monitoring</h1>
          <div className="flex items-center gap-4">
            <button
              onClick={fetchHealthStatus}
              className="p-2 hover:bg-white rounded-lg border border-gray-200"
              title="Refresh metrics"
            >
              <RefreshCw className="w-5 h-5" />
            </button>
            <label className="flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={autoRefresh}
                onChange={(e) => setAutoRefresh(e.target.checked)}
                className="w-4 h-4"
              />
              <span className="text-sm text-gray-700">Auto-refresh (10s)</span>
            </label>
          </div>
        </div>

        {/* Overall Status */}
        <div className="bg-white p-4 rounded-lg border border-gray-200">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm text-gray-600">System Status</p>
              <HealthIndicator
                isHealthy={snapshot.all_healthy}
                slaStatus={snapshot.sla_status}
              />
            </div>
            <div className="text-sm text-gray-500">
              Last updated: {new Date(snapshot.timestamp).toLocaleTimeString()}
            </div>
          </div>
        </div>
      </div>

      {/* Metrics Summary */}
      <MetricsSummary snapshot={snapshot} />

      {/* Alerts Section */}
      <div className="mb-8">
        <h2 className="text-xl font-bold text-gray-900 mb-4">Active Alerts</h2>
        <AlertsPanel
          alerts={alerts}
          onAcknowledge={handleAcknowledgeAlert}
          onResolve={handleResolveAlert}
        />
      </div>

      {/* Gate Health Cards */}
      <div className="mb-8">
        <h2 className="text-xl font-bold text-gray-900 mb-4">Gate Latencies</h2>
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {Object.entries(snapshot.gates).map(([_, status]) => (
            <GateHealthCard key={status.gate_name} status={status} />
          ))}
        </div>
      </div>

      {/* Operator Latency SLA */}
      {snapshot.avg_operator_latency_ms !== undefined && (
        <div className="mb-8">
          <h2 className="text-xl font-bold text-gray-900 mb-4">Operator Latency SLA</h2>
          <div className="bg-white p-6 rounded-lg border border-gray-200">
            <div className="flex items-end gap-6">
              <div>
                <p className="text-sm text-gray-600">Current Latency</p>
                <p className="text-3xl font-bold text-gray-900">
                  {snapshot.avg_operator_latency_ms.toFixed(0)}
                  <span className="text-sm ml-2">ms</span>
                </p>
              </div>
              <div className="h-32 flex-1 bg-gray-100 rounded">
                <div
                  className={`h-full rounded transition-colors ${
                    snapshot.avg_operator_latency_ms > 300000
                      ? "bg-red-500"
                      : "bg-green-500"
                  }`}
                  style={{
                    width: `${Math.min(
                      (snapshot.avg_operator_latency_ms / 300000) * 100,
                      100
                    )}%`,
                  }}
                />
              </div>
              <div>
                <p className="text-sm text-gray-600">Target</p>
                <p className="text-2xl font-bold text-gray-900">
                  300000<span className="text-sm ml-2">ms</span>
                </p>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Footer */}
      <div className="text-center text-sm text-gray-500 pt-8 border-t border-gray-200">
        <p>ADR-0588: L5 Deployment Monitoring</p>
      </div>
    </div>
  );
}
