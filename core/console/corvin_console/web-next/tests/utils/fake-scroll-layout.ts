/**
 * jsdom has no layout: scrollHeight and clientHeight are always 0, so a
 * windowed list would mount nothing and "scroll to bottom" would be a no-op.
 * This gives every `overflow-y-auto` element a plausible geometry: a fixed
 * viewport, and content height = spacer heights + mounted rows × row height.
 * scrollTop is stored by jsdom already, so only the two getters are added.
 *
 * It also replaces the global ResizeObserver mock from tests/setup.ts, which is
 * a vi.fn arrow implementation and cannot be called with `new` (the restore
 * function puts the previous global back).
 * Rows are measured at `rowHeight` (the hook's estimate), so no observer fires.
 */
export function installFakeScrollLayout(opts: { rowHeight?: number; viewport?: number } = {}) {
  const rowHeight = opts.rowHeight ?? 120;
  const viewport = opts.viewport ?? 800;
  const originals = {
    scrollHeight: Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'scrollHeight'),
    clientHeight: Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'clientHeight'),
  };
  const prevResizeObserver = globalThis.ResizeObserver;
  const isScroller = (el: HTMLElement) => el.classList.contains('overflow-y-auto');
  const contentHeight = (el: HTMLElement) => {
    const rows = el.querySelector<HTMLElement>('[data-testid="chat-message-rows"]');
    if (!rows) return 0;
    const spacers = [rows.firstElementChild, rows.lastElementChild] as HTMLElement[];
    const spaced = spacers.reduce((sum, sp) => sum + (parseFloat(sp?.style.height || '0') || 0), 0);
    return spaced + rows.querySelectorAll('[data-row-key]').length * rowHeight + 400;
  };
  Object.defineProperty(HTMLElement.prototype, 'scrollHeight', {
    configurable: true,
    get(this: HTMLElement) {
      return isScroller(this) ? contentHeight(this) : 0;
    },
  });
  Object.defineProperty(HTMLElement.prototype, 'clientHeight', {
    configurable: true,
    get(this: HTMLElement) {
      return isScroller(this) ? viewport : 0;
    },
  });

  class FakeResizeObserver {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  globalThis.ResizeObserver = FakeResizeObserver as unknown as typeof ResizeObserver;

  return () => {
    if (originals.scrollHeight) Object.defineProperty(HTMLElement.prototype, 'scrollHeight', originals.scrollHeight);
    if (originals.clientHeight) Object.defineProperty(HTMLElement.prototype, 'clientHeight', originals.clientHeight);
    globalThis.ResizeObserver = prevResizeObserver;
  };
}
