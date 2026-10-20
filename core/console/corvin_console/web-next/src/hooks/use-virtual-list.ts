import * as React from "react";
import { buildOffsets, indexAt, windowRange, type RowRange } from "@/lib/virtual-window";

/**
 * Windowed vertical list over a scroll container. Only the rows that intersect
 * the viewport (plus `overscan` px) are mounted; the rest are stood in for by
 * two spacer heights. Row heights are measured with a ResizeObserver and fall
 * back to `estimate` until a row has been seen.
 *
 * Scroll anchoring: the top row and its screen y are recorded whenever the
 * reader is not stuck. When older rows are prepended above it, the row is moved
 * back to its recorded y in the following frames, from the measured DOM position.
 *
 * Stick-to-bottom: while `stickRef.current` is true the view is pinned to the
 * end on every commit. The flag is owned by the caller and is written here only
 * from the throttled scroll handler, or from a commit that found a scroll the
 * handler has not seen yet. Neither runs on a per-keystroke path.
 */
export interface UseVirtualListOptions {
  /** The scroll container. Must be a positioned element (offsetParent of the list). */
  scrollRef: React.RefObject<HTMLElement>;
  /** The element wrapping the rows; its offsetTop is the list's origin inside the scroller. */
  listRef: React.RefObject<HTMLElement>;
  /** Stable row identities, in order. */
  keys: readonly string[];
  stickRef: React.MutableRefObject<boolean>;
  /** Height used for rows that have not been measured yet, px. */
  estimate?: number;
  /** Extra px mounted above and below the viewport. */
  overscan?: number;
  /** Distance from the end (px) that still counts as "at the bottom". */
  stickThreshold?: number;
}

export interface VirtualListResult extends RowRange {
  /** Spacer height above the mounted rows. */
  padTop: number;
  /** Spacer height below the mounted rows. */
  padBottom: number;
  /** Ref callback factory: `ref={measure(key)}` on each mounted row (the element must carry `data-row-key`). */
  measure: (key: string) => (node: HTMLElement | null) => void;
}

const DEFAULT_ESTIMATE = 120;
const DEFAULT_OVERSCAN = 1000;
const DEFAULT_VIEWPORT = 800; // used when the container reports no size (jsdom, hidden tab)

