import { describe, expect, it } from "vitest";
import type { RunEvent } from "@/lib/api/forge-runs";
import {
  chooseDiff, classifyDiffLines, clock, droppedCount, filesWritten, groupByPhase, iterationRows, mergeEvents, summarizeFindings, testSummary,
} from "@/lib/forge-run-feed";

const ev = (seq: number, type: RunEvent["type"], over: Partial<RunEvent> = {}): RunEvent => ({
  seq, ts: "2026-10-12T00:34:38.123+00:00", phase: "generation", level: "info", type, message: `m${seq}`, data: {}, ...over,
});

describe("mergeEvents", () => {
  it("appends new events, ignores a repeated page and keeps seq order", () => {
    const a = [ev(1, "progress"), ev(2, "progress")];
    const merged = mergeEvents(a, [ev(2, "progress"), ev(3, "progress")]);
    expect(merged.map((e) => e.seq)).toEqual([1, 2, 3]);
  });
  it("sorts out-of-order pages", () => {
    expect(mergeEvents([ev(3, "progress")], [ev(1, "progress"), ev(2, "progress")]).map((e) => e.seq)).toEqual([1, 2, 3]);
  });
  it("returns the SAME array when nothing is new (no needless re-render)", () => {
    const a = [ev(1, "progress")];
    expect(mergeEvents(a, [])).toBe(a);
  });
});

describe("groupByPhase", () => {
  it("groups consecutive events of one phase and keeps a phase that comes back as a new group", () => {
    const g = groupByPhase([
      ev(1, "progress", { phase: "planning" }), ev(2, "progress", { phase: "planning" }),
      ev(3, "progress", { phase: "review" }), ev(4, "progress", { phase: "planning" }),
    ]);
    expect(g.map((x) => [x.phase, x.events.length])).toEqual([["planning", 2], ["review", 1], ["planning", 1]]);
  });
  it("puts an event without a phase under 'run' instead of dropping it", () => {
    expect(groupByPhase([ev(1, "progress", { phase: null })])[0].phase).toBe("run");
  });
  it("is empty for no events", () => {
    expect(groupByPhase([])).toEqual([]);
  });
});

describe("summarizeFindings", () => {
  it("counts per dimension and verdict, sorted by dimension", () => {
    const rows = summarizeFindings([
      ev(1, "finding", { data: { dimension: "security", verdict: "refuted" } }),
      ev(2, "finding", { data: { dimension: "correctness", verdict: "confirmed" } }),
      ev(3, "finding", { data: { dimension: "correctness", verdict: "confirmed" } }),
      ev(4, "finding", { data: { dimension: "correctness", verdict: "plausible" } }),
    ]);
    expect(rows).toEqual([
      { dimension: "correctness", confirmed: 2, plausible: 1, refuted: 0 },
      { dimension: "security", confirmed: 0, plausible: 0, refuted: 1 },
    ]);
  });
  it("counts an unknown verdict as plausible and a missing dimension as 'unspecified' - nothing is dropped", () => {
    const rows = summarizeFindings([ev(1, "finding", { data: { verdict: "maybe" } })]);
    expect(rows).toEqual([{ dimension: "unspecified", confirmed: 0, plausible: 1, refuted: 0 }]);
  });
  it("ignores events that are not findings", () => {
    expect(summarizeFindings([ev(1, "progress")])).toEqual([]);
  });
});

describe("testSummary / iterationRows / filesWritten / droppedCount", () => {
  it("the last test_result wins", () => {
    expect(testSummary([ev(1, "test_result", { data: { passed: 1, total: 3 } }), ev(2, "test_result", { data: { passed: 3, total: 3 } })]))
      .toEqual({ passed: 3, total: 3 });
  });
  it("is null when no test ran, and a number the log lacked is null, not 0", () => {
    expect(testSummary([ev(1, "progress")])).toBeNull();
    expect(testSummary([ev(1, "test_result", { data: {} })])).toEqual({ passed: null, total: null });
  });
  it("lists each file once", () => {
    expect(filesWritten([ev(1, "file_written", { data: { path: "plugin.py" } }), ev(2, "file_written", { data: { path: "plugin.py" } }), ev(3, "file_written", { data: { path: "docs/a.md" } })]))
      .toEqual(["plugin.py", "docs/a.md"]);
  });
  it("reads iteration rows with unknown numbers as null", () => {
    expect(iterationRows([ev(5, "iteration", { data: { iterations: 5, quality: 0.5 } }), ev(6, "iteration", { data: {} })]))
      .toEqual([{ seq: 5, iterations: 5, quality: 0.5 }, { seq: 6, iterations: null, quality: null }]);
  });
  it("reads how many events the cap left out, 0 when there is no marker", () => {
    expect(droppedCount([ev(9, "truncated", { data: { dropped: 7 } })])).toBe(7);
    expect(droppedCount([ev(1, "progress")])).toBe(0);
  });
});

describe("clock", () => {
  it("is pinned to UTC HH:MM:SS and survives a bad timestamp", () => {
    expect(clock("2026-10-12T00:34:38.123+00:00")).toBe("00:34:38");
    expect(clock("not a time")).toBe("—");
  });
});

describe("chooseDiff", () => {
  it("is null with fewer than two snapshots (nothing to compare, nothing drawn)", () => {
    expect(chooseDiff([])).toBeNull();
    expect(chooseDiff([{ n: 1, label: "iteration 1" }])).toBeNull();
  });
  it("compares the last two iterations when there is no previous version", () => {
    expect(chooseDiff([{ n: 1, label: "iteration 1" }, { n: 2, label: "iteration 2" }, { n: 3, label: "iteration 3" }]))
      .toEqual({ from: "2", to: "3" });
  });
  it("compares the previous version against the latest for a refine", () => {
    expect(chooseDiff([{ n: 1, label: "previous version" }, { n: 2, label: "iteration 1" }, { n: 3, label: "iteration 2" }]))
      .toEqual({ from: "1", to: "3" });
  });
  it("a previous version that is also the last snapshot falls back to the last two", () => {
    expect(chooseDiff([{ n: 1, label: "iteration 1" }, { n: 2, label: "previous version" }])).toEqual({ from: "1", to: "2" });
  });
});

describe("classifyDiffLines", () => {
  it("colours additions and deletions but treats the --- / +++ headers as meta", () => {
    const rows = classifyDiffLines("--- a/f.py\n+++ b/f.py\n@@ -1 +1 @@\n-old\n+new\n same\n");
    expect(rows.map((r) => r.kind)).toEqual(["meta", "meta", "hunk", "del", "add", "context"]);
  });
  it("drops only the trailing empty line a final newline leaves", () => {
    expect(classifyDiffLines("+a\n")).toHaveLength(1);
    expect(classifyDiffLines("")).toEqual([]);
  });
  it("inside a hunk, a deleted '-- comment' (shown as '--- comment') is a deletion, not a file header", () => {
    const rows = classifyDiffLines("--- a/q.sql\n+++ b/q.sql\n@@ -1 +1 @@\n--- a SQL comment\n+++ a new line starting with ++\n");
    expect(rows.map((r) => r.kind)).toEqual(["meta", "meta", "hunk", "del", "add"]);
  });
  it("a diff with no hunk yet treats --- and +++ as headers", () => {
    expect(classifyDiffLines("--- a\n+++ b").map((r) => r.kind)).toEqual(["meta", "meta"]);
  });
});
