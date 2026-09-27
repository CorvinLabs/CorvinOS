/**
 * Skill Feedback Volume & Ratio Chart (ADR-0722)
 *
 * Form: Stacked bar (skill on x, feedback count on y, up/down segments)
 * Color: Diverging (emerald for up, rose for down)
 *
 * Dataviz Rules:
 * - Stacked bar with status colors
 * - Trend line overlay (optional, secondary y-axis)
 * - Hover tooltips
 * - Table fallback
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
} from 'recharts';
import { AlertCircle } from 'lucide-react';
import styles from '../skills_observability.module.css';

export interface SkillFeedbackMetric {
  skill_id: string;
  thumbs_up: number;
  thumbs_down: number;
  ratio_percent: number;
  trend_7d: string;
  limited_data?: boolean;
}

export interface SkillFeedbackChartProps {
  data?: { feedback: SkillFeedbackMetric[] };
  timeRange: '1d' | '7d' | '30d';
  selectedSkill?: string | null;
  onSkillSelect?: (skillId: string | null) => void;
}

/**
 * Custom tooltip for feedback chart
 */
const CustomFeedbackTooltip: React.FC<any> = ({ active, payload }) => {
  if (!active || !payload || payload.length === 0) return null;

  const data = payload[0].payload;

  return (
    <div style={{
      backgroundColor: 'var(--surface)',
      border: '1px solid var(--viz-gridline)',
      borderRadius: '6px',
      padding: '8px 12px'
    }}>
      <p style={{ margin: '0 0 6px', fontWeight: 600, color: 'var(--text-primary)' }}>
        {data.skill_id}
      </p>
      <p style={{ margin: '4px 0', fontSize: '12px', color: 'var(--viz-status-good)' }}>
        👍 Thumbs Up: {data.thumbs_up}
      </p>
      <p style={{ margin: '4px 0', fontSize: '12px', color: 'var(--viz-status-critical)' }}>
        👎 Thumbs Down: {data.thumbs_down}
      </p>
      <p style={{ margin: '4px 0', fontSize: '12px', fontWeight: 500, color: 'var(--text-secondary)' }}>
        Ratio: {data.ratio_percent.toFixed(1)}% positive
      </p>
      {data.limited_data && (
        <p style={{ margin: '4px 0', fontSize: '11px', color: 'var(--text-muted)', fontStyle: 'italic' }}>
          Limited data ({data.thumbs_up + data.thumbs_down} events)
        </p>
      )}
    </div>
  );
};

/**
 * SkillFeedbackChart Component
 */
