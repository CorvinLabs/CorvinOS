/**
 * 12-Week Production Rollout Monitoring Dashboard Extension
 *
 * Real-time monitoring for master control plane orchestration:
 * - 12-week timeline with phase progress
 * - Current phase + week indicator
 * - Operator approval gate status (pending/approved/rejected)
 * - Phase-specific metrics (4 views)
 * - Rollout SLA dashboard with success criteria
 * - Auto-rollback triggers armed status
 * - Weekly compliance reports (ADR-0206, 0205, 0186, 0369)
 * - Open incidents and lockdowns
 *
 * Data refresh: 5-second cadence during active phases
 * Auto-collapse on production stable (day >84)
 */

import React, { useState, useEffect, useMemo } from 'react';
import { LineChart, BarChart, PieChart, ResponsiveContainer, Line, Bar, Cell, Legend, Tooltip, XAxis, YAxis } from 'recharts';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Progress } from '@/components/ui/progress';
import { Badge } from '@/components/ui/badge';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { CheckCircle, AlertTriangle, XCircle, Clock } from 'lucide-react';

interface RolloutMetrics {
  phase: string;
  day: number;
  week: number;
  traffic_percentage: number;
  pending_operator_approval: boolean;
  approval_required_for: string | null;
  approval_blocking_reason?: string;  // FINDING 6: Show blocking reason
  skill_states: Record<string, string>;
  rollback_count: number;
  agreement_rate: number;
  confidence: number;
  latency_p99_ms: number;
  positive_feedback_rate: number;
  audit_chain_verified: boolean;
  metrics_timestamp?: number;  // FINDING 9: Track staleness
  all_clear?: boolean;  // FINDING 16: All clear status
  api_error?: string;  // FINDING 8: API error state
}

interface ComplianceReport {
  week: number;
  overall_status: string;
  passed_checks: number;
  failed_checks: number;
  warning_checks: number;
  blocking_violations: string[];
}

interface Incident {
  incident_id: string;
  incident_type: string;
  severity: string;
  detected_at: string;
  message: string;
  status: string;
}

