/**
 * ccc-bus.ts — Global CCC entity event bus (ADR-0168 M4).
 *
 * When the chat WebSocket emits a `ccc_action` event, chat-registry
 * calls `emitCCCEvent()` here. Any tab can subscribe to specific
 * entity types via `subscribeCCCEvents()`.
 *
 * Intentionally dependency-free — no React, no store, no polling.
 */

export interface CCCEvent {
  action_id: string;
  entity_type: string;
  entity_id: string | null;
  status: string;
  message: string;
  payload: Record<string, unknown> | null;
}

type CCCListener = (evt: CCCEvent) => void;

const _listeners = new Map<string, Set<CCCListener>>();
const _wildcards = new Set<CCCListener>();

/** Emit a CCC entity event to all matching subscribers. */
export function emitCCCEvent(evt: CCCEvent): void {
  const byType = _listeners.get(evt.entity_type);
  if (byType) byType.forEach((fn) => fn(evt));
  _wildcards.forEach((fn) => fn(evt));
}

/**
 * Subscribe to CCC events for a specific entity type (or "*" for all).
 * Returns an unsubscribe function.
 */
export function subscribeCCCEvents(
  entityType: string,
  fn: CCCListener,
): () => void {
  if (entityType === "*") {
    _wildcards.add(fn);
    return () => { _wildcards.delete(fn); };
  }
  if (!_listeners.has(entityType)) _listeners.set(entityType, new Set());
  _listeners.get(entityType)!.add(fn);
  return () => {
    const set = _listeners.get(entityType);
    if (!set) return;
    set.delete(fn);
    if (set.size === 0) _listeners.delete(entityType);
  };
}

/** React hook: subscribe to CCC events for one entity type. */
import { useEffect, useRef } from "react";

export function useCCCEvents(entityType: string, fn: CCCListener): void {
  // The subscription is keyed on entityType only; the listener is read through a
  // ref so a caller may pass an inline closure without re-subscribing each render.
  const fnRef = useRef(fn);
  fnRef.current = fn;
  useEffect(() => {
    const unsub = subscribeCCCEvents(entityType, (evt) => fnRef.current(evt));
    return unsub;
  }, [entityType]);
}
