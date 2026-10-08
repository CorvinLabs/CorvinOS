/**
 * Grows a composer textarea to fit its content (operator request
 * 2026-10-05: a pasted multi-line message was silently clipped to the
 * fixed one-line height all three chat composers use — the full text was
 * only readable by scrolling inside a 2rem box).
 *
 * The textarea's own CSS can't do this: `height: auto` on an element with
 * `rows={1}` never grows past that row count from typed content alone, and
 * there is no pure-CSS way to size a block to its own scrollHeight. The
 * standard fix is this exact imperative measure-then-set — collapse to
 * `auto` first (so a shrink, e.g. deleting a line, is measured correctly
 * instead of being stuck at the previous max), read `scrollHeight`, then
 * clamp it into the pixel height. Runs in `useLayoutEffect`, not `useEffect`,
 * so the resize happens before the browser paints the old height.
 *
 * The composer sits in the footer below a `flex-1` message list — growing
 * the textarea's height shrinks the list's share of the flex column
 * automatically, which is what makes it look like the composer "expands
 * upward" without this hook touching layout outside the textarea itself.
 */
import * as React from "react";

const DEFAULT_MAX_PX = 240; // ~10 lines at the composer's text size — past this it scrolls

export function useAutosizeTextarea(
  ref: React.RefObject<HTMLTextAreaElement | null>,
  value: string,
  maxPx: number = DEFAULT_MAX_PX,
): void {
  const prevLen = React.useRef(0);
  React.useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const grew = value.length >= prevLen.current;
    prevLen.current = value.length;
    // Fast path (every ordinary keystroke): text got longer and still fits
    // the current box -> nothing to resize. Skips the height:auto reset that
    // forces a second synchronous layout of the whole page per key.
    if (grew && el.style.height !== "" && el.scrollHeight <= el.clientHeight) return;
    el.style.height = "auto";
    const h = el.scrollHeight;
    el.style.height = `${Math.min(h, maxPx)}px`;
    el.style.overflowY = h > maxPx ? "auto" : "hidden";
  }, [ref, value, maxPx]);
}