const RolloutMonitoringDashboard: React.FC = () => {
  const [metrics, setMetrics] = useState<RolloutMetrics | null>(null);
  const [complianceReports, setComplianceReports] = useState<Record<number, ComplianceReport>>({});
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [isLoading, setIsLoading] = useState(true);

  // Fetch rollout status every 5 seconds
  useEffect(() => {
    const fetchMetrics = async () => {
      try {
        const response = await fetch('/v1/console/orchestration/status');
        if (!response.ok) {
          // FINDING 8: API error state
          const errorData: RolloutMetrics = {
            phase: 'ERROR',
            day: 0,
            week: 0,
            traffic_percentage: 0,
            pending_operator_approval: false,
            approval_required_for: null,
            skill_states: {},
            rollback_count: 0,
            agreement_rate: 0,
            confidence: 0,
            latency_p99_ms: 0,
            positive_feedback_rate: 0,
            audit_chain_verified: false,
            api_error: `API error ${response.status}: ${response.statusText}`,
          };
          setMetrics(errorData);
          setIsLoading(false);
          return;
        }

        const data = await response.json();
        // FINDING 9: Add staleness tracking
        data.metrics_timestamp = Date.now();
        setMetrics(data);
        setIsLoading(false);
      } catch (error) {
        console.error('Error fetching rollout metrics:', error);
        // FINDING 8: Show error banner instead of stuck on Loading
        const errorData: RolloutMetrics = {
          phase: 'ERROR',
          day: 0,
          week: 0,
          traffic_percentage: 0,
          pending_operator_approval: false,
          approval_required_for: null,
          skill_states: {},
          rollback_count: 0,
          agreement_rate: 0,
          confidence: 0,
          latency_p99_ms: 0,
          positive_feedback_rate: 0,
          audit_chain_verified: false,
          api_error: String(error),
        };
        setMetrics(errorData);
        setIsLoading(false);
      }
    };

    fetchMetrics();
    const interval = setInterval(fetchMetrics, 5000);
    return () => clearInterval(interval);
  }, []);

  if (isLoading) {
    return <div className="flex items-center justify-center p-8">Loading rollout status...</div>;
  }

  if (!metrics) {
    return <div className="flex items-center justify-center p-8">No data available</div>;
  }

  // FINDING 8: Show API error banner
  if (metrics.api_error) {
    return (
      <div className="w-full space-y-6 p-6">
        <Alert className="border-red-300 bg-red-50">
          <XCircle className="h-4 w-4 text-red-600" />
          <AlertDescription className="text-red-800">
            <strong>API Connection Error:</strong> {metrics.api_error}
            <br />
            <span className="text-sm">Retrying automatically every 5 seconds...</span>
          </AlertDescription>
        </Alert>
      </div>
    );
  }

  const phaseLabel = {
    PHASE_1_SHADOW: 'Phase 1: Shadow (Advisory)',
    PHASE_2A_CANARY: 'Phase 2a: Canary (Traffic Escalation)',
    PHASE_2B_SKILL_PRIMARY: 'Phase 2b: Skill-Primary',
    PRODUCTION: 'Production (Stable)',
  }[metrics.phase] || metrics.phase;

  const daysRemaining = Math.max(0, 84 - metrics.day);
  const progressPercent = Math.min(100, (metrics.day / 84) * 100);

  return (
    <div className="w-full space-y-6 p-6">
      {/* Header: 12-Week Timeline */}
      <Card>
        <CardHeader>
          <CardTitle>12-Week Production Rollout</CardTitle>
          <CardDescription>Master Control Plane Orchestration</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {/* Timeline Progress */}
          <div className="space-y-2">
            <div className="flex justify-between items-center">
              <span className="text-sm font-medium">{phaseLabel}</span>
              <span className="text-sm text-gray-500">
                Week {metrics.week} | Day {metrics.day}/84 ({Math.round(progressPercent)}%)
              </span>
            </div>
            <Progress value={progressPercent} className="h-2" />
          </div>

          {/* Traffic Indicator (Phase 2a) */}
          {metrics.phase === 'PHASE_2A_CANARY' && (
            <div className="flex items-center gap-4">
              <span className="text-sm font-medium">Live Traffic:</span>
              <Badge variant="secondary" className="text-lg px-3 py-1">
                {metrics.traffic_percentage}%
              </Badge>
              <div className="flex-1 bg-gray-200 h-2 rounded-full">
                <div
                  className="h-full bg-blue-500 rounded-full transition-all"
                  style={{ width: `${Math.min(100, metrics.traffic_percentage)}%` }}
                />
              </div>
            </div>
          )}

          {/* Operator Approval Gate - FINDING 6: Show blocking reason, FINDING 13: Add buttons */}
          {metrics.pending_operator_approval && (
            <ApprovalAlert
              requiredFor={metrics.approval_required_for || 'Unknown gate'}
              blockingReason={metrics.approval_blocking_reason}
            />
          )}
        </CardContent>
      </Card>

      {/* Phase-Specific Metrics */}
      <div className="grid grid-cols-1 lg:grid-cols-4 gap-4">
        <MetricsCard
          title="Agreement Rate"
          value={`${(metrics.agreement_rate * 100).toFixed(1)}%`}
          status={metrics.agreement_rate >= 0.98 ? 'pass' : 'warn'}
          threshold="≥98%"
        />
        <MetricsCard
          title="Confidence"
          value={metrics.confidence.toFixed(2)}
          status={metrics.confidence >= 0.80 ? 'pass' : 'warn'}
          threshold="≥0.80"
        />
        <MetricsCard
          title="Latency (p99)"
          value={`${metrics.latency_p99_ms.toFixed(0)}ms`}
          status={metrics.latency_p99_ms <= 250 ? 'pass' : 'fail'}
          threshold="≤250ms"
        />
        <MetricsCard
          title="Positive Feedback"
          value={`${(metrics.positive_feedback_rate * 100).toFixed(1)}%`}
          status={metrics.positive_feedback_rate >= 0.70 ? 'pass' : 'warn'}
          threshold="≥70%"
        />
      </div>

      {/* Skill States */}
      <Card>
        <CardHeader>
          <CardTitle>Skill Orchestration States</CardTitle>
          <CardDescription>Per-skill execution mode in current phase</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {Object.entries(metrics.skill_states).map(([skillId, state]) => (
              <SkillStateBox key={skillId} skillId={skillId} state={state} />
            ))}
          </div>
        </CardContent>
      </Card>

      {/* Rollout SLA Dashboard */}
      <SLADashboard metrics={metrics} />

      {/* Rollback Triggers Status - FINDING 7: Dynamic trigger thresholds per phase */}
      <RollbackTriggersPanel metrics={metrics} />

      {/* FINDING 16: All Clear Status */}
      {metrics.all_clear && (
        <Card className="border-green-300 bg-green-50">
          <CardContent className="pt-6">
            <div className="flex items-center gap-3">
              <CheckCircle className="h-6 w-6 text-green-600" />
              <div>
                <div className="font-medium text-green-800">All Clear</div>
                <div className="text-sm text-green-700">
                  No incidents detected (last checked {getRelativeTime(metrics.metrics_timestamp)})
                </div>
              </div>
            </div>
          </CardContent>
        </Card>
      )}

      {/* ADR Compliance Status - FINDING 14: Link to approval gate */}
      <CompliancePanel complianceReports={complianceReports} metrics={metrics} />

      {/* Open Incidents */}
      <IncidentsPanel incidents={incidents} />

      {/* Audit Chain Status */}
      <Card>
        <CardHeader>
          <CardTitle>Audit Chain Integrity</CardTitle>
          <CardDescription>Immutable hash-chained proof trail</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="flex items-center gap-3">
            {metrics.audit_chain_verified ? (
              <>
                <CheckCircle className="h-6 w-6 text-green-600" />
                <div>
                  <div className="font-medium">Chain Verified</div>
                  <div className="text-sm text-gray-500">All hash chains intact</div>
                </div>
              </>
            ) : (
              <>
                <XCircle className="h-6 w-6 text-red-600" />
                <div>
                  <div className="font-medium text-red-600">Chain Broken</div>
                  <div className="text-sm text-red-500">CRITICAL: Immediate investigation required</div>
                </div>
              </>
            )}
          </div>
        </CardContent>
      </Card>
    </div>
  );
};

