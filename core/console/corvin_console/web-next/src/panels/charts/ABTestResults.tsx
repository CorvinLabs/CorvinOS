/**
 * A/B Test Results Chart (ADR-0722)
 *
 * Form: Horizontal bars (experiment name on y, improvement % on x, centered at 0)
 * Color: Status palette (good=winner, warning=inconclusive, critical=regression)
 *
 * Dataviz Rules:
 * - Diverging bar centered at 0
 * - Error bars show 95% CI
 * - Status color + label (no color alone)
 * - Direct labels with outcome word
 */

import React, { useState } from 'react';
import { AlertCircle, TrendingUp, AlertTriangle, TrendingDown } from 'lucide-react';
import styles from '../skills_observability.module.css';

export interface ABTestResult {
  test_id: string;
  name: string;
  outcome: 'winner' | 'inconclusive' | 'regression';
  improvement_percent: number;
  lower_ci_percent: number;
  upper_ci_percent: number;
  control_skill: string;
  variant_skill: string;
  sample_size: number;
  started_at: string;
  status: 'active' | 'completed' | 'stopped';
}

export interface ABTestResultsProps {
  data?: ABTestResult[];
}

/**
 * Outcome icon & color helper
 */
const getOutcomeStyle = (outcome: string) => {
  switch (outcome) {
    case 'winner':
      return {
        color: 'var(--viz-status-good)',
        icon: <TrendingUp size={16} />,
        label: 'Winner',
        bgColor: 'rgba(12, 163, 12, 0.1)'
      };
    case 'inconclusive':
      return {
        color: 'var(--viz-status-warning)',
        icon: <AlertTriangle size={16} />,
        label: 'Inconclusive',
        bgColor: 'rgba(250, 178, 25, 0.1)'
      };
    case 'regression':
      return {
        color: 'var(--viz-status-critical)',
        icon: <TrendingDown size={16} />,
        label: 'Regression',
        bgColor: 'rgba(208, 59, 59, 0.1)'
      };
    default:
      return {
        color: 'var(--text-muted)',
        icon: null,
        label: 'Unknown',
        bgColor: 'transparent'
      };
  }
};

/**
 * Individual test card component
 */
