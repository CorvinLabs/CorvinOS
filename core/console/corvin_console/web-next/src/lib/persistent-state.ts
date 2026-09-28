/**
 * usePersistentState — a drop-in React.useState replacement whose value
 * survives panel unmount/remount (tab/route switches) AND a full page
 * reload within the same browser session.
 *
 * Root cause this fixes (analysis 2026-09-28): the console mounts every
 * panel as a react-router <Route> (registry.tsx / App.tsx, ADR-0353).
 * Switching tabs/panels unmounts the previous route's component tree
 * outright — any `React.useState` in it is gone, by design, the moment
 * react-router swaps the route. `chat.tsx` / `chat-registry.ts` solved this
 * once, ad hoc, for chat messages via a module-level Map keyed by session id
 * (see chat-registry.ts's own header comment) — but that pattern was never
 * generalized, so every OTHER panel (Skill Forge's AutonomousForgePanel
 * progress, Skill Manager tabs, …) still loses its in-flight state on every
 * tab switch. This hook is that generalization: same idea (persist outside
 * the component tree, key by a stable id), packaged so any panel can opt in
 * with a single line instead of reinventing a module-level Map.
 *
 * Design choices:
 *  - sessionStorage, not localStorage: this is ephemeral UI/progress state
 *    (a generation in flight, a scroll position, a draft), not a durable
 *    user preference — localStorage is what `lib/preferences.ts` already
 *    owns for that. sessionStorage also matches the existing convention in
 *    chat-message-persistence.ts / streaming-state.ts / chat-registry.ts's
 *    own debug log, and the explicit UX text in chat.tsx: "Chat sessions
 *    are scoped to this browser."
 *  - In-memory mirror (module-level Map) backs every read so a tab switch
 *    within the SAME page load never pays JSON parse/stringify cost — only
 *    an actual page (re)load does.
 *  - Fails open: storage errors (quota, private-mode) degrade to in-memory
 *    only, never throw into the render tree — same posture as
 *    preferences.ts and chat-message-persistence.ts.
 */

import * as React from "react";

const STORAGE_PREFIX = "corvin.pstate.";

// In-memory mirror — avoids repeated JSON.parse(sessionStorage.getItem(...))
// on every remount within the same page load.
const memory = new Map<string, unknown>();

function readStorage<T>(key: string): T | undefined {
  if (memory.has(key)) return memory.get(key) as T;
  try {
    const raw = sessionStorage.getItem(STORAGE_PREFIX + key);
    if (raw == null) return undefined;
    const value = JSON.parse(raw) as T;
    memory.set(key, value);
    return value;
  } catch {
    // Quota exceeded, private-mode, or corrupt JSON — degrade to "unset".
    return undefined;
  }
}

function writeStorage<T>(key: string, value: T): void {
  memory.set(key, value);
  try {
    sessionStorage.setItem(STORAGE_PREFIX + key, JSON.stringify(value));
  } catch {
    // Storage unavailable — the in-memory mirror above still makes this
    // survive unmount/remount within the page load, just not a reload.
  }
}

/** Drop the persisted value for a key (call on explicit reset, e.g. "New chat"). */
export function clearPersistentState(key: string): void {
  memory.delete(key);
  try {
    sessionStorage.removeItem(STORAGE_PREFIX + key);
  } catch {
    /* ignore */
  }
}

/**
 * `usePersistentState("skill-forge.forkRun", null)` behaves like
 * `useState(null)`, except the value is restored from the in-memory/session
 * mirror on every mount (including after a route change unmounted the
 * component) instead of resetting to the initial value.
 *
 * `key` MUST be stable and, when the state is per-entity (per chat, per
 * skill run), include that entity's id — a shared key across unrelated
 * entities would leak one entity's state into another's UI.
 */
export function usePersistentState<T>(
  key: string,
  initial: T,
): [T, React.Dispatch<React.SetStateAction<T>>] {
  const [value, setValue] = React.useState<T>(() => {
    const stored = readStorage<T>(key);
    return stored !== undefined ? stored : initial;
  });

  // Re-hydrate if `key` changes while the component stays mounted (e.g. a
  // panel that switches between entities via a param without unmounting).
  const keyRef = React.useRef(key);
  if (keyRef.current !== key) {
    keyRef.current = key;
    const stored = readStorage<T>(key);
    setValue(stored !== undefined ? stored : initial);
  }

  const setPersisted = React.useCallback<React.Dispatch<React.SetStateAction<T>>>(
    (next) => {
      setValue((prev) => {
        const resolved = typeof next === "function" ? (next as (p: T) => T)(prev) : next;
        writeStorage(key, resolved);
        return resolved;
      });
    },
    [key],
  );

  return [value, setPersisted];
}
