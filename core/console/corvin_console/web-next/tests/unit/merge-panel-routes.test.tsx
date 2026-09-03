/**
 * mergePanelRoutes — a manifest panel this bundle cannot render must NOT shadow
 * the registry route for the same path. 2026-09-03: "/app/vibe-engineering
 * doesn't exist" after the manifest loaded, because the manifest lists it with
 * the unsupported `react` kind and the dedupe was keyed on manifest ids.
 */
import { describe, it, expect, vi } from "vitest";
import { mergePanelRoutes, PANELS } from "@/panels/registry";
import type { PanelDescriptor } from "@/adapters/capabilities";

const paths = (routes: ReturnType<typeof mergePanelRoutes>) =>
  routes.filter(Boolean).map((r) => String(r!.props.path));

function panel(id: string, element: PanelDescriptor["element"], route = id): PanelDescriptor {
  return {
    id, title: id, route, icon: "Blocks", kind: "feature", source: "builtin", nav_group: "primary",
    requiredFlag: null, requiredCapability: null, element, version: "1.0.0", audit_events: [], tenant_scoped: true,
  };
}

describe("mergePanelRoutes", () => {
  it("without a manifest mounts every registry panel", () => {
    const p = paths(mergePanelRoutes(undefined));
    expect(p).toContain("vibe-engineering");
    expect(p).toHaveLength(PANELS.length);
  });

  it("keeps the registry route when the manifest lists the panel with an unsupported kind", () => {
    vi.spyOn(console, "warn").mockImplementation(() => {});
    const m = [panel("vibe-engineering", { kind: "react", load: "@/pages/vibe-engineering" })];
    const p = paths(mergePanelRoutes(m));
    expect(p.filter((x) => x === "vibe-engineering")).toHaveLength(1);
  });

  it("keeps the registry route when the manifest names an unknown component", () => {
    vi.spyOn(console, "warn").mockImplementation(() => {});
    const m = [panel("dashboard", { kind: "react-component", component: "NoSuchPage" })];
    expect(paths(mergePanelRoutes(m)).filter((x) => x === "dashboard")).toHaveLength(1);
  });

  it("lets a renderable manifest panel take over its path exactly once", () => {
    const m = [panel("dashboard", { kind: "react-component", component: "DashboardPage" })];
    const routes = mergePanelRoutes(m);
    const dash = routes.filter((r) => r && String(r.props.path) === "dashboard");
    expect(dash).toHaveLength(1);
    expect(dash[0]!.key).toBe("dashboard");
  });

  it("dedupes by path even when the manifest id differs from the registry id", () => {
    const m = [panel("plugins", { kind: "react-component", component: "ExtensionsPage" }, "plugin-center")];
    expect(paths(mergePanelRoutes(m)).filter((x) => x === "plugin-center")).toHaveLength(1);
  });
});
