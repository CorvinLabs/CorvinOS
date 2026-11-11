/**
 * Keeps Tab and Shift+Tab inside a panel that presents itself as a modal dialog (role="dialog" aria-modal="true"), the way
 * the Radix dialogs do for the wizard. Used by the side panels the chat opens (Explorer, Changes): without it, Tab walked
 * out to the page behind the panel (REVIEW-0067 #15). Focus that is already outside the panel is brought back in on the
 * next Tab. Escape and the initial focus stay with the panel itself.
 */
import { useEffect, type RefObject } from "react";

const FOCUSABLE = [
  "a[href]", "button:not([disabled])", "input:not([disabled]):not([type=hidden])", "select:not([disabled])",
  "textarea:not([disabled])", "[tabindex]:not([tabindex='-1'])",
].join(",");

function focusables(root: HTMLElement): HTMLElement[] {
  return Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE))
    .filter((el) => !el.hasAttribute("inert") && el.getClientRects().length > 0);
}

export function useFocusTrap(ref: RefObject<HTMLElement | null>, active = true): void {
  useEffect(() => {
    if (!active) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Tab") return;
      const root = ref.current;
      if (!root) return;
      const items = focusables(root);
      if (items.length === 0) { e.preventDefault(); root.focus(); return; }
      const first = items[0];
      const last = items[items.length - 1];
      const cur = document.activeElement as HTMLElement | null;
      const inside = !!cur && root.contains(cur);
      if (!inside) { e.preventDefault(); (e.shiftKey ? last : first).focus(); return; }
      if (e.shiftKey && (cur === first || cur === root)) { e.preventDefault(); last.focus(); return; }
      if (!e.shiftKey && cur === last) { e.preventDefault(); first.focus(); }
    };
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  }, [ref, active]);
}
