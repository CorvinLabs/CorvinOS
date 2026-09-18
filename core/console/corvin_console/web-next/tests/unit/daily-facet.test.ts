/**
 * Daily-series facet encodings (2026-09-19) — the pure mapping from a row
 * with nulls to what the chart draws. Live finding: the worker facet drew a
 * flat $0.00 for 2026-09-16..18 (no delegated run existed on those days) next
 * to a real $1.79 on the 15th, because the route zero-filled the worker half
 * on every OS-day and the area chart connected the zeros.
 */
import { describe, expect, it } from 'vitest';
import { dailyDomainMax, dailyFacetShape, dayGapNote, type DailyPoint } from '@/panels/cost-viz';

const rows: DailyPoint[] = [
  { date: '2026-09-15', actual_usd: 14.3, baseline_usd: 71.5, counted_turns: 67, total_turns: 70,
    acs_actual_usd: 1.7939, acs_baseline_usd: 3.1044, acs_counted_turns: 9, acs_total_turns: 9 },
  { date: '2026-09-16', actual_usd: 22.7, baseline_usd: 113.7, counted_turns: 141, total_turns: 142,
    acs_actual_usd: null, acs_baseline_usd: null, acs_counted_turns: 0, acs_total_turns: 0 },
  { date: '2026-09-17', actual_usd: 11.1, baseline_usd: 55.5, counted_turns: 79, total_turns: 79,
    acs_actual_usd: null, acs_baseline_usd: null, acs_counted_turns: 0, acs_total_turns: 3 },
  { date: '2026-09-18', actual_usd: 14.6, baseline_usd: 73.0, counted_turns: 84, total_turns: 87,
    acs_actual_usd: null, acs_baseline_usd: null, acs_counted_turns: 0, acs_total_turns: 0 },
];

describe('dailyFacetShape — the form degrades per facet', () => {
  it('OS: four priced days → area; worker: one priced day → bars', () => {
    expect(dailyFacetShape(rows, 'actual_usd')).toEqual({ pricedDays: 4, totalDays: 4, mode: 'area' });
    expect(dailyFacetShape(rows, 'acs_actual_usd')).toEqual({ pricedDays: 1, totalDays: 4, mode: 'bars' });
  });
  it('a 0.0 value is priced (a measured zero), a null is not', () => {
    expect(dailyFacetShape([{ date: 'd', v: 0 }], 'v').pricedDays).toBe(1);
    expect(dailyFacetShape([{ date: 'd', v: null }], 'v').pricedDays).toBe(0);
  });
});

describe('dailyDomainMax — one domain, nulls are gaps', () => {
  it('takes the peak over both facets and ignores nulls', () => {
    const max = dailyDomainMax(rows, ['actual_usd', 'baseline_usd', 'acs_actual_usd', 'acs_baseline_usd']);
    expect(max).toBeCloseTo(113.7 * 1.1, 6);
  });
  it('keeps an all-null window renderable', () => {
    expect(dailyDomainMax([{ date: 'd', v: null }], ['v'])).toBeCloseTo(0.0001 * 1.1, 10);
  });
});

describe('dayGapNote — why there is no mark', () => {
  it('distinguishes "nothing recorded" from "recorded, unpriced"', () => {
    expect(dayGapNote(0, 0)).toBe('no run recorded');
    expect(dayGapNote(0, 3)).toBe('3 runs, none with token data — not measured');
    expect(dayGapNote(0, 1, 'turn')).toBe('1 turn, none with token data — not measured');
    expect(dayGapNote(9, 9)).toBeNull();
  });
});
