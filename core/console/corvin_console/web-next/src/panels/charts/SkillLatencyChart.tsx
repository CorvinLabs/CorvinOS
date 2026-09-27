/**
 * Skill Execution Latency Chart (ADR-0722)
 *
 * Form: Grouped bars (skill on x, latency [ms] on y, grouped by percentile)
 * Color: Status encoding for percentile tiers (good/warning/critical)
 *
 * Dataviz Rules Applied:
 * - Thin bars (4px rounded ends)
 * - Direct labels (p99 value if >= 100ms)
 * - Hover tooltip (crosshair + per-bar details)
 * - Legend + table fallback for accessibility
 */

import React, { useState } from 'react';
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
  Cell,
  LineChart,
  Line,
  ComposedChart,
} from 'recharts';
import { AlertCircle } from 'lucide-react';
import styles from '../skills_observability.module.css';

export interface LatencyDataPoint {
  skill_id: string;
  p50_ms: number;
  p95_ms: number;
  p99_ms: number;
  sample_count: number;
}

export interface SkillLatencyChartProps {
  data?: { metrics: LatencyDataPoint[] };
  timeRange: '1d' | '7d' | '30d';
  onSkillSelect?: (skillId: string | null) => void;
}

/**
 * Custom tooltip for latency chart
 * Shows: skill name, percentile, latency value, interpretation
 */
const CustomLatencyTooltip: React.FC<any> = ({ active, payload, label }) => {
  if (!active || !payload) return null;

  return (
    <div style={{
      backgroundColor: 'var(--surface)',
      border: '1px solid var(--viz-gridline)',
      borderRadius: '6px',
      padding: '8px 12px'
    }}>
      <p style={{ margin: '0 0 6px', fontWeight: 600, color: 'var(--text-primary)' }}>
        {payload[0]?.payload?.skill_id || label}
      </p>
      {payload.map((entry: any, idx: number) => {
        const interpretation =
          entry.name === 'p50_ms' ? 'Median (Good)' :
          entry.name === 'p95_ms' ? 'Warning' :
          'Critical (p99)';

        return (
          <p key={idx} style={{ margin: '4px 0', fontSize: '12px', color: entry.color }}>
            {entry.name.toUpperCase()}: {entry.value.toFixed(1)}ms ({interpretation})
          </p>
        );
      })}
    </div>
  );
};

/**
 * Custom shape for rounded bar ends
 */
const RoundedBar = (props: any) => {
  const { fill, x, y, width, height } = props;
  const radius = 4;

  return (
    <path
      d={`
        M ${x} ${y + radius}
        L ${x} ${y + height - radius}
        Q ${x} ${y + height} ${x + radius} ${y + height}
        L ${x + width - radius} ${y + height}
        Q ${x + width} ${y + height} ${x + width} ${y + height - radius}
        L ${x + width} ${y + radius}
        Q ${x + width} ${y} ${x + width - radius} ${y}
        L ${x + radius} ${y}
        Q ${x} ${y} ${x} ${y + radius}
      `}
      fill={fill}
      stroke="none"
    />
  );
};

/**
 * SkillLatencyChart Component
 */
