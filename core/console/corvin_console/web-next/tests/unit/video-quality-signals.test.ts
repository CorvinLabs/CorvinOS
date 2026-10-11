/**
 * Encodings of the Video Producer's production signals, run on the report a REAL job wrote
 * (core/console/tests/fixtures/video_producer/report_real_job_2026-10-11.json - the plugin's
 * test_report_e2e run: real job process, mock TTS, so degradation_rate fails on 2 of 2 scenes).
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import {
  MIN_SHARE_SAMPLE, formatThreshold, formatValue, reportAbsence, sentence, signalRows, verdictSummary, verdictToken,
  type ProductionReport, type ReportSignal,
} from "@/lib/video-quality-signals";

// vitest runs from web-next/; the fixture sits with the console's Python E2E tests
const raw = JSON.parse(readFileSync(resolve(process.cwd(), "../../tests/fixtures/video_producer/report_real_job_2026-10-11.json"), "utf-8"));
const signals: ReportSignal[] = (raw.signals as ReportSignal[]).map((s) => ({ invalidated_by: null, reason: null, detail: null, ...s }));
const counts: Record<string, number> = {};
for (const s of signals) counts[s.verdict] = (counts[s.verdict] ?? 0) + 1;
const REPORT: ProductionReport = { state: "present", schema: 1, signals, verdicts: counts, plugin_version: raw.plugin_version, status: "complete" };
const byId = (id: string) => signals.find((s) => s.id === id)!;

describe("production signal encodings", () => {
  it("a share over fewer than MIN_SHARE_SAMPLE scenes is written n of N, never as a percentage", () => {
    const dr = byId("degradation_rate");
    expect(dr.basis.of).toBeLessThan(MIN_SHARE_SAMPLE);
    expect(formatValue(dr)).toBe("2 of 2 scenes");
    expect(formatValue(dr)).not.toMatch(/%/);
    const row = signalRows(REPORT).find((r) => r.id === "degradation_rate")!;
    expect(row.note).toMatch(/Only 2 scenes - too few to state as a percentage/);
    // over a real sample the percentage appears, with its basis
    expect(formatValue({ ...dr, value: 0.083, basis: { n: 1, of: 12 } })).toBe("8 % (1 of 12 scenes)");
  });

  it("thresholds are written from the report's own numbers", () => {
    expect(formatThreshold(byId("degradation_rate"))).toBe("no storyboard model fallback · grounding never lost · at most 10 % of scenes · no fallback slide");
    expect(formatThreshold(byId("layout_clean"))).toBe("0 collisions kept");
    expect(formatThreshold(byId("mux_integrity"))).toBe("each clip at least its voice length minus 0.1 s · audio within 3 % of the picture");
    expect(formatThreshold(byId("plan_estimate_error"))).toBe("information only");
    expect(formatThreshold(byId("style_fidelity"))).toBe("ΔE at most 3");
  });

  it("plan_estimate_error is information in seconds, an unmeasured value is never 0", () => {
    expect(formatValue(byId("plan_estimate_error"))).toBe("9.2 s narrated · 16 s planned");
    expect(formatValue(byId("fact_support"))).toBe("not measured");
    expect(formatValue(byId("mux_integrity"))).toBe("2 scenes intact");
  });

  it("the gate comes first and the signals it invalidated say so", () => {
    const rows = signalRows(REPORT);
    expect(rows[0].id).toBe("degradation_rate");
    expect(rows.map((r) => r.id).sort()).toEqual(signals.map((s) => s.id).sort());
    const layout = rows.find((r) => r.id === "layout_clean")!;
    expect(layout.valid).toBe(false);
    expect(layout.note).toMatch(/Not valid: "Degraded scenes" failed/);
    expect(rows.find((r) => r.id === "mux_integrity")!.valid).toBe(true);
    expect(rows.find((r) => r.id === "mux_integrity")!.basis).toBe("2 of 2");
    expect(rows.find((r) => r.id === "fact_support")!.basis).toBe("—");
  });

  it("verdict colours are the --viz-verdict tokens, never the accent; neutral verdicts are hollow", () => {
    for (const v of ["pass", "warn", "fail"] as const) expect(verdictToken(v)).toEqual({ color: `var(--viz-verdict-${v})`, filled: true });
    for (const v of ["skip", "info", "unknown"] as const) expect(verdictToken(v).filled).toBe(false);
    for (const v of ["pass", "warn", "fail", "skip", "info", "unknown"] as const) expect(verdictToken(v).color).not.toMatch(/accent/);
  });

  it("names a missing, refused or unreadable report instead of filling it in", () => {
    expect(reportAbsence(REPORT)).toBeNull();
    expect(reportAbsence({ state: "missing", signals: [] })).toMatch(/No production report for this job/);
    expect(reportAbsence({ state: "not_written", signals: [] })).toMatch(/was not written/);
    expect(reportAbsence({ state: "unreadable", reason: "too_large", signals: [] })).toMatch(/could not be read \(too large\)/);
    expect(reportAbsence(undefined)).toMatch(/No production report/);
    expect(verdictSummary(REPORT)).toBe("7 signals · 1 fail · 2 pass · 2 information · 2 not measured");
  });

  it("a plugin reason reads as a sentence in the row note", () => {
    expect(sentence("scene share above 10 %")).toBe("Scene share above 10 %.");
    expect(sentence("Already one.")).toBe("Already one.");
    const dr = signalRows(REPORT).find((r) => r.id === "degradation_rate");
    expect(dr?.note).toMatch(/^Scene share above 10 %\. Only 2 scenes/);
  });
});
