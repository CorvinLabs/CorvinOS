/**
 * Encoding for the "Automatic model saving" settings card (ADR-2259).
 *
 * Kept out of the component on purpose: a wrong number→text mapping (a clamped
 * value shown as if it were in force, a ratio printed before the meter has any
 * data) is invisible in a screenshot until an operator hits the case.
 *
 * Nothing here knows the Free ceiling — it arrives from the server on every
 * response, so the number lives in exactly one place (the capability matrix).
 */

export interface CostOptimizeResponse {
  tier: "free" | "member";
  choice: { cap_ratio: number | null; floor_usd: number | null };
  ceiling: { ratio: number; floor_usd: number };
  effective: { ratio: number | null; floor_usd: number };
  clamped: boolean;
  state: { capped: boolean; reason: string };
  measured: {
    month: string;
    window: { unit: "utc_month"; month: string };
    ratio: number | null;
    saved_usd: number;
    counterfactual_usd: number;
    actual_usd: number;
    counted_turns: number;
    total_turns: number;
    default_model: string;
    priced: boolean;
  } | null;
  meta: Record<string, { min: number; max: number }>;
}

/** 0.07 → 7 (rounded to 2 decimals so 0.07 * 100 never shows 7.000000000000001). */
export function toPercent(ratio: number | null): number | null {
  if (ratio === null) return null;
  return Math.round(ratio * 10000) / 100;
}

/** 7 → 0.07, or `undefined` when the server would reject it. */
export function fromPercent(percent: number): number | undefined {
  if (!Number.isFinite(percent) || percent < 0 || percent > 100) return undefined;
  return Math.round(percent * 100) / 10000;
}

/** Largest value the input may take: Free stops at the server's ceiling. */
export function inputMaxPercent(r: CostOptimizeResponse): number {
  return r.tier === "member" ? 100 : (toPercent(r.ceiling.ratio) ?? 0);
}

export type Tone = "active" | "paused" | "off" | "measuring" | "unlimited";

export interface StateText {
  tone: Tone;
  headline: string;
  detail: string;
  window: string | null;
  reference: string | null;
  clampNotice: string | null;
}

const pct = (v: number | null | undefined, digits = 1): string =>
  v === null || v === undefined ? "—" : `${(Math.round(v * 100 * 10 ** digits) / 10 ** digits).toFixed(digits)} %`;

export function describeState(r: CostOptimizeResponse): StateText {
  const m = r.measured;
  const window = m ? `UTC month ${m.window.month}` : null;
  const reference = m ? `Compared with the default model ${m.default_model} at list prices.` : null;
  const effective = r.effective.ratio;

  let clampNotice: string | null = null;
  if (r.clamped && r.tier === "free") {
    clampNotice =
      `The Free tier is limited to ${pct(r.ceiling.ratio, 0)}; your stored value is higher, ` +
      `so ${pct(r.ceiling.ratio, 0)} is in force. A Member licence lifts this limit.`;
  }

  const measuredText = m && m.ratio !== null
    ? `Saved ${pct(m.ratio)} this month ($${m.saved_usd.toFixed(2)} of $${m.counterfactual_usd.toFixed(2)}).`
    : null;

  if (effective === 0) {
    return {
      tone: "off", window, reference, clampNotice,
      headline: "Automatic saving is off.",
      detail: "Simple turns run on the default model. Models you pin in Models are used as chosen.",
    };
  }
  if (r.state.reason === "meter_warming" || (!m && r.state.reason !== "tier_unlimited")) {
    return {
      tone: "measuring", window, reference, clampNotice,
      headline: "Measuring this month’s saving…",
      detail: "The first measurement takes a moment after the console starts. Nothing is limited meanwhile.",
    };
  }
  if (r.state.capped) {
    return {
      tone: "paused", window, reference, clampNotice,
      headline: measuredText ?? "Automatic saving is paused.",
      detail:
        `The limit of ${pct(effective, 0)} is reached, so simple turns run on the default model until ` +
        `the saved share falls below it or the month ends. Pin a model in Models to keep saving, ` +
        `or raise the limit with a Member licence.`,
    };
  }
  if (effective === null) {
    return {
      tone: "unlimited", window, reference, clampNotice,
      headline: measuredText ?? "No limit set.",
      detail: "Automatic saving runs without a limit.",
    };
  }
  return {
    tone: "active", window, reference, clampNotice,
    headline: measuredText ?? "Automatic saving is active.",
    detail: `Automatic saving pauses once it reaches ${pct(effective, 0)} this month. Models you pin are never limited.`,
  };
}
