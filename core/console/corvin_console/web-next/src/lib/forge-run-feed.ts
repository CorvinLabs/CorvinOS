/**
 * What the run feed shows, computed from the event log. Pure functions: a wrong grouping or count is invisible in a
 * screenshot, so the mapping from events to what the operator reads lives here and is unit-tested.
 * Nothing is invented: a number the log did not carry is `null`, and the view says "unknown".
 */
import type { RunEvent } from "@/lib/api/forge-runs";

/** Append new events, drop repeats (a poll can overlap), keep them ordered by seq. */
export function mergeEvents(prev: RunEvent[], next: RunEvent[]): RunEvent[] {
  if (next.length === 0) return prev;
  const bySeq = new Map<number, RunEvent>();
  for (const e of prev) bySeq.set(e.seq, e);
  for (const e of next) bySeq.set(e.seq, e);
  return [...bySeq.values()].sort((a, b) => a.seq - b.seq);
}

export interface PhaseGroup {
  phase: string;
  events: RunEvent[];
}

/** Consecutive events of the same phase form one group, in the order the phases happened. */
export function groupByPhase(events: RunEvent[]): PhaseGroup[] {
  const groups: PhaseGroup[] = [];
  for (const e of events) {
    const phase = e.phase ?? "run";
    const last = groups[groups.length - 1];
    if (last && last.phase === phase) last.events.push(e);
    else groups.push({ phase, events: [e] });
  }
  return groups;
}

export interface FindingCounts {
  dimension: string;
  confirmed: number;
  plausible: number;
  refuted: number;
}

/** Findings per review dimension, split by the reviewer's verdict. An unknown verdict is counted as plausible, never dropped. */
export function summarizeFindings(events: RunEvent[]): FindingCounts[] {
  const by = new Map<string, FindingCounts>();
  for (const e of events) {
    if (e.type !== "finding") continue;
    const dimension = String(e.data.dimension ?? "unspecified");
    const row = by.get(dimension) ?? { dimension, confirmed: 0, plausible: 0, refuted: 0 };
    const verdict = String(e.data.verdict ?? "");
    if (verdict === "confirmed") row.confirmed += 1;
    else if (verdict === "refuted") row.refuted += 1;
    else row.plausible += 1;
    by.set(dimension, row);
  }
  return [...by.values()].sort((a, b) => a.dimension.localeCompare(b.dimension));
}

export function filesWritten(events: RunEvent[]): string[] {
  return [...new Set(events.filter((e) => e.type === "file_written").map((e) => String(e.data.path ?? e.message)))];
}

export interface TestSummary {
  passed: number | null;
  total: number | null;
}

/** The last test_result wins (a retry re-runs the tests). `null` when the run reported none. */
export function testSummary(events: RunEvent[]): TestSummary | null {
  const last = [...events].reverse().find((e) => e.type === "test_result");
  if (!last) return null;
  const num = (v: unknown) => (typeof v === "number" ? v : null);
  return { passed: num(last.data.passed), total: num(last.data.total) };
}

export interface IterationRow {
  seq: number;
  iterations: number | null;
  quality: number | null;
}

export function iterationRows(events: RunEvent[]): IterationRow[] {
  const num = (v: unknown) => (typeof v === "number" ? v : null);
  return events
    .filter((e) => e.type === "iteration")
    .map((e) => ({ seq: e.seq, iterations: num(e.data.iterations), quality: num(e.data.quality) }));
}

/** How many events the caps left out, from the final `truncated` marker; 0 when there is none. */
export function droppedCount(events: RunEvent[]): number {
  const t = events.find((e) => e.type === "truncated");
  const n = t?.data.dropped;
  return typeof n === "number" ? n : 0;
}

/** HH:MM:SS in UTC, pinned so the same log reads the same on every machine. */
export function clock(ts: string): string {
  const d = new Date(ts);
  return Number.isNaN(d.getTime()) ? "—" : d.toISOString().slice(11, 19);
}
