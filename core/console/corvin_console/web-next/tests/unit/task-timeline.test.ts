import { describe, expect, it } from "vitest";
import { currentStatus, rowLabel, rowTone, type TimelineRow } from "@/lib/task-timeline";

const row = (seq: number, state: TimelineRow["state"], kind = "transition", text: string | null = null): TimelineRow =>
  ({ seq, at: 1700000000 + seq, state, kind, text });

describe("task timeline model", () => {
  it("an interruption reads as a warning, never as done", () => {
    const r = row(2, "interrupted");
    expect(rowLabel(r)).toBe("Interrupted");
    expect(rowTone(r)).toBe("warn");
  });

  it("an operator stop is worded as the user's own action", () => {
    expect(rowLabel(row(2, "cancelled"))).toBe("Stopped by you");
  });

  it("notice rows say what the user was told", () => {
    expect(rowLabel(row(3, null, "needs_user"))).toMatch(/waiting for your decision/);
    expect(rowTone(row(3, null, "gave_up"))).toBe("bad");
    expect(rowTone(row(3, null, "recovered"))).toBe("ok");
  });

  it("current status follows the last state row, and a gave_up notice ends it", () => {
    const rows = [row(0, "pending"), row(1, "running"), row(2, "interrupted"), row(3, null, "interrupted_resuming")];
    expect(currentStatus(rows)).toBe("Interrupted");
    expect(currentStatus([...rows, row(4, null, "gave_up")])).toBe("Ended - no automatic attempts left");
    expect(currentStatus([])).toBe("No history yet");
  });
});