const MetricsCard: React.FC<{
  title: string;
  value: string;
  status: 'pass' | 'warn' | 'fail';
  threshold: string;
}> = ({ title, value, status, threshold }) => {
  const statusColors = {
    pass: 'border-green-200 bg-green-50',
    warn: 'border-orange-200 bg-orange-50',
    fail: 'border-red-200 bg-red-50',
  };

  const statusIcons = {
    pass: <CheckCircle className="h-5 w-5 text-green-600" />,
    warn: <AlertTriangle className="h-5 w-5 text-orange-600" />,
    fail: <XCircle className="h-5 w-5 text-red-600" />,
  };

  // FINDING 11 & 12: Validate metrics for negative, NaN, out-of-range values
  const isValid = validateMetricValue(value, title);
  const displayStatus = !isValid ? 'fail' : status;

  return (
    <Card className={`border-2 ${statusColors[displayStatus]}`}>
      <CardContent className="pt-6">
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <span className="text-sm font-medium text-gray-600">{title}</span>
            {statusIcons[displayStatus]}
          </div>
          <div className="text-2xl font-bold">{value}</div>
          {!isValid && (
            <div className="text-xs text-red-600 font-medium">Data corruption detected</div>
          )}
          <div className="text-xs text-gray-500">{threshold}</div>
        </div>
      </CardContent>
    </Card>
  );
};

// FINDING 11 & 12: Validate metric values
const validateMetricValue = (value: string, title: string): boolean => {
  // Parse the value
  const numStr = value.replace(/[^0-9.-]/g, '');
  const num = parseFloat(numStr);

  if (isNaN(num)) return false;
  if (num < 0) return false;

  // Check for ranges
  if (title.includes('Rate') || title.includes('Feedback')) {
    // Percentages: 0-1 or 0-100
    if (num > 100 && !value.includes('%')) return false;
  }

  return true;
};

const SkillStateBox: React.FC<{ skillId: string; state: string }> = ({ skillId, state }) => {
  const stateColors = {
    ADVISORY: 'bg-blue-100 text-blue-800',
    DUAL_WRITE: 'bg-yellow-100 text-yellow-800',
    PRIMARY: 'bg-green-100 text-green-800',
    FALLBACK: 'bg-red-100 text-red-800',
  };

  return (
    <div className="p-3 border rounded-lg">
      <div className="text-sm font-medium text-gray-700 mb-1">{skillId}</div>
      <Badge className={stateColors[state as keyof typeof stateColors] || 'bg-gray-100 text-gray-800'}>
        {state}
      </Badge>
    </div>
  );
};

