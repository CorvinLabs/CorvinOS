/**
 * Background activity strip (ADR-2236 `bg_status`): the registry turns the event into
 * rows, the strip renders kind / scrubbed label / live clock / state, ticks while
 * something runs, and never takes the composer's focus.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { __resetForTests, ensureConnected, getSessionState, sendMessage } from "@/lib/chat-registry";
import { BackgroundActivity, formatElapsed, orderChildren } from "@/components/chat/BackgroundActivity";

class FakeSocket {
  static last: FakeSocket | null = null;
  static OPEN = 1;
  readyState = 1;
  onopen: (() => void) | null = null;
  onmessage: ((ev: { data: string }) => void) | null = null;
  onclose: ((ev: unknown) => void) | null = null;
  onerror: ((ev: unknown) => void) | null = null;
  constructor(public url: string) { FakeSocket.last = this; }
  send() {}
  close() {}
  emit(evt: Record<string, unknown>) { this.onmessage?.({ data: JSON.stringify(evt) }); }
}

const SID = "bg-activity-test";
const child = (over: Partial<{ id: string; kind: string; state: string; label: string; ageS: number }> = {}) =>
  ({ id: "t1", kind: "bash", state: "running", label: "npm run build", ageS: 75, ...over });

describe("bg_status → session state", () => {
  beforeEach(() => {
    __resetForTests();
    vi.stubGlobal("WebSocket", FakeSocket as unknown as typeof WebSocket);
    ensureConnected(SID);
    FakeSocket.last!.onopen?.();
    sendMessage(SID, "build it");
  });
  afterEach(() => { vi.unstubAllGlobals(); __resetForTests(); });

  it("keeps every child with kind, state, label and age, and the arrival time", () => {
    const before = Date.now();
    FakeSocket.last!.emit({ type: "bg_status", open: 2, children: [
      { id: "a", kind: "bash", state: "running", age_s: 5, label: "npm run build" },
      { id: "b", kind: "monitor", state: "running", age_s: 40, label: "tail -f app.log" },
    ] });
    const s = getSessionState(SID);
    expect(s.bgOpen).toBe(2);
    expect(s.bgChildren.map((c) => [c.id, c.kind, c.label, c.ageS])).toEqual([
      ["a", "bash", "npm run build", 5], ["b", "monitor", "tail -f app.log", 40]]);
    expect(s.bgChildrenAt).toBeGreaterThanOrEqual(before);
  });

  it("clears the list when the turn ends", () => {
    const ws = FakeSocket.last!;
    ws.emit({ type: "bg_status", open: 1, children: [{ id: "a", kind: "bash", state: "running", age_s: 1, label: "x" }] });
    expect(getSessionState(SID).bgChildren).toHaveLength(1);
    ws.emit({ type: "done" });
    expect(getSessionState(SID).bgChildren).toEqual([]);
    expect(getSessionState(SID).bgOpen).toBe(0);
  });

  it("tolerates an older server without id / label", () => {
    FakeSocket.last!.emit({ type: "bg_status", open: 1, children: [{ kind: "agent", state: "running", age_s: 3 }] });
    const c = getSessionState(SID).bgChildren[0];
    expect(c.label).toBe("");
    expect(c.id).toBeTruthy();
  });
});

describe("formatElapsed / orderChildren", () => {
  it("formats m:ss and h:mm:ss", () => {
    expect([0, 9, 75, 3600, 3700].map(formatElapsed)).toEqual(["0:00", "0:09", "1:15", "1:00:00", "1:01:40"]);
    expect(formatElapsed(-4)).toBe("0:00");
  });
  it("lists running first (oldest first) then only the last finished ones", () => {
    const rows = orderChildren([
      child({ id: "f1", state: "completed" }), child({ id: "f2", state: "completed" }),
      child({ id: "f3", state: "failed" }), child({ id: "f4", state: "completed" }),
      child({ id: "r1", ageS: 5 }), child({ id: "r2", ageS: 50 }),
    ]);
    expect(rows.map((r) => r.id)).toEqual(["r2", "r1", "f2", "f3", "f4"]);
  });
});

describe("<BackgroundActivity>", () => {
  beforeEach(() => { vi.useFakeTimers({ toFake: ["setInterval", "clearInterval", "Date"] }); });
  afterEach(() => { cleanup(); vi.useRealTimers(); });

  it("shows kind, label, a clock that keeps running, and a running count", () => {
    const at = Date.now();
    render(<BackgroundActivity children={[child(), child({ id: "m", kind: "monitor", label: "tail -f x", ageS: 3 })]} receivedAt={at} />);
    const rows = screen.getAllByTestId("bg-activity-row");
    expect(rows.map((r) => r.getAttribute("data-kind"))).toEqual(["bash", "monitor"]);
    expect(rows[0].textContent).toContain("Shell command");
    expect(rows[0].textContent).toContain("npm run build");
    expect(rows[0].textContent).toContain("1:15");
    expect(screen.getByTestId("bg-activity-summary").textContent).toBe("2 running in the background");
    act(() => { vi.advanceTimersByTime(5000); });
    expect(screen.getAllByTestId("bg-activity-row")[0].textContent).toContain("1:20");
  });

  it("marks failed and finished children and says when all work is done", () => {
    render(<BackgroundActivity children={[child({ state: "failed" }), child({ id: "o", state: "completed" })]} receivedAt={Date.now()} />);
    expect(screen.getByTestId("bg-activity-summary").textContent).toBe("Background work finished");
    expect(screen.getByLabelText("failed")).toBeTruthy();
    expect(screen.getByLabelText("finished")).toBeTruthy();
    expect(screen.queryByLabelText("running")).toBeNull();
  });

  it("collapses and re-expands, and renders nothing for an empty list", () => {
    const { container, rerender } = render(<BackgroundActivity children={[child()]} receivedAt={Date.now()} />);
    fireEvent.click(screen.getByRole("button"));
    expect(screen.queryAllByTestId("bg-activity-row")).toHaveLength(0);
    fireEvent.click(screen.getByRole("button"));
    expect(screen.getAllByTestId("bg-activity-row")).toHaveLength(1);
    rerender(<BackgroundActivity children={[]} receivedAt={0} />);
    expect(container.firstChild).toBeNull();
  });

  it("does not steal focus from the composer", () => {
    const ta = document.createElement("textarea");
    document.body.appendChild(ta);
    ta.focus();
    render(<BackgroundActivity children={[child()]} receivedAt={Date.now()} />);
    expect(document.activeElement).toBe(ta);
    ta.remove();
  });

  it("falls back to a generic label for an unknown kind and a missing description", () => {
    render(<BackgroundActivity children={[child({ kind: "other", label: "" })]} receivedAt={Date.now()} />);
    expect(screen.getByTestId("bg-activity-row").textContent).toContain("Background task");
  });
});
