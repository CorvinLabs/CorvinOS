/**
 * mergeManifestNav (layout.tsx) — the ADR-0561 console manifest may only ADD
 * sidebar entries; it must never hide a core panel from NAV_GROUPS.
 *
 * 2026-09-03: rendering the sidebar FROM the manifest (7 backend-known panels)
 * hid ~30 core panels, and the manifest fetch 404'd anyway, leaving a sidebar
 * that said "Nav loading (fallback)" and linked nothing.
 */
import { describe, it, expect } from "vitest";
import { mergeManifestNav } from "@/components/layout";
import type { ConsoleManifest, PanelDescriptor } from "@/adapters/capabilities";

const Icon = () => null;
const base = () => [
  { id: "primary", items: [{ to: "/app/chat", label: "Chat", icon: Icon }, { to: "/app/dashboard", label: "Dashboard", icon: Icon }] },
  { id: "build", label: "Build", collapsible: true, defaultOpen: true, items: [{ to: "/app/skills", label: "Skills", icon: Icon }] },
];

function panel(id: string, route = id, extra: Partial<PanelDescriptor> = {}): PanelDescriptor {
  return {
    id, title: id.toUpperCase(), route, icon: "Blocks", kind: "plugin", source: "installed",
    nav_group: "build", requiredFlag: null, requiredCapability: null,
    element: { kind: "plugin-inspector", plugin_id: id }, version: "1.0.0", audit_events: [], tenant_scoped: true,
    ...extra,
  };
}

function manifest(panels: PanelDescriptor[], nav_groups: ConsoleManifest["nav_groups"], flags: Record<string, boolean> = {}, capabilities: string[] = []): ConsoleManifest {
  return { version: "2.0", timestamp: "t", contract_version: "1", capabilities, flags, panels, nav_groups, hash: "h" };
}

describe("mergeManifestNav", () => {
  it("leaves the static nav untouched when there is no manifest", () => {
    expect(mergeManifestNav(base(), undefined)).toEqual(base());
    expect(mergeManifestNav(base(), null)).toEqual(base());
  });

  it("never removes a core entry, even when the manifest knows fewer panels", () => {
    const m = manifest([panel("chat")], [{ id: "primary", label: null, collapsible: false, defaultOpen: true, items: [{ panel_id: "chat" }] }]);
    const out = mergeManifestNav(base(), m);
    expect(out.flatMap((g) => g.items.map((i) => i.to))).toEqual(["/app/chat", "/app/dashboard", "/app/skills"]);
  });

  it("adds a manifest-only panel into the existing group of the same id", () => {
    const m = manifest([panel("my-plugin")], [{ id: "build", label: "Build", collapsible: true, defaultOpen: true, items: [{ panel_id: "my-plugin" }] }]);
    const out = mergeManifestNav(base(), m);
    const build = out.find((g) => g.id === "build")!;
    expect(build.items.map((i) => i.to)).toEqual(["/app/skills", "/app/my-plugin"]);
    expect(build.items[1].label).toBe("MY-PLUGIN");
  });

  it("creates a new group for a manifest group the static nav lacks", () => {
    const m = manifest([panel("ext")], [{ id: "extensions", label: "Extensions", collapsible: true, defaultOpen: false, items: [{ panel_id: "ext" }] }]);
    const out = mergeManifestNav(base(), m);
    const g = out.find((x) => x.id === "extensions")!;
    expect(g.label).toBe("Extensions");
    expect(g.defaultOpen).toBe(false);
    expect(g.items.map((i) => i.to)).toEqual(["/app/ext"]);
  });

  it("does not add a manifest panel whose flag or capability gate fails", () => {
    const m = manifest(
      [panel("flagged", "flagged", { requiredFlag: "x" }), panel("capped", "capped", { requiredCapability: "cap" }), panel("ok", "ok", { requiredFlag: "y" })],
      [{ id: "build", label: "Build", collapsible: true, defaultOpen: true, items: [{ panel_id: "flagged" }, { panel_id: "capped" }, { panel_id: "ok" }] }],
      { x: false, y: true },
      [],
    );
    const out = mergeManifestNav(base(), m);
    expect(out.find((g) => g.id === "build")!.items.map((i) => i.to)).toEqual(["/app/skills", "/app/ok"]);
  });

  it("dedupes by route, so a manifest panel with a different id but a known route is not linked twice", () => {
    const m = manifest([panel("plugins", "plugin-center")], [{ id: "build", label: "Build", collapsible: true, defaultOpen: true, items: [{ panel_id: "plugins" }] }]);
    const groups = base();
    groups[1].items.push({ to: "/app/plugin-center", label: "Plugins & Extensions", icon: Icon });
    const out = mergeManifestNav(groups, m);
    expect(out.find((g) => g.id === "build")!.items.filter((i) => i.to === "/app/plugin-center")).toHaveLength(1);
  });
});
