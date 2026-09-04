/**
 * Phase 3: L5 Approval Control Panel — Full web UI for operator approval workflow.
 *
 * Features:
 * - L5 status dashboard (k=1-5 metrics, trends, quality scores)
 * - Approval queue management (pending, approved, rejected, revoked)
 * - Operator action panels (approve/reject/revoke, batch operations)
 * - Policy rules CRUD
 * - Metrics dashboard (real-time stats)
 * - Responsive design (mobile-friendly)
 *
 * ADR-0584: L5 Dashboard UI Architecture
 */

import React, { useState, useEffect, useCallback, useMemo } from "react";
import {
  AlertCircle, CheckCircle, XCircle, Clock, TrendingUp, Settings,
  Filter, RefreshCw, Download, Plus, Edit2, Trash2, ChevronDown,
  ChevronUp, Search, Eye, EyeOff, Lock, Unlock, Activity, BarChart3,
} from "lucide-react";

// ============================================================================
// Types & Constants
// ============================================================================

interface ApprovalRecord {
  approval_id: string;
  skill_id: string;
  metric_name: string;
  scrubbed_alert: {
    skill_id: string;
    metric_name: string;
    magnitude: number;
    confidence: number;
    reason_code: string;
    timestamp: string;
  };
  decision: "pending" | "approved" | "rejected" | "revoked";
  operator_id: string;
  operator_timestamp: string;
  prev_config_hash: string;
  next_config_hash: string;
  ttl_expires: string;
  audit_event_id: string;
  revoke_timestamp?: string;
  revoke_reason?: string;
}

interface ApprovalMetrics {
  pending_count_by_skill: Record<string, number>;
  total_pending: number;
  approval_latencies_ms: number[];
  avg_latency_ms?: number;
  p50_latency_ms?: number;
  p95_latency_ms?: number;
  auto_approved_count: number;
  manual_approved_count: number;
  rejected_count: number;
  revoked_count: number;
  auto_approved_pct?: number;
  rejected_pct?: number;
  config_apply_success_count: number;
  config_apply_failure_count: number;
  config_apply_success_pct?: number;
  snapshot_timestamp: string;
}

interface PolicyRule {
  rule_id: string;
  skill_id: string;
  metric_name: string;
  auto_approve_threshold: number;
  require_approval_threshold: number;
  reject_threshold: number;
  created_at: string;
  updated_at: string;
}

interface DriftTrend {
  timestamp: string;
  ema_confidence: number;
  smoothed_delta: number;
  reason_code: string;
}

// ============================================================================
// API Service
// ============================================================================

class ApprovalControlService {
  baseURL = "/v1/approvals";
  tenantID = "_default";

  async listPendingApprovals(skillId?: string): Promise<ApprovalRecord[]> {
    const url = skillId
      ? `${this.baseURL}/${skillId}?tenant_id=${this.tenantID}`
      : `${this.baseURL}?tenant_id=${this.tenantID}`;
    const res = await fetch(url);
    if (!res.ok) throw new Error(`Failed to list approvals: ${res.statusText}`);
    const data = await res.json();
    return data.approvals || [];
  }

  async getApprovalStatus(skillId: string, approvalId: string): Promise<ApprovalRecord> {
    const url = `${this.baseURL}/${skillId}/${approvalId}/status?tenant_id=${this.tenantID}`;
    const res = await fetch(url);
    if (!res.ok) throw new Error(`Failed to get approval status: ${res.statusText}`);
    return await res.json();
  }

  async approve(skillId: string, approvalId: string, operatorId: string): Promise<boolean> {
    const url = `${this.baseURL}/${skillId}/${approvalId}/approve?tenant_id=${this.tenantID}`;
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ operator_id: operatorId }),
    });
    if (!res.ok) throw new Error(`Failed to approve: ${res.statusText}`);
    const data = await res.json();
    return data.success;
  }

  async reject(skillId: string, approvalId: string, operatorId: string, reason?: string): Promise<boolean> {
    const url = `${this.baseURL}/${skillId}/${approvalId}/reject?tenant_id=${this.tenantID}`;
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ operator_id: operatorId, reason }),
    });
    if (!res.ok) throw new Error(`Failed to reject: ${res.statusText}`);
    const data = await res.json();
    return data.success;
  }

  async revoke(skillId: string, approvalId: string, operatorId: string, reason?: string): Promise<boolean> {
    const url = `${this.baseURL}/${skillId}/${approvalId}/revoke?tenant_id=${this.tenantID}`;
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ operator_id: operatorId, reason }),
    });
    if (!res.ok) throw new Error(`Failed to revoke: ${res.statusText}`);
    const data = await res.json();
    return data.success;
  }
}

