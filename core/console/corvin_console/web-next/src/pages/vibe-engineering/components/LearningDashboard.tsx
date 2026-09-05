/**
 * Learning Dashboard — Tenant Maturity Visualization
 *
 * Shows how well a tenant has matured over time through 5 Learning Subsystems:
 * - Delegation Router (decides which agent to use)
 * - Context Adapter (preserves/injects context)
 * - Workflow Optimizer (learns execution chains)
 * - Security Orchestrator (learns threat patterns)
 * - Flow Guard (learns safe data shapes)
 *
 * Design: Hero Score + Radar + Trajectories + Heatmap
 * Colors: Sequential (confidence), Status (health), Categorical (fixed 5 systems)
 * Mode: Dark-preferred (monitoring dashboard aesthetic)
 */

import React, { useState } from 'react';
import {
  AreaChart, Area,
  XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
  RadarChart, PolarGrid, PolarAngleAxis, PolarRadiusAxis, Radar,
} from 'recharts';
import MethodDiscoveryPanel from '@/panels/MethodDiscoveryPanel';

// ============================================================================
// TYPES & DATA
// ============================================================================

interface SystemLearning {
  system_id: string;
  system_name: string;
  color: string;
  confidence: number; // 0–1
  outcome_quality: number; // 0–1
  learning_velocity: number; // -1 to 1 (trend)
  last_updated: string;
  feedback_count: number;
  decision_count: number;
}

interface TenantLearningMetrics {
  learning_score: number; // 0–10, aggregated
  learning_trend: number; // +/- delta over last 7 days
  systems: SystemLearning[];
  trajectory_data: Array<{
    date: string;
    [key: string]: number | string; // system_id → confidence
  }>;
  health_matrix: Array<{
    system: string;
    confidence: number;
    outcome: number;
    velocity: number;
  }>;
}

// ============================================================================
// PALETTE (Validated, CVD-Safe, Dark Mode)
// ============================================================================

const PALETTE = {
  systems: [
    { id: 'delegation_router', name: 'Delegation Router', hex: '#FF6B6B' }, // warm red
    { id: 'context_adapter', name: 'Context Adapter', hex: '#4ECDC4' }, // cool cyan
    { id: 'workflow_optimizer', name: 'Workflow Optimizer', hex: '#95E1D3' }, // soft teal
    { id: 'security_orchestrator', name: 'Security Orchestrator', hex: '#F7B801' }, // amber
    { id: 'flow_guard', name: 'Flow Guard', hex: '#6C63FF' }, // indigo
  ],
  sequential: {
    light: '#F0F0F0',
    mid: '#888888',
    dark: '#1A1A1A',
  },
  status: {
    good: '#2ECC71', // green
    warning: '#F39C12', // orange
    serious: '#E74C3C', // red
  },
  surface: {
    dark: '#0D1117', // GitHub dark
    card: '#161B22',
    border: '#30363D',
    text: '#C9D1D9',
    muted: '#8B949E',
  },
};

// ============================================================================
// MOCK DATA (Replace with real API)
// ============================================================================

