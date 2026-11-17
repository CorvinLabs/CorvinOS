/**
 * React error #185 ("Maximum update depth exceeded") in the chat message list.
 *
 * `useVirtualList` re-derives the mounted window in a layout effect on EVERY commit and
 * calls setState when the window moved. When the browser's own geometry answers a window
 * change with a different scroll position (scroll anchoring, clamping after rows unmount
 * above the reader, a streaming row that grows), the next commit derives a different
 * window again. The resize observer that would settle the heights cannot run inside a
 * synchronous re-render cascade, so React gives up after 50 nested updates and the route
 * error boundary replaces the whole chat. Reported 98 times between 2026-11-07 and
 * 2026-11-17 as `react-185` on `/app/chat` (audit event console.client_error).
 *
 * The layout is simulated here: the scroll position of the scroller depends on which rows
 * are mounted, which is exactly the feedback the browser provides. Nothing in this file
 * waits for a frame, so a cascade that never settles fails the test at once.
 */
import * as React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render } from "@testing-library/react";
import { useVirtualList } from "@/hooks/use-virtual-list";
import { MessageRowsBoundary } from "@/components/chat/MessageRowsBoundary";

const KEYS = Array.from({ length: 300 }, (_, i) => `k${i}`);

/** Scroll position as the (simulated) browser reports it: it depends on the rows mounted. */
function installLayout(scroller: HTMLElement, list: HTMLElement) {
  Object.defineProperty(scroller, "clientHeight", { configurable: true, value: 500 });
  Object.defineProperty(scroller, "scrollHeight", { configurable: true, value: 60_000 });
  Object.defineProperty(list, "offsetTop", { configurable: true, value: 0 });
  let written = 0;
  Object.defineProperty(scroller, "scrollTop", {
    configurable: true,
    get() {
      // Anchoring: with the head of the list mounted the browser parks the reader far down;
      // without it, back at the top. Each answer invites the opposite window.
      const first = list.querySelector("[data-row-key]")?.getAttribute("data-row-key");
      return first === "k0" ? 20_000 : written || 0;
    },
    set(v: number) { written = v; },
  });
}

function Harness({ onRender }: { onRender: () => void }) {
  const scrollRef = React.useRef<HTMLDivElement>(null);
  const listRef = React.useRef<HTMLDivElement>(null);
  const stickRef = React.useRef(false);
  const { start, end, padTop, padBottom, measure } = useVirtualList({
    scrollRef: scrollRef as React.RefObject<HTMLElement>,
    listRef: listRef as React.RefObject<HTMLElement>,
    keys: KEYS,
    stickRef,
  });
  onRender();
  React.useLayoutEffect(() => {
    if (scrollRef.current && listRef.current && !(scrollRef.current as unknown as { __l?: boolean }).__l) {
      (scrollRef.current as unknown as { __l?: boolean }).__l = true;
      installLayout(scrollRef.current, listRef.current);
    }
  }, []);
  return (
    <div ref={scrollRef} data-testid="scroller">
      <div ref={listRef}>
        <div style={{ height: padTop }} />
        {KEYS.slice(start, end).map((k) => (
          <div key={k} data-row-key={k} ref={measure(k)} style={{ height: 100 }}>{k}</div>
        ))}
        <div style={{ height: padBottom }} />
      </div>
    </div>
  );
}

class NoopResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("useVirtualList under a window/scroll feedback", () => {
  it("settles instead of exhausting React's nested-update budget (#185)", () => {
    vi.stubGlobal("ResizeObserver", NoopResizeObserver);
    const spy = vi.spyOn(console, "error").mockImplementation(() => undefined);
    let renders = 0;
    let thrown: unknown = null;
    class Boundary extends React.Component<{ children: React.ReactNode }, { failed: boolean }> {
      state = { failed: false };
      static getDerivedStateFromError() { return { failed: true }; }
      componentDidCatch(e: unknown) { thrown = e; }
      render() { return this.state.failed ? <div data-testid="boundary" /> : this.props.children; }
    }
    act(() => {
      render(<Boundary><Harness onRender={() => { renders++; }} /></Boundary>);
    });
    spy.mockRestore();
    expect(String((thrown as Error | null)?.message ?? "")).not.toMatch(/Maximum update depth|#185/);
    // A bounded number of passes, far below React's limit of 50 nested updates.
    expect(renders).toBeLessThan(30);
  });
});

describe("MessageRowsBoundary", () => {
  function Bomb({ boom }: { boom: boolean }) {
    if (boom) throw new Error("window logic failed");
    return <div data-testid="windowed" />;
  }

  it("keeps a list failure inside the list and retries when the chat changes", () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const view = render(
      <MessageRowsBoundary resetKey="a" fallback={<div data-testid="plain" />}>
        <Bomb boom />
      </MessageRowsBoundary>,
    );
    expect(view.queryByTestId("plain")).not.toBeNull();
    expect(view.queryByTestId("windowed")).toBeNull();
    view.rerender(
      <MessageRowsBoundary resetKey="b" fallback={<div data-testid="plain" />}>
        <Bomb boom={false} />
      </MessageRowsBoundary>,
    );
    expect(view.queryByTestId("windowed")).not.toBeNull();
    spy.mockRestore();
  });
});
