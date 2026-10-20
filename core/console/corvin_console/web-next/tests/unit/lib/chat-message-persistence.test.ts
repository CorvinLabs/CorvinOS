/**
 * chat-message-persistence: what is written, what is cut, and what is reported.
 *
 * A quota error used to be swallowed (`catch {}`), so a long session silently
 * stopped mirroring and a reload lost the whole chat tail without a trace.
 * Now the newest messages are kept and a warning is logged.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  persistMessages,
  loadPersistedMessages,
  CHECKPOINT_TAIL,
  QUOTA_FALLBACK_TAIL,
} from "@/lib/chat-message-persistence";
import type { ChatMessage } from "@/lib/chat-registry";


function msgs(n: number, size = 100): ChatMessage[] {
  return Array.from({ length: n }, (_, i) => ({
    id: `m-${i}`,
    role: i % 2 ? "assistant" : "user",
    parts: [{ kind: "text", text: `${i}:`.padEnd(size, "x") }],
    ts: i,
  })) as ChatMessage[];
}

function quotaError(): Error {
  const err = new Error("The quota has been exceeded.");
  err.name = "QuotaExceededError";
  return err;
}

describe("persistMessages", () => {
  let store: Map<string, string>;
  let limit: number;

  beforeEach(() => {
    store = new Map();
    limit = Infinity;
    vi.spyOn(console, "warn").mockImplementation(() => {});
    vi.stubGlobal("sessionStorage", {
      getItem: (k: string) => store.get(k) ?? null,
      setItem: (k: string, v: string) => {
        if (v.length > limit) throw quotaError();
        store.set(k, v);
      },
      removeItem: (k: string) => {
        store.delete(k);
      },
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("saves the full list when it fits", () => {
    expect(persistMessages("s1", msgs(10))).toBe("saved");
    expect(loadPersistedMessages("s1")).toHaveLength(10);
  });

  it("writes only the newest messages for a streaming checkpoint", () => {
    const outcome = persistMessages("s1", msgs(200), { tail: CHECKPOINT_TAIL });
    expect(outcome).toBe("truncated");
    const stored = loadPersistedMessages("s1") ?? [];
    expect(stored).toHaveLength(CHECKPOINT_TAIL);
    expect(stored[stored.length - 1].id).toBe("m-199");
  });

  it("keeps the newest messages and warns when the full list exceeds the quota", () => {
    limit = 5_000;
    const outcome = persistMessages("s1", msgs(300));
    expect(outcome).toBe("truncated");
    const stored = loadPersistedMessages("s1") ?? [];
    expect(stored).toHaveLength(QUOTA_FALLBACK_TAIL);
    expect(stored[stored.length - 1].id).toBe("m-299");
    expect(console.warn).toHaveBeenCalledWith(expect.stringContaining("quota"));
  });

  it("reports a failure instead of swallowing it when nothing can be stored", () => {
    limit = 1;
    expect(persistMessages("s1", msgs(3))).toBe("failed");
    expect(console.warn).toHaveBeenCalled();
  });
});
