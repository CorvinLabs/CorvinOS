/**
 * Cost-visualisation encodings — ADR-0761.
 *
 * These test the two places where a chart can silently tell the reader
 * something false, both of which it DID before this suite existed:
 *
 *   1. A model priced ABOVE the reference produces a negative saving, and the
 *      two-branch label formatter fell through to "Referenz" for it — labelling
 *      the most expensive turn on the install as the neutral baseline.
 *   2. Small multiples with independent scales: two facets, same unit, same
 *      visual bar length, silently different axes. The reader compares the
 *      pictures and draws a conclusion the numbers do not support.
 *
 * The encoding functions are pinned here rather than the rendered SVG: what
 * must not drift is the mapping from number to mark, and that is pure.
 */

import { describe, it, expect } from 'vitest';
import {
  shortModel,
  tierOf,
  toModelRows,
  savingLabel,
  sharedDomainMax,
} from '@/panels/cost-viz';

describe('shortModel', () => {
  it('drops the vendor prefix and the dated snapshot suffix', () => {
    expect(shortModel('claude-haiku-4-5-20251001')).toBe('haiku-4-5');
    expect(shortModel('claude-opus-5')).toBe('opus-5');
  });

  it('leaves an unrecognised id intact rather than mangling it', () => {
    expect(shortModel('eu.anthropic.claude-sonnet-5')).toBe('eu.anthropic.claude-sonnet-5');
  });
});

describe('tierOf — drives the ORDINAL colour ramp', () => {
  it('orders the families by price, cheapest first', () => {
    expect(tierOf('claude-haiku-4-5-20251001')).toBe(0);
    expect(tierOf('claude-sonnet-5')).toBe(1);
    expect(tierOf('claude-opus-5')).toBe(2);
  });

  it('recognises a Bedrock inference-profile id', () => {
    // On a Bedrock host this is the spelling the engine is actually given, and
    // a tier lookup that missed it would paint the whole install one shade.
    expect(tierOf('eu.anthropic.claude-sonnet-5')).toBe(1);
  });

  it('caps at the last ramp step instead of inventing a hue', () => {
    // Fable/Mythos sit above Opus in the price table; the ramp has three steps.
    expect(tierOf('claude-fable-5-1')).toBe(2);
    expect(tierOf('claude-mythos-5')).toBe(2);
  });

  it('gives an unknown family the middle step, never a generated colour', () => {
    expect(tierOf('some-future-model')).toBe(1);
  });
});

describe('savingLabel — three outcomes, not two', () => {
  it('labels a cheaper model with the saving', () => {
    expect(savingLabel(80)).toBe('−80%');
  });

  it('labels the reference model as the reference', () => {
    expect(savingLabel(0)).toBe('reference');
  });

  it('labels a model priced ABOVE the reference as more expensive', () => {
    // The regression this file exists for: -23 is not 0, and must not read as
    // "reference".
    expect(savingLabel(-23)).toBe('+23% costlier');
    expect(savingLabel(-23)).not.toContain('reference');
  });

  it('treats sub-rounding noise as the reference, in both directions', () => {
    expect(savingLabel(0.01)).toBe('reference');
    expect(savingLabel(-0.01)).toBe('reference');
  });
});

describe('toModelRows', () => {
  const costs = {
    'claude-haiku-4-5-20251001': { actual_usd: 0.1, baseline_usd: 0.5, turns: 2 },
    'claude-opus-5': { actual_usd: 0.73, baseline_usd: 0.73, turns: 2 },
    'claude-sonnet-5': { actual_usd: 0.3, baseline_usd: 0.75, turns: 2 },
  };

  it('sorts most-expensive-first so the actionable row is on top', () => {
    expect(toModelRows(costs).map((r) => r.label)).toEqual([
      'opus-5', 'sonnet-5', 'haiku-4-5',
    ]);
  });

  it('makes the visible span the saving, anchored at the lower value', () => {
    const haiku = toModelRows(costs).find((r) => r.label === 'haiku-4-5')!;
    expect(haiku.lo).toBeCloseTo(0.1, 6);
    expect(haiku.span).toBeCloseTo(0.4, 6);
    expect(haiku.lo + haiku.span).toBeCloseTo(haiku.baseline, 6);
    expect(haiku.savedPct).toBeCloseTo(80, 6);
  });

  it('gives the reference model a zero span — the honest rendering of "saved nothing"', () => {
    const opus = toModelRows(costs).find((r) => r.label === 'opus-5')!;
    expect(opus.span).toBeCloseTo(0, 6);
    expect(opus.savedPct).toBeCloseTo(0, 6);
  });

  it('keeps the span positive when the model cost MORE than the reference', () => {
    // A negative-length bar renders nothing; the sign lives in the label.
    const rows = toModelRows({
      'claude-fable-5-1': { actual_usd: 1.0, baseline_usd: 0.5, turns: 1 },
    });
    expect(rows[0].span).toBeCloseTo(0.5, 6);
    expect(rows[0].lo).toBeCloseTo(0.5, 6);
    expect(rows[0].savedPct).toBeLessThan(0);
    expect(savingLabel(rows[0].savedPct)).toBe('+100% costlier');
  });

  it('reports no saving rather than dividing by zero on a $0 baseline', () => {
    const rows = toModelRows({ x: { actual_usd: 0, baseline_usd: 0, turns: 1 } });
    expect(rows[0].savedPct).toBe(0);
    expect(Number.isFinite(rows[0].savedPct)).toBe(true);
  });

  it('is empty for an absent map, never throws', () => {
    expect(toModelRows(undefined)).toEqual([]);
    expect(toModelRows({})).toEqual([]);
  });
});

describe('sharedDomainMax — the small-multiples trap', () => {
  it('spans BOTH facets so the two are directly comparable', () => {
    const os = toModelRows({ a: { actual_usd: 0.02, baseline_usd: 0.1, turns: 1 } });
    const worker = toModelRows({ b: { actual_usd: 1.13, baseline_usd: 1.99, turns: 6 } });
    const domain = sharedDomainMax([...os, ...worker]);
    // Driven by the worker facet's maximum, not by each facet's own.
    expect(domain).toBeGreaterThan(1.99);
    expect(domain).toBeLessThan(1.99 * 1.3);
  });

  it('lets a genuinely small facet look small', () => {
    const os = toModelRows({ a: { actual_usd: 0.02, baseline_usd: 0.1, turns: 1 } });
    const worker = toModelRows({ b: { actual_usd: 1.13, baseline_usd: 1.99, turns: 6 } });
    const domain = sharedDomainMax([...os, ...worker]);
    // The OS bar occupies a few percent of the axis. That is the fact, and the
    // shared domain is what lets the reader see it instead of seeing a
    // full-width bar on a hidden axis.
    expect(os[0].baseline / domain).toBeLessThan(0.1);
  });

  it('never returns zero, so an all-zero window still renders an axis', () => {
    expect(sharedDomainMax([])).toBeGreaterThan(0);
    expect(sharedDomainMax(toModelRows({ a: { actual_usd: 0, baseline_usd: 0, turns: 1 } })))
      .toBeGreaterThan(0);
  });
});
