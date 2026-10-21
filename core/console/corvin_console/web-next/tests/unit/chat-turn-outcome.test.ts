/**
 * T-0250: the `outcome` the server tags on a turn's closing `result` event
 * (ADR-2236 P1: ok / cut / process_died) reaches the assistant message of the turn and
 * maps to the English banner text the chat page renders. Driven through the registry's
 * real WebSocket message path (a fake socket only; nothing else is stubbed).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { __resetForTests, ensureConnected, getSessionState, sendMessage } from "@/lib/chat-registry";
import { parseTurnOutcome, turnOutcomeIsFailure, turnOutcomeText } from "@/lib/turn-outcome";

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

const SID = "turn-outcome-test";

function assistant() {
  return getSessionState(SID).messages.find((x) => x.role === "assistant")!;
}

describe("chat turn outcome — registry", () => {
  beforeEach(() => {
    __resetForTests();
    vi.stubGlobal("WebSocket", FakeSocket as unknown as typeof WebSocket);
    ensureConnected(SID);
    FakeSocket.last!.onopen?.();
    sendMessage(SID, "please do it");
    FakeSocket.last!.emit({ type: "delta", text: "started it" });
  });
  afterEach(() => { vi.unstubAllGlobals(); __resetForTests(); });

  it("a killed turn is stamped process_died and renders the failure banner", () => {
    FakeSocket.last!.emit({ type: "result", text: "ended while 1 background task was still running",
      final: true, pending_children: 0, outcome: "process_died" });
    const m = assistant();
    expect(m.outcome).toBe("process_died");
    expect(turnOutcomeText(m.outcome)).toMatch(/^The worker process died/);
    expect(turnOutcomeIsFailure(m.outcome)).toBe(true);
  });

  it("a turn cut at a scope limit is stamped cut and renders the notice", () => {
    FakeSocket.last!.emit({ type: "result", text: "cut", final: true, pending_children: 0, outcome: "cut" });
    const m = assistant();
    expect(m.outcome).toBe("cut");
    expect(turnOutcomeText(m.outcome)).toMatch(/^The turn was cut at the scope limit/);
    expect(turnOutcomeIsFailure(m.outcome)).toBe(false);
  });

  it("a clean turn is stamped ok and renders nothing", () => {
    FakeSocket.last!.emit({ type: "result", text: "done", annotation_pending: false, outcome: "ok" });
    expect(assistant().outcome).toBe("ok");
    expect(turnOutcomeText(assistant().outcome)).toBeNull();
  });

  it("an interim result never closes the turn, so its outcome is not stamped", () => {
    FakeSocket.last!.emit({ type: "result", text: "update", interim: true, pending_children: 1, outcome: "ok" });
    expect(assistant().outcome).toBeUndefined();
    FakeSocket.last!.emit({ type: "result", text: "final", final: true, pending_children: 0, outcome: "process_died" });
    expect(assistant().outcome).toBe("process_died");
  });

  it("a result without the field, or outside the enum, leaves the turn untagged", () => {
    FakeSocket.last!.emit({ type: "result", text: "plain" });
    expect(assistant().outcome).toBeUndefined();
    FakeSocket.last!.emit({ type: "result", text: "odd", outcome: "exploded" });
    expect(assistant().outcome).toBeUndefined();
  });

  it("the outcome survives the end of the turn", () => {
    FakeSocket.last!.emit({ type: "result", text: "x", final: true, outcome: "cut" });
    FakeSocket.last!.emit({ type: "done" });
    expect(assistant().outcome).toBe("cut");
  });
});

describe("chat turn outcome — renderer", () => {
  it("maps the closed enum to English and nothing else", () => {
    expect(parseTurnOutcome("ok")).toBe("ok");
    expect(parseTurnOutcome("cut")).toBe("cut");
    expect(parseTurnOutcome("process_died")).toBe("process_died");
    expect(parseTurnOutcome("killed")).toBeNull();
    expect(parseTurnOutcome(42)).toBeNull();
    expect(turnOutcomeText("process_died")).toBe(
      "The worker process died while a background task was still running — the text above is its failure notice, not an answer.");
    expect(turnOutcomeText("cut")).toBe(
      "The turn was cut at the scope limit — its remaining background tasks were stopped; the text above says what was cut.");
    expect(turnOutcomeText("ok")).toBeNull();
    expect(turnOutcomeText(undefined)).toBeNull();
  });
});
