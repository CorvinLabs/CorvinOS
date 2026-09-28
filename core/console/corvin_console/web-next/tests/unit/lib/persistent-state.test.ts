/**
 * Unit tests for usePersistentState (lib/persistent-state.ts).
 *
 * Covers the two failure modes this hook exists to fix (see the module's
 * header comment for the full root-cause analysis):
 *  1. A panel unmounts (route/tab switch) and remounts — state must survive
 *     via the in-memory mirror, without touching sessionStorage.
 *  2. The page reloads (module re-evaluated from scratch, in-memory mirror
 *     gone) — state must survive via the sessionStorage-backed read.
 * Also covers key-change rehydration, clearPersistentState, and graceful
 * degradation when sessionStorage throws (private-mode / quota).
 */
import { describe, it, expect, beforeEach, vi } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { usePersistentState, clearPersistentState } from "@/lib/persistent-state";

function resetSessionStorage() {
  sessionStorage.clear();
}

describe("usePersistentState", () => {
  beforeEach(() => {
    resetSessionStorage();
  });

  it("initializes from the given default when nothing is persisted", () => {
    const { result } = renderHook(() => usePersistentState("test.fresh-key", 42));
    expect(result.current[0]).toBe(42);
  });

  it("survives unmount/remount within the same page load (in-memory mirror)", () => {
    const key = "test.unmount-remount";
    const first = renderHook(() => usePersistentState(key, "initial"));
    act(() => {
      first.result.current[1]("changed");
    });
    expect(first.result.current[0]).toBe("changed");
    first.unmount();

    // Simulate the exact bug this hook fixes: a route change unmounts the
    // panel, then the user navigates back — a fresh component instance
    // calls the hook again with the SAME key.
    const second = renderHook(() => usePersistentState(key, "initial"));
    expect(second.result.current[0]).toBe("changed");
  });

  it("survives a full page reload via the sessionStorage-backed read", () => {
    const key = "test.reload";
    const first = renderHook(() => usePersistentState(key, { progress: 0 }));
    act(() => {
      first.result.current[1]({ progress: 77 });
    });
    first.unmount();

    // A real reload re-evaluates the module — the in-memory Map is gone,
    // only sessionStorage remains. Assert on the raw storage value directly
    // to prove the write actually reached durable storage, not just memory.
    const raw = sessionStorage.getItem("corvin.pstate." + key);
    expect(raw).not.toBeNull();
    expect(JSON.parse(raw!)).toEqual({ progress: 77 });
  });

  it("supports the functional updater form, like React.useState", () => {
    const key = "test.functional-updater";
    const { result } = renderHook(() => usePersistentState(key, 1));
    act(() => {
      result.current[1]((prev) => prev + 1);
    });
    expect(result.current[0]).toBe(2);
  });

  it("rehydrates when the key changes while the component stays mounted", () => {
    sessionStorage.setItem("corvin.pstate.test.entity-b", JSON.stringify("b-value"));
    let key = "test.entity-a";
    const { result, rerender } = renderHook(() => usePersistentState(key, "a-default"));
    expect(result.current[0]).toBe("a-default");

    key = "test.entity-b";
    rerender();
    expect(result.current[0]).toBe("b-value");
  });

  it("clearPersistentState drops both the in-memory mirror and sessionStorage", () => {
    const key = "test.clear";
    const { result } = renderHook(() => usePersistentState(key, "x"));
    act(() => {
      result.current[1]("y");
    });
    clearPersistentState(key);
    expect(sessionStorage.getItem("corvin.pstate." + key)).toBeNull();

    const fresh = renderHook(() => usePersistentState(key, "default-after-clear"));
    expect(fresh.result.current[0]).toBe("default-after-clear");
  });

  it("degrades to in-memory only when sessionStorage throws (private-mode / quota)", () => {
    const key = "test.storage-throws";
    const spy = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("QuotaExceededError");
    });
    const { result } = renderHook(() => usePersistentState(key, "ok"));
    expect(() => {
      act(() => {
        result.current[1]("still-ok");
      });
    }).not.toThrow();
    expect(result.current[0]).toBe("still-ok");
    spy.mockRestore();
  });
});
