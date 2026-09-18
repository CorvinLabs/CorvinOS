/**
 * Derived rows of the cost-optimizer payload — moved verbatim (as a pure
 * function) from the body of panels/ModelCostOptimizer.tsx on 2026-09-18 so
 * the Usage & Cost and Learning tabs share ONE derivation. Every comment on a
 * rule is the original's; the encodings live in panels/cost-viz.ts (ADR-0761).
 */
import { dailyDomainMax as dailyDomain, shortModel, tierOf, toModelRows, sharedDomainMax } from "@/panels/cost-viz";
import type { DashboardStatus } from "../components/cost-charts";

export const MIN_SAMPLES_FOR_ADVICE = 25;
const LOW_COVERAGE_THRESHOLD = 0.5;
const TIER_RANK: Record<string, number> = { simple: 0, medium: 1, complex: 2 };

export const modelMixLabel = (id: string) => id.replace(/^claude-/, "").replace(/-\d{8}$/, "");

export function deriveCost(status: DashboardStatus) {
  // Model mix — a single-model mix means cost_savings_percent is just that
  // model's fixed price ratio against the baseline model, not evidence of
  // any routing decision (live finding 2026-09-13).
  const modelMixEntries = Object.entries(status.cost_model_mix || {}).sort((a, b) => b[1] - a[1]);
  const modelMixTotal = modelMixEntries.reduce((sum, [, n]) => sum + n, 0);
  const isSingleModel = modelMixEntries.length === 1;

  const acsMixEntries = Object.entries(status.acs_model_mix || {}).sort((a, b) => b[1] - a[1]);
  const acsMixTotal = acsMixEntries.reduce((sum, [, n]) => sum + n, 0);
  const acsCoverage =
    status.acs_total_turns && status.acs_total_turns > 0
      ? Math.round(((status.acs_counted_turns ?? 0) / status.acs_total_turns) * 100)
      : null;

  // Workload per complexity tier: volume, reliability, spend, and which model
  // served it. Ordered cheapest-tier-first so the list reads simple -> complex.
  const tierTurnTotal = status.thresholds.reduce((n, t) => n + t.sample_count, 0);
  const tierRows = status.thresholds
    .map((t) => {
      const priced = t.priced_turns ?? 0;
      const actual = t.actual_usd ?? 0;
      const baseline = t.baseline_usd ?? 0;
      return {
        tier: t.task_type,
        turns: t.sample_count,
        sharePct: tierTurnTotal > 0 ? (t.sample_count / tierTurnTotal) * 100 : 0,
        successPct: t.success_rate * 100,
        dominantModel: t.dominant_model ?? "",
        modelTier: tierOf(t.dominant_model ?? ""),
        actual,
        baseline,
        pricedTurns: priced,
        // Per PRICED turn, not per turn: dividing by turns that carried no
        // token counts would quietly understate the unit cost.
        perTurn: priced > 0 ? actual / priced : 0,
        savedPct: baseline > 0 ? (1 - actual / baseline) * 100 : 0,
        learned: t.learned_threshold,
      };
    })
    .sort((a, b) => (TIER_RANK[a.tier] ?? 9) - (TIER_RANK[b.tier] ?? 9));

  // A recommendation has to rest on evidence. "100% success" over two turns is
  // one data point wearing a percentage. Withheld until the tier has a sample
  // worth reading.
  const hardest = tierRows.find((t) => t.tier === "complex");
  const cheapestObserved = tierRows.length ? Math.min(...tierRows.map((t) => t.modelTier)) : 0;
  const cheapestServesHardest =
    hardest && hardest.turns >= MIN_SAMPLES_FOR_ADVICE && hardest.successPct >= 90 &&
    hardest.modelTier === cheapestObserved && hardest.dominantModel
      ? hardest
      : null;

  const osCostRows = toModelRows(status.cost_model_cost);
  const workerCostRows = toModelRows(status.acs_model_cost);
  // ONE domain for both model facets, 12 % headroom for the direct labels.
  const modelDomainMax = sharedDomainMax([...osCostRows, ...workerCostRows]);
  // ONE domain for both daily facets, same reason — over PRICED values; a
  // null day is a gap and must not pull the floor (2026-09-19).
  const dailyDomainMax = dailyDomain(status.cost_history, [
    "actual_usd", "baseline_usd", "acs_actual_usd", "acs_baseline_usd",
  ]);

  // Days in the window (either series priced). Each facet decides bars vs.
  // area from ITS OWN priced days (DailySource); this only drives the heading.
  const dayCount = status.cost_history.length;
  const singleDay = dayCount < 2;

  // Routing distribution: turns per model, split by the role that ran them —
  // counts, deliberately a different chart from the dollars (no dual axis).
  const byModel = new Map<string, { label: string; model: string; os: number; worker: number; tier: number }>();
  for (const [model, n] of Object.entries(status.cost_model_mix || {})) {
    const e = byModel.get(model) || { label: shortModel(model), model, os: 0, worker: 0, tier: tierOf(model) };
    e.os += n;
    byModel.set(model, e);
  }
  for (const [model, n] of Object.entries(status.acs_model_mix || {})) {
    const e = byModel.get(model) || { label: shortModel(model), model, os: 0, worker: 0, tier: tierOf(model) };
    e.worker += n;
    byModel.set(model, e);
  }
  const routingRows = [...byModel.values()].sort((a, b) => a.tier - b.tier);

  // Share of seen turns the cost totals are actually computed from.
  const costCoverage =
    status.cost_total_turns && status.cost_total_turns > 0
      ? Math.round(((status.cost_counted_turns ?? 0) / status.cost_total_turns) * 100)
      : null;

  // Days where most completed turns had no usable token data look like a cost
  // crash in the raw $ numbers alone. Flag them explicitly.
  const lowCoverageDays = status.cost_history.filter(
    (p) => p.total_turns > 0 && p.counted_turns / p.total_turns < LOW_COVERAGE_THRESHOLD,
  );

  return {
    modelMixEntries, modelMixTotal, isSingleModel,
    acsMixEntries, acsMixTotal, acsCoverage,
    tierRows, hardest, cheapestServesHardest,
    osCostRows, workerCostRows, modelDomainMax, dailyDomainMax, dayCount, singleDay,
    routingRows, costCoverage, lowCoverageDays,
  };
}