export function useVirtualList({
  scrollRef,
  listRef,
  keys,
  stickRef,
  estimate = DEFAULT_ESTIMATE,
  overscan = DEFAULT_OVERSCAN,
  stickThreshold = 64,
}: UseVirtualListOptions): VirtualListResult {
  const heights = React.useRef(new Map<string, number>());
  const nodes = React.useRef(new Map<string, HTMLElement>());
  const refCallbacks = React.useRef(new Map<string, (node: HTMLElement | null) => void>());
  const observer = React.useRef<ResizeObserver | null>(null);
  const keysRef = React.useRef(keys);
  keysRef.current = keys;
  const rangeRef = React.useRef<RowRange>({ start: 0, end: 0 });
  // scrollTop after this hook's last write or read. A commit that finds a
  // different value means the reader scrolled and the throttled scroll frame
  // has not run yet: trust the live geometry, not the stale stick/anchor.
  const lastTop = React.useRef(-1);
  // Top row while not stuck, with its screen y (see scheduleAnchorCorrection).
  const anchorRef = React.useRef<{ key: string; y: number } | null>(null);
  const firstKey = React.useRef<string | undefined>(undefined);
  const correctionFrame = React.useRef(0);
  const [range, setRange] = React.useState<RowRange>({ start: 0, end: 0 });
  const [layoutTick, setLayoutTick] = React.useState(0);
  const rafRef = React.useRef(0);

  // Mirrors the latest sync() so the stable callbacks below never go stale.
  const syncRef = React.useRef<(source: "commit" | "scroll") => void>(() => {});

  /**
   * Re-derive the mounted range from the real scroll position.
   *
   * scrollTop is written only to pin to the bottom while stuck, and to restore
   * the reader after a prepend (scheduleAnchorCorrection, always in a frame). The
   * window while stuck is derived from the virtual end, never from a scrollHeight
   * read back after a write in the same commit: that DOM height depends on the
   * rows mounted from the previous pass, and the loop re-enters render until
   * React gives up.
   */
  const sync = React.useCallback((source: "commit" | "scroll") => {
    const el = scrollRef.current;
    const list = listRef.current;
    if (!el || !list) return;
    const currentKeys = keysRef.current;
    const offsets = buildOffsets(currentKeys, heights.current, estimate);
    const total = offsets[currentKeys.length] ?? 0;
    const origin = list.offsetTop;
    const viewport = el.clientHeight;

    // Position (list coordinates) the window is derived from.
    let top = el.scrollTop - origin;
    if (source === "commit") {
      // A scroll the throttled tracker has not seen yet must not be undone by a
      // stale stick flag: trust the live geometry for that one case.
      const userMoved = lastTop.current >= 0 && Math.abs(el.scrollTop - lastTop.current) > 0.5;
      if (userMoved) {
        stickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < stickThreshold;
      } else if (stickRef.current && viewport > 0) {
        // Stuck: the window follows the virtual end, not a DOM height read back here.
        top = Math.max(0, total - viewport);
        if (el.scrollTop < el.scrollHeight - viewport - 0.5) el.scrollTop = el.scrollHeight;
      }
      // Older rows were prepended above the reader: restore the row they were on.
      const first = currentKeys[0];
      const prevFirst = firstKey.current;
      firstKey.current = first;
      if (!stickRef.current && anchorRef.current && prevFirst !== undefined && first !== prevFirst) {
        scheduleAnchorCorrection(anchorRef.current.key, anchorRef.current.y);
      }
    }
    lastTop.current = el.scrollTop;

    // A hidden (display:none) or not-yet-laid-out container reports no height.
    // Mount a default-sized window instead of guessing from a zero-height viewport.
    if (viewport === 0) {
      if (rangeRef.current.end === 0 && currentKeys.length > 0) {
        const next = windowRange(offsets, Math.max(0, top), DEFAULT_VIEWPORT, overscan);
        rangeRef.current = next;
        setRange(next);
      }
      return;
    }

    const next = windowRange(offsets, Math.max(0, top), viewport, overscan);
    const prev = rangeRef.current;
    if (prev.start !== next.start || prev.end !== next.end) {
      rangeRef.current = next;
      setRange(next);
    }

    // Record the row at the top and its screen y while the reader is not stuck.
    // Read-only: a DOM read here never writes or sets state. The commit path
    // also records it, because the row under the reader may only be mounted
    // after the window has moved to it.
    if (!stickRef.current && currentKeys.length) {
      const idx = indexAt(offsets, Math.max(0, top));
      const key = currentKeys[idx];
      const node = nodes.current.get(key);
      anchorRef.current = node?.isConnected
        ? { key, y: node.getBoundingClientRect().top - el.getBoundingClientRect().top }
        : null;
    }

    /** Next frame(s): the reader's row must be back at its recorded screen y.
     *  If the row is not mounted (prepended rows pushed it out of the window),
     *  seek to its virtual position first; once it is mounted, the measured DOM
     *  position corrects the estimate. Frames, never the commit: a state update
     *  here is asynchronous. */
    function scheduleAnchorCorrection(key: string, y: number, attempt = 0) {
      if (correctionFrame.current) cancelAnimationFrame(correctionFrame.current);
      correctionFrame.current = requestAnimationFrame(() => {
        correctionFrame.current = 0;
        const scroller = scrollRef.current;
        const row = scroller ? nodes.current.get(key) : undefined;
        if (!scroller || stickRef.current || attempt > 3) return;
        if (row?.isConnected) {
          const drift = row.getBoundingClientRect().top - scroller.getBoundingClientRect().top - y;
          if (Math.abs(drift) > 0.5) {
            scroller.scrollTop += drift;
            syncRef.current("scroll");
            scheduleAnchorCorrection(key, y, attempt + 1);
          }
          return;
        }
        const keysNow = keysRef.current;
        const idx = keysNow.indexOf(key);
        if (idx < 0 || !listRef.current) return;
        const offsetsNow = buildOffsets(keysNow, heights.current, estimate);
        scroller.scrollTop = listRef.current.offsetTop + offsetsNow[idx] - y;
        syncRef.current("scroll");
        scheduleAnchorCorrection(key, y, attempt + 1);
      });
    }
  }, [estimate, overscan, listRef, scrollRef, stickRef, stickThreshold]);
  syncRef.current = sync;

  // Every commit: pin to the end while stuck, restore the reader after a prepend,
  // then re-derive the window.
  React.useLayoutEffect(() => {
    sync("commit");
  });

  // Throttled, passive scroll tracking: at most one geometry read per frame.
  React.useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const onScroll = () => {
      if (rafRef.current) return;
      rafRef.current = requestAnimationFrame(() => {
        rafRef.current = 0;
        stickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < stickThreshold;
        syncRef.current("scroll");
      });
    };
    el.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      el.removeEventListener("scroll", onScroll);
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
      rafRef.current = 0;
    };
  }, [scrollRef, stickRef, stickThreshold]);

  // Measure rows as they mount, resize, or unmount.
  const ensureObserver = () => {
    if (observer.current || typeof ResizeObserver === "undefined") return observer.current;
    observer.current = new ResizeObserver((entries) => {
      let changed = false;
      for (const entry of entries) {
        const node = entry.target as HTMLElement;
        const key = node.dataset.rowKey;
        if (key === undefined) continue;
        const h = entry.borderBoxSize?.[0]?.blockSize ?? entry.contentRect.height;
        // 0 means the row is inside a hidden subtree; keep the last real height.
        if (h <= 0) continue;
        if (Math.abs((heights.current.get(key) ?? -1) - h) > 0.5) {
          heights.current.set(key, h);
          changed = true;
        }
      }
      if (changed) setLayoutTick((t) => t + 1);
    });
    return observer.current;
  };

  const measure = React.useCallback((key: string) => {
    let cb = refCallbacks.current.get(key);
    if (!cb) {
      cb = (node: HTMLElement | null) => {
        const prev = nodes.current.get(key);
        if (prev && prev !== node) {
          observer.current?.unobserve(prev);
          nodes.current.delete(key);
        }
        if (node) {
          nodes.current.set(key, node);
          ensureObserver()?.observe(node);
        }
      };
      refCallbacks.current.set(key, cb);
    }
    return cb;
  }, []);

  // StrictMode runs effects twice: re-attach every mounted row to a fresh
  // observer after a disconnect, and disconnect on unmount.
  React.useEffect(() => {
    for (const node of nodes.current.values()) ensureObserver()?.observe(node);
    return () => {
      observer.current?.disconnect();
      observer.current = null;
    };
  }, []);

  // Re-render when a measured height changes (the layout effect above then
  // re-derives the window). `layoutTick` only exists to trigger that render.
  void layoutTick;

  const offsets = buildOffsets(keys, heights.current, estimate);
  const total = offsets[keys.length] ?? 0;
  const start = Math.min(range.start, keys.length);
  const end = Math.min(range.end, keys.length);
  return {
    start,
    end,
    padTop: offsets[start] ?? 0,
    padBottom: total - (offsets[end] ?? total),
    measure,
  };
}
