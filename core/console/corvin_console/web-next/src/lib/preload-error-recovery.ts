/**
 * Vite's own recommended stale-deployment recovery hook (fires on any
 * dynamically-imported JS module or CSS chunk failing to load because its
 * content-hashed filename no longer exists on the server -- e.g. the
 * console restarted with a freshly rebuilt SPA, either an auto-update or a
 * crash-restart, while a tab still held asset references from the OLD
 * build). Live-reported: "Unable to preload CSS for /console/assets/
 * highlight-BEHUn5zE.css", caught by RouteErrorBoundary's "Try again" (a
 * component-state reset, which can never re-fetch a 404'd asset) instead of
 * a real reload -- the user saw a dead-end error card, and a second browser
 * window opened alongside it that also failed to recover on its own.
 *
 * A single automatic reload fixes this the vast majority of the time: the
 * browser's next navigation picks up the new build's asset manifest. Guard
 * against a reload loop with a one-shot sessionStorage flag -- if the SAME
 * tab hits this again immediately after its own reload, the server itself
 * is still down (not merely a stale bundle), and looping reloads against a
 * dead server would just be noise; let the normal connection-error state
 * take over instead.
 */
export const PRELOAD_RETRY_KEY = "corvin:preload-error-reload-attempted";

export interface PreloadErrorRecoveryDeps {
  addEventListener: typeof window.addEventListener;
  sessionStorage: Storage;
  reload: () => void;
}

/** Wires the recovery listener. Returns an unsubscribe function (mainly for
 * tests; the real app never needs to remove this for the tab's lifetime). */
export function installPreloadErrorRecovery(
  deps: PreloadErrorRecoveryDeps = {
    addEventListener: window.addEventListener.bind(window),
    sessionStorage: window.sessionStorage,
    reload: () => window.location.reload(),
  },
): () => void {
  const handler = (event: Event) => {
    if (deps.sessionStorage.getItem(PRELOAD_RETRY_KEY)) return;
    deps.sessionStorage.setItem(PRELOAD_RETRY_KEY, "1");
    event.preventDefault();
    deps.reload();
  };
  deps.addEventListener("vite:preloadError", handler);
  return () => window.removeEventListener("vite:preloadError", handler);
}

/** Call once the app has rendered successfully: clears the one-shot guard
 * so a LATER, genuinely separate stale-deployment episode (e.g. next
 * week's auto-update, same long-lived pinned tab) also gets its own fresh
 * auto-reload attempt instead of being silently blocked forever by a flag
 * set during an earlier incident. `scheduleDelayed` is injectable so tests
 * can assert the callback directly instead of racing real/faked timers. */
export function clearPreloadErrorGuardAfterSuccessfulRender(
  sessionStorageImpl: Storage = window.sessionStorage,
  delayMs = 3000,
  scheduleDelayed: (cb: () => void, ms: number) => void = (cb, ms) => setTimeout(cb, ms),
): void {
  scheduleDelayed(() => sessionStorageImpl.removeItem(PRELOAD_RETRY_KEY), delayMs);
}
