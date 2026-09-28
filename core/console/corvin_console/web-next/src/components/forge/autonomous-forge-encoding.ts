/**
 * Encodings for the Autonomous Skill Forge panel — kept out of the components
 * so the number→label mapping is unit-tested (ADR-0761).
 */
import type { CanaryStatus, CanaryView, MetricPoint, Variant } from "@/lib/api/autonomous-forge";

export const VARIANT_LABEL: Record<Variant, string> = { live: "Live", candidate: "Candidate" };
/** Nominal identity colours (validated light #ffffff / dark #0e1320). */
export const VARIANT_COLOR: Record<Variant, string> = {
  live: "var(--viz-variant-live)",
  candidate: "var(--viz-variant-candidate)",
};

export const STATUS_LABEL: Record<CanaryStatus, string> = {
  canary: "Running",
  paused: "Paused",
  ready: "Ready for approval",
  approved: "Approved",
  deferred: "Deferred",
  rolled_back: "Rolled back",
};

export const ACTIVE_STATUSES: CanaryStatus[] = ["canary", "paused", "ready"];

export function isActive(status: CanaryStatus | null | undefined): boolean {
  return !!status && ACTIVE_STATUSES.includes(status);
}

/** A score in [0,1] as an operator reads it; "—" when there is no measurement. */
export function formatScore(v: number | null | undefined): string {
  return typeof v === "number" ? v.toFixed(2) : "—";
}

export function verdictLabel(c: CanaryView): string {
  if (c.status === "paused") return "Paused — every chat gets the live skill. Resume to keep collecting ratings.";
  if (c.status === "ready") return "Gates pass at the top step — approve to roll out";
  if (c.status !== "canary") return `This canary is ${STATUS_LABEL[c.status].toLowerCase()}.`;
  switch (c.verdict.decision) {
    case "escalate": {
      const next = c.gates.traffic_steps.find((s) => s > c.traffic_percent);
      return next ? `Gates pass — next step ${next}% traffic` : "Gates pass";
    }
    case "ready":
      return "Gates pass at the top step — approve to roll out";
    case "rollback":
      return "Candidate is worse — autopilot will roll back";
    default:
      return c.verdict.reason;
  }
}

/** Share of chats that get the candidate RIGHT NOW: the traffic step while
 *  running or ready (the backend keeps serving until a decision), 0 while
 *  paused or finished. */
export function effectiveTraffic(c: CanaryView): number {
  return c.status === "canary" || c.status === "ready" ? c.traffic_percent : 0;
}

export function sampleProgress(c: CanaryView): { have: number; need: number } {
  return { have: c.stats.candidate.outcome_n, need: c.gates.min_samples };
}

export interface SeriesRow {
  /** 1-based index of the outcome grade within its variant. */
  n: number;
  live?: number | null;
  candidate?: number | null;
}

/**
 * Running outcome mean per variant, indexed by grade number so two variants
 * with different traffic share one x-axis. Usage grades carry no quality
 * signal and are left out.
 */
export function outcomeSeries(points: MetricPoint[]): SeriesRow[] {
  const rows: SeriesRow[] = [];
  const counters: Record<Variant, number> = { live: 0, candidate: 0 };
  for (const p of points) {
    if (p.kind !== "outcome") continue;
    counters[p.variant] += 1;
    const i = counters[p.variant] - 1;
    rows[i] = { ...(rows[i] ?? { n: i + 1 }), [p.variant]: p.outcome_mean };
  }
  return rows;
}

export const TRIGGER_LABEL: Record<string, string> = {
  operator_request: "Started by the operator",
  outcome_mean_below_threshold: "Started by the autopilot — users rated the skill low",
};
