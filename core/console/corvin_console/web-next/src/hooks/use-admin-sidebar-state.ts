/**
 * Collapse/expand state for the chat command-center admin sidebar.
 * Persisted to localStorage (not a URL param) — purely a display preference,
 * never a routable/shareable piece of state.
 */
import * as React from "react";

const STORAGE_KEY = "corvin.chat.adminSidebarCollapsed";

export function useAdminSidebarState(): [boolean, () => void] {
  const [collapsed, setCollapsed] = React.useState<boolean>(() => {
    try {
      return window.localStorage.getItem(STORAGE_KEY) === "1";
    } catch {
      return false;
    }
  });

  const toggle = React.useCallback(() => {
    setCollapsed((prev) => {
      const next = !prev;
      try {
        window.localStorage.setItem(STORAGE_KEY, next ? "1" : "0");
      } catch {
        /* localStorage may be blocked in private mode */
      }
      return next;
    });
  }, []);

  return [collapsed, toggle];
}
