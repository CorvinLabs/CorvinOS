/**
 * Layout-read budget of the composer autosize hook.
 *
 * Every read of scrollHeight / clientHeight after a DOM mutation forces a
 * synchronous layout. The old hook read both on every ordinary keystroke
 * (fast path) and reset height:auto on the rest, so typing in a long chat
 * paid a forced layout per key. This measures the reads a burst of
 * keystrokes causes. jsdom/happy-dom never lay out, so the reads are counted
 * on instance getters (the same stub technique the hook's own tests use).
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import * as React from "react";
import { act, render } from "@testing-library/react";
import { useAutosizeTextarea } from "@/hooks/use-autosize-textarea";

function Probe({ value }: { value: string }) {
  const ref = React.useRef<HTMLTextAreaElement>(null);
  useAutosizeTextarea(ref, value);
  return <textarea ref={ref} data-testid="ta" readOnly value={value} />;
}

/** Counts every layout-forcing read on the element and returns a fixed 40px box. */
function countLayoutReads(el: HTMLElement) {
  const counter = { reads: 0 };
  for (const prop of ["scrollHeight", "clientHeight"] as const) {
    Object.defineProperty(el, prop, {
      configurable: true,
      get() {
        counter.reads++;
        return 40;
      },
    });
  }
  return counter;
}

const KEYSTROKES = 20;

describe("composer autosize layout reads", () => {
  // Fallback path (no field-sizing): happy-dom would otherwise report support.
  beforeEach(() => {
    vi.stubGlobal("CSS", { supports: () => false });
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("does not force a synchronous layout read per keystroke", async () => {
    const { getByTestId, rerender } = render(<Probe value="" />);
    const ta = getByTestId("ta") as HTMLTextAreaElement;
    const counter = countLayoutReads(ta);

    let text = "";
    for (let i = 0; i < KEYSTROKES; i++) {
      text += "a";
      act(() => {
        rerender(<Probe value={text} />);
      });
    }
    const syncReads = counter.reads;

    // Let any batched measurement run (one per frame at most).
    await new Promise((resolve) => setTimeout(resolve, 60));
    const batchedReads = counter.reads - syncReads;

    console.info(
      `[perf-measure] composer layout reads per ${KEYSTROKES} keystrokes: ` +
        `synchronous=${syncReads}, batched-after-frame=${batchedReads}`,
    );
    expect(syncReads).toBe(0);
    expect(batchedReads).toBeLessThanOrEqual(2);
  });

  it("still measures synchronously when a newline is inserted", () => {
    const { getByTestId, rerender } = render(<Probe value="one" />);
    const ta = getByTestId("ta") as HTMLTextAreaElement;
    Object.defineProperty(ta, "scrollHeight", { configurable: true, value: 96 });
    act(() => {
      rerender(<Probe value={"one\ntwo"} />);
    });
    expect(ta.style.height).toBe("96px");
  });

  it("uses CSS field-sizing without reading layout when the engine supports it", () => {
    vi.stubGlobal("CSS", { supports: (prop: string, value: string) => prop === "field-sizing" && value === "content" });
    const { getByTestId, rerender } = render(<Probe value="" />);
    const ta = getByTestId("ta") as HTMLTextAreaElement;
    const counter = countLayoutReads(ta);
    act(() => {
      rerender(<Probe value={"a\nb\nc"} />);
    });
    expect(counter.reads).toBe(0);
    expect(ta.style.getPropertyValue("field-sizing")).toBe("content");
    expect(ta.style.maxHeight).toBe("240px");
  });
});