const mockLearningMetrics: TenantLearningMetrics = {
  learning_score: 7.3,
  learning_trend: 0.5,
  systems: [
    {
      system_id: 'delegation_router',
      system_name: 'Delegation Router',
      color: PALETTE.systems[0].hex,
      confidence: 0.87,
      outcome_quality: 0.82,
      learning_velocity: 0.12,
      last_updated: '2026-09-04T14:32:00Z',
      feedback_count: 342,
      decision_count: 1240,
    },
    {
      system_id: 'context_adapter',
      system_name: 'Context Adapter',
      color: PALETTE.systems[1].hex,
      confidence: 0.76,
      outcome_quality: 0.79,
      learning_velocity: 0.08,
      last_updated: '2026-09-04T14:31:00Z',
      feedback_count: 287,
      decision_count: 982,
    },
    {
      system_id: 'workflow_optimizer',
      system_name: 'Workflow Optimizer',
      color: PALETTE.systems[2].hex,
      confidence: 0.68,
      outcome_quality: 0.71,
      learning_velocity: 0.15,
      last_updated: '2026-09-04T14:30:00Z',
      feedback_count: 156,
      decision_count: 521,
    },
    {
      system_id: 'security_orchestrator',
      system_name: 'Security Orchestrator',
      color: PALETTE.systems[3].hex,
      confidence: 0.92,
      outcome_quality: 0.89,
      learning_velocity: 0.06,
      last_updated: '2026-09-04T14:29:00Z',
      feedback_count: 198,
      decision_count: 743,
    },
    {
      system_id: 'flow_guard',
      system_name: 'Flow Guard',
      color: PALETTE.systems[4].hex,
      confidence: 0.81,
      outcome_quality: 0.84,
      learning_velocity: 0.11,
      last_updated: '2026-09-04T14:28:00Z',
      feedback_count: 221,
      decision_count: 856,
    },
  ],
  trajectory_data: [
    { date: '2026-08-06', delegation_router: 0.65, context_adapter: 0.52, workflow_optimizer: 0.48, security_orchestrator: 0.78, flow_guard: 0.61 },
    { date: '2026-08-13', delegation_router: 0.71, context_adapter: 0.58, workflow_optimizer: 0.54, security_orchestrator: 0.84, flow_guard: 0.67 },
    { date: '2026-08-20', delegation_router: 0.79, context_adapter: 0.66, workflow_optimizer: 0.61, security_orchestrator: 0.88, flow_guard: 0.74 },
    { date: '2026-08-27', delegation_router: 0.84, context_adapter: 0.72, workflow_optimizer: 0.66, security_orchestrator: 0.90, flow_guard: 0.79 },
    { date: '2026-09-04', delegation_router: 0.87, context_adapter: 0.76, workflow_optimizer: 0.68, security_orchestrator: 0.92, flow_guard: 0.81 },
  ],
  health_matrix: [
    { system: 'Delegation Router', confidence: 0.87, outcome: 0.82, velocity: 0.12 },
    { system: 'Context Adapter', confidence: 0.76, outcome: 0.79, velocity: 0.08 },
    { system: 'Workflow Optimizer', confidence: 0.68, outcome: 0.71, velocity: 0.15 },
    { system: 'Security Orchestrator', confidence: 0.92, outcome: 0.89, velocity: 0.06 },
    { system: 'Flow Guard', confidence: 0.81, outcome: 0.84, velocity: 0.11 },
  ],
};

// ============================================================================
// HELPERS
// ============================================================================

const getScoreColor = (score: number): string => {
  if (score >= 0.85) return PALETTE.status.good;
  if (score >= 0.70) return PALETTE.status.warning;
  return PALETTE.status.serious;
};

const getScoreStatus = (score: number): string => {
  if (score >= 0.85) return '✓ Excellent';
  if (score >= 0.70) return '⚠ Good';
  if (score >= 0.50) return '⚠ Fair';
  return '✗ Learning';
};

const formatPercent = (val: number) => `${(val * 100).toFixed(0)}%`;

// ============================================================================
// COMPONENTS
// ============================================================================

