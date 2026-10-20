/**
 * Write budget of the chat sessionStorage mirror while a turn streams.
 *
 * Measures how often and how much the registry serialises the message list
 * into sessionStorage during a long streamed answer in a long session. Uses a
 * fake Storage that records every setItem call and its size. Real timers: fake
 * timers hang vitest's own scheduling in this suite (measured 2026-10-20).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  ensureConnected,
  loadHistory,
  sendMessage,
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
  open() {
    this.readyState = WS_OPEN;
    this.onopen?.();
  }
  emit(data: object) {
    this.onmessage?.({ data: JSON.stringify(data) });
  }
  close() {
    this.readyState = 3;
  }
  send(data: string) {
    this.sent.push(data);
  }
}

const SID = "perf-s1";
const STREAM_DELTAS = 300;
const DELTA_GAP_MS = 10; // ~3 s of streaming in total
const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

describe("chat sessionStorage writes during a streamed turn", () => {
  let ws: MockWS;
  let store: Map<string, string>;
  let writes: { key: string; bytes: number }[];

  beforeEach(() => {
    __resetForTests();
    store = new Map();
    writes = [];
    vi.stubGlobal("sessionStorage", {
      getItem: (k: string) => store.get(k) ?? null,
      setItem: (k: string, v: string) => {
        writes.push({ key: k, bytes: v.length });
        store.set(k, v);
      },
      removeItem: (k: string) => {
        store.delete(k);
      },
      key: (i: number) => [...store.keys()][i] ?? null,
      get length() {
        return store.size;
      },
    });
    ws = new MockWS();
    const ctor = vi.fn(function () {
      return ws;
    });
    Object.assign(ctor, { CONNECTING: 0, OPEN: WS_OPEN, CLOSING: 2, CLOSED: 3 });
    vi.stubGlobal("WebSocket", ctor);
    vi.stubGlobal("window", { location: { protocol: "http:", host: "localhost" } });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("checkpoints mid-stream with a bounded payload and persists the full history at turn end", async () => {
    const seed: ChatMessage[] = Array.from({ length: 150 }, (_, i) => ({
      id: `h-${i}`,
      role: i % 2 ? "assistant" : "user",
      parts: [{ kind: "text", text: `history message ${i} `.repeat(40) }],
      ts: 1_700_000_000 + i,
    }));
    loadHistory(SID, seed);
    ensureConnected(SID);
    ws.open();
    writes.length = 0;

    sendMessage(SID, "go");
    for (let i = 0; i < STREAM_DELTAS; i++) {
      ws.emit({ type: "delta", text: "x".repeat(20) });
      await sleep(DELTA_GAP_MS);
    }
    const streamWrites = writes.filter((w) => w.key === `corvin_chat_${SID}`);
    const streamChars = streamWrites.reduce((n, w) => n + w.bytes, 0);

    ws.emit({ type: "done" });
    await sleep(20);
    const persisted = JSON.parse(store.get(`corvin_chat_${SID}`) ?? "[]") as ChatMessage[];
    const last = persisted[persisted.length - 1];
    const lastText = last.parts.map((p) => ("text" in p ? p.text : "")).join("");

    console.info(
      `[perf-measure] sessionStorage writes during a ${STREAM_DELTAS}-delta stream ` +
        `(${(STREAM_DELTAS * DELTA_GAP_MS) / 1000}s, ${seed.length}-message session): ` +
        `count=${streamWrites.length}, chars=${streamChars}; ` +
        `after turn end: messages persisted=${persisted.length} of ${seed.length + 2}`,
    );

    // Mid-stream checkpoint exists (a reload during a long answer keeps it) ...
    expect(streamWrites.length).toBeGreaterThanOrEqual(1);
    // ... and the throttle keeps it to a handful of writes, not one per delta.
    expect(streamWrites.length).toBeLessThanOrEqual(4);
    // Turn end persists the complete history, so nothing is lost on reload.
    expect(persisted.length).toBe(seed.length + 2);
    expect(lastText.length).toBe(20 * STREAM_DELTAS);
  }, 15_000);
});
