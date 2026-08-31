/**
 * Stale-deployment recovery (2026-08-02).
 *
 * Root cause, live-reported (image attachment): after a console restart
 * (auto-update or crash-restart) left a browser tab holding asset
 * references from the OLD SPA build, Vite's own "Unable to preload CSS
 * for /console/assets/highlight-BEHUn5zE.css" error was caught by
 * RouteErrorBoundary's "Try again" -- a component-state reset that can
 * never re-fetch a 404'd asset -- instead of a real page reload. The user
 * saw a dead-end error card, and a second browser window opened alongside
 * it that also failed to recover on its own.
 *
 * These tests exercise the real recovery module with a fake window/
 * sessionStorage, never mocking the module under test itself.
 */
import { describe, it, expect, vi } from "vitest";
import {
  installPreloadErrorRecovery,
  clearPreloadErrorGuardAfterSuccessfulRender,
  PRELOAD_RETRY_KEY,
  type PreloadErrorRecoveryDeps,
} from "@/lib/preload-error-recovery";

function fakeSessionStorage(): Storage {
  const store = new Map<string, string>();
  return {
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => void store.set(k, v),
    removeItem: (k: string) => void store.delete(k),
    clear: () => store.clear(),
    key: (i: number) => Array.from(store.keys())[i] ?? null,
    get length() {
      return store.size;
    },
  };
}

function fakeDeps(): PreloadErrorRecoveryDeps & {
  handlers: Map<string, EventListener>;
  reload: ReturnType<typeof vi.fn>;
} {
  const handlers = new Map<string, EventListener>();
  return {
    addEventListener: ((type: string, handler: EventListener) => {
      handlers.set(type, handler);
    }) as typeof window.addEventListener,
    sessionStorage: fakeSessionStorage(),
    reload: vi.fn(),
    handlers,
  };
}

function firePreloadError(deps: ReturnType<typeof fakeDeps>): Event {
  const handler = deps.handlers.get("vite:preloadError");
  expect(handler).toBeDefined();
  const event = new Event("vite:preloadError", { cancelable: true });
  const preventDefaultSpy = vi.spyOn(event, "preventDefault");
  handler!(event);
  return Object.assign(event, { preventDefaultSpy });
}

describe("installPreloadErrorRecovery", () => {
  it("reloads the page on the first preload error", () => {
    const deps = fakeDeps();
    installPreloadErrorRecovery(deps);

    firePreloadError(deps);

    expect(deps.reload).toHaveBeenCalledTimes(1);
    expect(deps.sessionStorage.getItem(PRELOAD_RETRY_KEY)).toBe("1");
  });

  it("calls preventDefault so Vite does not also throw", () => {
    const deps = fakeDeps();
    installPreloadErrorRecovery(deps);

    const event = firePreloadError(deps) as Event & {
      preventDefaultSpy: ReturnType<typeof vi.spyOn>;
    };

    expect(event.preventDefaultSpy).toHaveBeenCalled();
  });

  it("does NOT reload a second time in the same episode (no reload loop)", () => {
    const deps = fakeDeps();
    installPreloadErrorRecovery(deps);

    firePreloadError(deps);
    firePreloadError(deps); // simulates the error recurring before the reload lands

    expect(deps.reload).toHaveBeenCalledTimes(1);
  });

  it("reloads again if the guard was already cleared (a later, separate episode)", () => {
    const deps = fakeDeps();
    installPreloadErrorRecovery(deps);

    firePreloadError(deps);
    expect(deps.reload).toHaveBeenCalledTimes(1);

    // Simulates clearPreloadErrorGuardAfterSuccessfulRender() having run
    // after a successful render following the first reload.
    deps.sessionStorage.removeItem(PRELOAD_RETRY_KEY);

    firePreloadError(deps);
    expect(deps.reload).toHaveBeenCalledTimes(2);
  });
});

describe("clearPreloadErrorGuardAfterSuccessfulRender", () => {
  it("schedules the clear with the given delay and clears on fire", () => {
    const storage = fakeSessionStorage();
    storage.setItem(PRELOAD_RETRY_KEY, "1");

    let firedCallback: (() => void) | null = null;
    const scheduleDelayed = vi.fn((cb: () => void, ms: number) => {
      expect(ms).toBe(1000);
      firedCallback = cb;
    });

    clearPreloadErrorGuardAfterSuccessfulRender(storage, 1000, scheduleDelayed);

    expect(scheduleDelayed).toHaveBeenCalledTimes(1);
    expect(storage.getItem(PRELOAD_RETRY_KEY)).toBe("1"); // not yet -- callback hasn't fired

    firedCallback!();
    expect(storage.getItem(PRELOAD_RETRY_KEY)).toBeNull();
  });
});
