import { describe, it, expect } from 'vitest';
import { buildOffsets, indexAt, windowRange } from '@/lib/virtual-window';

const keys = (n: number) => Array.from({ length: n }, (_, i) => `k${i}`);

describe('buildOffsets', () => {
  it('uses measured heights where known and the estimate elsewhere', () => {
    const offsets = buildOffsets(['a', 'b', 'c'], new Map([['b', 50]]), 100);
    expect(Array.from(offsets)).toEqual([0, 100, 150, 250]);
  });

  it('is a single zero for an empty list', () => {
    expect(Array.from(buildOffsets([], new Map(), 100))).toEqual([0]);
  });
});

describe('indexAt', () => {
  const offsets = Float64Array.from([0, 100, 150, 250]);

  it('returns the row whose top edge is at or above y', () => {
    expect(indexAt(offsets, 0)).toBe(0);
    expect(indexAt(offsets, 99)).toBe(0);
    expect(indexAt(offsets, 100)).toBe(1);
    expect(indexAt(offsets, 200)).toBe(2);
  });

  it('clamps to the last row past the end and to row 0 above the start', () => {
    expect(indexAt(offsets, -40)).toBe(0);
    expect(indexAt(offsets, 10_000)).toBe(2);
    expect(indexAt(Float64Array.from([0]), 5)).toBe(0);
  });
});

describe('windowRange', () => {
  it('mounts only the rows around the viewport, independent of the list length', () => {
    const small = buildOffsets(keys(100), new Map(), 120);
    const large = buildOffsets(keys(100_000), new Map(), 120);
    const a = windowRange(small, 5_000, 800, 1000);
    const b = windowRange(large, 5_000, 800, 1000);
    expect(a).toEqual(b);
    expect(b.end - b.start).toBeLessThanOrEqual(30);
  });

  it('covers the viewport and the overscan band', () => {
    const offsets = buildOffsets(keys(1000), new Map(), 100);
    const r = windowRange(offsets, 2_000, 500, 200);
    // rows whose box intersects [1800, 2700)
    expect(r.start).toBe(18);
    expect(r.end).toBe(28);
  });

  it('returns an empty range for an empty list', () => {
    expect(windowRange(buildOffsets([], new Map(), 100), 0, 800, 1000)).toEqual({ start: 0, end: 0 });
  });

  it('reaches the last row when scrolled to the bottom', () => {
    const offsets = buildOffsets(keys(50), new Map(), 100);
    const r = windowRange(offsets, 5_000 - 800, 800, 0);
    expect(r.end).toBe(50);
    expect(r.start).toBeLessThan(r.end);
  });
});
