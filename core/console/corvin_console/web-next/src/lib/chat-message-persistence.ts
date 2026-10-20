/**
 * Persistent storage for chat messages using sessionStorage (with fallback to memory).
 *
 * Keeps chat history across page refreshes within the same browser session.
 * Each session ID has its own message store, keyed as `corvin_chat_<sid>`.
 *
 * Writes are the registry's job (chat-registry.ts decides WHEN): a full copy
 * at turn end and on pagehide, and a bounded checkpoint of the newest
 * messages while a turn streams. This module decides HOW: it never drops a
 * write silently. A quota error keeps the newest messages instead (see
 * QUOTA_FALLBACK_TAIL) and says so on the console.
 */

import type { ChatMessage } from "./chat-registry";

const STORAGE_PREFIX = "corvin_chat_";

/** Newest messages kept by a mid-stream checkpoint. Turn end writes the full list. */
export const CHECKPOINT_TAIL = 50;
/** Newest messages kept when the full list does not fit the storage quota. */
export const QUOTA_FALLBACK_TAIL = 20;

/** `saved`: full list stored · `truncated`: newest messages stored (checkpoint or quota) · `failed`: nothing stored. */
export type PersistOutcome = "saved" | "truncated" | "failed";

function toPersistable(messages: ChatMessage[]) {
  // Filter out volatile fields (streaming, live-only) — keep only what the backend would persist.
  return messages.map((m) => ({
    id: m.id,
    role: m.role,
    parts: m.parts,
    ts: m.ts,
    // Preserve TDE progress if present (it's persisted server-side too).
    ...(m.tdeProgress ? { tdeProgress: m.tdeProgress } : {}),
    // Don't save engine/engineLabel (live-only, re-derived on reload from tdeProgress).
    // Don't save streaming (false after done anyway).
  }));
}

/** DOMException.name / code for "storage quota exceeded", across browsers. */
function isQuotaError(err: unknown): boolean {
  const e = err as { name?: unknown; code?: unknown } | null;
  return (
    e?.name === "QuotaExceededError" ||
    e?.name === "NS_ERROR_DOM_QUOTA_REACHED" ||
    e?.code === 22
  );
}

/**
 * Serialize and save messages to sessionStorage for a specific chat session.
 *
 * `tail` limits the write to the newest N messages (a streaming checkpoint).
 * If the storage quota is exceeded, the newest QUOTA_FALLBACK_TAIL messages are
 * stored instead and a console warning names the cut; if even that fails, the
 * failure is reported the same way. Nothing is swallowed without a trace.
 */
export function persistMessages(
  sid: string,
  messages: ChatMessage[],
  options: { tail?: number } = {},
): PersistOutcome {
  const key = `${STORAGE_PREFIX}${sid}`;
  const subset = options.tail === undefined ? messages : messages.slice(-options.tail);
  try {
    sessionStorage.setItem(key, JSON.stringify(toPersistable(subset)));
    return options.tail === undefined ? "saved" : "truncated";
  } catch (err) {
    if (!isQuotaError(err) || subset.length <= QUOTA_FALLBACK_TAIL) {
      console.warn(`[corvin-chat] could not mirror ${subset.length} messages to sessionStorage`, err);
      return "failed";
    }
    try {
      sessionStorage.setItem(
        key,
        JSON.stringify(toPersistable(messages.slice(-QUOTA_FALLBACK_TAIL))),
      );
      console.warn(
        `[corvin-chat] sessionStorage quota reached: mirror keeps only the newest ${QUOTA_FALLBACK_TAIL} of ${messages.length} messages`,
      );
      return "truncated";
    } catch (fallbackErr) {
      console.warn("[corvin-chat] sessionStorage quota reached: nothing mirrored", fallbackErr);
      return "failed";
    }
  }
}

/**
 * Load messages from sessionStorage for a specific chat session.
 * Returns null if storage is unavailable or the session was never saved.
 */
export function loadPersistedMessages(sid: string): ChatMessage[] | null {
  try {
    const key = `${STORAGE_PREFIX}${sid}`;
    const stored = sessionStorage.getItem(key);
    if (!stored) return null;
    const parsed = JSON.parse(stored) as ChatMessage[];
    return parsed;
  } catch {
    // Storage corruption or parse error — fail silently and refetch from backend.
    return null;
  }
}

/**
 * Clear persisted messages for a specific chat session.
 * Called when the user explicitly clears chat history or deletes a session.
 */
export function clearPersistedMessages(sid: string): void {
  try {
    const key = `${STORAGE_PREFIX}${sid}`;
    sessionStorage.removeItem(key);
  } catch {
    // Ignore errors during cleanup.
  }
}

/**
 * Clear ALL persisted chat messages (debugging/cleanup only).
 */
export function clearAllPersistedMessages(): void {
  try {
    for (let i = sessionStorage.length - 1; i >= 0; i--) {
      const key = sessionStorage.key(i);
      if (key?.startsWith(STORAGE_PREFIX)) {
        sessionStorage.removeItem(key);
      }
    }
  } catch {
    // Ignore errors.
  }
}
