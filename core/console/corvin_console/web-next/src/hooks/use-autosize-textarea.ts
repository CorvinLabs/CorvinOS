/**
 * Grows a composer textarea to fit its content (operator request
 * 2026-10-05: a pasted multi-line message was silently clipped to the
 * fixed one-line height all three chat composers use — the full text was
 * only readable by scrolling inside a 2rem box).
 *
 * Layout cost is the constraint here, not just correctness. Reading
 * `scrollHeight` after a DOM write forces a synchronous layout, and the old
 * hook did that on every keystroke (2 reads per key, measured 2026-10-20:
 * 40 forced reads per 20 keystrokes), so typing in a long chat competed with
 * the whole message list for the main thread. Three paths, cheapest first:
 *
 * 1. `field-sizing: content` where the browser supports it — the engine
 *    sizes the box itself; this hook only sets the `max-height` cap and
 *    reads nothing.
 * 2. Otherwise, a plain keystroke (one character appended, no newline) only
 *    schedules a measurement for the next animation frame, so any number of
 *    keystrokes in one frame cost at most one read, and that read never
 *    resets `height` (growth only).
 * 3. Everything else — newline, paste, deletion, programmatic change —
 *    measures synchronously in `useLayoutEffect`, before the browser paints
 *    the old height. A shrink needs the `height: auto` reset to be measured
 *    correctly (otherwise it stays stuck at the previous maximum).
 *
 * The composer sits in the footer below a `flex-1` message list — growing
 * the textarea's height shrinks the list's share of the flex column
 * automatically, which is what makes it look like the composer "expands
 * upward" without this hook touching layout outside the textarea itself.
 */
import * as React from "react";

const DEFAULT_MAX_PX = 240; // ~10 lines at the composer's text size — past this it scrolls

/** True when the engine can size a textarea to its content in CSS. Asked once
 *  per mounted composer, not per keystroke, and not cached at module level so
 *  a test environment can answer either way. */
export function supportsFieldSizing(): boolean {
  return (
    typeof CSS !== "undefined" &&
    typeof CSS.supports === "function" &&
    CSS.supports("field-sizing", "content")
  );
}

/** One character appended to the previous value, and it is not a newline. */
function isPlainAppend(prev: string | null, next: string): boolean {
  return (
    prev !== null &&
    next.length === prev.length + 1 &&
    next.startsWith(prev) &&
    next.charCodeAt(next.length - 1) !== 10
  );
}

/** Full measure: collapse to auto first so a shrink is measured correctly. */
function measureSync(el: HTMLTextAreaElement, maxPx: number): void {
  el.style.height = "auto";
  const h = el.scrollHeight;
  el.style.height = `${Math.min(h, maxPx)}px`;
  el.style.overflowY = h > maxPx ? "auto" : "hidden";
}

/** Growth-only measure: never resets height, so it costs one layout, not two. */
function growIfOverflowing(el: HTMLTextAreaElement, maxPx: number): void {
  if (el.scrollHeight <= el.clientHeight) return;
  const h = el.scrollHeight;
  el.style.height = `${Math.min(h, maxPx)}px`;
  el.style.overflowY = h > maxPx ? "auto" : "hidden";
}

export function useAutosizeTextarea(
  ref: React.RefObject<HTMLTextAreaElement | null>,
  value: string,
  maxPx: number = DEFAULT_MAX_PX,
): void {
  const prevValue = React.useRef<string | null>(null);
  const frame = React.useRef<number | null>(null);
  const cssSizing = React.useRef<boolean | null>(null);
  if (cssSizing.current === null) cssSizing.current = supportsFieldSizing();

  React.useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const prev = prevValue.current;
    prevValue.current = value;

    if (cssSizing.current) {
      el.style.setProperty("field-sizing", "content");
      el.style.maxHeight = `${maxPx}px`;
      el.style.overflowY = "auto";
      return;
    }

    if (isPlainAppend(prev, value)) {
      if (frame.current === null) {
        frame.current = requestAnimationFrame(() => {
          frame.current = null;
          const current = ref.current;
          if (current) growIfOverflowing(current, maxPx);
        });
      }
      return;
    }

    if (frame.current !== null) {
      cancelAnimationFrame(frame.current);
      frame.current = null;
    }
    measureSync(el, maxPx);
  }, [ref, value, maxPx]);

  React.useEffect(
    () => () => {
      if (frame.current !== null) cancelAnimationFrame(frame.current);
    },
    [],
  );
}