const HeroScoreTile: React.FC<{ metrics: TenantLearningMetrics }> = ({ metrics }) => {
  const scoreColor = getScoreColor(metrics.learning_score / 10);
  const status = getScoreStatus(metrics.learning_score / 10);
  const trendSign = metrics.learning_trend >= 0 ? '+' : '';

  return (
    <div
      className="bg-card border border-border rounded-lg p-8 text-center relative overflow-hidden"
    >
      {/* Background Gauge (SVG) */}
      <svg
        style={{
          position: 'absolute',
          top: 0,
          right: 0,
          opacity: 0.1,
          width: '200px',
          height: '200px',
        }}
        viewBox="0 0 200 200"
      >
        {/* Gauge arc (0–360°) */}
        <circle
          cx="100"
          cy="100"
          r="80"
          fill="none"
          stroke={PALETTE.surface.border}
          strokeWidth="3"
          strokeDasharray={`${(metrics.learning_score / 10) * 251.2} 251.2`}
          transform="rotate(-90 100 100)"
        />
      </svg>

      {/* Content */}
      <div style={{ position: 'relative', zIndex: 1 }}>
        <h3 style={{ color: PALETTE.surface.muted, fontSize: '12px', margin: '0 0 12px' }}>
          TENANT LEARNING SCORE
        </h3>
        <div style={{ fontSize: '64px', fontWeight: 700, color: scoreColor, margin: '0 0 8px' }}>
          {metrics.learning_score.toFixed(1)}
          <span style={{ fontSize: '32px', color: PALETTE.surface.muted }}>/10</span>
        </div>
        <p style={{ color: PALETTE.surface.text, fontSize: '14px', margin: '0 0 16px' }}>
          {status}
        </p>

        {/* Trend */}
        <div
          style={{
            background: PALETTE.surface.border,
            borderRadius: '6px',
            padding: '8px 12px',
            fontSize: '12px',
            color: PALETTE.surface.text,
            display: 'inline-block',
          }}
        >
          <span style={{ color: metrics.learning_trend >= 0 ? PALETTE.status.good : PALETTE.status.serious }}>
            {trendSign}{metrics.learning_trend >= 0 ? '↗' : '↘'}
          </span>
          {' '}
          {Math.abs(metrics.learning_trend).toFixed(2)} points (7 days)
        </div>

        {/* Description */}
        <p style={{ color: PALETTE.surface.muted, fontSize: '12px', marginTop: '16px', margin: '0' }}>
          Your tenant is actively learning. Systems improve confidence through feedback loops.
        </p>
      </div>
    </div>
  );
};

const RadarChartComponent: React.FC<{ systems: SystemLearning[] }> = ({ systems }) => {
  const radarData = systems.map((sys) => ({
    name: sys.system_name.split(' ')[0], // short name
    confidence: sys.confidence,
    outcome: sys.outcome_quality,
    velocity: Math.max(0, sys.learning_velocity + 1) * 0.5, // normalize -1..1 → 0..1
  }));

  return (
    <div className="bg-card border border-border rounded-lg p-4">
      <h3 className="text-foreground text-sm font-semibold mb-4">
        5-System Confidence Profile
      </h3>
      <ResponsiveContainer width="100%" height={300}>
        <RadarChart data={radarData}>
          <PolarGrid stroke={PALETTE.surface.border} />
          <PolarAngleAxis dataKey="name" stroke={PALETTE.surface.muted} />
          <PolarRadiusAxis angle={90} domain={[0, 1]} stroke={PALETTE.surface.muted} />
          <Radar name="Confidence" dataKey="confidence" stroke="#FF6B6B" fill="#FF6B6B" fillOpacity={0.25} />
          <Radar name="Outcome" dataKey="outcome" stroke="#4ECDC4" fill="#4ECDC4" fillOpacity={0.15} />
          <Radar name="Velocity" dataKey="velocity" stroke="#95E1D3" fill="#95E1D3" fillOpacity={0.1} />
          <Legend />
          <Tooltip
            contentStyle={{
              background: PALETTE.surface.dark,
              border: `1px solid ${PALETTE.surface.border}`,
              borderRadius: '6px',
              color: PALETTE.surface.text,
            }}
            formatter={(value) => formatPercent(value as number)}
          />
        </RadarChart>
      </ResponsiveContainer>
    </div>
  );
};