export const SkillLatencyChart: React.FC<SkillLatencyChartProps> = ({
  data,
  timeRange,
  onSkillSelect
}) => {
  const [selectedSkill, setSelectedSkill] = useState<string | null>(null);
  const [showTable, setShowTable] = useState(false);

  if (!data || !data.metrics || data.metrics.length === 0) {
    return (
      <div className={styles.emptyState}>
        <AlertCircle size={32} />
        <p>No latency data available for this period</p>
      </div>
    );
  }

  // Prepare chart data (transform flat structure to grouped)
  const metrics = data.metrics.slice(0, 8); // Cap at 8 skills (series ceiling per dataviz)

  // Calculate stats for summary
  const avgP50 = metrics.reduce((sum, m) => sum + m.p50_ms, 0) / metrics.length;
  const avgP95 = metrics.reduce((sum, m) => sum + m.p95_ms, 0) / metrics.length;
  const avgP99 = metrics.reduce((sum, m) => sum + m.p99_ms, 0) / metrics.length;
  const totalSamples = metrics.reduce((sum, m) => sum + m.sample_count, 0);

  const chartData = metrics.map(m => ({
    skill_id: m.skill_id,
    p50_ms: m.p50_ms,
    p95_ms: m.p95_ms,
    p99_ms: m.p99_ms,
    sample_count: m.sample_count,
    // Add displayName for direct label
    displayP99: m.p99_ms >= 100 ? `${m.p99_ms.toFixed(0)}ms` : ''
  }));

  const handleSkillClick = (skillId: string) => {
    setSelectedSkill(skillId === selectedSkill ? null : skillId);
    onSkillSelect?.(skillId === selectedSkill ? null : skillId);
  };

  return (
    <div className={styles.chartContainer}>
      <h3 className={styles.chartTitle}>Execution Latency Percentiles</h3>
      <p className={styles.chartSubtitle}>
        Distribution of skill execution latency over {timeRange} — lower is better.
        <br />
        Percentiles: p50 (median, green), p95 (warning, amber), p99 (critical, red).
      </p>

      {/* Statistics Summary */}
      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))',
        gap: '16px',
        marginBottom: '24px',
        padding: '16px',
        backgroundColor: 'var(--surface)',
        borderRadius: '6px',
        border: '1px solid var(--viz-gridline)'
      }}>
        <div>
          <p style={{ margin: '0 0 4px', fontSize: '12px', color: 'var(--text-secondary)' }}>
            Average p50
          </p>
          <p style={{ margin: 0, fontSize: '20px', fontWeight: 600, color: 'var(--viz-status-good)' }}>
            {avgP50.toFixed(1)}ms
          </p>
        </div>
        <div>
          <p style={{ margin: '0 0 4px', fontSize: '12px', color: 'var(--text-secondary)' }}>
            Average p95
          </p>
          <p style={{ margin: 0, fontSize: '20px', fontWeight: 600, color: 'var(--viz-status-warning)' }}>
            {avgP95.toFixed(1)}ms
          </p>
        </div>
        <div>
          <p style={{ margin: '0 0 4px', fontSize: '12px', color: 'var(--text-secondary)' }}>
            Average p99
          </p>
          <p style={{ margin: 0, fontSize: '20px', fontWeight: 600, color: 'var(--viz-status-critical)' }}>
            {avgP99.toFixed(1)}ms
          </p>
        </div>
        <div>
          <p style={{ margin: '0 0 4px', fontSize: '12px', color: 'var(--text-secondary)' }}>
            Total Samples
          </p>
          <p style={{ margin: 0, fontSize: '20px', fontWeight: 600, color: 'var(--text-primary)' }}>
            {totalSamples.toLocaleString()}
          </p>
        </div>
      </div>

      {/* Chart */}
      <ResponsiveContainer width="100%" height={400}>
        <BarChart
          data={chartData}
          margin={{ top: 20, right: 30, left: 0, bottom: 60 }}
        >
          <CartesianGrid strokeDasharray="4" stroke="var(--viz-gridline)" vertical={false} />
          <XAxis
            dataKey="skill_id"
            angle={-45}
            textAnchor="end"
            height={100}
            tick={{ fill: 'var(--text-secondary)', fontSize: 12 }}
          />
          <YAxis
            label={{ value: 'Latency (ms)', angle: -90, position: 'insideLeft', offset: 10 }}
            tick={{ fill: 'var(--text-secondary)', fontSize: 12 }}
          />
          <Tooltip content={<CustomLatencyTooltip />} />
          <Legend
            wrapperStyle={{ paddingTop: '20px' }}
            iconType="square"
            height={40}
          />

          {/* Bars: p50 (good), p95 (warning), p99 (critical) */}
          <Bar
            dataKey="p50_ms"
            fill="var(--viz-status-good)"
            name="p50 (Median)"
            shape={<RoundedBar />}
            radius={[4, 4, 0, 0]}
            onClick={(data) => handleSkillClick(data.skill_id)}
          />
          <Bar
            dataKey="p95_ms"
            fill="var(--viz-status-warning)"
            name="p95 (Warning)"
            shape={<RoundedBar />}
            radius={[4, 4, 0, 0]}
            onClick={(data) => handleSkillClick(data.skill_id)}
          />
          <Bar
            dataKey="p99_ms"
            fill="var(--viz-status-critical)"
            name="p99 (Critical)"
            shape={<RoundedBar />}
            radius={[4, 4, 0, 0]}
            onClick={(data) => handleSkillClick(data.skill_id)}
          />
        </BarChart>
      </ResponsiveContainer>

      {/* Accessibility Controls */}
      <div className={styles.accessibilityControls}>
        <label className={styles.accessibilityToggle}>
          <input
            type="checkbox"
            checked={showTable}
            onChange={(e) => setShowTable(e.target.checked)}
          />
          <span>Show data table</span>
        </label>
      </div>

      {/* Table Fallback (Accessibility) */}
      {showTable && (
        <div className={styles.tableFallback}>
          <table className={styles.tableContainer}>
            <thead>
              <tr>
                <th>Skill ID</th>
                <th>p50 (ms)</th>
                <th>p95 (ms)</th>
                <th>p99 (ms)</th>
                <th>Samples</th>
              </tr>
            </thead>
            <tbody>
              {metrics.map((m) => (
                <tr key={m.skill_id}>
                  <td>
                    <button
                      onClick={() => handleSkillClick(m.skill_id)}
                      style={{
                        background: 'none',
                        border: 'none',
                        color: 'var(--viz-seq-blue-400)',
                        cursor: 'pointer',
                        textDecoration: selectedSkill === m.skill_id ? 'underline' : 'none'
                      }}
                    >
                      {m.skill_id}
                    </button>
                  </td>
                  <td>{m.p50_ms.toFixed(2)}</td>
                  <td>{m.p95_ms.toFixed(2)}</td>
                  <td>{m.p99_ms.toFixed(2)}</td>
                  <td>{m.sample_count.toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Interpretation Guide */}
      <div style={{
        marginTop: '20px',
        padding: '12px 16px',
        backgroundColor: 'rgba(12, 163, 12, 0.05)',
        border: '1px solid var(--viz-gridline)',
        borderRadius: '6px',
        fontSize: '12px',
        color: 'var(--text-secondary)'
      }}>
        <p style={{ margin: '0 0 8px', fontWeight: 500 }}>💡 Interpretation:</p>
        <ul style={{ margin: 0, paddingLeft: '20px' }}>
          <li>p50 (green): Median latency — most typical execution time</li>
          <li>p95 (amber): 95th percentile — slow outliers start here</li>
          <li>p99 (red): 99th percentile — extreme outliers; may indicate bottlenecks</li>
        </ul>
      </div>
    </div>
  );
};

export default SkillLatencyChart;
