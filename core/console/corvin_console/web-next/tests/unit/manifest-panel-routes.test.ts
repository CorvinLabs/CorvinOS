/**
 * Test manifestPanelRoutes() — manifest-driven panel routing (ADR-0561, Phase 2)
 */
import { describe, it, expect, vi } from "vitest";
import { manifestPanelRoutes, PanelNotAvailable } from "@/panels/registry";
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
    // Framed without allow-same-origin: scripts only.
    const host = routes[0]?.props.element;
    expect(host.props.src).toBe("https://example.com/panel.html");
    expect(host.props.sandbox).toBe("allow-scripts");
  });

  it("does not frame an iframe panel whose src is not on the allowlist", () => {
    const base = {
      title: "Custom", icon: "Box", kind: "feature" as const, source: "installed" as const,
      nav_group: "plugins", requiredFlag: null, requiredCapability: null,
      version: "1.0.0", audit_events: [], tenant_scoped: true,
    };
    const bad = [
      "javascript:alert(document.cookie)",
      "data:text/html,<script>1</script>",
      "http://example.com/panel.html",
      "/console/app/settings",
      "/v1/console/panels/../auth/whoami",
    ];
    const panels = bad.map((src, i) => ({
      ...base, id: `p${i}`, route: `p${i}`, element: { kind: "iframe", src },
    })) as unknown as PanelDescriptor[];
    const routes = manifestPanelRoutes(panels);
    expect(routes).toHaveLength(bad.length);
    for (const r of routes) {
      const el = r?.props.element;
      expect(el.type).toBe(PanelNotAvailable);
      expect(el.props.src).toBeUndefined();
    }
  });

  it("mounts a plugin inspector without invented state", () => {
    const panels = [{
      id: "plugin-x", title: "Plugin X", route: "plugins/x", icon: "Box", kind: "feature",
      source: "installed", nav_group: "plugins", requiredFlag: null, requiredCapability: null,
      element: { kind: "plugin-inspector", plugin_id: "x" },
      version: "1.0.0", audit_events: [], tenant_scoped: true,
    }] as unknown as PanelDescriptor[];
    const el = manifestPanelRoutes(panels)[0]?.props.element;
    expect(el.props.pluginId).toBe("x");
    expect(el.props.enabled).toBeUndefined();
    expect(el.props.onToggleEnabled).toBeUndefined();
    expect(el.props.config).toBeUndefined();
    expect(el.props.auditEvents).toBeUndefined();
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
