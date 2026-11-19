/**
 * ProductionMonitoringDashboard: Production health monitoring UI (ADR-0906)
 *
 * Displays:
 * - System health score gauge (0-100)
 * - Autonomous Forge statistics (skills forged, success rate, avg latency)
 * - Per-skill performance table (confidence, latency p50/p95/p99, error rate, status)
 * - Alerts timeline (active & recent)
 * - Historical charts (confidence trend, latency trend, error rate trend)
 *
 * Real-time updates every 5-10 seconds during active operations.
 */
import React, { useState, useEffect, useCallback } from 'react';
import { AlertCircle, CheckCircle, AlertTriangle, RefreshCw } from 'lucide-react';
import { LineChart, Line, BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer } from 'recharts';
import { Button } from '@/components/ui/button';
import { icon } from '@/lib/status-markers';

// ─────────────────────────────────────────────────────────────────────────────
// Type Definitions
// ─────────────────────────────────────────────────────────────────────────────

// The backend reports ``null`` for every figure it does not measure (it used
// to invent them). Each numeric field is therefore nullable, and is rendered
// through fmt()/pct() below - a bare ``.toFixed`` on null crashed the panel.
interface HealthResponse {
  health_score: number | null;
  status: 'healthy' | 'degraded' | 'critical';
  uptime_hours: number | null;
  last_check: string;
  timestamp: string;
  tenant_id: string;
}

interface AutonomousStats {
  skills_forged: number | null;
  skills_approved: number | null;
  skills_deferred: number | null;
  skills_active: number | null;
  success_rate: number | null;
  avg_optimization_confidence: number | null;
  avg_latency_ms: number | null;
  avg_error_rate: number | null;
  last_fork_timestamp: string | null;
  timestamp: string;
  tenant_id: string;
}

interface SkillMetric {
  skill_id: string;
  version: string;
  confidence: number | null;
  latency_p50_ms: number | null;
  latency_p95_ms: number | null;
  latency_p99_ms: number | null;
  error_rate: number | null;
  execution_count: number | null;
  last_error: string | null;
  status: 'healthy' | 'degraded' | 'critical' | 'inactive';
  last_updated: string;
}

interface AlertEvent {
  alert_id: string;
  severity: 'info' | 'warning' | 'critical';
  title: string;
  message: string;
  skill_id: string | null;
  metric_name: string | null;
  metric_value: number | null;
  threshold: number | null;
  created_at: string;
  resolved_at: string | null;
}

interface TimeSeriesPoint {
  timestamp: string;
  value: number;
  skill_id: string | null;
}

// ─────────────────────────────────────────────────────────────────────────────
// Formatting (null-safe)
// ─────────────────────────────────────────────────────────────────────────────

/** Shown for a figure the backend does not measure. */
export const NOT_AVAILABLE = '\u2014';

const isNum = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);

/** ``value`` with ``digits`` decimals and ``suffix``; NOT_AVAILABLE for null/NaN. */
export function fmt(value: number | null | undefined, digits = 1, suffix = ''): string {
  return isNum(value) ? `${value.toFixed(digits)}${suffix}` : NOT_AVAILABLE;
}

/** A 0..1 ratio as a percentage; NOT_AVAILABLE for null/NaN. */
export function pct(value: number | null | undefined, digits = 0): string {
  return isNum(value) ? `${(value * 100).toFixed(digits)}%` : NOT_AVAILABLE;
}

/** An integer count with en-US grouping; NOT_AVAILABLE for null/NaN. */
export function count(value: number | null | undefined): string {
  return isNum(value) ? value.toLocaleString('en-US') : NOT_AVAILABLE;
}

// ─────────────────────────────────────────────────────────────────────────────
// Sub-Components
// ─────────────────────────────────────────────────────────────────────────────

