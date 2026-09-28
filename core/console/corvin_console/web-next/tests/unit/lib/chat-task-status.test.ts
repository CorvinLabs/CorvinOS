import { describe, expect, it } from "vitest";
import {
  elapsedSeconds,
  formatElapsed,
  pickIndicator,
  statusLine,
  taskLabel,
  RECENT_FINISH_WINDOW_S,
  STALE_AFTER_S,
  type ServerTask,
} from "@/lib/chat-task-status";

const NOW = 1_781_137_800; // epoch SECONDS, like the server's time.time()

function task(p: Partial<ServerTask>): ServerTask {
  return {
    task_id: "t",
    chat_key: "web:x",
    status: "running",
    created_at: NOW - 100,
    started_at: NOW - 90,
    ended_at: null,
    exit_code: null,
    instruction_preview: "Summarise the quarterly report",
    last_event_at: NOW - 5,
    duration_ms: null,
    ...p,
  };
}

describe("pickIndicator", () => {
  it("prefers a running task over a newer pending one", () => {
    const ind = pickIndicator(
      [task({ task_id: "p", status: "pending", started_at: null }), task({ task_id: "r" })],
      NOW,
    );
    expect(ind.phase).toBe("running");
    expect(ind.task?.task_id).toBe("r");
    expect(ind.activeCount).toBe(2);
  });

  it("shows a recently finished task, then goes idle", () => {
    const done = task({ status: "completed", ended_at: NOW - 10, duration_ms: 80_000 });
    expect(pickIndicator([done], NOW).phase).toBe("completed");
    expect(pickIndicator([done], NOW + RECENT_FINISH_WINDOW_S).phase).toBe("idle");
  });

  it("is idle with no tasks", () => {
    expect(pickIndicator([], NOW)).toEqual({ phase: "idle", task: null, activeCount: 0, stale: false });
  });

  it("flags an in-flight task that has logged nothing for STALE_AFTER_S", () => {
    const hung = task({ last_event_at: NOW - STALE_AFTER_S });
    const ind = pickIndicator([hung], NOW);
    expect(ind.stale).toBe(true);
    expect(statusLine(ind, NOW)).toBe("Running · 1:30 · no activity for 10:00");
    expect(pickIndicator([task({})], NOW).stale).toBe(false);
  });
});

describe("elapsed time uses epoch seconds", () => {
  it("counts a running task from started_at", () => {
    expect(elapsedSeconds(task({}), "running", NOW)).toBe(90);
  });

  it("counts a queued task from created_at", () => {
    expect(elapsedSeconds(task({ status: "pending", started_at: null }), "pending", NOW)).toBe(100);
  });

  it("uses duration_ms for a finished task", () => {
    expect(elapsedSeconds(task({ status: "failed", duration_ms: 42_400 }), "failed", NOW)).toBe(42);
  });

  it("never goes negative on clock skew", () => {
    expect(elapsedSeconds(task({ started_at: NOW + 5 }), "running", NOW)).toBe(0);
  });
});

describe("formatting", () => {
  it("formats m:ss and h:mm:ss", () => {
    expect(formatElapsed(5)).toBe("0:05");
    expect(formatElapsed(154)).toBe("2:34");
    expect(formatElapsed(3_725)).toBe("1:02:05");
  });

  it("labels from the instruction, collapsing whitespace and truncating", () => {
    expect(taskLabel(task({ instruction_preview: "  a\n  b  " }))).toBe("a b");
    expect(taskLabel(task({ instruction_preview: "x".repeat(100) }))).toHaveLength(48);
    expect(taskLabel(task({ instruction_preview: "" }))).toBe("Background task");
  });

  it("builds the status line", () => {
    const run = pickIndicator([task({}), task({ task_id: "q", status: "pending", last_event_at: NOW })], NOW);
    expect(statusLine(run, NOW)).toBe("Running · 1:30 · +1 more");
    const q = pickIndicator([task({ status: "pending", started_at: null })], NOW);
    expect(statusLine(q, NOW)).toBe("Queued · waiting 1:40");
  });
});
