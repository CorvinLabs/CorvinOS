/**
 * Test manifestPanelRoutes() — manifest-driven panel routing (ADR-0561, Phase 2)
 */
import { describe, it, expect, vi } from "vitest";
import { manifestPanelRoutes } from "@/panels/registry";
import type { PanelDescriptor } from "@/adapters/capabilities";

describe("manifestPanelRoutes", () => {
  it("converts react-component panels to routes", () => {
    const panels: PanelDescriptor[] = [
      {
        id: "dashboard",
        title: "Dashboard",
        route: "dashboard",
        icon: "LayoutDashboard",
        kind: "feature",
        source: "builtin",
        nav_group: "primary",
        requiredFlag: null,
        requiredCapability: null,
        element: { kind: "react-component", component: "DashboardPage" },
        version: "1.0.0",
        audit_events: ["console_panel_opened"],
        tenant_scoped: true,
      },
    ];

    const routes = manifestPanelRoutes(panels);
    expect(routes).toHaveLength(1);
    expect(routes[0]).toBeDefined();
    expect(routes[0]?.key).toBe("dashboard");
    expect(routes[0]?.props.path).toBe("dashboard");
  });

  it("skips unknown components with warning", () => {
    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
    const panels: PanelDescriptor[] = [
      {
        id: "unknown",
        title: "Unknown",
        route: "unknown",
        icon: "Help",
        kind: "feature",
        source: "builtin",
        nav_group: "primary",
        requiredFlag: null,
        requiredCapability: null,
        element: { kind: "react-component", component: "NonExistentPage" },
        version: "1.0.0",
        audit_events: [],
        tenant_scoped: true,
      },
    ];

    const routes = manifestPanelRoutes(panels);
    expect(routes).toHaveLength(1);
    expect(routes[0]).toBeNull();
    expect(warnSpy).toHaveBeenCalledWith(
      expect.stringContaining("unknown component")
    );

    warnSpy.mockRestore();
  });

  it("handles iframe panels", () => {
    const panels: PanelDescriptor[] = [
      {
        id: "custom-panel",
        title: "Custom",
        route: "custom",
        icon: "Box",
        kind: "feature",
        source: "installed",
        nav_group: "plugins",
        requiredFlag: null,
        requiredCapability: null,
        element: {
          kind: "iframe",
          src: "https://example.com/panel.html",
        },
        version: "1.0.0",
        audit_events: [],
        tenant_scoped: true,
      },
    ];

    const routes = manifestPanelRoutes(panels);
    expect(routes).toHaveLength(1);
    expect(routes[0]).toBeDefined();
    expect(routes[0]?.props.path).toBe("custom");
  });

  it("filters out null routes", () => {
    const panels: PanelDescriptor[] = [
      {
        id: "async-panel",
        title: "Async",
        route: "async",
        icon: "Code",
        kind: "feature",
        source: "builtin",
        nav_group: "primary",
        requiredFlag: null,
        requiredCapability: null,
        element: {
          kind: "react",
          load: "@/pages/some-page",
        },
        version: "1.0.0",
        audit_events: [],
        tenant_scoped: true,
      },
    ];

    const routes = manifestPanelRoutes(panels);
    // Async "react" kind not yet supported; should return null
    expect(routes.filter((r) => r !== null)).toHaveLength(0);
  });

  it("handles mixed panel types", () => {
    const panels: PanelDescriptor[] = [
      {
        id: "dashboard",
        title: "Dashboard",
        route: "dashboard",
        icon: "LayoutDashboard",
        kind: "feature",
        source: "builtin",
        nav_group: "primary",
        requiredFlag: null,
        requiredCapability: null,
        element: { kind: "react-component", component: "DashboardPage" },
        version: "1.0.0",
        audit_events: [],
        tenant_scoped: true,
      },
      {
        id: "custom",
        title: "Custom",
        route: "custom",
        icon: "Box",
        kind: "feature",
        source: "installed",
        nav_group: "plugins",
        requiredFlag: null,
        requiredCapability: null,
        element: {
          kind: "iframe",
          src: "https://example.com/panel.html",
        },
        version: "1.0.0",
        audit_events: [],
        tenant_scoped: true,
      },
    ];

    const routes = manifestPanelRoutes(panels);
    const validRoutes = routes.filter((r) => r !== null);
    expect(validRoutes).toHaveLength(2);
  });
});
