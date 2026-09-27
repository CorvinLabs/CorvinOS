/**
 * Skill Confidence Trends Chart (ADR-0722)
 *
 * Form: Line chart (date on x, confidence % on y, one line per skill)
 * Color: Categorical (skill identity, fixed order)
 *
 * Dataviz Rules:
 * - 2px lines, square join
 * - Direct labels at line end (no legend box clutter)
 * - Hover crosshair + tooltip
 * - Table fallback for accessibility
 */

import React, { useState } from 'react';
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from 'recharts';
import { AlertCircle } from 'lucide-react';
import styles from '../skills_observability.module.css';

export interface ConfidenceDataPoint {
  date: string;
  confidence: number;
}

export interface SkillConfidenceTrendData {
  skill_id: string;
  data_points: ConfidenceDataPoint[];
}

export interface SkillConfidenceTrendsProps {
  data?: { trends: SkillConfidenceTrendData[] };
  timeRange: '1d' | '7d' | '30d';
  selectedSkill?: string | null;
  onSkillSelect?: (skillId: string | null) => void;
}

/**
 * Custom tooltip for confidence trends
 */
const CustomConfidenceTooltip: React.FC<any> = ({ active, payload, label }) => {
  if (!active || !payload) return null;

  return (
    <div style={{
      backgroundColor: 'var(--surface)',
      border: '1px solid var(--viz-gridline)',
      borderRadius: '6px',
      padding: '8px 12px'
    }}>
      <p style={{ margin: '0 0 6px', fontWeight: 600, color: 'var(--text-primary)' }}>
        {label}
      </p>
      {payload.map((entry: any, idx: number) => (
        <p key={idx} style={{ margin: '4px 0', fontSize: '12px', color: entry.color }}>
          {entry.name}: {(entry.value * 100).toFixed(1)}%
        </p>
      ))}
    </div>
  );
};

/**
 * SkillConfidenceTrends Component
 */
export const SkillConfidenceTrends: React.FC<SkillConfidenceTrendsProps> = ({
  data,
  timeRange,
  selectedSkill,
  onSkillSelect
}) => {
  const [showTable, setShowTable] = useState(false);

  if (!data || !data.trends || data.trends.length === 0) {
    return (
      <div className={styles.emptyState}>
        <AlertCircle size={32} />
        <p>No confidence trend data available</p>
      </div>
    );
  }

  const trends = data.trends.slice(0, 8); // Cap at 8 skills

  // Merge all data points into single array (one row per date)
  const allDates = new Set<string>();
  trends.forEach(t => t.data_points.forEach(dp => allDates.add(dp.date)));

  const chartData = Array.from(allDates).sort().map(date => {
    const row: any = { date };
    trends.forEach(t => {
      const dp = t.data_points.find(p => p.date === date);
      row[t.skill_id] = dp ? dp.confidence : null;
    });
    return row;
  });

  // Calculate average confidence per skill
  const skillStats = trends.map(t => ({
    skill_id: t.skill_id,
    avg: t.data_points.reduce((sum, dp) => sum + dp.confidence, 0) / t.data_points.length,
    latest: t.data_points[t.data_points.length - 1]?.confidence || 0,
    trend: t.data_points.length >= 3
      ? t.data_points[t.data_points.length - 1].confidence - t.data_points[t.data_points.length - 3].confidence
      : 0
  }));

  // Color palette (categorical, fixed order)
  const colors = [
    'var(--viz-cat-1-light)',
    'var(--viz-cat-2-light)',
    'var(--viz-cat-3-light)',
    'var(--viz-cat-4-light)',
    'var(--viz-cat-5-light)',
    'var(--viz-cat-6-light)',
    'var(--viz-cat-7-light)',
    'var(--viz-cat-8-light)',
  ];

  return (
    <div className={styles.chartContainer}>
      <h3 className={styles.chartTitle}>Confidence Trends</h3>
      <p className={styles.chartSubtitle}>
        7-day rolling average confidence per skill. Higher indicates more reliable skill decisions.
      </p>

      {/* Statistics Summary */}
      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))',
        gap: '12px',
        marginBottom: '24px',
        overflowX: 'auto'
      }}>
        {skillStats.map((stat, idx) => (
          <div
            key={stat.skill_id}
            onClick={() => onSkillSelect?.(selectedSkill === stat.skill_id ? null : stat.skill_id)}
            style={{
              padding: '12px',
              backgroundColor: 'var(--surface)',
              borderRadius: '6px',
              border: `1px solid ${selectedSkill === stat.skill_id ? colors[idx] : 'var(--viz-gridline)'}`,
              cursor: 'pointer',
              transition: 'all 0.2s'
            }}
          >
            <p style={{ margin: '0 0 4px', fontSize: '11px', color: 'var(--text-secondary)' }}>
              {stat.skill_id}
            </p>
            <p style={{ margin: '0 0 6px', fontSize: '18px', fontWeight: 600, color: colors[idx] }}>
              {(stat.latest * 100).toFixed(0)}%
            </p>
            <p style={{ margin: 0, fontSize: '11px', color: stat.trend > 0 ? 'var(--viz-status-good)' : 'var(--text-secondary)' }}>
              {stat.trend > 0 ? '↑' : stat.trend < 0 ? '↓' : '—'} {(stat.trend * 100).toFixed(1)}%
            </p>
          </div>
        ))}
      </div>

      {/* Chart */}
      <ResponsiveContainer width="100%" height={380}>
        <LineChart data={chartData} margin={{ top: 5, right: 30, left: 0, bottom: 5 }}>
          <CartesianGrid strokeDasharray="4" stroke="var(--viz-gridline)" vertical={false} />
          <XAxis
            dataKey="date"
            tick={{ fill: 'var(--text-secondary)', fontSize: 12 }}
          />
          <YAxis
            domain={[0, 1]}
            tickFormatter={(v) => `${(v * 100).toFixed(0)}%`}
            label={{ value: 'Confidence', angle: -90, position: 'insideLeft', offset: 10 }}
            tick={{ fill: 'var(--text-secondary)', fontSize: 12 }}
          />
          <Tooltip content={<CustomConfidenceTooltip />} />
          <Legend />

          {/* Lines: one per skill */}
          {trends.map((trend, idx) => (
            <Line
              key={trend.skill_id}
              type="monotone"
              dataKey={trend.skill_id}
              stroke={colors[idx]}
              dot={{ fill: colors[idx], r: 3 }}
              isAnimationActive={false}
              strokeWidth={2}
              name={trend.skill_id}
            />
          ))}
        </LineChart>
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
                <th>Date</th>
                {trends.map(t => <th key={t.skill_id}>{t.skill_id}</th>)}
              </tr>
            </thead>
            <tbody>
              {chartData.map((row) => (
                <tr key={row.date}>
                  <td>{row.date}</td>
                  {trends.map(t => (
                    <td key={t.skill_id}>
                      {row[t.skill_id] !== null ? `${(row[t.skill_id] * 100).toFixed(1)}%` : '—'}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
};

export default SkillConfidenceTrends;
