/**
 * Encoding functions for the cost charts — ADR-0761.
 *
 * Extracted from `ModelCostOptimizer.tsx` so the mapping from NUMBER to MARK is
 * testable on its own. That mapping is where a chart lies: the rendered SVG can
 * be inspected by eye, but "a model priced above the reference is labelled
 * Referenz" is invisible until a tenant actually runs one. See
 * `tests/unit/cost-viz.test.ts`.
 */

/** Short model label: "claude-haiku-4-5-20251001" -> "haiku-4-5". */
export const shortModel = (id: string): string =>
  id.replace(/^claude-/, '').replace(/-\d{8}$/, '');

/**
 * Price tier of a Claude model, 0 = cheapest, capped at the ramp's last step.
 *
 * Drives the ORDINAL colour ramp, so darker literally means more expensive.
 * Derived from the family name rather than from the measured cost on purpose:
 * colouring a bar darker-because-bigger would double-encode bar length as hue
 * and burn the only free channel on information the bar already shows.
 *
 * Matches on the family substring so a Bedrock/Vertex routing prefix
 * (`eu.anthropic.claude-sonnet-5`) resolves to the same tier as the bare id —
 * on a platform host that spelling is what the engine is actually given, and a
 * lookup that missed it would paint the whole install one shade.
 *
 * An unknown family takes the middle step. Never an invented hue: a generated
 * colour is indistinguishable from an existing one under CVD.
 */
const TIER_ORDER = ['haiku', 'sonnet', 'opus', 'fable', 'mythos'];
const TIER_STEPS = 3;

export const tierOf = (id: string): number => {
  const i = TIER_ORDER.findIndex((t) => id.includes(t));
  return i < 0 ? 1 : Math.min(i, TIER_STEPS - 1);
};

export const TIER_VAR = ['var(--viz-tier-1)', 'var(--viz-tier-2)', 'var(--viz-tier-3)'];

export const usd = (n: number): string => `$${n.toFixed(n < 1 ? 4 : 2)}`;

/**
 * The direct label on a per-model bar. THREE outcomes, not two.
 *
 * The pricing table carries families priced ABOVE the Opus reference (Fable,
 * Mythos), so the saving can be negative — and a two-branch formatter falls
 * through to "Referenz" for exactly those, labelling the most expensive turn on
 * the install as the neutral baseline. The floating bar has a positive length
 * either way, so only the sign says which side of the reference it sits on.
 */
export const savingLabel = (savedPct: number): string =>
  savedPct > 0.05
    ? `−${savedPct.toFixed(0)}%`
    : savedPct < -0.05
      ? `+${Math.abs(savedPct).toFixed(0)}% costlier`
      : 'reference';

export interface ModelCostRow {
  model: string;
  label: string;
  actual: number;
  baseline: number;
  /**
   * Floating-bar geometry: an invisible bar to `lo`, then the visible `span`.
   * The visible span IS the saving, which is what makes the gap readable as a
   * quantity rather than as a gap between two separate bars. `span` is always
   * positive — a negative length renders nothing at all.
   */
  lo: number;
  span: number;
  savedPct: number;
  turns: number;
  tier: number;
}

export type ModelCostMap = Record<
  string,
  { actual_usd: number; baseline_usd: number; turns: number }
>;

export function toModelRows(costs: ModelCostMap | undefined): ModelCostRow[] {
  return Object.entries(costs || {})
    .map(([model, v]) => ({
      model,
      label: shortModel(model),
      actual: v.actual_usd,
      baseline: v.baseline_usd,
      lo: Math.min(v.actual_usd, v.baseline_usd),
      span: Math.abs(v.baseline_usd - v.actual_usd),
      // A $0 baseline means nothing was measured, not that everything was
      // saved. Reporting 0 keeps the row on the chart and off the headline.
      savedPct: v.baseline_usd > 0 ? (1 - v.actual_usd / v.baseline_usd) * 100 : 0,
      turns: v.turns,
      tier: tierOf(model),
    }))
    // Most expensive first: the row an operator acts on is at the top.
    .sort((a, b) => b.actual - a.actual);
}

/**
 * ONE axis domain across BOTH facets.
 *
 * Small multiples with independent scales are the trap this closes: two charts
 * side by side, same unit, same visual bar length, silently different axes — a
 * reader compares the pictures and draws a conclusion the numbers do not
 * support. One domain means a facet that is genuinely small LOOKS small, which
 * is the point rather than a cost.
 *
 * The floor keeps an all-zero window renderable: a [0, 0] domain draws no axis
 * at all, which reads as a broken panel rather than as an empty one.
 */
export function sharedDomainMax(rows: ModelCostRow[], headroom = 1.12): number {
  const peak = rows.reduce((m, r) => Math.max(m, r.actual, r.baseline), 0);
  return Math.max(peak, 0.0001) * headroom;
}

// ── Daily series: per-facet shape and gaps (2026-09-19) ─────────────────

/**
 * One day of the daily series. A series with no PRICED turn on a date carries
 * `null` there — the route sends an unmeasured day as absent, never as a zero
 * (ADR-0763). The two counts say why: 0/0 = no run recorded, 0/N = N runs,
 * none with token data. Until 2026-09-19 the route filled the worker half
 * with 0.0 on every OS-day, and the worker facet drew a flat $0.00 for three
 * days on which no delegated run existed.
 */
/** Any row with a date; the series columns are read by key so the pinned
 *  `CostDayPoint` interface (no index signature) fits without a cast. */
export type DailyPoint = { date: string };

const col = (p: DailyPoint, key: string): unknown => (p as unknown as Record<string, unknown>)[key];

export type DayMode = 'bars' | 'area';

export interface DailyFacetShape {
  /** Days on which THIS series has a priced value. */
  pricedDays: number;
  totalDays: number;
  /** ADR-0761: below two points a line or area draws nothing while still
   *  being titled "Trend" — the form degrades PER FACET, not per page: the OS
   *  series can span four days while the worker series has one. */
  mode: DayMode;
}

const isPriced = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);

export function dailyFacetShape(points: DailyPoint[], actualKey: string): DailyFacetShape {
  const pricedDays = points.filter((p) => isPriced(col(p, actualKey))).length;
  return { pricedDays, totalDays: points.length, mode: pricedDays < 2 ? 'bars' : 'area' };
}

/**
 * ONE domain for both daily facets (same rule as `sharedDomainMax`), over the
 * PRICED values only — a null is a gap, not a zero, and must not pull the
 * floor. Headroom so the top mark does not touch the frame.
 */
export function dailyDomainMax(points: DailyPoint[], keys: string[], headroom = 1.1): number {
  let peak = 0;
  for (const p of points) for (const k of keys) {
    const v = col(p, k);
    if (isPriced(v)) peak = Math.max(peak, v);
  }
  return Math.max(peak, 0.0001) * headroom;
}

/** Why a day has no mark in a facet — rendered in its tooltip, never as $0. */
export function dayGapNote(counted: number, total: number, noun = 'run'): string | null {
  if (counted > 0) return null;
  if (total === 0) return `no ${noun} recorded`;
  return `${total} ${total === 1 ? noun : noun + 's'}, none with token data — not measured`;
}
