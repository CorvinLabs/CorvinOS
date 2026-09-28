/**
 * Autonomous Skill Forge — StatusDisplay + the encoding module it renders from.
 * The canary shows real per-variant ratings; it never shows latency / error
 * rate / confidence, which no source on this build measures.
 */
import React from 'react';
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import StatusDisplay from '@/components/forge/StatusDisplay';
import { formatScore, outcomeSeries, verdictLabel } from '@/components/forge/autonomous-forge-encoding';
import type { CanaryView, MetricPoint } from '@/lib/api/autonomous-forge';

const canary: CanaryView = {
  skill_id: 'assistant.check_json_syntax',
  canary_id: 'abc123',
  status: 'canary',
  traffic_percent: 10,
  source: 'autopilot',
  trigger: { reason: 'outcome_mean_below_threshold', live_mean: 0.1, live_n: 3 },
  quality: 0.8,
  findings: [],
  created_at: 1_790_000_000,
  updated_at: 1_790_000_100,
  stats: {
    live: { outcome_n: 6, outcome_mean: 0.1, usage_n: 2, served_n: 40 },
    candidate: { outcome_n: 6, outcome_mean: 0.9, usage_n: 1, served_n: 5 },
  },
  reference: { mean: 0.1, source: 'live_during_canary' },
  verdict: { decision: 'escalate', reason: 'candidate 0.90 >= reference 0.10' },
  gates: { min_samples: 5, rollback_margin: 0.15, traffic_steps: [10, 25, 50] },
};

describe('StatusDisplay', () => {
  it('says so when no canary runs', () => {
    render(<StatusDisplay canary={null} />);
    expect(screen.getByText('No active canary')).toBeInTheDocument();
  });

  it('shows traffic, per-variant ratings and the gate verdict', () => {
    render(<StatusDisplay canary={canary} />);
    expect(screen.getByText('10%')).toBeInTheDocument();
    expect(screen.getByText('0.90')).toBeInTheDocument();
    expect(screen.getByTestId('canary-verdict')).toHaveTextContent('next step 25% traffic');
    expect(screen.queryByText(/latency/i)).toBeNull();
    expect(screen.queryByText(/error rate/i)).toBeNull();
  });
});

describe('encoding', () => {
  it('formats a missing measurement as a dash, never as 0', () => {
    expect(formatScore(null)).toBe('—');
    expect(formatScore(0)).toBe('0.00');
  });

  it('names the approval step at the top of the ladder', () => {
    expect(verdictLabel({ ...canary, traffic_percent: 50, verdict: { decision: 'ready', reason: '' } }))
      .toMatch(/approve/);
  });

  it('indexes each variant by its own rating number and drops usage grades', () => {
    const p = (variant: 'live' | 'candidate', kind: 'outcome' | 'usage', mean: number | null): MetricPoint =>
      ({ ts: 0, variant, kind, score: 0, outcome_mean: mean, outcome_n: 0 });
    const rows = outcomeSeries([
      p('live', 'outcome', 0.1), p('candidate', 'usage', null),
      p('candidate', 'outcome', 0.9), p('live', 'outcome', 0.5),
    ]);
    expect(rows).toEqual([{ n: 1, live: 0.1, candidate: 0.9 }, { n: 2, live: 0.5 }]);
  });
});

describe('paused canary', () => {
  it('shows 0% served while paused and says how to continue', () => {
    render(<StatusDisplay canary={{ ...canary, status: 'paused', verdict: { decision: 'hold', reason: 'canary is paused' } }} />);
    expect(screen.getByText('0%')).toBeInTheDocument();
    expect(screen.getByText(/10% when resumed/)).toBeInTheDocument();
    expect(screen.getByTestId('canary-verdict')).toHaveTextContent(/Resume/);
  });
});

describe('effectiveTraffic', () => {
  it('matches what the backend serves: running and ready serve, paused and finished do not', async () => {
    const { effectiveTraffic } = await import('@/components/forge/autonomous-forge-encoding');
    expect(effectiveTraffic({ ...canary, status: 'canary' })).toBe(10);
    expect(effectiveTraffic({ ...canary, status: 'ready' })).toBe(10);
    expect(effectiveTraffic({ ...canary, status: 'paused' })).toBe(0);
    expect(effectiveTraffic({ ...canary, status: 'approved' })).toBe(0);
  });
});
