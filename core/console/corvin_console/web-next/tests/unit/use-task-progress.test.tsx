/**
 * BGT-14 (2026-10-21): the cross-chat progress hook typed and auto-cleaned the three old
 * terminal states only; an `interrupted` task (killed worker, PLAN-0958 P1) stayed in the
 * list forever. Driven through the hook's real WebSocket message path (a fake socket stands
 * in for the network, nothing else is stubbed).
 */
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useTaskProgress, type TaskProgress } from "@/hooks/use-task-progress";

class FakeSocket {
  static last: FakeSocket | null = null;
  readyState = 1;
  onopen: (() => void) | null = null;
  onmessage: ((ev: { data: string }) => void) | null = null;
  onclose: ((ev: unknown) => void) | null = null;
  onerror: ((ev: unknown) => void) | null = null;
  constructor(public url: string) {
    FakeSocket.last = this;
  }
  send() {}
  close() {}
  emit(evt: Record<string, unknown>) {
    this.onmessage?.({ data: JSON.stringify(evt) });
  }
}

const FIVE_MIN = 5 * 60 * 1000;

describe("useTaskProgress — interrupted tasks", () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    vi.stubGlobal("WebSocket", FakeSocket as unknown as typeof WebSocket);
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it("types interrupted as a task status", () => {
    const status: TaskProgress["status"] = "interrupted";
    expect(status).toBe("interrupted");
  });

  it("auto-cleans an interrupted task after five minutes, like the other ended states", () => {
    const { result } = renderHook(() => useTaskProgress());
    const ws = FakeSocket.last!;
    act(() => {
      ws.onopen?.();
      ws.emit({ task_id: "t-run", chat_key: "web:a", status: "running" });
      ws.emit({ task_id: "t-int", chat_key: "web:b", status: "running" });
      ws.emit({ task_id: "t-int", chat_key: "web:b", status: "interrupted" });
    });
    expect(result.current.tasks.map((t) => [t.task_id, t.status])).toEqual([
      ["t-run", "running"],
      ["t-int", "interrupted"],
    ]);
    act(() => {
      vi.advanceTimersByTime(FIVE_MIN + 1);
    });
    // the running task stays (positive control); the interrupted one is gone
    expect(result.current.tasks.map((t) => t.task_id)).toEqual(["t-run"]);
  });

  it("control: a completed task is cleaned on the same schedule", () => {
    const { result } = renderHook(() => useTaskProgress());
    const ws = FakeSocket.last!;
    act(() => {
      ws.onopen?.();
      ws.emit({ task_id: "t-done", chat_key: "web:a", status: "completed" });
    });
    expect(result.current.tasks).toHaveLength(1);
    act(() => {
      vi.advanceTimersByTime(FIVE_MIN + 1);
    });
    expect(result.current.tasks).toHaveLength(0);
  });
});
