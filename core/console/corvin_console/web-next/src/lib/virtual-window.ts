/**
 * Pure geometry for a windowed (virtualised) vertical list with dynamic row
 * heights. No DOM access here: callers pass the measured/estimated heights in
 * and get index ranges and offsets out, so the maths is unit-testable.
 *
 * Offsets are relative to the first row's top edge. `offsets[i]` is the top of
 * row `i`; `offsets[n]` is the total height of all `n` rows.
 */

/** Prefix sums of row heights: `offsets.length === keys.length + 1`. */
export function buildOffsets(
  keys: readonly string[],
  measured: ReadonlyMap<string, number>,
  estimate: number,
): Float64Array {
  const n = keys.length;
  const offsets = new Float64Array(n + 1);
  for (let i = 0; i < n; i++) {
    offsets[i + 1] = offsets[i] + (measured.get(keys[i]) ?? estimate);
  }
  return offsets;
}

/**
 * Largest row index `i` (within `[0, n-1]`) whose top edge is at or above `y`.
 * Returns 0 for an empty list or a `y` above the first row.
 */
export function indexAt(offsets: ArrayLike<number>, y: number): number {
  const n = offsets.length - 1;
  if (n <= 0) return 0;
  let lo = 0;
  let hi = n - 1;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (offsets[mid] <= y) lo = mid;
    else hi = mid - 1;
  }
  return lo;
}

export interface RowRange {
  /** First mounted row (inclusive). */
  start: number;
  /** One past the last mounted row (exclusive). */
  end: number;
}

/**
 * Rows intersecting the viewport `[top, top + viewport)` widened by `overscan`
 * pixels on both sides. `top` is relative to the first row's top edge.
 */
export function windowRange(
  offsets: ArrayLike<number>,
  top: number,
  viewport: number,
  overscan: number,
): RowRange {
  const n = offsets.length - 1;
  if (n <= 0) return { start: 0, end: 0 };
  const start = indexAt(offsets, Math.max(0, top - overscan));
  const end = Math.min(n, indexAt(offsets, top + viewport + overscan) + 1);
  return { start, end: Math.max(start + 1, end) };
}
