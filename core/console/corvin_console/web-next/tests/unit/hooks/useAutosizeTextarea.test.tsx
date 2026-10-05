/**
 * Operator request 2026-10-05: the one-line composer silently clipped a
 * pasted multi-line message instead of growing to show it. This proves the
 * resize math against a real <textarea> in jsdom, not just that the hook
 * runs without throwing — jsdom doesn't lay out text, so scrollHeight is
 * stubbed per test to stand in for "how tall the browser says this content
 * is," the same value a real browser supplies at the call site this hook
 * reads from.
 */
import { describe, it, expect } from "vitest";
import * as React from "react";
import { render } from "@testing-library/react";
import { useAutosizeTextarea } from "@/hooks/use-autosize-textarea";

function Probe({ value, maxPx }: { value: string; maxPx?: number }) {
  const ref = React.useRef<HTMLTextAreaElement>(null);
  useAutosizeTextarea(ref, value, maxPx);
  return <textarea ref={ref} data-testid="ta" readOnly value={value} />;
}

/** jsdom always reports scrollHeight as 0 — stub it to the height a real
 * browser would compute for the given content, scoped to one element. */
function stubScrollHeight(el: HTMLElement, px: number) {
  Object.defineProperty(el, "scrollHeight", { configurable: true, value: px });
}

describe("useAutosizeTextarea", () => {
  it("grows the inline height to fit scrollHeight on mount", () => {
    const { getByTestId, rerender } = render(<Probe value="line one" />);
    const ta = getByTestId("ta") as HTMLTextAreaElement;
    stubScrollHeight(ta, 96);
    // A later prop change re-runs the layout effect with the stub in place
    // (the effect itself sets height:"auto" before reading scrollHeight, so
    // the stub must already be installed for the read to reflect it).
    rerender(<Probe value="line one\nline two" />);
    expect(ta.style.height).toBe("96px");
  });

  it("clamps at maxPx and switches overflow to auto past that point", () => {
    const { getByTestId, rerender } = render(<Probe value="x" maxPx={200} />);
    const ta = getByTestId("ta") as HTMLTextAreaElement;
    stubScrollHeight(ta, 500);
    rerender(<Probe value={"x\n".repeat(30)} maxPx={200} />);
    expect(ta.style.height).toBe("200px");
    expect(ta.style.overflowY).toBe("auto");
  });

  it("shrinks back down and hides overflow when content is cleared", () => {
    const { getByTestId, rerender } = render(<Probe value="a lot of text" maxPx={200} />);
    const ta = getByTestId("ta") as HTMLTextAreaElement;
    stubScrollHeight(ta, 180);
    rerender(<Probe value="a lot of text\nmore" maxPx={200} />);
    expect(ta.style.height).toBe("180px");

    stubScrollHeight(ta, 32);
    rerender(<Probe value="" maxPx={200} />);
    expect(ta.style.height).toBe("32px");
    expect(ta.style.overflowY).toBe("hidden");
  });

  it("no-ops safely when the ref isn't attached yet", () => {
    const ref = { current: null } as React.RefObject<HTMLTextAreaElement | null>;
    expect(() => {
      renderHookEffect(() => useAutosizeTextarea(ref, "x"));
    }).not.toThrow();
  });
});

function renderHookEffect(fn: () => void) {
  function Wrapper() { fn(); return null; }
  return render(<Wrapper />);
}
