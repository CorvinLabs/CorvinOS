/**
 * Persistent storage for chat messages using sessionStorage (with fallback to memory).
 *
 * Keeps chat history across page refreshes within the same browser session.
 * Each session ID has its own message store, keyed as `corvin_chat_<sid>`.
 */

import type { ChatMessage } from "./chat-registry";

const STORAGE_PREFIX = "corvin_chat_";

/**
 * Serialize and save messages to sessionStorage for a specific chat session.
 * Falls back silently if storage is unavailable (private mode, quota exceeded).
 */
export function persistMessages(sid: string, messages: ChatMessage[]): void {
  try {
    const key = `${STORAGE_PREFIX}${sid}`;
    // Filter out volatile fields (streaming, live-only) — keep only what the backend would persist.
    const persistable = messages.map((m) => ({
      id: m.id,
      role: m.role,
      parts: m.parts,
      ts: m.ts,
      // Preserve TDE progress if present (it's persisted server-side too).
      ...(m.tdeProgress ? { tdeProgress: m.tdeProgress } : {}),
      // Don't save engine/engineLabel (live-only, re-derived on reload from tdeProgress).
      // Don't save streaming (false after done anyway).
    }));
    sessionStorage.setItem(key, JSON.stringify(persistable));
  } catch {
    // Storage quota exceeded or private-browsing mode — fail silently.
    // The fallback is: reload from backend on next mount (getChatTurns).
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
