/**
 * Module-level external store for chat streaming / interrupted state.
 *
 * Two states per chat:
 *   "streaming"   — ChatPane is actively generating a response right now.
 *   "interrupted" — ChatPane was streaming when the user switched away.
 *                   The WS closed mid-response; the indicator persists in the
 *                   sidebar until the user navigates back to that chat.
 *
 * We use React 18's useSyncExternalStore pattern so any component can
 * subscribe without prop-drilling or a context tree.
 *
 * INVARIANT: `getSnapshot` must return a NEW reference on each mutation so
 * that useSyncExternalStore can detect changes via Object.is. We keep a
 * separate `snapshot` variable that is replaced (not mutated) on every write.
 */

import { useSyncExternalStore } from "react";

export type ChatStreamState = "streaming" | "interrupted";

// Mutable working copy — mutated by the write functions.
const states = new Map<string, ChatStreamState>();

// Immutable snapshot returned by getSnapshot — replaced (new reference) on
// every mutation so useSyncExternalStore can detect the change via Object.is.
let snapshot: ReadonlyMap<string, ChatStreamState> = new Map();

const listeners = new Set<() => void>();

function notify() {
  // Replace snapshot with a new Map so Object.is detects the change.
  snapshot = new Map(states);
  listeners.forEach((fn) => fn());
}

/** Mark a chat as actively streaming. */
export function markStreaming(chatId: string): void {
  if (states.get(chatId) === "streaming") return;
  states.set(chatId, "streaming");
  notify();
}

/**
 * Mark a chat as interrupted (was streaming when the user switched away).
 * Only transitions from "streaming" → "interrupted"; no-ops otherwise.
 * Also writes to sessionStorage so ChatPane can detect this on remount
 * — the module store is cleared on mount before the banner can read it.
 */
export function markInterrupted(chatId: string): void {
  if (states.get(chatId) !== "streaming") return;
  states.set(chatId, "interrupted");
  try {
    sessionStorage.setItem(`corvin_stream_interrupted_${chatId}`, "1");
  } catch { /* sessionStorage may be unavailable */ }
  notify();
}

/**
 * Called once on ChatPane mount: returns true if this chat was interrupted
 * mid-stream on a previous visit, and clears the flag.
 */
export function checkAndClearInterrupted(chatId: string): boolean {
  try {
    const key = `corvin_stream_interrupted_${chatId}`;
    const was = sessionStorage.getItem(key) === "1";
    if (was) sessionStorage.removeItem(key);
    return was;
  } catch {
    return false;
  }
}

/** Clear any streaming/interrupted state for a chat (called on mount). */
export function clearStreamState(chatId: string): void {
  if (!states.has(chatId)) return;
  states.delete(chatId);
  notify();
}

/** Fully clear when streaming ends normally (done event received). */
export function markDone(chatId: string): void {
  if (!states.has(chatId)) return;
  states.delete(chatId);
  notify();
}

function getSnapshot(): ReadonlyMap<string, ChatStreamState> {
  return snapshot;
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/**
 * Hook: returns the live Map of chat streaming states.
 * Re-renders the consuming component on any state change.
 */
export function useStreamStates(): ReadonlyMap<string, ChatStreamState> {
  return useSyncExternalStore(subscribe, getSnapshot);
}

/** Reset all state — only for unit tests. */
export function __resetForTests(): void {
  states.clear();
  snapshot = new Map();
}