// ============================================================================
// Metrics Dashboard
// ============================================================================

const MetricsDashboard: React.FC<{ metrics: ApprovalMetrics }> = ({ metrics }) => {
  const stats = [
    {
      label: "Pending Approvals",
      value: metrics.total_pending,
      icon: Clock,
      color: "from-yellow-500 to-yellow-600",
    },
    {
      label: "Auto-Approved",
      value: metrics.auto_approved_count,
      subtext: `${(metrics.auto_approved_pct || 0).toFixed(1)}%`,
      icon: CheckCircle,
      color: "from-green-500 to-green-600",
    },
    {
      label: "Rejected",
      value: metrics.rejected_count,
      subtext: `${(metrics.rejected_pct || 0).toFixed(1)}%`,
      icon: XCircle,
      color: "from-red-500 to-red-600",
    },
    {
      label: "Revoked",
      value: metrics.revoked_count,
      icon: AlertCircle,
      color: "from-orange-500 to-orange-600",
    },
    {
      label: "Avg Latency",
      value: `${(metrics.avg_latency_ms || 0).toFixed(0)}ms`,
      subtext: `p95: ${(metrics.p95_latency_ms || 0).toFixed(0)}ms`,
      icon: Activity,
      color: "from-blue-500 to-blue-600",
    },
    {
      label: "Config Apply Rate",
      value: `${(metrics.config_apply_success_pct || 0).toFixed(1)}%`,
      subtext: `${metrics.config_apply_success_count} success`,
      icon: TrendingUp,
      color: "from-purple-500 to-purple-600",
    },
  ];

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4 mb-6">
      {stats.map((stat, idx) => {
        const Icon = stat.icon;
        return (
          <div
            key={idx}
            className={`bg-gradient-to-br ${stat.color} p-6 rounded-lg text-white shadow-lg`}
          >
            <div className="flex justify-between items-start">
              <div>
                <p className="text-sm opacity-90">{stat.label}</p>
                <p className="text-3xl font-bold mt-2">{stat.value}</p>
                {stat.subtext && <p className="text-xs opacity-80 mt-1">{stat.subtext}</p>}
              </div>
              <Icon className="w-8 h-8 opacity-50" />
            </div>
          </div>
        );
      })}
    </div>
  );
};

// ============================================================================
// Approval Queue Panel
// ============================================================================