const ABTestCard: React.FC<{ test: ABTestResult }> = ({ test }) => {
  const outcomeStyle = getOutcomeStyle(test.outcome);
  const width = Math.min(300, Math.abs(test.improvement_percent) * 3);
  const ciWidth = Math.abs(test.upper_ci_percent - test.lower_ci_percent) * 1.5;

  return (
    <div style={{
      display: 'flex',
      flexDirection: 'column',
      gap: '12px',
      padding: '16px',
      backgroundColor: outcomeStyle.bgColor,
      border: `1px solid ${outcomeStyle.color}`,
      borderRadius: '8px',
      marginBottom: '12px'
    }}>
      {/* Header: Name + Status Badge */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <h4 style={{
          margin: 0,
          fontSize: '14px',
          fontWeight: 600,
          color: 'var(--text-primary)',
          flex: 1
        }}>
          {test.name}
        </h4>
        <div style={{
          display: 'flex',
          alignItems: 'center',
          gap: '6px',
          padding: '4px 8px',
          backgroundColor: outcomeStyle.color,
          color: 'white',
          borderRadius: '4px',
          fontSize: '11px',
          fontWeight: 500,
          whiteSpace: 'nowrap',
          marginLeft: '12px'
        }}>
          {outcomeStyle.icon}
          {outcomeStyle.label}
        </div>
      </div>

      {/* Test Info Row */}
      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fit, minmax(120px, 1fr))',
        gap: '12px',
        fontSize: '12px',
        color: 'var(--text-secondary)'
      }}>
        <div>
          <p style={{ margin: '0 0 4px' }}>Control Skill</p>
          <code style={{ fontSize: '11px', color: 'var(--text-primary)' }}>
            {test.control_skill}
          </code>
        </div>
        <div>
          <p style={{ margin: '0 0 4px' }}>Variant Skill</p>
          <code style={{ fontSize: '11px', color: 'var(--text-primary)' }}>
            {test.variant_skill}
          </code>
        </div>
        <div>
          <p style={{ margin: '0 0 4px' }}>Sample Size</p>
          <p style={{ margin: 0, color: 'var(--text-primary)', fontWeight: 500 }}>
            {test.sample_size.toLocaleString()}
          </p>
        </div>
        <div>
          <p style={{ margin: '0 0 4px' }}>Started</p>
          <p style={{ margin: 0, fontSize: '11px' }}>
            {new Date(test.started_at).toLocaleDateString()}
          </p>
        </div>
      </div>

      {/* Diverging Bar Chart (ASCII for now, SVG in production) */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginTop: '8px' }}>
        {/* Left axis label */}
        <div style={{ textAlign: 'right', width: '60px', fontSize: '11px', color: 'var(--text-muted)' }}>
          {test.lower_ci_percent.toFixed(1)}%
        </div>

        {/* Diverging bar container */}
        <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '0' }}>
          {/* Regression side (left) */}
          {test.improvement_percent < 0 && (
            <div style={{
              width: `${width}px`,
              height: '24px',
              backgroundColor: outcomeStyle.color,
              borderRadius: '4px 0 0 4px',
              position: 'relative',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'flex-end',
              paddingRight: '8px',
              color: 'white',
              fontSize: '12px',
              fontWeight: 600
            }}>
              {test.improvement_percent.toFixed(1)}%
            </div>
          )}

          {/* Center baseline (zero) */}
          <div style={{
            width: '2px',
            height: '32px',
            backgroundColor: 'var(--text-muted)',
            margin: '0 8px'
          }} />

          {/* Improvement side (right) */}
          {test.improvement_percent > 0 && (
            <div style={{
              width: `${width}px`,
              height: '24px',
              backgroundColor: outcomeStyle.color,
              borderRadius: '0 4px 4px 0',
              position: 'relative',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'flex-start',
              paddingLeft: '8px',
              color: 'white',
              fontSize: '12px',
              fontWeight: 600
            }}>
              +{test.improvement_percent.toFixed(1)}%
            </div>
          )}

          {/* If no improvement detected */}
          {test.improvement_percent === 0 && (
            <div style={{
              fontSize: '12px',
              color: 'var(--text-secondary)',
              textAlign: 'center'
            }}>
              No significant difference
            </div>
          )}
        </div>

        {/* Right axis label */}
        <div style={{ textAlign: 'left', width: '60px', fontSize: '11px', color: 'var(--text-muted)' }}>
          {test.upper_ci_percent.toFixed(1)}%
        </div>
      </div>

      {/* Confidence Interval Note */}
      <p style={{
        margin: '8px 0 0',
        fontSize: '11px',
        color: 'var(--text-muted)',
        fontStyle: 'italic'
      }}>
        95% CI: [{test.lower_ci_percent.toFixed(1)}, {test.upper_ci_percent.toFixed(1)}]%
      </p>

      {/* Status Badge */}
      <div style{{
        fontSize: '11px',
        color: 'var(--text-secondary)',
        marginTop: '4px'
      }}>
        Status: <span style={{ fontWeight: 500 }}>
          {test.status === 'active' ? '🟢 Active' : test.status === 'completed' ? '✅ Completed' : '⏸️ Stopped'}
        </span>
      </div>
    </div>
  );
};

/**
 * ABTestResults Component
 */