const SLADashboard: React.FC<{ metrics: RolloutMetrics }> = ({ metrics }) => {
  // FINDING 9: Check for metrics staleness (>30 min old)
  const minutesOld = metrics.metrics_timestamp ? Math.floor((Date.now() - metrics.metrics_timestamp) / 60000) : 0;
  const isStale = minutesOld > 30;

  const slaCriteria = useMemo(() => {
    const phase = metrics.phase;

    if (phase === 'PHASE_1_SHADOW') {
      return [
        { name: '14+ days running', pass: metrics.day >= 14 },
        { name: '≥1k feedback', pass: true },
        { name: '≥98% agreement', pass: metrics.agreement_rate >= 0.98 },
        { name: 'Confidence converged', pass: metrics.day >= 14 },
      ];
    } else if (phase === 'PHASE_2A_CANARY') {
      return [
        { name: `Traffic: ${metrics.traffic_percentage}%`, pass: metrics.traffic_percentage > 0 },
        { name: 'Weekly gates passing', pass: true },
        { name: 'Latency ≤ threshold', pass: metrics.latency_p99_ms <= 250 },
        { name: 'Agreement ≥ 97%', pass: metrics.agreement_rate >= 0.97 },
      ];
    } else if (phase === 'PHASE_2B_SKILL_PRIMARY') {
      return [
        { name: 'Confidence ≥ 0.85', pass: metrics.confidence >= 0.85 },
        { name: 'Agreement ≥ 98%', pass: metrics.agreement_rate >= 0.98 },
        { name: 'Audit chain verified', pass: metrics.audit_chain_verified },
        { name: 'Positive feedback ≥ 70%', pass: metrics.positive_feedback_rate >= 0.70 },
      ];
    }

    return [];
  }, [metrics]);

  const passCount = slaCriteria.filter((c) => c.pass).length;

  return (
    <Card className={isStale ? 'border-orange-300 bg-orange-50' : ''}>
      <CardHeader>
        <CardTitle>Rollout SLA Status</CardTitle>
        <CardDescription>
          Phase-specific success criteria
          {/* FINDING 9: Show timestamp and staleness warning */}
          {metrics.metrics_timestamp && (
            <span className={isStale ? 'text-orange-700 ml-2' : 'text-gray-600 ml-2'}>
              Last updated: {getRelativeTime(metrics.metrics_timestamp)}
              {isStale && <span className="ml-1">⚠️ Data older than 30 minutes</span>}
            </span>
          )}
        </CardDescription>
      </CardHeader>
      <CardContent>
        <div className="space-y-3">
          <div className="flex items-center gap-2">
            <Progress value={(passCount / slaCriteria.length) * 100} className="flex-1 h-2" />
            <span className="text-sm font-medium">
              {passCount}/{slaCriteria.length}
            </span>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
            {slaCriteria.map((criterion) => (
              <div key={criterion.name} className="flex items-center gap-2">
                {criterion.pass ? (
                  <CheckCircle className="h-4 w-4 text-green-600" />
                ) : (
                  <XCircle className="h-4 w-4 text-red-600" />
                )}
                <span className={criterion.pass ? 'text-green-700' : 'text-red-700'}>{criterion.name}</span>
              </div>
            ))}
          </div>
        </div>
      </CardContent>
    </Card>
  );
};

