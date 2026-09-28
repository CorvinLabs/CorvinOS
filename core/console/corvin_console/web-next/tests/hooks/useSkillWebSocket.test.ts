/**
 * Hook tests for useSkillWebSocket / useSkillStream (Phase 3a, ADR-2050).
 *
 * The socket is a controllable fake installed with vi.stubGlobal — MSW's
 * WebSocket interceptor makes `global.WebSocket` read-only, so the previous
 * `global.WebSocket = ...` assignment threw in every test, and the fake had
 * no OPEN/CLOSED statics, so the hook's readyState checks compared against
 * `undefined`.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { renderHook, act, waitFor } from "@testing-library/react";
import { useSkillWebSocket, useSkillStream } from "@/hooks/useSkillWebSocket";
import type { WebSocketEvent } from "@/types/websocket-events";

class FakeSocket {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;
  static instances: FakeSocket[] = [];

  readyState = FakeSocket.CONNECTING;
  sent: string[] = [];
  onopen: (() => void) | null = null;
  onclose: ((e: { code: number; reason: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  onmessage: ((e: { data: string }) => void) | null = null;
  close = vi.fn(() => {
    if (this.readyState === FakeSocket.CLOSED) return;
    this.readyState = FakeSocket.CLOSED;
    this.onclose?.({ code: 1005, reason: "" });
  });

  constructor(public url: string) {
    FakeSocket.instances.push(this);
  }
  send(data: string) {
    this.sent.push(data);
  }
  // test helpers
  open() {
    this.readyState = FakeSocket.OPEN;
    this.onopen?.();
  }
  drop(code = 1006, reason = "") {
    this.readyState = FakeSocket.CLOSED;
    this.onclose?.({ code, reason });
  }
  emit(event: WebSocketEvent) {
    this.onmessage?.({ data: JSON.stringify(event) });
  }
}

const latest = () => FakeSocket.instances[FakeSocket.instances.length - 1];

const confidence = (stream_id: string, v: number): WebSocketEvent =>
  ({
    type: "confidence_updated",
    stream_id,
    data: { new_confidence: v, version: 1, timestamp: "2026-09-27T00:00:00Z" },
    timestamp: "2026-09-27T00:00:00Z",
  }) as WebSocketEvent;

beforeEach(() => {
  FakeSocket.instances = [];
  vi.stubGlobal("WebSocket", FakeSocket);
});

afterEach(() => {
  // A test that fails mid-way never reaches its own useRealTimers(); without
  // this the faked clock leaks into the next test.
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("useSkillWebSocket", () => {
  it("starts disconnected and connects to the learning stream path", () => {
    const { result } = renderHook(() => useSkillWebSocket());
    expect(result.current.isConnected).toBe(false);
    expect(result.current.error).toBe(null);
    expect(result.current.subscribedChannels.size).toBe(0);
    expect(FakeSocket.instances).toHaveLength(1);
    expect(latest().url).toMatch(/\/v1\/console\/learning\/stream$/);
  });

  it("subscribes, sends the documented message, and unsubscribes", async () => {
    const { result } = renderHook(() => useSkillWebSocket());
    act(() => latest().open());
    await waitFor(() => expect(result.current.isConnected).toBe(true));

    const cb = vi.fn();
    act(() => {
      result.current.subscribe("workflow-optimizer", cb);
    });
    expect(result.current.subscribedChannels.has("workflow-optimizer")).toBe(true);
    expect(JSON.parse(latest().sent[0])).toEqual({ action: "subscribe", channel: "workflow-optimizer" });

    act(() => result.current.unsubscribe("workflow-optimizer", cb));
    expect(result.current.subscribedChannels.has("workflow-optimizer")).toBe(false);
    expect(JSON.parse(latest().sent[1])).toEqual({ action: "unsubscribe", channel: "workflow-optimizer" });
  });

  it("dispatches an event only to the matching channel's callbacks", async () => {
    const { result } = renderHook(() => useSkillWebSocket());
    act(() => latest().open());
    await waitFor(() => expect(result.current.isConnected).toBe(true));

    const a = vi.fn();
    const b = vi.fn();
    act(() => {
      result.current.subscribe("workflow-optimizer", a);
      result.current.subscribe("security-orchestrator", b);
    });
    const ev = confidence("workflow-optimizer", 0.75);
    act(() => latest().emit(ev));
    expect(a).toHaveBeenCalledWith(ev);
    expect(b).not.toHaveBeenCalled();
  });

  it("reconnects with backoff after a drop and re-subscribes on the new socket", async () => {
    vi.useFakeTimers();
    const { result } = renderHook(() => useSkillWebSocket());
    act(() => latest().open());
    act(() => {
      result.current.subscribe("flow-guard", vi.fn());
    });

    act(() => latest().drop());
    expect(result.current.isConnected).toBe(false);
    expect(result.current.isReconnecting).toBe(true);
    expect(FakeSocket.instances).toHaveLength(1);

    act(() => {
      vi.advanceTimersByTime(1000);
    });
    expect(FakeSocket.instances).toHaveLength(2);
    act(() => latest().open());
    expect(result.current.isConnected).toBe(true);
    expect(JSON.parse(latest().sent[0])).toEqual({ action: "subscribe", channel: "flow-guard" });
    // Real timers again BEFORE the shared cleanup hooks run: RTL's async
    // cleanup waits on setTimeout and would hang on a faked clock.
    vi.useRealTimers();
  });

  it("closes on unmount and never reconnects afterwards", () => {
    vi.useFakeTimers();
    const { unmount } = renderHook(() => useSkillWebSocket());
    const first = latest();
    act(() => first.open());
    unmount();
    expect(first.close).toHaveBeenCalled();
    act(() => {
      vi.advanceTimersByTime(120_000);
    });
    // Before the fix, the unmount's own close() fired onclose, which
    // scheduled a reconnect: a new socket appeared here.
    expect(FakeSocket.instances).toHaveLength(1);
    expect(vi.getTimerCount()).toBe(0);
    vi.useRealTimers();
  });

  // routes/learning_stream.py accepts, then closes 4401 (no session) or 4501
  // (not_implemented). Both are final answers: reconnecting only repeats them.
  // Before the fix the hook reopened the socket every second forever — onopen
  // reset the backoff, so it never even backed off.
  it.each([
    [4401, "no session"],
    [4501, "not_implemented"],
  ])("stops reconnecting on close code %i", (code, reason) => {
    vi.useFakeTimers();
    const { result } = renderHook(() => useSkillWebSocket());
    act(() => latest().open());
    act(() => latest().drop(code, reason));
    act(() => {
      vi.advanceTimersByTime(120_000);
    });
    expect(FakeSocket.instances).toHaveLength(1);
    expect(result.current.isConnected).toBe(false);
    expect(result.current.isReconnecting).toBe(false);
    expect(result.current.error).toContain(String(code));
    expect(vi.getTimerCount()).toBe(0);
    vi.useRealTimers();
  });

  it("keeps backing off when a socket opens and drops without delivering a message", () => {
    vi.useFakeTimers();
    renderHook(() => useSkillWebSocket());
    // open → drop cycles: an open alone is not proof of a working stream, so
    // the delay must grow 1s, 2s, 4s instead of resetting to 1s each time.
    for (const delay of [1000, 2000, 4000]) {
      const n = FakeSocket.instances.length;
      act(() => latest().open());
      act(() => latest().drop());
      act(() => {
        vi.advanceTimersByTime(delay - 1);
      });
      expect(FakeSocket.instances).toHaveLength(n);
      act(() => {
        vi.advanceTimersByTime(1);
      });
      expect(FakeSocket.instances).toHaveLength(n + 1);
    }
    vi.useRealTimers();
  });

  it("resets the backoff once a message has been received", () => {
    vi.useFakeTimers();
    renderHook(() => useSkillWebSocket());
    // Two failed cycles push the next delay to 4s …
    for (const delay of [1000, 2000]) {
      act(() => latest().open());
      act(() => latest().drop());
      act(() => {
        vi.advanceTimersByTime(delay);
      });
    }
    // … a delivered message proves the stream works, so the next drop is 1s.
    act(() => latest().open());
    act(() => latest().emit(confidence("flow-guard", 0.5)));
    act(() => latest().drop());
    const n = FakeSocket.instances.length;
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    expect(FakeSocket.instances).toHaveLength(n + 1);
    vi.useRealTimers();
  });

  it("does not reopen the socket on every render when callbacks are inline", () => {
    const { rerender } = renderHook(() => useSkillWebSocket({ onConnect: () => {} }));
    act(() => latest().open());
    rerender();
    rerender();
    expect(FakeSocket.instances).toHaveLength(1);
  });
});

describe("useSkillStream", () => {
  it("subscribes automatically and collects updates for its channel", async () => {
    const { result } = renderHook(() => useSkillStream("workflow-optimizer"));
    act(() => latest().open());
    await waitFor(() => expect(result.current.isConnected).toBe(true));
    expect(latest().sent.map((s) => JSON.parse(s))).toContainEqual({
      action: "subscribe",
      channel: "workflow-optimizer",
    });

    act(() => latest().emit(confidence("workflow-optimizer", 0.9)));
    act(() => latest().emit(confidence("flow-guard", 0.1)));
    await waitFor(() => expect(result.current.updates).toHaveLength(1));
  });
});