const TrajectoriesChart: React.FC<{ data: typeof mockLearningMetrics.trajectory_data }> = ({ data }) => {
  return (
    <div className="bg-card border border-border rounded-lg p-4">
      <h3 className="text-foreground text-sm font-semibold mb-4">
        30-Day Learning Trajectories (Confidence Score)
      </h3>
      <ResponsiveContainer width="100%" height={250}>
        <AreaChart data={data}>
          <defs>
            {PALETTE.systems.map((sys) => (
              <linearGradient key={sys.id} id={`grad-${sys.id}`} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={sys.hex} stopOpacity={0.3} />
                <stop offset="100%" stopColor={sys.hex} stopOpacity={0} />
              </linearGradient>
            ))}
          </defs>
          <CartesianGrid stroke={PALETTE.surface.border} strokeDasharray="4 4" />
          <XAxis dataKey="date" stroke={PALETTE.surface.muted} />
          <YAxis domain={[0, 1]} stroke={PALETTE.surface.muted} tickFormatter={formatPercent} />
          <Tooltip
            contentStyle={{
              background: PALETTE.surface.dark,
              border: `1px solid ${PALETTE.surface.border}`,
              borderRadius: '6px',
              color: PALETTE.surface.text,
            }}
            formatter={(value) => formatPercent(value as number)}
          />
          <Legend />
          {PALETTE.systems.map((sys) => (
            <Area
              key={sys.id}
              type="monotone"
              dataKey={sys.id}
              stroke={sys.hex}
              fill={`url(#grad-${sys.id})`}
              strokeWidth={2}
              isAnimationActive={false}
              name={sys.name}
            />
          ))}
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
};

const HealthHeatmap: React.FC<{ data: typeof mockLearningMetrics.health_matrix }> = ({ data }) => {
  const metrics = ['confidence', 'outcome', 'velocity'];

  return (
    <div className="bg-card border border-border rounded-lg p-4">
      <h3 className="text-foreground text-sm font-semibold mb-4">
        System Health Heatmap
      </h3>

      <div style={{ overflowX: 'auto' }}>
        <table
          style={{
            width: '100%',
            borderCollapse: 'collapse',
            fontSize: '12px',
          }}
        >
          <thead>
            <tr style={{ borderBottom: `1px solid ${PALETTE.surface.border}` }}>
              <th
                style={{
                  textAlign: 'left',
                  padding: '8px',
                  color: PALETTE.surface.muted,
                  fontWeight: 600,
                }}
              >
                System
              </th>
              {metrics.map((m) => (
                <th
                  key={m}
                  style={{
                    textAlign: 'center',
                    padding: '8px',
                    color: PALETTE.surface.muted,
                    fontWeight: 600,
                  }}
                >
                  {m.charAt(0).toUpperCase() + m.slice(1)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data.map((row, idx) => (
              <tr key={idx} style={{ borderBottom: `1px solid ${PALETTE.surface.border}` }}>
                <td style={{ padding: '8px', color: PALETTE.surface.text }}>
                  {row.system}
                </td>
                {metrics.map((metric) => {
                  const value = row[metric as keyof typeof row] as number;
                  const cellColor = getScoreColor(value);
                  const opacity = value * 0.6 + 0.2; // scale opacity 0.2–0.8

                  return (
                    <td
                      key={metric}
                      style={{
                        textAlign: 'center',
                        padding: '8px',
                        background: cellColor,
                        opacity: opacity,
                        color: PALETTE.surface.text,
                        fontWeight: 600,
                        borderRadius: '4px',
                      }}
                    >
                      {formatPercent(value)}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
};

const SystemDetailCards: React.FC<{ systems: SystemLearning[] }> = ({ systems }) => {
  return (
    <div
      style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))',
        gap: '12px',
      }}
    >
      {systems.map((sys) => {
        const status = getScoreStatus(sys.confidence);
        const statusColor = getScoreColor(sys.confidence);

        return (
          <div
            key={sys.system_id}
            className="bg-card border-2 rounded-lg p-4 relative"
            style={{
              borderColor: sys.color,
            }}
          >
            {/* System Name */}
            <h4 style={{ color: sys.color, fontSize: '13px', fontWeight: 700, margin: '0 0 8px' }}>
              {sys.system_name}
            </h4>

            {/* Confidence Badge */}
            <div style={{ display: 'flex', gap: '8px', marginBottom: '12px', alignItems: 'center' }}>
              <div
                style={{
                  background: statusColor,
                  color: '#000',
                  borderRadius: '4px',
                  padding: '4px 8px',
                  fontSize: '11px',
                  fontWeight: 700,
                }}
              >
                {formatPercent(sys.confidence)}
              </div>
              <span style={{ color: PALETTE.surface.muted, fontSize: '11px' }}>
                {status}
              </span>
            </div>

            {/* Quick Stats */}
            <div style={{ fontSize: '11px', color: PALETTE.surface.muted, lineHeight: '1.6' }}>
              <div>
                <span style={{ color: PALETTE.surface.text }}>Outcome:</span> {formatPercent(sys.outcome_quality)}
              </div>
              <div>
                <span style={{ color: PALETTE.surface.text }}>Velocity:</span>{' '}
                <span style={{ color: sys.learning_velocity > 0 ? PALETTE.status.good : PALETTE.status.serious }}>
                  {sys.learning_velocity >= 0 ? '+' : ''}{(sys.learning_velocity * 100).toFixed(0)}%
                </span>
              </div>
              <div>
                <span style={{ color: PALETTE.surface.text }}>Decisions:</span> {sys.decision_count.toLocaleString()}
              </div>
              <div>
                <span style={{ color: PALETTE.surface.text }}>Feedback:</span> {sys.feedback_count.toLocaleString()}
              </div>
            </div>

            {/* Last Updated */}
            <div
              style={{
                marginTop: '12px',
                paddingTop: '8px',
                borderTop: `1px solid ${PALETTE.surface.border}`,
                fontSize: '10px',
                color: PALETTE.surface.muted,
              }}
            >
              Updated: {new Date(sys.last_updated).toLocaleTimeString()}
            </div>
          </div>
        );
      })}
    </div>
  );
};

// ============================================================================
// MAIN COMPONENT
// ============================================================================

const LearningDashboard: React.FC = () => {
  const [metrics] = useState<TenantLearningMetrics>(mockLearningMetrics);

  // TODO: Replace with real API call
  // useEffect(() => {
  //   fetch('/v1/vibe/learning-metrics')
  //     .then((r) => r.json())
  //     .then(setMetrics)
  //     .catch(console.error);
  // }, []);

  return (
    <div
      className="min-h-screen bg-background p-6"
      style={{
        color: PALETTE.surface.text,
      }}
    >
      {/* Header */}
      <div style={{ marginBottom: '32px' }}>
        <h1 style={{ margin: '0 0 8px', fontSize: '28px', fontWeight: 700 }}>
          Learning Dashboard
        </h1>
        <p style={{ color: PALETTE.surface.muted, margin: 0, fontSize: '14px' }}>
          Real-time tenant maturity through 5 Learning Subsystems. Shows how your tenant self-improves over time.
        </p>
      </div>

      {/* Hero Score */}
      <div style={{ marginBottom: '32px' }}>
        <HeroScoreTile metrics={metrics} />
      </div>

      {/* Charts Grid */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(500px, 1fr))',
          gap: '16px',
          marginBottom: '32px',
        }}
      >
        <RadarChartComponent systems={metrics.systems} />
        <TrajectoriesChart data={metrics.trajectory_data} />
      </div>

      {/* Heatmap */}
      <div style={{ marginBottom: '32px' }}>
        <HealthHeatmap data={metrics.health_matrix} />
      </div>

      {/* System Detail Cards */}
      <div style={{ marginBottom: '32px' }}>
        <h2 style={{ color: PALETTE.surface.text, fontSize: '16px', margin: '0 0 16px', fontWeight: 700 }}>
          System Details
        </h2>
        <SystemDetailCards systems={metrics.systems} />
      </div>

      {/* Method Discovery (ADR-0548) — real API data, not part of the mock
          metrics above. Mounted here rather than as its own sidebar panel:
          Vibe Engineering was deliberately collapsed to one panel on
          2026-09-05 and a new nav entry would re-open that. */}
      <div style={{ marginBottom: '32px' }}>
        <MethodDiscoveryPanel />
      </div>

      {/* Footer */}
      <div className="border-t border-border pt-4 text-xs text-muted-foreground">

        <p>
          Data refreshed every 5 minutes. Learning events audited and hash-chained.
          See{' '}
          <a href="#" style={{ color: PALETTE.systems[0].hex, textDecoration: 'none' }}>
            full audit trail
          </a>
          .
        </p>
      </div>
    </div>
  );
};

export default LearningDashboard;
export { LearningDashboard };