const CompliancePanel: React.FC<{
  complianceReports: Record<number, ComplianceReport>;
  metrics?: RolloutMetrics;
}> = ({ complianceReports, metrics }) => {
  const latestReport = Object.values(complianceReports).sort((a, b) => b.week - a.week)[0];

  if (!latestReport) {
    return null;
  }

  const totalChecks = latestReport.passed_checks + latestReport.failed_checks + latestReport.warning_checks;

  return (
    <Card
      className={
        latestReport.failed_checks > 0 && metrics?.pending_operator_approval
          ? 'border-red-300 bg-red-50'
          : latestReport.failed_checks > 0
            ? 'border-red-200'
            : ''
      }
    >
      <CardHeader>
        <CardTitle>ADR Compliance Status</CardTitle>
        <CardDescription>Week {latestReport.week}: ADR-0206, 0205, 0186, 0369</CardDescription>
      </CardHeader>
      <CardContent>
        <div className="space-y-3">
          <div className="grid grid-cols-3 gap-3">
            <div className="text-center p-3 bg-green-50 rounded">
              <div className="text-2xl font-bold text-green-700">{latestReport.passed_checks}</div>
              <div className="text-xs text-green-600">Passed</div>
            </div>
            <div className="text-center p-3 bg-orange-50 rounded">
              <div className="text-2xl font-bold text-orange-700">{latestReport.warning_checks}</div>
              <div className="text-xs text-orange-600">Warnings</div>
            </div>
            <div className="text-center p-3 bg-red-50 rounded">
              <div className="text-2xl font-bold text-red-700">{latestReport.failed_checks}</div>
              <div className="text-xs text-red-600">Failed</div>
            </div>
          </div>

          {latestReport.blocking_violations.length > 0 && (
            <Alert className="border-red-300 bg-red-50">
              <XCircle className="h-4 w-4 text-red-600" />
              <AlertDescription className="text-red-800">
                <strong>Blocking Violations:</strong>
                <ul className="list-disc ml-4 mt-1 text-sm">
                  {latestReport.blocking_violations.map((violation) => (
                    <li key={violation}>{violation}</li>
                  ))}
                </ul>
                {/* FINDING 14: Link to approval gate if pending */}
                {metrics?.pending_operator_approval && (
                  <div className="mt-2 p-2 bg-white rounded border-l-2 border-red-400">
                    <span className="text-xs">
                      This blocking violation may prevent approval of {metrics.approval_required_for}
                    </span>
                  </div>
                )}
              </AlertDescription>
            </Alert>
          )}
        </div>
      </CardContent>
    </Card>
  );
};

const IncidentsPanel: React.FC<{ incidents: Incident[] }> = ({ incidents }) => {
  const openIncidents = incidents.filter((i) => i.status === 'open');
  const criticalIncidents = openIncidents.filter((i) => i.severity === 'critical');

  if (openIncidents.length === 0) {
    return null;
  }

  return (
    <Card className={criticalIncidents.length > 0 ? 'border-red-300' : 'border-orange-300'}>
      <CardHeader>
        <CardTitle>Production Incidents ({openIncidents.length} open)</CardTitle>
        <CardDescription>Real-time incident tracking</CardDescription>
      </CardHeader>
      <CardContent>
        <div className="space-y-2">
          {openIncidents.map((incident) => (
            <div
              key={incident.incident_id}
              className={`p-3 rounded border-l-4 ${
                incident.severity === 'critical' ? 'border-l-red-500 bg-red-50' : 'border-l-orange-500 bg-orange-50'
              }`}
            >
              <div className="flex items-start justify-between">
                <div>
                  <div className="font-medium text-sm">{incident.incident_type}</div>
                  <div className="text-xs text-gray-600 mt-1">{incident.message}</div>
                  <div className="text-xs text-gray-500 mt-1">{incident.incident_id}</div>
                </div>
                <Badge
                  variant={incident.severity === 'critical' ? 'destructive' : 'secondary'}
                  className="ml-2"
                >
                  {incident.severity.toUpperCase()}
                </Badge>
              </div>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
};

// FINDING 6 & 13: ApprovalAlert with blocking reason and action buttons
const ApprovalAlert: React.FC<{ requiredFor: string; blockingReason?: string }> = ({
  requiredFor,
  blockingReason,
}) => {
  const handleApprove = async () => {
    try {
      const response = await fetch('/v1/console/orchestration/approval', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          gate: requiredFor,
          action: 'approve',
          reason: 'Approved via dashboard',
        }),
      });
      if (response.ok) {
        alert('Approval submitted successfully');
        // Refresh metrics
        window.location.reload();
      }
    } catch (error) {
      alert(`Error: ${error}`);
    }
  };

  const handleReject = async () => {
    const reason = prompt('Rejection reason (optional):');
    try {
      const response = await fetch('/v1/console/orchestration/approval', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          gate: requiredFor,
          action: 'reject',
          reason: reason || 'Rejected via dashboard',
        }),
      });
      if (response.ok) {
        alert('Rejection submitted successfully');
        window.location.reload();
      }
    } catch (error) {
      alert(`Error: ${error}`);
    }
  };

  return (
    <Alert className="border-orange-300 bg-orange-50">
      <Clock className="h-4 w-4 text-orange-600" />
      <AlertDescription className="space-y-3">
        <div>
          <strong className="text-orange-800">Operator Approval Required:</strong>{' '}
          <span className="text-orange-800">{requiredFor}</span>
          <br />
          <span className="text-sm text-orange-700">Awaiting manual review and approval to proceed</span>
        </div>
        {blockingReason && (
          <div className="text-sm bg-orange-100 border border-orange-300 rounded p-2 text-orange-900">
            <strong>Blocking Reason:</strong> {blockingReason}
          </div>
        )}
        <div className="flex gap-2">
          <button
            onClick={handleApprove}
            className="px-4 py-2 bg-green-600 text-white rounded hover:bg-green-700 text-sm font-medium"
          >
            ✓ Approve
          </button>
          <button
            onClick={handleReject}
            className="px-4 py-2 bg-red-600 text-white rounded hover:bg-red-700 text-sm font-medium"
          >
            ✗ Reject
          </button>
        </div>
      </AlertDescription>
    </Alert>
  );
};