const HealthScoreGauge: React.FC<{ health: HealthResponse }> = ({ health }) => {
  const getStatusColor = (score: number | null) => {
    if (!isNum(score)) return 'text-gray-400';
    if (score >= 67) return 'text-green-500';
    if (score >= 34) return 'text-yellow-500';
    return 'text-red-500';
  };

  const getStatusBg = (score: number | null) => {
    if (!isNum(score)) return 'bg-gray-50';
    if (score >= 67) return 'bg-green-50';
    if (score >= 34) return 'bg-yellow-50';
    return 'bg-red-50';
  };

  return (
    <div className={`rounded-lg border p-6 ${getStatusBg(health.health_score)}`}>
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-lg font-semibold">System Health</h3>
          <p className="text-sm text-gray-600">Overall system status</p>
        </div>
        <div className="text-right">
          <div className={`text-5xl font-bold ${getStatusColor(health.health_score)}`}>
            {fmt(health.health_score)}
          </div>
          <div className="text-sm text-gray-600">
            {health.status === 'healthy' && `${icon('done')} Healthy`}
            {health.status === 'degraded' && `${icon('warning')} Degraded`}
            {health.status === 'critical' && `${icon('failed')} Critical`}
            {!['healthy', 'degraded', 'critical'].includes(health.status) && 'Not measured on this build'}
          </div>
          <div className="text-xs text-gray-500 mt-1">
            Uptime: {fmt(health.uptime_hours, 1, 'h')}
          </div>
        </div>
      </div>
    </div>
  );
};

const StatsCards: React.FC<{ stats: AutonomousStats }> = ({ stats }) => {
  return (
    <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
      <div className="rounded-lg border bg-blue-50 p-4">
        <div className="text-2xl font-bold text-blue-600">{count(stats.skills_forged)}</div>
        <p className="text-xs text-gray-600">Skills Forged</p>
      </div>
      <div className="rounded-lg border bg-green-50 p-4">
        <div className="text-2xl font-bold text-green-600">{pct(stats.success_rate)}</div>
        <p className="text-xs text-gray-600">Success Rate</p>
      </div>
      <div className="rounded-lg border bg-purple-50 p-4">
        <div className="text-2xl font-bold text-purple-600">{fmt(stats.avg_latency_ms, 1, 'ms')}</div>
        <p className="text-xs text-gray-600">Avg Latency</p>
      </div>
      <div className="rounded-lg border bg-orange-50 p-4">
        <div className="text-2xl font-bold text-orange-600">{pct(stats.avg_optimization_confidence)}</div>
        <p className="text-xs text-gray-600">Avg Confidence</p>
      </div>
    </div>
  );
};

