/**
 * The `turn_usage` stream event must reach the assistant bubble of the turn and
 * format as model + tokens; absent usage reads "unknown", never a number.
 * Driven through the registry's real WebSocket message path (fake socket only).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { __resetForTests, ensureConnected, getSessionState, sendMessage } from "@/lib/chat-registry";
import { formatPendingTurnUsage, formatTurnUsage, parseTurnUsage } from "@/lib/turn-usage";
import { hydrateChatTurn } from "@/pages/chat";

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

const SID = "turn-usage-test";

describe("chat turn usage", () => {
  beforeEach(() => {
    __resetForTests();
    vi.stubGlobal("WebSocket", FakeSocket as unknown as typeof WebSocket);
    ensureConnected(SID);
    FakeSocket.last!.onopen?.();
  });
  afterEach(() => { vi.unstubAllGlobals(); __resetForTests(); });

  it("stamps model and tokens on the assistant message", () => {
    const ws = FakeSocket.last!;
    sendMessage(SID, "hello");
    ws.emit({ type: "delta", text: "hi" });
    ws.emit({ type: "engine", engine: "claude_code", label: "Claude Code (OS-Engine)" });
    ws.emit({ type: "turn_usage", model: "claude-haiku-4-5", usage: {
      input_tokens: 1200, output_tokens: 340, cache_read_input_tokens: 5000, cache_creation_input_tokens: 0 } });
    const m = getSessionState(SID).messages.find((x) => x.role === "assistant")!;
    expect(m.turnModel).toBe("claude-haiku-4-5");
    expect(formatTurnUsage(m.turnModel, m.turnUsage)).toBe(
      "Model: claude-haiku-4-5 · Tokens: 1,200 in / 340 out · Cache: 5,000 read");
  });

  it("says unknown when the engine reported nothing", () => {
    expect(formatTurnUsage(null, null)).toBe("Model: unknown · Tokens: unknown");
  });

  it("shows a count the CLI did not report as unknown, never as 0", () => {
    const usage = parseTurnUsage({ output_tokens: 5 });
    expect(usage).toEqual({
      input_tokens: null, output_tokens: 5,
      cache_read_input_tokens: null, cache_creation_input_tokens: null,
    });
    expect(formatTurnUsage("m", usage)).toBe("Model: m · Tokens: unknown in / 5 out");
  });

  it("names the routed model only when the confirmed one differs", () => {
    expect(formatTurnUsage("claude-haiku-4-5", null, "claude-opus-5")).toBe(
      "Model: claude-haiku-4-5 (routed: claude-opus-5) · Tokens: unknown");
    // A dated snapshot and its alias are the same model: no hint.
    expect(formatTurnUsage("claude-haiku-4-5-20251001", null, "claude-haiku-4-5")).toBe(
      "Model: claude-haiku-4-5-20251001 · Tokens: unknown");
  });

  it("shows the routed model while the turn runs, before any confirmed model", () => {
    const ws = FakeSocket.last!;
    sendMessage(SID, "hello");
    ws.emit({ type: "engine", engine: "claude_code", label: "Claude Code (OS-Engine)",
              routed_model: "claude-opus-5" });
    const m = getSessionState(SID).messages.find((x) => x.role === "assistant")!;
    expect(m.turnUsageSeen).toBeFalsy();
    expect(m.streaming).toBe(true);
    expect(m.turnRoutedModel).toBe("claude-opus-5");
    // SRF-11: a running turn's values are pending, never "unknown".
    expect(formatPendingTurnUsage(m.turnRoutedModel)).toBe(
      "Model: claude-opus-5 (routed) · Tokens: pending");
    expect(formatPendingTurnUsage(null)).toBe("Model: pending · Tokens: pending");
  });

  it("restores the model and tokens of a persisted turn after a reload", () => {
    const m = hydrateChatTurn({
      role: "assistant", ts: 1, parts: [{ kind: "text", text: "done" }],
      turn_usage: {
        model: "claude-haiku-4-5", routed_model: "claude-opus-5",
        usage: { input_tokens: 1200, output_tokens: 340,
                 cache_read_input_tokens: 5000, cache_creation_input_tokens: 0 },
      },
    }, 0, SID);
    expect(m.engine).toBe("claude_code");
    expect(m.turnUsageSeen).toBe(true);
    expect(formatTurnUsage(m.turnModel, m.turnUsage, m.turnRoutedModel)).toBe(
      "Model: claude-haiku-4-5 (routed: claude-opus-5) · Tokens: 1,200 in / 340 out · Cache: 5,000 read");
  });

  it("leaves a legacy turn without turn_usage with no model line at all", () => {
    const m = hydrateChatTurn({ role: "assistant", ts: 2, parts: [{ kind: "text", text: "old" }] }, 1, SID);
    expect(m.engine).toBeUndefined();
    expect(m.turnUsageSeen).toBeUndefined();
  });
});
