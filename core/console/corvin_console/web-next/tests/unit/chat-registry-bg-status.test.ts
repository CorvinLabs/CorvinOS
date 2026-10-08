/**
 * A turn whose claude process keeps background tasks alive emits one `result` per
 * wake-up plus `bg_status` events. The registry must (a) track how many tasks are open,
 * (b) never treat an interim result as the answer to replay/speak, and (c) clear the
 * count when the turn ends. Driven through the real WebSocket message path of the
 * registry (a fake socket stands in for the network, nothing else is stubbed).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  __resetForTests,
  ensureConnected,
  getSessionState,
  subscribeEvents,
  type StreamEvent,
} from "@/lib/chat-registry";

class FakeSocket {
  static last: FakeSocket | null = null;
  static OPEN = 1;
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

const SID = "bg-status-test";

describe("chat registry — background tasks of a turn", () => {
  beforeEach(() => {
    __resetForTests();
    vi.stubGlobal("WebSocket", FakeSocket as unknown as typeof WebSocket);
    ensureConnected(SID);
    FakeSocket.last!.onopen?.();
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    __resetForTests();
  });

  it("tracks the open count from bg_status and clears it when the turn ends", () => {
    const ws = FakeSocket.last!;
    ws.emit({ type: "bg_status", open: 2, children: [] });
    expect(getSessionState(SID).bgOpen).toBe(2);
    ws.emit({ type: "bg_status", open: 0, children: [] });
    expect(getSessionState(SID).bgOpen).toBe(0);
    ws.emit({ type: "bg_status", open: 1, children: [] });
    ws.emit({ type: "done" });
    expect(getSessionState(SID).bgOpen).toBe(0);
  });

  it("an interim result is not the text to replay; the final one is", () => {
    const ws = FakeSocket.last!;
    ws.emit({ type: "result", text: "started it", interim: true, pending_children: 1 });
    expect(getSessionState(SID).latestResultText).toBeNull();
    ws.emit({ type: "result", text: "all done", final: true, pending_children: 0 });
    expect(getSessionState(SID).latestResultText).toBe("all done");
  });

  it("a result without the new flags behaves exactly as before", () => {
    FakeSocket.last!.emit({ type: "result", text: "plain answer" });
    expect(getSessionState(SID).latestResultText).toBe("plain answer");
  });

  it("subscribers see the interim flag so the chat page can skip speaking it", () => {
    const seen: StreamEvent[] = [];
    const off = subscribeEvents(SID, (e) => seen.push(e));
    FakeSocket.last!.emit({ type: "result", text: "update", interim: true });
    off();
    expect(seen.map((e) => [e.type, e.interim])).toEqual([["result", true]]);
  });
});