const SkillPerformanceTable: React.FC<{ skills: SkillMetric[] }> = ({ skills }) => {
  const getStatusColor = (status: string) => {
    switch (status) {
      case 'healthy':
        return 'text-green-600 bg-green-50';
      case 'degraded':
        return 'text-yellow-600 bg-yellow-50';
      case 'critical':
        return 'text-red-600 bg-red-50';
      default:
        return 'text-gray-600 bg-gray-50';
    }
  };

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="border-b bg-gray-50">
          <tr>
            <th className="px-4 py-3 text-left font-semibold">Skill</th>
            <th className="px-4 py-3 text-left font-semibold">Confidence</th>
            <th className="px-4 py-3 text-left font-semibold">P50 Latency</th>
            <th className="px-4 py-3 text-left font-semibold">P95 Latency</th>
            <th className="px-4 py-3 text-left font-semibold">Error Rate</th>
            <th className="px-4 py-3 text-left font-semibold">Executions</th>
            <th className="px-4 py-3 text-left font-semibold">Status</th>
          </tr>
        </thead>
        <tbody>
          {skills.map((skill) => (
            <tr key={skill.skill_id} className="border-b hover:bg-gray-50">
              <td className="px-4 py-3 font-mono text-xs">{skill.skill_id}</td>
              <td className="px-4 py-3">
                <div className="font-semibold">{pct(skill.confidence)}</div>
              </td>
              <td className="px-4 py-3">{fmt(skill.latency_p50_ms, 1, 'ms')}</td>
              <td className="px-4 py-3">{fmt(skill.latency_p95_ms, 1, 'ms')}</td>
              <td className="px-4 py-3">{pct(skill.error_rate, 2)}</td>
              <td className="px-4 py-3">{count(skill.execution_count)}</td>
              <td className={`px-4 py-3 rounded font-semibold ${getStatusColor(skill.status)}`}>
                {skill.status.charAt(0).toUpperCase() + skill.status.slice(1)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
};

const AlertsTimeline: React.FC<{ activeAlerts: AlertEvent[]; recentAlerts: AlertEvent[] }> = ({
  activeAlerts,
  recentAlerts,
}) => {
  const getAlertIcon = (severity: string) => {
    switch (severity) {
      case 'critical':
        return <AlertCircle className="h-5 w-5 text-red-500" />;
      case 'warning':
        return <AlertTriangle className="h-5 w-5 text-yellow-500" />;
      default:
        return <CheckCircle className="h-5 w-5 text-blue-500" />;
    }
  };

  return (
    <div className="space-y-4">
      <div>
        <h4 className="font-semibold mb-2">Active Alerts ({activeAlerts.length})</h4>
        {activeAlerts.length === 0 ? (
          <div className="text-sm text-gray-500">No active alerts</div>
        ) : (
          <div className="space-y-2">
            {activeAlerts.map((alert) => (
              <div key={alert.alert_id} className="flex items-start gap-3 rounded border border-yellow-200 bg-yellow-50 p-3">
                {getAlertIcon(alert.severity)}
                <div className="flex-1 text-sm">
                  <div className="font-semibold">{alert.title}</div>
                  <div className="text-gray-600">{alert.message}</div>
                  {alert.metric_name && (
                    <div className="text-xs text-gray-500 mt-1">
                      {alert.metric_name}: {fmt(alert.metric_value, 3)} (threshold: {fmt(alert.threshold, 3)})
                    </div>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {recentAlerts.length > 0 && (
        <div>
          <h4 className="font-semibold mb-2">Recent (Resolved)</h4>
          <div className="space-y-2">
            {recentAlerts.map((alert) => (
              <div key={alert.alert_id} className="flex items-start gap-3 rounded border border-green-200 bg-green-50 p-3 opacity-75">
                <CheckCircle className="h-5 w-5 text-green-500" />
                <div className="flex-1 text-sm">
                  <div className="font-semibold">{alert.title}</div>
                  <div className="text-gray-600">{alert.message}</div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
};

const ConfidenceTrendChart: React.FC<{ data: TimeSeriesPoint[] }> = ({ data }) => {
  return (
    <div className="rounded-lg border p-4">
      <h4 className="font-semibold mb-4">Confidence Trend (7 days)</h4>
      <ResponsiveContainer width="100%" height={300}>
        <LineChart data={data.filter((p) => isNum(p.value)).map((p) => ({ ...p, value: p.value * 100 }))}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis
            dataKey="timestamp"
            tick={{ fontSize: 12 }}
            tickFormatter={(ts) => new Date(ts).toLocaleDateString()}
          />
          <YAxis
            domain={[0, 100]}
            label={{ value: 'Confidence %', angle: -90, position: 'insideLeft' }}
          />
          <Tooltip
            formatter={(val: number) => fmt(val, 1, '%')}
            labelFormatter={(ts) => new Date(ts as string | number).toLocaleString("en-US")}
          />
          <Legend />
          <Line
            type="monotone"
            dataKey="value"
            stroke="#10b981"
            name="Confidence"
            strokeWidth={2}
            dot={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
};

const LatencyTrendChart: React.FC<{ data: TimeSeriesPoint[] }> = ({ data }) => {
  return (
    <div className="rounded-lg border p-4">
      <h4 className="font-semibold mb-4">P95 Latency Trend (7 days)</h4>
      <ResponsiveContainer width="100%" height={300}>
        <BarChart data={data}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis
            dataKey="timestamp"
            tick={{ fontSize: 12 }}
            tickFormatter={(ts) => new Date(ts).toLocaleDateString()}
          />
          <YAxis label={{ value: 'Latency (ms)', angle: -90, position: 'insideLeft' }} />
          <Tooltip
            formatter={(val: number) => fmt(val, 1, 'ms')}
            labelFormatter={(ts) => new Date(ts as string | number).toLocaleString("en-US")}
          />
          <Legend />
          <Bar dataKey="value" fill="#f59e0b" name="P95 Latency (ms)" />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
};

// ─────────────────────────────────────────────────────────────────────────────
// Main Component
// ─────────────────────────────────────────────────────────────────────────────

export const ProductionMonitoringDashboard: React.FC = () => {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [stats, setStats] = useState<AutonomousStats | null>(null);
  const [skills, setSkills] = useState<SkillMetric[]>([]);
  const [activeAlerts, setActiveAlerts] = useState<AlertEvent[]>([]);
  const [recentAlerts, setRecentAlerts] = useState<AlertEvent[]>([]);
  const [confidenceTrend, setConfidenceTrend] = useState<TimeSeriesPoint[]>([]);
  const [latencyTrend, setLatencyTrend] = useState<TimeSeriesPoint[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [lastRefresh, setLastRefresh] = useState<Date>(new Date());

  const fetchData = useCallback(async () => {
    try {
      setLoading(true);

      // Fetch all metrics in parallel
      const [healthRes, statsRes, skillsRes, alertsRes, confidenceRes, latencyRes] = await Promise.all([
        fetch('/v1/console/monitoring/health'),
        fetch('/v1/console/monitoring/autonomous-stats'),
        fetch('/v1/console/monitoring/skill-performance'),
        fetch('/v1/console/monitoring/alerts'),
        fetch('/v1/console/monitoring/metrics/timeseries?metric=confidence&days=7'),
        fetch('/v1/console/monitoring/metrics/timeseries?metric=latency_p95_ms&days=7'),
      ]);

      if (healthRes.ok) setHealth(await healthRes.json());
      if (statsRes.ok) setStats(await statsRes.json());
      if (skillsRes.ok) {
        const skillData = await skillsRes.json();
        setSkills(skillData.skills ?? []);
      }
      if (alertsRes.ok) {
        const alertData = await alertsRes.json();
        setActiveAlerts(alertData.active_alerts ?? []);
        setRecentAlerts(alertData.recent_alerts ?? []);
      }
      if (confidenceRes.ok) {
        const confData = await confidenceRes.json();
        setConfidenceTrend(confData.data_points ?? []);
      }
      if (latencyRes.ok) {
        const latData = await latencyRes.json();
        setLatencyTrend(latData.data_points ?? []);
      }

      setLastRefresh(new Date());
    } catch (err) {
      console.error('Failed to fetch monitoring data:', err);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  // Initial load
  useEffect(() => {
    fetchData();
  }, [fetchData]);

  // Auto-refresh every 10 seconds
  useEffect(() => {
    const interval = setInterval(() => {
      setRefreshing(true);
      fetchData();
    }, 10000);

    return () => clearInterval(interval);
  }, [fetchData]);

  const handleRefresh = async () => {
    setRefreshing(true);
    await fetchData();
  };

  if (loading && !health) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="text-center">
          <RefreshCw className="h-8 w-8 animate-spin mx-auto mb-4 text-gray-400" />
          <p className="text-gray-600">Loading monitoring data...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6 p-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-bold">Production Monitoring</h2>
          <p className="text-sm text-gray-600">
            Last updated: {lastRefresh.toLocaleTimeString()} {refreshing && '(refreshing...)'}
          </p>
        </div>
        <Button
          onClick={handleRefresh}
          disabled={refreshing}
          className="gap-2"
        >
          <RefreshCw className={`h-4 w-4 ${refreshing ? 'animate-spin' : ''}`} />
          Refresh
        </Button>
      </div>

      {/* Health Score */}
      {health && (
        <HealthScoreGauge health={health} />
      )}

      {/* Stats Cards */}
      {stats && (
        <div>
          <h3 className="text-lg font-semibold mb-4">Autonomous Forge Statistics</h3>
          <StatsCards stats={stats} />
        </div>
      )}

      {/* Skill Performance Table */}
      {skills.length > 0 && (
        <div>
          <h3 className="text-lg font-semibold mb-4">Per-Skill Performance</h3>
          <div className="rounded-lg border overflow-hidden">
            <SkillPerformanceTable skills={skills} />
          </div>
        </div>
      )}

      {/* Charts */}
      <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
        {confidenceTrend.length > 0 && (
          <ConfidenceTrendChart data={confidenceTrend} />
        )}
        {latencyTrend.length > 0 && (
          <LatencyTrendChart data={latencyTrend} />
        )}
      </div>

      {/* Alerts */}
      <div>
        <h3 className="text-lg font-semibold mb-4">Alerts & Events</h3>
        <div className="rounded-lg border p-4">
          <AlertsTimeline activeAlerts={activeAlerts} recentAlerts={recentAlerts} />
        </div>
      </div>
    </div>
  );
};

export default ProductionMonitoringDashboard;
