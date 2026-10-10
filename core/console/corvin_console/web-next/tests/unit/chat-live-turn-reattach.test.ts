/**
 * A reloaded / reconnected tab must show the agent's output of a turn that was
 * running when the page went away (2026-10-09: every answer of a chat missing
 * after a console_auto_reload redeploy mid-turn).
 *
 * Three mechanisms, one test group each:
 *  1. the sessionStorage mirror restored after a reload never outranks the
 *     server's record (it used to block loadHistory, and refreshHistory's
 *     length check read [user, empty placeholder] as equal to [user, answer]);
 *  2. a socket that connects while a turn runs re-attaches to it
 *     (`turn_attach` + replay, live_turns.py) without doubling the bubble;
 *  3. an attached turn's `done` reconciles with the persisted record.
 */
import { describe, it, expect, beforeEach, vi } from "vitest";
import {
  ensureConnected,
  loadHistory,
  refreshHistory,
  sendMessage,
  getSessionState,
  registerHistoryReader,
  anyChatStreaming,
  __resetForTests,
  type ChatMessage,
} from "@/lib/chat-registry";

const WS_OPEN = 1;

class MockWS {
  readyState = 0;
  sent: string[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((ev: { data: string }) => void) | null = null;
  onclose: ((ev: { code: number }) => void) | null = null;
  onerror: (() => void) | null = null;
  open() { this.readyState = WS_OPEN; this.onopen?.(); }
  emit(data: object) { this.onmessage?.({ data: JSON.stringify(data) }); }
  close(code = 1006) { this.readyState = 3; this.onclose?.({ code }); }
  send(d: string) { this.sent.push(d); }
}

let sockets: MockWS[] = [];

function stubWs() {
  const ctor = vi.fn(function (this: unknown) {
    const ws = new MockWS();
    sockets.push(ws);
    return ws;
  });
  Object.assign(ctor, { CONNECTING: 0, OPEN: 1, CLOSING: 2, CLOSED: 3 });
  vi.stubGlobal("WebSocket", ctor);
}

const store = new Map<string, string>();

beforeEach(() => {
  __resetForTests();
  sockets = [];
  store.clear();
  stubWs();
  vi.stubGlobal("window", { location: { protocol: "http:", host: "localhost" } });
  vi.stubGlobal("sessionStorage", {
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => void store.set(k, v),
    removeItem: (k: string) => void store.delete(k),
    key: (i: number) => [...store.keys()][i] ?? null,
    get length() { return store.size; },
  });
});

const msg = (id: string, role: "user" | "assistant", text: string): ChatMessage => ({
  id, role, ts: 1, parts: text ? [{ kind: "text", text }] : [],
});

const texts = (sid: string) =>
  getSessionState(sid).messages.map(
    (m) => `${m.role}:${m.parts.map((p) => (p.kind === "text" ? p.text : `[${p.kind}]`)).join("")}`,
  );

describe("1. the restored mirror never outranks the record", () => {
  it("a mirror saved mid-turn ([user, empty placeholder]) is replaced by the finished record", () => {
    // What the tab last saw before the redeploy reload: the question and an
    // empty, still-streaming assistant placeholder.
    store.set("corvin_chat_s1", JSON.stringify([
      msg("u-1", "user", "frage"),
      { ...msg("a-2", "assistant", ""), streaming: true },
    ]));
    // The reloaded page: the restored placeholder is not live.
    expect(getSessionState("s1").messages[1].streaming).toBe(false);
    expect(getSessionState("s1").streaming).toBe(false);

    // History read while the turn still runs: only the question is persisted.
    loadHistory("s1", [msg("h-1", "user", "frage")]);
    expect(texts("s1")).toEqual(["user:frage"]);

    // Turn ended, the answer is persisted — same message COUNT as the mirror
    // had, which the old length check took for "nothing new".
    refreshHistory("s1", [msg("h-1", "user", "frage"), msg("h-2", "assistant", "die antwort")]);
    expect(texts("s1")).toEqual(["user:frage", "assistant:die antwort"]);
  });

  it("the record replaces restored content even when the history read comes after the turn ended", () => {
    store.set("corvin_chat_s2", JSON.stringify([
      msg("u-1", "user", "frage"),
      { ...msg("a-2", "assistant", "halb"), streaming: true },
    ]));
    getSessionState("s2");
    loadHistory("s2", [msg("h-1", "user", "frage"), msg("h-2", "assistant", "ganz fertig")]);
    expect(texts("s2")).toEqual(["user:frage", "assistant:ganz fertig"]);
    expect(getSessionState("s2").messages.every((m) => !m.restored)).toBe(true);
  });

  it("a snapshot shorter than the page's own streamed content never erases it", () => {
    ensureConnected("s3");
    sockets[0].open();
    sendMessage("s3", "q");
    sockets[0].emit({ type: "delta", text: "live answer" });
    sockets[0].emit({ type: "done" });
    refreshHistory("s3", [msg("h-1", "user", "q")]); // read before the answer was persisted
    expect(texts("s3")).toEqual(["user:q", "assistant:live answer"]);
  });
});

describe("2. a reconnecting socket re-attaches to the running turn", () => {
  it("a fresh page attaches: placeholder, replayed events, then live events", () => {
    loadHistory("s4", [msg("h-1", "user", "frage")]);
    ensureConnected("s4");
    const ws = sockets[0];
    ws.open();
    ws.emit({ type: "ready" });
    ws.emit({ type: "turn_attach", turn_id: "t-1", started_at: 1 });
    ws.emit({ type: "delta", text: "Ich schaue " });           // replayed
    ws.emit({ type: "tool_use", name: "Bash", input: {} });     // replayed
    ws.emit({ type: "delta", text: "nach." });                  // live
    expect(getSessionState("s4").streaming).toBe(true);
    expect(anyChatStreaming()).toBe(true);
    expect(texts("s4")).toEqual(["user:frage", "assistant:Ich schaue [tool]nach."]);
  });

  it("the same page re-attaching after a socket drop rebuilds the bubble instead of doubling it", () => {
    ensureConnected("s5");
    sockets[0].open();
    sendMessage("s5", "frage");
    sockets[0].emit({ type: "turn_started", turn_id: "t-9" });
    sockets[0].emit({ type: "delta", text: "Teil 1. " });
    sockets[0].close(1006); // network blip: partial bubble kept, no live socket

    ensureConnected("s5");  // the reconnect
    const ws2 = sockets[1];
    ws2.open();
    ws2.emit({ type: "turn_attach", turn_id: "t-9", started_at: 1 });
    ws2.emit({ type: "delta", text: "Teil 1. " });  // replay from the start
    ws2.emit({ type: "delta", text: "Teil 2." });
    expect(texts("s5")).toEqual(["user:frage", "assistant:Teil 1. Teil 2."]);
    expect(getSessionState("s5").messages.filter((m) => m.role === "assistant")).toHaveLength(1);
  });

  it("history that arrives after the replay keeps the attached turn after the record", () => {
    ensureConnected("s6");
    sockets[0].open();
    sockets[0].emit({ type: "turn_attach", turn_id: "t-2", started_at: 1 });
    sockets[0].emit({ type: "delta", text: "laufend" });
    loadHistory("s6", [msg("h-0", "user", "alt"), msg("h-1", "assistant", "alt-antwort"), msg("h-2", "user", "frage")]);
    expect(texts("s6")).toEqual(["user:alt", "assistant:alt-antwort", "user:frage", "assistant:laufend"]);
  });
});

describe("3. an attached turn reconciles with the record when it ends", () => {
  it("done{attached} reads the record and shows the persisted answer", async () => {
    const reader = vi.fn(async () => [msg("h-1", "user", "frage"), msg("h-2", "assistant", "persistierte Antwort")]);
    registerHistoryReader(reader);
    loadHistory("s7", [msg("h-1", "user", "frage")]);
    ensureConnected("s7");
    sockets[0].open();
    // Replay truncated: the attached view only saw the tail.
    sockets[0].emit({ type: "turn_attach", turn_id: "t-3", started_at: 1, truncated: true });
    sockets[0].emit({ type: "delta", text: "…Antwort" });
    sockets[0].emit({ type: "done", attached: true, turn_id: "t-3" });
    await vi.waitFor(() => expect(reader).toHaveBeenCalledWith("s7"));
    await vi.waitFor(() =>
      expect(texts("s7")).toEqual(["user:frage", "assistant:persistierte Antwort"]),
    );
    expect(getSessionState("s7").streaming).toBe(false);
    expect(anyChatStreaming()).toBe(false);
  });

  it("the turn's own socket does not re-read on a normal done", () => {
    const reader = vi.fn(async () => []);
    registerHistoryReader(reader);
    ensureConnected("s8");
    sockets[0].open();
    sendMessage("s8", "q");
    sockets[0].emit({ type: "turn_started", turn_id: "t-4" });
    sockets[0].emit({ type: "delta", text: "a" });
    sockets[0].emit({ type: "done" });
    expect(reader).not.toHaveBeenCalled();
  });
});
