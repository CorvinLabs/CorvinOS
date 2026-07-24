/**
 * Unit tests for chat-registry.ts
 *
 * Tests cover:
 * - Session creation and state isolation
 * - loadHistory guard (race with live WS messages)
 * - applyEvent: all StreamEvent types
 * - sendMessage: happy path + WS-not-ready guard
 * - onclose: ghost-placeholder resolution
 * - subscribeState / subscribeEvents wiring
 * - closeSession: cleanup + no leaked listeners
 * - getSessionState snapshot freshness (useSyncExternalStore contract)
 * - __resetForTests isolation between test cases
 */

import { describe, it, expect, beforeEach, vi } from "vitest";
import {
  ensureConnected,
  loadHistory,
  sendMessage,
  getSessionState,
  subscribeState,
  subscribeEvents,
  closeSession,
  consumePendingTitle,
  __resetForTests,
  type ChatMessage,
} from "@/lib/chat-registry";

// ── WebSocket mock ────────────────────────────────────────────────────────────
// Use numeric constants (0/1/2/3) directly rather than WebSocket.CONNECTING etc.
// because the stubbed WebSocket constructor does not set static properties.
const WS_CONNECTING = 0;
const WS_OPEN = 1;
const WS_CLOSING = 2;
const WS_CLOSED = 3;

