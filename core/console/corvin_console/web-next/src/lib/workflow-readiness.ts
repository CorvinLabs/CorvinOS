/**
 * Pure helpers for the Ready tab: labels, the one-line verdict and the acceptance-case JSON
 * parser. Kept out of the component so the mappings are unit-testable.
 */
import type { AcceptanceCase, ReadinessReport, ReadinessSignal, ReadinessStatus } from "@/lib/api/workflows";

export const SIGNAL_LABELS: Record<string, string> = {
  validator: "Validation",
  reachability: "Reachability",
  acceptance: "Acceptance case",
  external_exposure: "Not exercised",
  repairs: "Designer repairs",
};

export const STATUS_LABELS: Record<ReadinessStatus, string> = {
  pass: "Pass",
  fail: "Fail",
  unmeasured: "Not measured",
  info: "Info",
};

export function signalLabel(name: string): string {
  return SIGNAL_LABELS[name] ?? name;
}

/** Tailwind classes per status; "not measured" is NOT styled like a pass. */
export function statusClass(status: ReadinessStatus): string {
  switch (status) {
    case "pass":
      return "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400";
    case "fail":
      return "bg-destructive/10 text-destructive";
    case "unmeasured":
      return "bg-amber-500/10 text-amber-600 dark:text-amber-400";
    default:
      return "bg-muted text-muted-foreground";
  }
}

/** The first signal that stops the draft from being ready (fail or not measured). */
export function firstBlocker(signals: ReadinessSignal[]): ReadinessSignal | null {
  return signals.find((s) => s.status === "fail" || s.status === "unmeasured") ?? null;
}

export function verdict(report: ReadinessReport): string {
  if (report.ready) return "Ready: every line passes.";
  const b = firstBlocker(report.signals);
  return b ? `Not ready: ${signalLabel(b.name).toLowerCase()} - ${b.detail}` : "Not ready.";
}

export const ACCEPTANCE_EXAMPLE = `{
  "inputs": {},
  "stub_responses": { "<agent name>": { "text": "..." } },
  "expect": {
    "state": "complete",
    "nodes_reached": ["<node id>"],
    "final_contains": { "<node>.<field>": "<text>" }
  }
}`;

export type ParsedAcceptance = { ok: true; value: AcceptanceCase } | { ok: false; error: string };

/** Client-side shape check; the server re-validates everything. */
export function parseAcceptance(text: string): ParsedAcceptance {
  let raw: unknown;
  try {
    raw = JSON.parse(text);
  } catch {
    return { ok: false, error: "The acceptance case must be valid JSON." };
  }
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    return { ok: false, error: "The acceptance case must be a JSON object." };
  }
  const expect = (raw as { expect?: unknown }).expect;
  if (!expect || typeof expect !== "object" || Array.isArray(expect) || Object.keys(expect).length === 0) {
    return { ok: false, error: 'Add an "expect" object with at least one expectation.' };
  }
  return { ok: true, value: raw as AcceptanceCase };
}

export function formatAcceptance(a: AcceptanceCase | null): string {
  return a ? JSON.stringify(a, null, 2) : "";
}
