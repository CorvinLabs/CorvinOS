/** The four tabs of the Models console (ADR-0885 D1). The id is the URL
 *  value (`?tab=`); the order is the tab-bar order. */
export const TAB_IDS = ["routing", "usage-cost", "learning", "catalog"] as const;
export type TabId = (typeof TAB_IDS)[number];

export const DEFAULT_TAB: TabId = "routing";

export const TAB_LABEL: Record<TabId, string> = {
  routing: "Routing",
  "usage-cost": "Usage & Cost",
  learning: "Learning",
  catalog: "Catalog",
};

export function isTabId(v: string | null | undefined): v is TabId {
  return v != null && (TAB_IDS as readonly string[]).includes(v);
}

/** Rendered caption of the page header — ALSO the deploy marker: a string
 *  literal that survives minification and exists nowhere else in the bundle
 *  (terser strips component identifiers, so a marker must be rendered text). */
export const MARKER_HEADER =
  "Which model serves each turn, what it costs, and what the selector has learned.";
