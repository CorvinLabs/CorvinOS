/** The pure run → work-view mappings (ADR-2081 P4). */
import { describe, expect, it } from "vitest";
import type { UnifiedTask } from "@/lib/api/initiatives";
import {
  LANE_FINISHED_MAX, RUN_WINDOW_MS, windowTruncated, WORK_RUN_TYPES, groupRuns, runBoardColumn, runLane, runningNow, workRuns,
} from "@/pages/tasks/run-encodings";

const NOW = Date.parse("2026-09-27T12:00:00Z");
const iso = (ms: number) => new Date(ms).toISOString();
function r(id: string, over: Partial<UnifiedTask> = {}): UnifiedTask {
  return { id, type: "chat", type_label: "Chat", subtype: "discord", title: id, status: "done", raw_status: null,
    created_at: iso(NOW - 3600_000), started_at: iso(NOW - 3600_000), ended_at: iso(NOW - 1800_000),
    sort_ts: 0, duration_s: 1, stale_reason: null, detail: null, ...over };
}

describe("run encodings", () => {
  it("never shows commits or scheduled reminders as work runs", () => {
    expect(WORK_RUN_TYPES).not.toContain("commit");
    expect(WORK_RUN_TYPES).not.toContain("scheduled");
    expect(WORK_RUN_TYPES).toContain("a2a");
    expect(workRuns([r("cron", { status: "scheduled", ended_at: null })], [], NOW)).toEqual([]);
    expect(runningNow([r("cron", { status: "scheduled" })])).toEqual([]);
  });

  it("keeps every active run and only the finished ones inside the window, active first", () => {
    const active = [r("a", { status: "running", ended_at: null, sort_ts: 1 })];
    const finished = [r("in", { sort_ts: 5 }), r("out", { ended_at: iso(NOW - RUN_WINDOW_MS - 1000) })];
    expect(workRuns(active, finished, NOW).map((t) => t.id)).toEqual(["a", "in"]);
    expect(workRuns(active, finished, NOW, "IN").map((t) => t.id)).toEqual(["in"]);  // search, case-insensitive
  });

  it("maps a run's status to the board column of the same meaning", () => {
    expect(runBoardColumn("queued")).toBe("open");
    expect(runBoardColumn("scheduled")).toBe("open");
    expect(runBoardColumn("running")).toBe("in_progress");
    expect(runBoardColumn("paused")).toBe("in_progress");
    expect(runBoardColumn("failed")).toBe("blocked");
    expect(runBoardColumn("stale")).toBe("blocked");
    expect(runBoardColumn("done")).toBe("complete");
    expect(runBoardColumn("cancelled")).toBe("complete");
  });

  it("orders running before waiting in the live strip and drops finished runs", () => {
    const got = runningNow([r("q", { status: "queued" }), r("p", { status: "paused" }), r("x", { status: "running" }),
      r("s", { status: "stale" })]);
    expect(got.map((t) => t.id)).toEqual(["x", "p", "q"]);
  });

  it("groups by type and channel, groups with active runs first", () => {
    const g = groupRuns([r("a"), r("b", { type: "a2a", type_label: "A2A", subtype: "inbound", status: "running" })]);
    expect(g.map((x) => [x.key, x.label, x.active])).toEqual([["a2a:inbound", "A2A · inbound", 1], ["chat:discord", "Chat · discord", 0]]);
  });

  it("lays runs on a 24 h lane of its own, clipped at the window start, a live run reaching now", () => {
    const lane = runLane([
      r("live", { status: "running", started_at: iso(NOW - 6 * 3600_000), ended_at: null }),
      r("clip", { started_at: iso(NOW - 30 * 3600_000), ended_at: iso(NOW - 12 * 3600_000) }),
      r("gone", { started_at: iso(NOW - 40 * 3600_000), ended_at: iso(NOW - 30 * 3600_000) }),
    ], NOW);
    const by = Object.fromEntries(lane.rows.map((x) => [x.run.id, x]));
    expect(by.live.x0).toBeCloseTo(18 / 24);
    expect(by.live.x1).toBe(1);
    expect(by.clip.x0).toBe(0);
    expect(by.clip.x1).toBeCloseTo(12 / 24);
    expect(by.gone).toBeUndefined();
  });

  it("keeps the lane readable: every active run, only the latest finished ones", () => {
    const many = Array.from({ length: LANE_FINISHED_MAX + 25 }, (_, i) => r(`f${i}`, { sort_ts: i }));
    const lane = runLane([...many, r("live", { status: "running", ended_at: null })], NOW);
    expect(lane.rows).toHaveLength(LANE_FINISHED_MAX + 1);
    expect(lane.hidden).toBe(25);
    expect(lane.rows.some((x) => x.run.id === "live")).toBe(true);
    expect(lane.rows.some((x) => x.run.id === "f0")).toBe(false);   // the oldest go to Activity
  });

  it("says when the fetched page ends inside the 24 h window", () => {
    const recent = [r("a", { ended_at: iso(NOW - 3600_000) }), r("b", { ended_at: iso(NOW - 7200_000) })];
    expect(windowTruncated(recent, 2, NOW)).toBe(false);          // everything returned
    expect(windowTruncated(recent, 900, NOW)).toBe(true);         // more exist, page ends inside the window
    const old = [r("c", { ended_at: iso(NOW - RUN_WINDOW_MS - 60_000) })];
    expect(windowTruncated(old, 900, NOW)).toBe(false);           // page already reaches past the window
  });
});
