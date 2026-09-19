/** The four tabs of the Marketplace (ADR-0892 D1). The id is the URL value
 *  (`?tab=`); the order is the tab-bar order. */
export const TAB_IDS = ["browse", "installed", "packages", "tools"] as const;
export type TabId = (typeof TAB_IDS)[number];

export const DEFAULT_TAB: TabId = "browse";

export const TAB_LABEL: Record<TabId, string> = {
  browse: "Browse",
  installed: "Installed",
  packages: "Packages",
  tools: "MCP tools",
};

export function isTabId(v: string | null | undefined): v is TabId {
  return v != null && (TAB_IDS as readonly string[]).includes(v);
}

/** Rendered caption of the page header — ALSO the deploy marker: a string
 *  literal that survives minification and exists nowhere else in the bundle
 *  (terser strips component identifiers, so a marker must be rendered text). */
export const MARKER_HEADER =
  "Install, enable and remove plugins, skill packages and MCP tools — every action changes this install and is audited.";