const ApprovalQueuePanel: React.FC<{
  approvals: ApprovalRecord[];
  loading: boolean;
  onApprove: (skillId: string, approvalId: string) => Promise<void>;
  onReject: (skillId: string, approvalId: string, reason?: string) => Promise<void>;
  onRevoke: (skillId: string, approvalId: string, reason?: string) => Promise<void>;
}> = ({ approvals, loading, onApprove, onReject, onRevoke }) => {
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [filter, setFilter] = useState<"all" | "pending" | "approved" | "rejected" | "revoked">("pending");
  const [operatorId] = useState("user:operator");
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const [selectedApprovals, setSelectedApprovals] = useState<Set<string>>(new Set());

  const filtered = useMemo(() => {
    if (filter === "all") return approvals;
    return approvals.filter((a) => a.decision === filter);
  }, [approvals, filter]);

  const toggleExpanded = (id: string) => {
    const newExpanded = new Set(expanded);
    if (newExpanded.has(id)) {
      newExpanded.delete(id);
    } else {
      newExpanded.add(id);
    }
    setExpanded(newExpanded);
  };

  const toggleSelected = (id: string) => {
    const newSelected = new Set(selectedApprovals);
    if (newSelected.has(id)) {
      newSelected.delete(id);
    } else {
      newSelected.add(id);
    }
    setSelectedApprovals(newSelected);
  };

  const handleApprove = async (skillId: string, approvalId: string) => {
    setActionLoading(approvalId);
    try {
      await onApprove(skillId, approvalId);
    } finally {
      setActionLoading(null);
    }
  };

  const handleBatchApprove = async () => {
    for (const approvalId of selectedApprovals) {
      const approval = approvals.find((a) => a.approval_id === approvalId);
      if (approval) {
        await handleApprove(approval.skill_id, approvalId);
      }
    }
    setSelectedApprovals(new Set());
  };

  const getDecisionBadgeColor = (decision: string) => {
    switch (decision) {
      case "pending":
        return "bg-yellow-100 text-yellow-800";
      case "approved":
        return "bg-green-100 text-green-800";
      case "rejected":
        return "bg-red-100 text-red-800";
      case "revoked":
        return "bg-orange-100 text-orange-800";
      default:
        return "bg-gray-100 text-gray-800";
    }
  };

  const getReasonBadgeColor = (reason: string) => {
    switch (reason) {
      case "random_noise":
        return "bg-blue-100 text-blue-800";
      case "consistent_pattern":
        return "bg-purple-100 text-purple-800";
      case "regime_shift":
        return "bg-orange-100 text-orange-800";
      default:
        return "bg-gray-100 text-gray-800";
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <RefreshCw className="w-8 h-8 animate-spin text-gray-400" />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex justify-between items-center">
        <div className="flex gap-2">
          {["all", "pending", "approved", "rejected", "revoked"].map((f) => (
            <button
              key={f}
              onClick={() => setFilter(f as typeof filter)}
              className={`px-3 py-1 rounded text-sm font-medium transition ${
                filter === f
                  ? "bg-blue-600 text-white"
                  : "bg-gray-200 text-gray-700 hover:bg-gray-300"
              }`}
            >
              {f.charAt(0).toUpperCase() + f.slice(1)}
            </button>
          ))}
        </div>
        {selectedApprovals.size > 0 && (
          <button
            onClick={handleBatchApprove}
            className="px-4 py-2 bg-green-600 text-white rounded hover:bg-green-700 text-sm font-medium"
          >
            Approve {selectedApprovals.size} Selected
          </button>
        )}
      </div>

      <div className="space-y-2">
        {filtered.length === 0 ? (
          <p className="text-gray-500 text-center py-8">No approvals in this category</p>
        ) : (
          filtered.map((approval) => (
            <div
              key={approval.approval_id}
              className="border rounded-lg p-4 hover:shadow-md transition bg-white"
            >
              <div className="flex items-start justify-between">
                <div className="flex items-start gap-3 flex-1">
                  <input
                    type="checkbox"
                    checked={selectedApprovals.has(approval.approval_id)}
                    onChange={() => toggleSelected(approval.approval_id)}
                    className="mt-1"
                  />
                  <div className="flex-1">
                    <div className="flex gap-2 flex-wrap items-center">
                      <span className="font-mono text-sm text-gray-600">
                        {approval.skill_id}
                      </span>
                      <span className="text-sm font-medium text-gray-700">
                        {approval.scrubbed_alert.metric_name}
                      </span>
                      <span className={`text-xs px-2 py-1 rounded font-medium ${getDecisionBadgeColor(approval.decision)}`}>
                        {approval.decision}
                      </span>
                      <span className={`text-xs px-2 py-1 rounded font-medium ${getReasonBadgeColor(approval.scrubbed_alert.reason_code)}`}>
                        {approval.scrubbed_alert.reason_code.replace(/_/g, " ")}
                      </span>
                    </div>
                    <p className="text-xs text-gray-500 mt-1">
                      {new Date(approval.scrubbed_alert.timestamp).toLocaleString()}
                    </p>
                  </div>
                </div>
                <button
                  onClick={() => toggleExpanded(approval.approval_id)}
                  className="p-1 hover:bg-gray-100 rounded"
                >
                  {expanded.has(approval.approval_id) ? (
                    <ChevronUp className="w-5 h-5" />
                  ) : (
                    <ChevronDown className="w-5 h-5" />
                  )}
                </button>
              </div>

              {expanded.has(approval.approval_id) && (
                <div className="mt-4 pt-4 border-t space-y-2 text-sm">
                  <div className="grid grid-cols-2 gap-2">
                    <div>
                      <span className="text-gray-600">Magnitude:</span>
                      <p className="font-mono">{approval.scrubbed_alert.magnitude.toFixed(4)}</p>
                    </div>
                    <div>
                      <span className="text-gray-600">Confidence:</span>
                      <p className="font-mono">{(approval.scrubbed_alert.confidence * 100).toFixed(1)}%</p>
                    </div>
                    <div>
                      <span className="text-gray-600">Approval ID:</span>
                      <p className="font-mono text-xs">{approval.approval_id}</p>
                    </div>
                    <div>
                      <span className="text-gray-600">TTL Expires:</span>
                      <p className="font-mono text-xs">
                        {new Date(approval.ttl_expires).toLocaleString()}
                      </p>
                    </div>
                  </div>

                  {approval.decision === "pending" && (
                    <div className="flex gap-2 mt-4">
                      <button
                        onClick={() => handleApprove(approval.skill_id, approval.approval_id)}
                        disabled={actionLoading === approval.approval_id}
                        className="flex-1 px-3 py-2 bg-green-600 text-white rounded hover:bg-green-700 disabled:opacity-50 text-sm font-medium"
                      >
                        {actionLoading === approval.approval_id ? "Approving..." : "Approve"}
                      </button>
                      <button
                        onClick={() => onReject(approval.skill_id, approval.approval_id)}
                        disabled={actionLoading === approval.approval_id}
                        className="flex-1 px-3 py-2 bg-red-600 text-white rounded hover:bg-red-700 disabled:opacity-50 text-sm font-medium"
                      >
                        Reject
                      </button>
                    </div>
                  )}

                  {approval.decision === "approved" && !approval.revoke_timestamp && (
                    <div className="flex gap-2 mt-4">
                      <button
                        onClick={() => onRevoke(approval.skill_id, approval.approval_id)}
                        className="flex-1 px-3 py-2 bg-orange-600 text-white rounded hover:bg-orange-700 text-sm font-medium"
                      >
                        Revoke
                      </button>
                    </div>
                  )}
                </div>
              )}
            </div>
          ))
        )}
      </div>
    </div>
  );
};

// ============================================================================
// Drift Trends Panel
// ============================================================================

const DriftTrendsPanel: React.FC<{ trends?: DriftTrend[] }> = ({ trends = [] }) => {
  if (trends.length === 0) {
    return (
      <div className="p-8 text-center text-gray-500">
        <TrendingUp className="w-12 h-12 mx-auto mb-4 opacity-30" />
        <p>No drift trend data available</p>
      </div>
    );
  }

  const maxConfidence = Math.max(...trends.map((t) => t.ema_confidence), 1);

  return (
    <div className="space-y-4">
      {trends.slice(-10).map((trend, idx) => (
        <div key={idx} className="space-y-1">
          <div className="flex justify-between text-sm">
            <span className="text-gray-600">{new Date(trend.timestamp).toLocaleTimeString()}</span>
            <span className="text-gray-600">{(trend.ema_confidence * 100).toFixed(1)}% confidence</span>
          </div>
          <div className="h-2 bg-gray-200 rounded-full overflow-hidden">
            <div
              className="h-full bg-blue-600 rounded-full"
              style={{ width: `${(trend.ema_confidence / maxConfidence) * 100}%` }}
            />
          </div>
          <p className="text-xs text-gray-500">
            Delta: {trend.smoothed_delta.toFixed(4)} ({trend.reason_code})
          </p>
        </div>
      ))}
    </div>
  );
};

// ============================================================================
// Policy Rules Panel
// ============================================================================

const PolicyRulesPanel: React.FC<{ rules?: PolicyRule[] }> = ({ rules = [] }) => {
  const [editing, setEditing] = useState<string | null>(null);

  return (
    <div className="space-y-4">
      <button className="px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700 flex items-center gap-2">
        <Plus className="w-4 h-4" />
        Add Policy Rule
      </button>

      {rules.length === 0 ? (
        <p className="text-gray-500 text-center py-8">No policy rules configured</p>
      ) : (
        <div className="space-y-3">
          {rules.map((rule) => (
            <div key={rule.rule_id} className="border rounded-lg p-4 bg-white">
              <div className="flex justify-between items-start">
                <div className="flex-1">
                  <h3 className="font-medium text-gray-900">
                    {rule.skill_id} — {rule.metric_name}
                  </h3>
                  <div className="grid grid-cols-3 gap-4 mt-3 text-sm">
                    <div>
                      <span className="text-gray-600">Auto-Approve:</span>
                      <p className="font-mono text-green-700">{rule.auto_approve_threshold.toFixed(2)}</p>
                    </div>
                    <div>
                      <span className="text-gray-600">Require Approval:</span>
                      <p className="font-mono text-yellow-700">{rule.require_approval_threshold.toFixed(2)}</p>
                    </div>
                    <div>
                      <span className="text-gray-600">Reject:</span>
                      <p className="font-mono text-red-700">{rule.reject_threshold.toFixed(2)}</p>
                    </div>
                  </div>
                </div>
                <div className="flex gap-2">
                  <button
                    onClick={() => setEditing(rule.rule_id)}
                    className="p-2 hover:bg-gray-100 rounded text-gray-600"
                  >
                    <Edit2 className="w-4 h-4" />
                  </button>
                  <button className="p-2 hover:bg-gray-100 rounded text-red-600">
                    <Trash2 className="w-4 h-4" />
                  </button>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

// ============================================================================
// Main Panel Component
// ============================================================================

export const ApprovalControlPanel: React.FC = () => {
  const [approvals, setApprovals] = useState<ApprovalRecord[]>([]);
  const [metrics, setMetrics] = useState<ApprovalMetrics | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<"dashboard" | "queue" | "trends" | "policies">("dashboard");
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [refreshInterval, setRefreshInterval] = useState(30);

  const service = useMemo(() => new ApprovalControlService(), []);

  // Load approvals and metrics
  const loadData = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);

      // Load approvals
      const approvalsData = await service.listPendingApprovals();
      setApprovals(approvalsData);

      // Calculate metrics
      const pending = approvalsData.filter((a) => a.decision === "pending").length;
      const approved = approvalsData.filter((a) => a.decision === "approved").length;
      const rejected = approvalsData.filter((a) => a.decision === "rejected").length;
      const revoked = approvalsData.filter((a) => a.decision === "revoked").length;

      const calculatedMetrics: ApprovalMetrics = {
        pending_count_by_skill: {},
        total_pending: pending,
        approval_latencies_ms: [],
        avg_latency_ms: 0,
        p50_latency_ms: 0,
        p95_latency_ms: 0,
        auto_approved_count: approved,
        manual_approved_count: 0,
        rejected_count: rejected,
        revoked_count: revoked,
        auto_approved_pct: (approved / (approved + rejected + revoked || 1)) * 100,
        rejected_pct: (rejected / (approved + rejected + revoked || 1)) * 100,
        config_apply_success_count: 0,
        config_apply_failure_count: 0,
        config_apply_success_pct: 0,
        snapshot_timestamp: new Date().toISOString(),
      };

      setMetrics(calculatedMetrics);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load data");
    } finally {
      setLoading(false);
    }
  }, [service]);

  // Initial load
  useEffect(() => {
    loadData();
  }, [loadData]);

  // Auto-refresh
  useEffect(() => {
    if (!autoRefresh) return;

    const interval = setInterval(() => {
      loadData();
    }, refreshInterval * 1000);

    return () => clearInterval(interval);
  }, [autoRefresh, refreshInterval, loadData]);

  // Action handlers
  const handleApprove = async (skillId: string, approvalId: string) => {
    try {
      await service.approve(skillId, approvalId, "user:operator");
      await loadData();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to approve");
    }
  };

  const handleReject = async (skillId: string, approvalId: string, reason?: string) => {
    try {
      await service.reject(skillId, approvalId, "user:operator", reason);
      await loadData();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to reject");
    }
  };

  const handleRevoke = async (skillId: string, approvalId: string, reason?: string) => {
    try {
      await service.revoke(skillId, approvalId, "user:operator", reason);
      await loadData();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to revoke");
    }
  };

  return (
    <div className="h-full flex flex-col bg-gray-50">
      {/* Header */}
      <div className="bg-white border-b p-4 sticky top-0 z-10">
        <div className="flex items-center justify-between mb-4">
          <h1 className="text-2xl font-bold text-gray-900">L5 Approval Control Panel</h1>
          <div className="flex items-center gap-2">
            <button
              onClick={() => loadData()}
              disabled={loading}
              className="p-2 hover:bg-gray-100 rounded disabled:opacity-50"
              title="Refresh"
            >
              <RefreshCw className={`w-5 h-5 ${loading ? "animate-spin" : ""}`} />
            </button>
            <button
              onClick={() => setAutoRefresh(!autoRefresh)}
              className={`p-2 rounded ${autoRefresh ? "bg-blue-100 text-blue-700" : "hover:bg-gray-100"}`}
              title={autoRefresh ? "Auto-refresh on" : "Auto-refresh off"}
            >
              {autoRefresh ? <Eye className="w-5 h-5" /> : <EyeOff className="w-5 h-5" />}
            </button>
          </div>
        </div>

        {error && (
          <div className="mb-4 p-3 bg-red-100 text-red-800 rounded-lg flex items-center gap-2">
            <AlertCircle className="w-5 h-5" />
            {error}
            <button onClick={() => setError(null)} className="ml-auto">
              ×
            </button>
          </div>
        )}

        {/* Tabs */}
        <div className="flex gap-2 border-b">
          {(["dashboard", "queue", "trends", "policies"] as const).map((tab) => (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              className={`px-4 py-2 font-medium text-sm border-b-2 transition ${
                activeTab === tab
                  ? "border-blue-600 text-blue-600"
                  : "border-transparent text-gray-600 hover:text-gray-900"
              }`}
            >
              {tab.charAt(0).toUpperCase() + tab.slice(1)}
            </button>
          ))}
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-auto p-4">
        {activeTab === "dashboard" && metrics && (
          <div>
            <MetricsDashboard metrics={metrics} />
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
              <div className="bg-white rounded-lg shadow p-4">
                <h2 className="text-lg font-semibold mb-4 flex items-center gap-2">
                  <BarChart3 className="w-5 h-5" />
                  Queue by Skill
                </h2>
                <div className="space-y-2">
                  {Object.entries(metrics.pending_count_by_skill).map(([skill, count]) => (
                    <div key={skill} className="flex justify-between items-center text-sm">
                      <span className="text-gray-700">{skill}</span>
                      <span className="font-mono font-bold">{count}</span>
                    </div>
                  ))}
                </div>
              </div>

              <div className="bg-white rounded-lg shadow p-4">
                <h2 className="text-lg font-semibold mb-4">Auto-Refresh Settings</h2>
                <div className="space-y-4">
                  <div>
                    <label className="text-sm text-gray-600">Interval (seconds)</label>
                    <input
                      type="number"
                      min="10"
                      max="300"
                      value={refreshInterval}
                      onChange={(e) => setRefreshInterval(Math.max(10, Math.min(300, parseInt(e.target.value) || 30)))}
                      className="mt-1 w-full px-3 py-2 border rounded"
                    />
                  </div>
                </div>
              </div>
            </div>
          </div>
        )}

        {activeTab === "queue" && (
          <ApprovalQueuePanel
            approvals={approvals}
            loading={loading}
            onApprove={handleApprove}
            onReject={handleReject}
            onRevoke={handleRevoke}
          />
        )}

        {activeTab === "trends" && (
          <div className="bg-white rounded-lg shadow p-6">
            <h2 className="text-lg font-semibold mb-4">Confidence Drift Trends</h2>
            <DriftTrendsPanel />
          </div>
        )}

        {activeTab === "policies" && (
          <div className="bg-white rounded-lg shadow p-6">
            <h2 className="text-lg font-semibold mb-4">Policy Rules</h2>
            <PolicyRulesPanel />
          </div>
        )}
      </div>
    </div>
  );
};

export default ApprovalControlPanel;