export const SkillFeedbackChart: React.FC<SkillFeedbackChartProps> = ({
  data,
  timeRange,
  selectedSkill,
  onSkillSelect
}) => {
  const [showTable, setShowTable] = useState(false);

  if (!data || !data.feedback || data.feedback.length === 0) {
    return (
      <div className={styles.emptyState}>
        <AlertCircle size={32} />
        <p>No feedback data available</p>
      </div>
    );
  }

  const feedback = data.feedback.slice(0, 8); // Cap at 8 skills

  // Prepare chart data
  const chartData = feedback.map(f => ({
    skill_id: f.skill_id,
    thumbs_up: f.thumbs_up,
    thumbs_down: f.thumbs_down,
    ratio_percent: f.ratio_percent,
    limited_data: f.limited_data || false,
    total: f.thumbs_up + f.thumbs_down
  }));

  // Calculate averages
  const avgRatio = feedback.reduce((sum, f) => sum + f.ratio_percent, 0) / feedback.length;
  const totalFeedback = feedback.reduce((sum, f) => sum + f.thumbs_up + f.thumbs_down, 0);
  const totalThumbsUp = feedback.reduce((sum, f) => sum + f.thumbs_up, 0);

  return (
    <div className={styles.chartContainer}>
      <h3 className={styles.chartTitle}>Feedback Volume & Ratio</h3>
      <p className={styles.chartSubtitle}>
        Thumbs up (green) vs thumbs down (red) feedback count per skill. Higher ratio = more reliable skill.
      </p>

      {/* Statistics Summary */}
      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))',
        gap: '16px',
        marginBottom: '24px',
        padding: '16px',
        backgroundColor: 'var(--surface)',
        borderRadius: '6px',
        border: '1px solid var(--viz-gridline)'
      }}>
        <div>
          <p style={{ margin: '0 0 4px', fontSize: '12px', color: 'var(--text-secondary)' }}>
            Total Feedback
          </p>
          <p style={{ margin: 0, fontSize: '20px', fontWeight: 600, color: 'var(--text-primary)' }}>
            {totalFeedback.toLocaleString()}
          </p>
        </div>
        <div>
          <p style={{ margin: '0 0 4px', fontSize: '12px', color: 'var(--text-secondary)' }}>
            Thumbs Up
          </p>
          <p style={{ margin: 0, fontSize: '20px', fontWeight: 600, color: 'var(--viz-status-good)' }}>
            {totalThumbsUp.toLocaleString()}
          </p>
        </div>
        <div>
          <p style={{ margin: '0 0 4px', fontSize: '12px', color: 'var(--text-secondary)' }}>
            Average Ratio
          </p>
          <p style={{ margin: 0, fontSize: '20px', fontWeight: 600, color: 'var(--viz-seq-blue-400)' }}>
            {avgRatio.toFixed(1)}%
          </p>
        </div>
      </div>

      {/* Chart */}
      <ResponsiveContainer width="100%" height={400}>
        <BarChart data={chartData} margin={{ top: 20, right: 30, left: 0, bottom: 60 }}>
          <CartesianGrid strokeDasharray="4" stroke="var(--viz-gridline)" vertical={false} />
          <XAxis
            dataKey="skill_id"
            angle={-45}
            textAnchor="end"
            height={100}
            tick={{ fill: 'var(--text-secondary)', fontSize: 12 }}
          />
          <YAxis
            label={{ value: 'Feedback Events', angle: -90, position: 'insideLeft', offset: 10 }}
            tick={{ fill: 'var(--text-secondary)', fontSize: 12 }}
          />
          <Tooltip content={<CustomFeedbackTooltip />} />
          <Legend wrapperStyle={{ paddingTop: '20px' }} />

          {/* Stacked bars: thumbs up (good) + thumbs down (critical) */}
          <Bar
            dataKey="thumbs_up"
            stackId="feedback"
            fill="var(--viz-status-good)"
            name="👍 Thumbs Up"
            radius={[0, 0, 4, 4]}
          />
          <Bar
            dataKey="thumbs_down"
            stackId="feedback"
            fill="var(--viz-status-critical)"
            name="👎 Thumbs Down"
            radius={[4, 4, 0, 0]}
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

      {/* Table Fallback */}
      {showTable && (
        <div className={styles.tableFallback}>
          <table className={styles.tableContainer}>
            <thead>
              <tr>
                <th>Skill ID</th>
                <th>Thumbs Up</th>
                <th>Thumbs Down</th>
                <th>Ratio (%)</th>
                <th>Data Quality</th>
              </tr>
            </thead>
            <tbody>
              {feedback.map((f) => (
                <tr key={f.skill_id}>
                  <td>
                    <button
                      onClick={() => onSkillSelect?.(selectedSkill === f.skill_id ? null : f.skill_id)}
                      style={{
                        background: 'none',
                        border: 'none',
                        color: 'var(--viz-seq-blue-400)',
                        cursor: 'pointer',
                        textDecoration: selectedSkill === f.skill_id ? 'underline' : 'none'
                      }}
                    >
                      {f.skill_id}
                    </button>
                  </td>
                  <td style={{ color: 'var(--viz-status-good)', fontWeight: 500 }}>
                    {f.thumbs_up}
                  </td>
                  <td style={{ color: 'var(--viz-status-critical)', fontWeight: 500 }}>
                    {f.thumbs_down}
                  </td>
                  <td>{f.ratio_percent.toFixed(1)}%</td>
                  <td>
                    {f.limited_data ? '⚠️ Limited' : '✓ Sufficient'}
                  </td>
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
          <li>Ratio: % of positive feedback (up / total)</li>
          <li>Limited Data: &lt; 10 feedback events — treat confidence scores with caution</li>
          <li>Trend: 7-day change in ratio (positive = improving, negative = declining)</li>
        </ul>
      </div>
    </div>
  );
};

export default SkillFeedbackChart;