export const ABTestResults: React.FC<ABTestResultsProps> = ({ data }) => {
  const [filterStatus, setFilterStatus] = useState<'all' | 'active' | 'completed'>('active');

  if (!data || data.length === 0) {
    return (
      <div className={styles.emptyState}>
        <AlertCircle size={32} />
        <p>No A/B tests available at this time</p>
        <p style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
          Tests will appear here once experiments are launched and data is collected
        </p>
      </div>
    );
  }

  // Filter by status
  const filtered = data.filter(test => {
    if (filterStatus === 'all') return true;
    return test.status === filterStatus;
  });

  // Sort by outcome (winners first)
  const sorted = [...filtered].sort((a, b) => {
    const outcomeOrder = { 'winner': 0, 'inconclusive': 1, 'regression': 2 };
    return outcomeOrder[a.outcome as keyof typeof outcomeOrder] - outcomeOrder[b.outcome as keyof typeof outcomeOrder];
  });

  // Statistics
  const winners = data.filter(t => t.outcome === 'winner').length;
  const inconclusive = data.filter(t => t.outcome === 'inconclusive').length;
  const regressions = data.filter(t => t.outcome === 'regression').length;

  return (
    <div className={styles.chartContainer}>
      <h3 className={styles.chartTitle}>A/B Test Results</h3>
      <p className={styles.chartSubtitle}>
        Active and completed experiments. Results show improvement % with 95% confidence intervals.
      </p>

      {/* Statistics Summary */}
      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))',
        gap: '12px',
        marginBottom: '20px'
      }}>
        <div style={{
          padding: '12px',
          backgroundColor: 'rgba(12, 163, 12, 0.1)',
          border: '1px solid var(--viz-status-good)',
          borderRadius: '6px',
          textAlign: 'center'
        }}>
          <p style={{ margin: '0 0 4px', fontSize: '12px', color: 'var(--text-secondary)' }}>
            Winners
          </p>
          <p style={{ margin: 0, fontSize: '24px', fontWeight: 600, color: 'var(--viz-status-good)' }}>
            {winners}
          </p>
        </div>

        <div style={{
          padding: '12px',
          backgroundColor: 'rgba(250, 178, 25, 0.1)',
          border: '1px solid var(--viz-status-warning)',
          borderRadius: '6px',
          textAlign: 'center'
        }}>
          <p style={{ margin: '0 0 4px', fontSize: '12px', color: 'var(--text-secondary)' }}>
            Inconclusive
          </p>
          <p style={{ margin: 0, fontSize: '24px', fontWeight: 600, color: 'var(--viz-status-warning)' }}>
            {inconclusive}
          </p>
        </div>

        <div style={{
          padding: '12px',
          backgroundColor: 'rgba(208, 59, 59, 0.1)',
          border: '1px solid var(--viz-status-critical)',
          borderRadius: '6px',
          textAlign: 'center'
        }}>
          <p style={{ margin: '0 0 4px', fontSize: '12px', color: 'var(--text-secondary)' }}>
            Regressions
          </p>
          <p style={{ margin: 0, fontSize: '24px', fontWeight: 600, color: 'var(--viz-status-critical)' }}>
            {regressions}
          </p>
        </div>
      </div>

      {/* Filter Buttons */}
      <div style={{ display: 'flex', gap: '8px', marginBottom: '20px' }}>
        {(['active', 'completed', 'all'] as const).map(status => (
          <button
            key={status}
            onClick={() => setFilterStatus(status)}
            style={{
              padding: '6px 12px',
              fontSize: '12px',
              fontWeight: 500,
              border: `1px solid ${filterStatus === status ? 'var(--viz-seq-blue-400)' : 'var(--viz-gridline)'}`,
              backgroundColor: filterStatus === status ? 'rgba(57, 135, 229, 0.1)' : 'transparent',
              color: filterStatus === status ? 'var(--viz-seq-blue-400)' : 'var(--text-secondary)',
              borderRadius: '4px',
              cursor: 'pointer',
              transition: 'all 0.2s'
            }}
          >
            {status.charAt(0).toUpperCase() + status.slice(1)}
          </button>
        ))}
      </div>

      {/* Test Results Cards */}
      <div>
        {sorted.length > 0 ? (
          sorted.map(test => (
            <ABTestCard key={test.test_id} test={test} />
          ))
        ) : (
          <div style={{ textAlign: 'center', padding: '24px', color: 'var(--text-secondary)' }}>
            No {filterStatus === 'all' ? '' : filterStatus} tests found
          </div>
        )}
      </div>

      {/* Legend */}
      <div style={{
        marginTop: '24px',
        padding: '16px',
        backgroundColor: 'var(--surface)',
        border: '1px solid var(--viz-gridline)',
        borderRadius: '6px'
      }}>
        <p style={{ margin: '0 0 12px', fontSize: '12px', fontWeight: 500, color: 'var(--text-primary)' }}>
          Legend:
        </p>
        <div style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))',
          gap: '12px',
          fontSize: '12px'
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <TrendingUp size={14} color="var(--viz-status-good)" />
            <span>Winner: significant improvement detected</span>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <AlertTriangle size={14} color="var(--viz-status-warning)" />
            <span>Inconclusive: CI crosses zero or insufficient data</span>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <TrendingDown size={14} color="var(--viz-status-critical)" />
            <span>Regression: significant decline detected</span>
          </div>
        </div>
      </div>
    </div>
  );
};

export default ABTestResults;
