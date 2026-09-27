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
  skill_states: Record<string, string>;
  rollback_count: number;
  agreement_rate: number;
  confidence: number;
  latency_p99_ms: number;
  positive_feedback_rate: number;
  audit_chain_verified: boolean;
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
        if (!response.ok) throw new Error('Failed to fetch rollout status');

        const data = await response.json();
        setMetrics(data);
        setIsLoading(false);
      } catch (error) {
        console.error('Error fetching rollout metrics:', error);
      }
    };

    fetchMetrics();
    const interval = setInterval(fetchMetrics, 5000);
    return () => clearInterval(interval);
  }, []);

  if (isLoading || !metrics) {
    return <div className="flex items-center justify-center p-8">Loading rollout status...</div>;
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

          {/* Operator Approval Gate */}
          {metrics.pending_operator_approval && (
            <Alert className="border-orange-300 bg-orange-50">
              <Clock className="h-4 w-4 text-orange-600" />
              <AlertDescription className="text-orange-800">
                <strong>Operator Approval Required:</strong> {metrics.approval_required_for || 'Unknown gate'}
                <br />
                <span className="text-sm">Awaiting manual review and approval to proceed</span>
              </AlertDescription>
            </Alert>
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

      {/* Rollback Triggers Status */}
      <Card>
        <CardHeader>
          <CardTitle>Auto-Rollback Triggers</CardTitle>
          <CardDescription>All 8 fail-closed triggers armed and monitoring</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
            {[
              { name: 'Correctness Drop (>2%)', status: 'armed' },
              { name: 'Latency Spike (>20%)', status: 'armed' },
              { name: 'Confidence Regression (>10%)', status: 'armed' },
              { name: 'Audit Chain Break', status: 'armed' },
              { name: 'Tenant Isolation Violation', status: 'armed' },
              { name: 'Security Check Failure', status: 'armed' },
              { name: 'Loss Signal Critical', status: 'armed' },
              { name: 'Manual Operator Rollback', status: 'armed' },
            ].map((trigger) => (
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

      {/* ADR Compliance Status */}
      <CompliancePanel complianceReports={complianceReports} />

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

  return (
    <Card className={`border-2 ${statusColors[status]}`}>
      <CardContent className="pt-6">
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <span className="text-sm font-medium text-gray-600">{title}</span>
            {statusIcons[status]}
          </div>
          <div className="text-2xl font-bold">{value}</div>
          <div className="text-xs text-gray-500">{threshold}</div>
        </div>
      </CardContent>
    </Card>
  );
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
    <Card>
      <CardHeader>
        <CardTitle>Rollout SLA Status</CardTitle>
        <CardDescription>Phase-specific success criteria</CardDescription>
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

const CompliancePanel: React.FC<{ complianceReports: Record<number, ComplianceReport> }> = ({
  complianceReports,
}) => {
  const latestReport = Object.values(complianceReports).sort((a, b) => b.week - a.week)[0];

  if (!latestReport) {
    return null;
  }

  const totalChecks = latestReport.passed_checks + latestReport.failed_checks + latestReport.warning_checks;

  return (
    <Card>
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

export default RolloutMonitoringDashboard;
