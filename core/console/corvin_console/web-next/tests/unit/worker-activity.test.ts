/**
 * Worker activity timeline (2026-09-22). The cost facet has a point only
 * where a run was priced — one day on the live tenant — so it cannot show
 * WHEN delegation happened. Run counts are measured regardless of tokens.
 */
import { describe, expect, it } from 'vitest';
import { fillActivityDays } from '@/panels/cost-viz';

describe('fillActivityDays', () => {
  it('fills every day between the first recorded day and the end date', () => {
    const bars = fillActivityDays(
      [
        { date: '2026-09-06', priced_runs: 0, total_runs: 1 },
        { date: '2026-09-09', priced_runs: 9, total_runs: 9 },
      ],
      '2026-09-10',
    );
    expect(bars.map((b) => b.date)).toEqual([
      '2026-09-06', '2026-09-07', '2026-09-08', '2026-09-09', '2026-09-10',
    ]);
    expect(bars[0]).toEqual({ date: '2026-09-06', priced: 0, unpriced: 1 });
    expect(bars[1]).toEqual({ date: '2026-09-07', priced: 0, unpriced: 0 });
    expect(bars[3]).toEqual({ date: '2026-09-09', priced: 9, unpriced: 0 });
  });

  it('splits a day into priced and unpriced runs that sum to the total', () => {
    const [b] = fillActivityDays([{ date: '2026-07-27', priced_runs: 3, total_runs: 181 }], '2026-07-27');
    expect(b.priced + b.unpriced).toBe(181);
    expect(b.unpriced).toBe(178);
  });

  it('crosses a month boundary without skipping or repeating a day', () => {
    const bars = fillActivityDays([{ date: '2026-07-30', priced_runs: 0, total_runs: 2 }], '2026-08-02');
    expect(bars.map((b) => b.date)).toEqual(['2026-07-30', '2026-07-31', '2026-08-01', '2026-08-02']);
  });

  it('draws nothing when no run was recorded', () => {
    expect(fillActivityDays([], '2026-09-22')).toEqual([]);
  });

  it('never cuts off a recorded day that lies after the end date', () => {
    const bars = fillActivityDays([{ date: '2026-09-23', priced_runs: 1, total_runs: 1 }], '2026-09-22');
    expect(bars.map((b) => b.date)).toEqual(['2026-09-23']);
  });
});
