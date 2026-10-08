import { describe, expect, it } from "vitest";
import { act, renderHook } from "@testing-library/react";
import {
  loadHistory,
  refreshHistory,
  useChatSession,
  type ChatMessage,
} from "@/lib/chat-registry";

const msg = (id: string, role: ChatMessage["role"], text: string): ChatMessage => ({
  id,
  role,
  parts: [{ kind: "text", text }] as ChatMessage["parts"],
  ts: 1,
});

// The registry keeps module-level state per sid; one sid per test.
describe("refreshHistory", () => {
  it("adopts a longer server history (the answer of a task that outlived a reload)", () => {
    const { result } = renderHook(() => useChatSession("rh-grow"));
    act(() => loadHistory("rh-grow", [msg("1", "user", "go")]));
    expect(result.current.messages).toHaveLength(1);

    act(() => refreshHistory("rh-grow", [msg("1", "user", "go"), msg("2", "assistant", "done")]));
    expect(result.current.messages).toHaveLength(2);
    expect(result.current.messages[1].id).toBe("2");
  });

  it("never replaces live content with an equal or shorter server snapshot", () => {
    const { result } = renderHook(() => useChatSession("rh-same"));
    act(() => loadHistory("rh-same", [msg("1", "user", "go"), msg("2", "assistant", "live")]));
    act(() => refreshHistory("rh-same", [msg("1", "user", "go")]));
    expect(result.current.messages).toHaveLength(2);
    expect(result.current.messages[1].id).toBe("2");
  });

  it("ignores a chat the tab never opened", () => {
    expect(() => refreshHistory("rh-unknown", [msg("1", "user", "x")])).not.toThrow();
  });
});