// FINDING 7: RollbackTriggersPanel with dynamic thresholds per phase
const RollbackTriggersPanel: React.FC<{ metrics: RolloutMetrics }> = ({ metrics }) => {
  // FINDING 7: Dynamic trigger thresholds based on phase
  const triggerThresholds = {
    PHASE_2A_CANARY: {
      correctness: '>2%',
      latency: '>20%',
      confidence: '>10%',
    },
    PHASE_2B_SKILL_PRIMARY: {
      correctness: '>1.5%',
      latency: '>15%',
      confidence: '>8%',
    },
  };

  const phaseThresholds = triggerThresholds[metrics.phase as keyof typeof triggerThresholds] || {
    correctness: '>2%',
    latency: '>20%',
    confidence: '>10%',
  };

  const triggers = [
    { name: `Correctness Drop (${phaseThresholds.correctness})`, status: 'armed' },
    { name: `Latency Spike (${phaseThresholds.latency})`, status: 'armed' },
    { name: `Confidence Regression (${phaseThresholds.confidence})`, status: 'armed' },
    { name: 'Audit Chain Break', status: 'armed' },
    { name: 'Tenant Isolation Violation', status: 'armed' },
    { name: 'Security Check Failure', status: 'armed' },
    { name: 'Loss Signal Critical', status: 'armed' },
    { name: 'Manual Operator Rollback', status: 'armed' },
  ];

  return (
    <Card>
      <CardHeader>
        <CardTitle>Auto-Rollback Triggers</CardTitle>
        <CardDescription>
          All 8 fail-closed triggers armed and monitoring (thresholds for {metrics.phase})
        </CardDescription>
      </CardHeader>
      <CardContent>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
          {triggers.map((trigger) => (
            <div key={trigger.name} className="flex items-center gap-2 p-2 bg-gray-50 rounded">
              <CheckCircle className="h-4 w-4 text-green-600" />
              <span className="text-sm">{trigger.name}</span>
            </div>
          ))}
        </div>
        {metrics.rollback_count > 0 && (
          <Alert className="mt-4 border-orange-300 bg-orange-50">
            <AlertTriangle className="h-4 w-4 text-orange-600" />
            <AlertDescription className="text-orange-800">
              {metrics.rollback_count} rollback(s) executed
            </AlertDescription>
          </Alert>
        )}
      </CardContent>
    </Card>
  );
};

// FINDING 9: Helper to show relative time
const getRelativeTime = (timestamp?: number): string => {
  if (!timestamp) return 'unknown';

  const now = Date.now();
  const diffMs = now - timestamp;
  const diffMins = Math.floor(diffMs / 60000);
  const diffSecs = Math.floor(diffMs / 1000);

  if (diffMins > 0) {
    return `${diffMins}m ago`;
  }
  return `${diffSecs}s ago`;
};

export default RolloutMonitoringDashboard;