class MockWS {
  readyState: number = WS_CONNECTING;
  sent: string[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((ev: { data: string }) => void) | null = null;
  onclose: ((ev: { code: number }) => void) | null = null;
  onerror: (() => void) | null = null;

  open() {
    this.readyState = WS_OPEN;
    this.onopen?.();
  }

  emit(data: object) {
    this.onmessage?.({ data: JSON.stringify(data) });
  }

  close(code = 1000) {
    this.readyState = WS_CLOSED;
    this.onclose?.({ code });
  }

  error() {
    this.readyState = WS_CLOSED;
    this.onerror?.();
  }

  send(data: string) {
    this.sent.push(data);
  }
}

let mockWs: MockWS;

// Build a WebSocket stub that includes the required static constants.
function makeWebSocketStub(instance: MockWS) {
  // vitest@4: arrow functions cannot be constructors; use a regular function.
   
  const ctor = vi.fn(function (this: unknown) { return instance; });
  // These constants must match the values used in chat-registry.ts's runtime checks.
  Object.assign(ctor, {
    CONNECTING: WS_CONNECTING,
    OPEN: WS_OPEN,
    CLOSING: WS_CLOSING,
    CLOSED: WS_CLOSED,
  });
  return ctor;
}

beforeEach(() => {
  __resetForTests();
  mockWs = new MockWS();
  vi.stubGlobal("WebSocket", makeWebSocketStub(mockWs));
  vi.stubGlobal("window", {
    location: { protocol: "http:", host: "localhost" },
  });
});

// ── Helper ────────────────────────────────────────────────────────────────────

function connect(sid: string): MockWS {
  ensureConnected(sid);
  mockWs.open();
  return mockWs;
}

// ── Tests ─────────────────────────────────────────────────────────────────────

describe("ensureConnected", () => {
  it("opens a WebSocket for a new session", () => {
    ensureConnected("s1");
    expect(WebSocket).toHaveBeenCalledWith(
      "ws://localhost/v1/console/chat/sessions/s1/stream"
    );
  });

  it("is a no-op when already OPEN", () => {
    connect("s1");
    ensureConnected("s1");
    expect(WebSocket).toHaveBeenCalledTimes(1);
  });

  it("is a no-op when still CONNECTING", () => {
    ensureConnected("s1"); // readyState = CONNECTING, not yet open
    ensureConnected("s1");
    expect(WebSocket).toHaveBeenCalledTimes(1);
  });

  it("reconnects after WS is closed", () => {
    const ws1 = connect("s1");
    ws1.close(); // WS closes — readyState = CLOSED

    // Stub a fresh WS for the reconnect
    const ws2 = new MockWS();
    const stub2 = makeWebSocketStub(ws2);
    vi.stubGlobal("WebSocket", stub2);

    ensureConnected("s1");
    expect(stub2).toHaveBeenCalledTimes(1);
    expect(ws2.readyState).toBe(WS_CONNECTING);
  });

  it("sets error=null on open and notifies subscribers", () => {
    const listener = vi.fn();
    subscribeState("s1", listener);
    connect("s1");
    expect(listener).toHaveBeenCalled();
    expect(getSessionState("s1").error).toBeNull();
  });
});

describe("loadHistory", () => {
  it("loads history on first visit", () => {
    const msgs: ChatMessage[] = [
      { id: "h1", role: "user", parts: [{ kind: "text", text: "hi" }], ts: 1 },
    ];
    loadHistory("s1", msgs);
    expect(getSessionState("s1").messages).toHaveLength(1);
  });

  it("does not overwrite on second call (historyLoaded guard)", () => {
    const msgs1: ChatMessage[] = [
      { id: "h1", role: "user", parts: [{ kind: "text", text: "first" }], ts: 1 },
    ];
    const msgs2: ChatMessage[] = [
      { id: "h2", role: "user", parts: [{ kind: "text", text: "second" }], ts: 2 },
    ];
    loadHistory("s1", msgs1);
    loadHistory("s1", msgs2);
    expect(getSessionState("s1").messages[0].id).toBe("h1");
  });

  it("does NOT overwrite live-streamed messages (WS race fix)", () => {
    // Simulate: WS delivers a delta BEFORE getChatTurns resolves.
    connect("s1");
    sendMessage("s1", "hello");
    // Simulate one delta arriving
    mockWs.emit({ type: "delta", text: "live content" });

    const oldMessages = getSessionState("s1").messages;
    expect(oldMessages.length).toBeGreaterThan(0);

    // History arrives late — must NOT overwrite live buffer
    const history: ChatMessage[] = [
      { id: "hist-1", role: "user", parts: [{ kind: "text", text: "old" }], ts: 0 },
    ];
    loadHistory("s1", history);

    // Live content preserved
    expect(getSessionState("s1").messages).toBe(oldMessages);
    expect(getSessionState("s1").messages.some((m) => m.id === "hist-1")).toBe(false);
  });

  it("does NOT overwrite when session is currently streaming", () => {
    connect("s1");
    sendMessage("s1", "hello");
    // entry.streaming is now true

    const history: ChatMessage[] = [];
    loadHistory("s1", history);

    // Messages from sendMessage still present (user + placeholder)
    expect(getSessionState("s1").messages).toHaveLength(2);
  });

  it("notifies subscribers when history is loaded", () => {
    const listener = vi.fn();
    subscribeState("s1", listener);
    loadHistory("s1", []);
    expect(listener).toHaveBeenCalled();
  });
});

describe("applyEvent / StreamEvent processing", () => {
  it("appends delta text to the assistant placeholder", () => {
    connect("s1");
    sendMessage("s1", "hello");
    mockWs.emit({ type: "delta", text: "Hello " });
    mockWs.emit({ type: "delta", text: "world" });

    const msgs = getSessionState("s1").messages;
    const assistant = msgs.find((m) => m.role === "assistant")!;
    const textPart = assistant.parts.find((p) => p.kind === "text");
    expect(textPart?.kind === "text" && textPart.text).toBe("Hello world");
  });

  it("appends a tool_use part", () => {
    connect("s1");
    sendMessage("s1", "run");
    mockWs.emit({ type: "tool_use", name: "bash", input: { cmd: "ls" } });

    const msgs = getSessionState("s1").messages;
    const assistant = msgs.find((m) => m.role === "assistant")!;
    const toolPart = assistant.parts.find((p) => p.kind === "tool");
    expect(toolPart?.kind === "tool" && toolPart.name).toBe("bash");
  });

  it("stores latestResultText on result event", () => {
    connect("s1");
    sendMessage("s1", "test");
    mockWs.emit({ type: "result", text: "Final answer" });
    expect(getSessionState("s1").latestResultText).toBe("Final answer");
  });

  it("marks assistant message as errored on error event", () => {
    connect("s1");
    sendMessage("s1", "test");
    mockWs.emit({ type: "error", message: "rate limited" });

    const msgs = getSessionState("s1").messages;
    const assistant = msgs.find((m) => m.role === "assistant")!;
    expect(assistant.error).toBe("rate limited");
  });

  it("clears streaming flag and currentAssistantId on done event", () => {
    connect("s1");
    sendMessage("s1", "test");
    expect(getSessionState("s1").streaming).toBe(true);

    mockWs.emit({ type: "done" });
    const state = getSessionState("s1");
    expect(state.streaming).toBe(false);

    const assistant = state.messages.find((m) => m.role === "assistant")!;
    expect(assistant.streaming).toBe(false);
  });

  it("sets pendingTitle on session_title event", () => {
    connect("s1");
    mockWs.emit({ type: "session_title", title: "My Chat" });
    expect(getSessionState("s1").pendingTitle).toBe("My Chat");
  });

  it("attaches a voice artifact to the still-streaming assistant message", () => {
    connect("s1");
    sendMessage("s1", "hello");
    mockWs.emit({ type: "delta", text: "Hi" });
    mockWs.emit({
      type: "voice", name: "a.ogg", path: "voice/a.ogg", mime: "audio/ogg",
      size: 42, label: "voice",
    });

    const assistant = getSessionState("s1").messages.find((m) => m.role === "assistant")!;
    const voicePart = assistant.parts.find((p) => p.kind === "artifact");
    expect(voicePart).toMatchObject({ name: "a.ogg", path: "voice/a.ogg", mime: "audio/ogg", size: 42 });
  });

  it("attaches a late voice event (after done) to the just-completed turn via lastAssistantId", () => {
    connect("s1");
    sendMessage("s1", "hello");
    mockWs.emit({ type: "delta", text: "Hi" });
    mockWs.emit({ type: "done" });
    expect(getSessionState("s1").messages.find((m) => m.role === "assistant")!.parts
      .some((p) => p.kind === "artifact")).toBe(false);

    // /voice/tts resolves after "done" already cleared currentAssistantId —
    // the player must still land on the turn that just finished.
    mockWs.emit({
      type: "voice", name: "a.ogg", path: "voice/a.ogg", mime: "audio/ogg",
      size: 42, label: "voice",
    });

    const assistant = getSessionState("s1").messages.find((m) => m.role === "assistant")!;
    expect(assistant.parts.some((p) => p.kind === "artifact")).toBe(true);
  });

  it("attaches tdeProgress on engine_progress, including ADR-0216 quota + classification fields", () => {
    connect("s1");
    sendMessage("s1", "do a tiered task");
    mockWs.emit({ type: "delta", text: "working…" });
    mockWs.emit({
      type: "engine_progress",
      engine: "tiered_delegation",
      run_id: "tde-123-abc",
      total_steps: 3,
      completed_steps: 2,
      delegated_count: 1,
      local_count: 1,
      l34_forced: false,
      latency_delta_pct: 12.5,
      token_savings_pct: null,
      token_usage_instrumented: false,
      quota_used_today: 4,
      quota_limit: 10,
      task_type: "code_generation",
      complexity: "complex",
    });

    const assistant = getSessionState("s1").messages.find((m) => m.role === "assistant")!;
    expect(assistant.tdeProgress).toMatchObject({
      run_id: "tde-123-abc",
      total_steps: 3,
      completed_steps: 2,
      delegated_count: 1,
      local_count: 1,
      l34_forced: false,
      latency_delta_pct: 12.5,
      token_savings_pct: null,
      quota_used_today: 4,
      quota_limit: 10,
      task_type: "code_generation",
      complexity: "complex",
    });
    expect(assistant.tdeRunId).toBe("tde-123-abc");
  });

  it("maps engine_progress quota_limit to null for the unlimited-tier case (never fabricates a cap)", () => {
    connect("s1");
    sendMessage("s1", "do a tiered task");
    mockWs.emit({ type: "delta", text: "working…" });
    mockWs.emit({
      type: "engine_progress",
      engine: "tiered_delegation",
      run_id: "tde-456-def",
      total_steps: 1,
      completed_steps: 1,
      delegated_count: 0,
      local_count: 1,
      l34_forced: false,
      quota_used_today: 7,
      quota_limit: null,
      task_type: "analysis",
      complexity: "simple",
    });

    const assistant = getSessionState("s1").messages.find((m) => m.role === "assistant")!;
    expect(assistant.tdeProgress?.quota_used_today).toBe(7);
    expect(assistant.tdeProgress?.quota_limit).toBeNull();
  });

  it("ignores a voice event with no completed or in-flight turn to attach to", () => {
    connect("s1");
    mockWs.emit({
      type: "voice", name: "a.ogg", path: "voice/a.ogg", mime: "audio/ogg",
      size: 42, label: "voice",
    });
    expect(getSessionState("s1").messages).toHaveLength(0);
  });

  it("is a no-op for unknown event types", () => {
    connect("s1");
    const before = getSessionState("s1");
    mockWs.emit({ type: "pong" });
    // No crash, state unchanged
    expect(getSessionState("s1").messages).toEqual(before.messages);
  });

  it("silently skips malformed JSON in onmessage", () => {
    connect("s1");
    mockWs.onmessage?.({ data: "not-json" });
    // No crash
    expect(getSessionState("s1").messages).toHaveLength(0);
  });
});

describe("ghost-placeholder resolution on WS close", () => {
  it("removes empty placeholder on transient close — no ghost 'Connection lost' bubble", () => {
    connect("s1");
    sendMessage("s1", "hello");
    expect(getSessionState("s1").streaming).toBe(true);

    // WS drops without ANY delta arriving — placeholder is still empty
    mockWs.close(1006);

    const state = getSessionState("s1");
    expect(state.streaming).toBe(false);
    // Empty placeholder must be removed, not left with a "Connection lost" error
    expect(state.messages.find((m) => m.role === "assistant")).toBeUndefined();
    // Session-level error stays null (transient reconnect)
    expect(state.error).toBeNull();
  });

  it("ends streaming on partial response without error on transient close", () => {
    connect("s1");
    sendMessage("s1", "hello");
    // A delta arrives before the connection drops
    mockWs.emit({ type: "delta", text: "Hello" });

    mockWs.close(1006);

    const state = getSessionState("s1");
    const assistant = state.messages.find((m) => m.role === "assistant")!;
    expect(assistant).toBeDefined();
    expect(assistant.streaming).toBe(false);
    // No error — partial content is self-evident; reconnecting banner carries context
    expect(assistant.error).toBeUndefined();
    // Partial content is preserved
    expect(assistant.parts[0]).toMatchObject({ kind: "text", text: "Hello" });
  });

  it("sets 4401 error on session-expired close code", () => {
    connect("s1");
    mockWs.close(4401);
    expect(getSessionState("s1").error).toMatch(/expired/i);
  });

  it("sets 4404 error on session-not-found close code", () => {
    connect("s1");
    mockWs.close(4404);
    expect(getSessionState("s1").error).toMatch(/not found/i);
  });

  it("enters calm reconnecting state (no terminal error) on WS onerror", () => {
    connect("s1");
    mockWs.error();
    // onerror is transient: NOT a terminal error — the registry auto-recovers.
    expect(getSessionState("s1").error).toBeNull();
    expect(getSessionState("s1").reconnecting).toBe(true);
    expect(getSessionState("s1").streaming).toBe(false);
  });

  it("enters calm reconnecting state on unexpected close (1012 server restart)", () => {
    connect("s1");
    mockWs.close(1012);
    expect(getSessionState("s1").error).toBeNull();
    expect(getSessionState("s1").reconnecting).toBe(true);
  });
});

describe("sendMessage", () => {
  it("returns null if session does not exist yet", () => {
    expect(sendMessage("nonexistent", "hi")).toBeNull();
  });

  it("returns null if WS is not OPEN", () => {
    ensureConnected("s1"); // still CONNECTING
    expect(sendMessage("s1", "hi")).toBeNull();
  });

  it("returns null for empty text", () => {
    connect("s1");
    expect(sendMessage("s1", "  ")).toBeNull();
  });

  it("returns null when already streaming", () => {
    connect("s1");
    sendMessage("s1", "first");
    expect(sendMessage("s1", "second")).toBeNull();
  });

  it("adds user + placeholder messages and sets streaming=true", () => {
    connect("s1");
    const result = sendMessage("s1", "hello");

    expect(result).not.toBeNull();
    const state = getSessionState("s1");
    expect(state.messages).toHaveLength(2);
    expect(state.messages[0].role).toBe("user");
    expect(state.messages[1].role).toBe("assistant");
    expect(state.messages[1].streaming).toBe(true);
    expect(state.streaming).toBe(true);
  });

  it("sends the WS frame", () => {
    connect("s1");
    sendMessage("s1", "hello");
    expect(mockWs.sent).toHaveLength(1);
    expect(JSON.parse(mockWs.sent[0])).toEqual({ type: "user", text: "hello" });
  });

  it("resets latestResultText on new send", () => {
    connect("s1");
    sendMessage("s1", "first");
    mockWs.emit({ type: "result", text: "Previous result" });
    mockWs.emit({ type: "done" });

    sendMessage("s1", "second");
    expect(getSessionState("s1").latestResultText).toBeNull();
  });
});

describe("subscribeState", () => {
  it("fires on every notifyState call", () => {
    const listener = vi.fn();
    subscribeState("s1", listener);
    connect("s1");
    sendMessage("s1", "test");
    mockWs.emit({ type: "delta", text: "hi" });
    expect(listener.mock.calls.length).toBeGreaterThanOrEqual(3);
  });

  it("returns an unsubscribe function that stops further calls", () => {
    const listener = vi.fn();
    const unsub = subscribeState("s1", listener);
    connect("s1");
    const countAfterConnect = listener.mock.calls.length;
    unsub();
    sendMessage("s1", "test");
    expect(listener.mock.calls.length).toBe(countAfterConnect);
  });

  it("snapshot reference changes on each mutation (useSyncExternalStore contract)", () => {
    const s1 = getSessionState("s1");
    connect("s1"); // triggers notifyState
    const s2 = getSessionState("s1");
    // New object reference after mutation
    expect(s2).not.toBe(s1);
  });
});

describe("subscribeEvents", () => {
  it("fires with raw StreamEvent on each WS message", () => {
    const listener = vi.fn();
    subscribeEvents("s1", listener);
    connect("s1");
    sendMessage("s1", "test");
    mockWs.emit({ type: "delta", text: "x" });

    expect(listener).toHaveBeenCalledWith(
      expect.objectContaining({ type: "delta", text: "x" })
    );
  });

  it("returns an unsubscribe function", () => {
    const listener = vi.fn();
    const unsub = subscribeEvents("s1", listener);
    connect("s1");
    unsub();
    mockWs.emit({ type: "delta", text: "x" });
    expect(listener).not.toHaveBeenCalled();
  });
});

describe("consumePendingTitle", () => {
  it("clears pendingTitle and notifies", () => {
    connect("s1");
    mockWs.emit({ type: "session_title", title: "My Chat" });
    expect(getSessionState("s1").pendingTitle).toBe("My Chat");

    consumePendingTitle("s1");
    expect(getSessionState("s1").pendingTitle).toBeNull();
  });
});

describe("closeSession", () => {
  it("closes the WebSocket", () => {
    const ws = connect("s1");
    const closeSpy = vi.spyOn(ws, "close");
    closeSession("s1");
    expect(closeSpy).toHaveBeenCalled();
  });

  it("removes the session entry so a new session starts fresh", () => {
    connect("s1");
    loadHistory("s1", [
      { id: "h1", role: "user", parts: [{ kind: "text", text: "old" }], ts: 1 },
    ]);
    closeSession("s1");

    // New session should start with empty messages
    const ws2 = new MockWS();
    vi.mocked(WebSocket).mockImplementationOnce(function () { return ws2 as unknown as WebSocket; });
    ws2.open.call(ws2);
    ensureConnected("s1");
    ws2.open();
    expect(getSessionState("s1").messages).toHaveLength(0);
  });

  it("unregisters all listeners so subscribers don't fire after close", () => {
    const stateListener = vi.fn();
    const eventListener = vi.fn();
    subscribeState("s1", stateListener);
    subscribeEvents("s1", eventListener);
    connect("s1");
    stateListener.mockClear();
    eventListener.mockClear();

    closeSession("s1");
    // No more callbacks after close
    mockWs.emit({ type: "delta", text: "after-close" });
    expect(stateListener).not.toHaveBeenCalled();
    expect(eventListener).not.toHaveBeenCalled();
  });

  it("is a no-op for unknown session ids", () => {
    expect(() => closeSession("nonexistent")).not.toThrow();
  });
});

describe("session isolation (multiple sessions)", () => {
  it("messages from s1 do not appear in s2", () => {
    const ws1 = new MockWS();
    const ws2 = new MockWS();
    const wsMock = vi.mocked(WebSocket);
    wsMock.mockImplementationOnce(function () { return ws1 as unknown as WebSocket; });
    wsMock.mockImplementationOnce(function () { return ws2 as unknown as WebSocket; });

    ensureConnected("s1");
    ws1.open();
    ensureConnected("s2");
    ws2.open();

    sendMessage("s1", "chat-1 message");
    sendMessage("s2", "chat-2 message");

    ws1.emit({ type: "delta", text: "s1-reply" });
    ws2.emit({ type: "delta", text: "s2-reply" });

    const s1 = getSessionState("s1").messages;
    const s2 = getSessionState("s2").messages;

    expect(s1.every((m) => !JSON.stringify(m).includes("s2-reply"))).toBe(true);
    expect(s2.every((m) => !JSON.stringify(m).includes("s1-reply"))).toBe(true);
  });
});

describe("__resetForTests", () => {
  it("clears all sessions so tests don't bleed into each other", () => {
    connect("s1");
    loadHistory("s1", [
      { id: "h1", role: "user", parts: [], ts: 1 },
    ]);

    __resetForTests();

    // After reset, no messages
    expect(getSessionState("s1").messages).toHaveLength(0);
  });
});
