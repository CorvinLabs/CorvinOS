/**
 * Formatting + tile model for the per-chat cost / efficiency dashboard (ADR-2324).
 *
 * The server owns every number and every denominator; this module only decides how a
 * figure reads. The one rule it enforces: `null` is "not available", rendered as an
 * em dash - never "0", never "$0.00". Formatting is pinned to en-US so two panels
 * never disagree about a separator.
 */
import type { ChatEfficiency } from "@/lib/api/chat";

export const NA = "—";

const int = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });
const dec1 = new Intl.NumberFormat("en-US", { maximumFractionDigits: 1, minimumFractionDigits: 1 });
const dec2 = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2, minimumFractionDigits: 2 });

export function fmtUsd(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return NA;
  const a = Math.abs(v);
  const sign = v < 0 ? "-" : "";
  if (a === 0) return "$0.00";
  if (a < 0.01) return `${sign}$${a.toFixed(4)}`;
  return `${sign}$${dec2.format(a)}`;
}

export function fmtInt(v: number | null | undefined): string {
  return v === null || v === undefined || !Number.isFinite(v) ? NA : int.format(v);
}

/** 12,345 -> "12.3k"; the exact value belongs in a tooltip. */
export function fmtCompact(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return NA;
  if (v >= 1_000_000) return `${dec1.format(v / 1_000_000)}M`;
  if (v >= 10_000) return `${dec1.format(v / 1000)}k`;
  return int.format(v);
}

export function fmtPct(v: number | null | undefined): string {
  return v === null || v === undefined || !Number.isFinite(v) ? NA : `${dec1.format(v * 100)}%`;
}

export function fmtRatio(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return NA;
  return v >= 100 ? int.format(v) : dec1.format(v);
}

export interface Tile {
  id: string;
  label: string;
  value: string;
  sub?: string;
  /** Definition shown on hover: the formula and which turns it covers. */
  help: string;
}

/** The dashboard's metric tiles, in reading order. */
export function buildTiles(d: ChatEfficiency): Tile[] {
  const c = d.coverage;
  const lb = d.code.lines_lower_bound ? "≥ " : "";
  const lineScope = `${c.line_turns} turn${c.line_turns === 1 ? "" : "s"} with a known diff`;
  return [
    {
      id: "total-cost", label: "Total cost", value: fmtUsd(d.cost.total_usd),
      sub: `${c.priced} of ${c.turns} turns priced`,
      help: "Sum over priced turns of input, output, cache-write and cache-read tokens at the model's list price. Turns on a model outside the rate card, or without a complete usage report, are excluded - not counted as $0.",
    },
    {
      id: "cost-per-turn", label: "Cost per turn", value: fmtUsd(d.cost.per_turn_usd),
      help: "Total cost divided by the number of priced turns.",
    },
    {
      id: "cost-per-1k-out", label: "Cost per 1k output tokens", value: fmtUsd(d.cost.per_1k_output_tokens_usd),
      help: "Total cost divided by the output tokens of the same priced turns, per 1,000. It includes the cost of re-reading context, so it is higher than the model's output list price.",
    },
    {
      id: "cache-hit", label: "Cache hit rate", value: fmtPct(d.cache.hit_rate),
      sub: d.cost.cache_saving_usd === null ? undefined : `saved ${fmtUsd(d.cost.cache_saving_usd)}`,
      help: "Cache-read tokens divided by all input-side tokens (plain input + cache read + cache write), over turns with a complete usage report. 'saved' is the cost difference to billing every input-side token at the plain input price.",
    },
    {
      id: "lines", label: "Changed lines", value: `${lb}${fmtInt(d.code.lines_changed)}`,
      sub: `+${fmtInt(d.code.lines_added)} / −${fmtInt(d.code.lines_removed)}`,
      help: "Added plus removed lines reported by the file tools' own diffs (Edit, Write, Bash with named files). A truncated diff makes this a lower bound (≥); a diff withheld by the credential gate is not counted.",
    },
    {
      id: "tok-per-line", label: "Output tokens per changed line", value: fmtRatio(d.code.output_tokens_per_changed_line),
      sub: lineScope,
      help: "Output tokens divided by changed lines, over the same turns. Turns without code changes (discussion, research) are included, so this is the full price of a line, not just of typing it.",
    },
    {
      id: "lines-per-1k", label: "Changed lines per 1k output tokens", value: fmtRatio(d.code.changed_lines_per_1k_output_tokens),
      sub: lineScope,
      help: "Changed lines per 1,000 output tokens, over turns with a fully known diff. The inverse of 'Output tokens per changed line'; higher is better.",
    },
    {
      id: "cost-per-line", label: "Cost per changed line", value: fmtUsd(d.code.cost_per_changed_line_usd),
      help: "Cost of priced turns with a fully known diff divided by their changed lines.",
    },
  ];
}

/** Honest caveats for this chat, most important first. Empty when nothing is missing. */
export function buildNotes(d: ChatEfficiency): string[] {
  const c = d.coverage;
  const notes: string[] = [];
  if (c.turns === 0) return notes;
  const unmeasured = c.without_usage + c.incomplete_usage;
  if (unmeasured > 0)
    notes.push(`${unmeasured} turn${unmeasured === 1 ? "" : "s"} reported no complete token usage and ${unmeasured === 1 ? "is" : "are"} left out of all figures.`);
  if (d.unpriced_models.length > 0)
    notes.push(`No list price for ${d.unpriced_models.join(", ")}: those turns are counted in tokens but not in any cost.`);
  if (c.diff_withheld > 0)
    notes.push(`${c.diff_withheld} turn${c.diff_withheld === 1 ? "" : "s"} changed files whose diff was withheld (credential gate) and ${c.diff_withheld === 1 ? "is" : "are"} left out of the line figures.`);
  if (d.code.lines_lower_bound)
    notes.push("Some diffs were truncated, so changed lines are a lower bound.");
  if (d.window.history_truncated)
    notes.push("This chat is longer than the analysed history; the oldest turns are not included.");
  if (!d.includes_delegated_workers)
    notes.push("Delegated worker runs (ACS) are not part of this chat's turn log and are not included.");
  return notes;
}

/** Bars for the per-turn cost chart: priced turns only, newest last, at least 2px for a real value. */
export function costBars(d: ChatEfficiency, maxBars = 40, heightPx = 48) {
  const priced = d.rows.filter((r) => r.cost_usd !== null).slice(-maxBars);
  const max = Math.max(0, ...priced.map((r) => r.cost_usd as number));
  return priced.map((r) => ({
    ts: r.ts,
    model: r.model,
    cost: r.cost_usd as number,
    height: max > 0 ? Math.max(2, Math.round(((r.cost_usd as number) / max) * heightPx)) : 2,
  }));
}
