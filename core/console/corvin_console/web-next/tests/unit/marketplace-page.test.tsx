/**
 * Marketplace (ADR-0892) — the real page and tabs against MSW.
 *
 *  - tab ↔ URL: `?tab=packages` opens Packages; a bogus tab is rewritten to
 *    `browse` with REPLACE; the header renders the deploy marker.
 *  - Browse: an installable entry has an Install button that POSTs the real
 *    install route WITH X-CSRF-Token and renders the response's outcome; a
 *    non-installable entry shows its blocker and NO button; an installed entry
 *    hands off to the Installed tab.
 *  - Installed: enable/disable/uninstall hit the CSRF-signed /plugins routes;
 *    Uninstall is disabled while the plugin is enabled (the backend refuses).
 *  - Packages: Uninstall sends DELETE with CSRF after a two-click confirm.
 *  - Tools: a 503 renders the "not available on this build" state, never an
 *    empty catalogue; install POSTs the source with CSRF.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation, useNavigationType } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { server } from "../fixtures/server";

vi.mock("@/lib/auth", () => ({
  useAuth: () => ({
    session: { tenant_id: "_default", csrf_token: "csrf-test", tier: "owner" },
    loading: false, refresh: vi.fn(), logout: vi.fn(),
  }),
}));

import { MarketplacePage } from "@/pages/marketplace";
import { MARKER_HEADER } from "@/pages/marketplace/tabs";

const INDEX = {
  plugins: [
    { id: "plugin:buildin-memory-recall", type: "plugin", name: "Recall", version: "1.0.0", author: "CorvinLabs", license: "Apache-2.0",
      tier: "buildin", category: "memory", description: "Recall backend.", registry_id: "recall", installable: true, install_blocker: null,
      installed: false, enabled: false, runtime_loaded: false },
    { id: "plugin:contributor-integration-slack", type: "plugin", name: "Slack Notifier", version: "1.0.0", author: "x", license: "MIT",
      tier: "contributor", category: "integration", description: "Slack.", registry_id: null, installable: false,
      install_blocker: "only builtin plugins install locally", installed: false, enabled: false, runtime_loaded: false },
    { id: "plugin:buildin-observability-otel", type: "plugin", name: "OTEL", version: "2.0.0", author: "CorvinLabs", license: "Apache-2.0",
      tier: "buildin", category: "observability", description: "OTEL.", registry_id: "otel", installable: true, install_blocker: null,
      installed: true, enabled: true, runtime_loaded: true },
  ],
  count: 3, filtered_by: { category: null, tier: null },
};
const plugin = (id: string, enabled: boolean) => ({
  plugin_id: id, version: "1.0.0", display_name: id, plugin_type: "recall_backend", origin: "vetted", pii_risk: "none",
  locality: "local", network_egress: "none", egress_hosts: [], enabled, runtime_loaded: enabled, contained_by: null,
  requires_consent: false, settings: {}, settings_schema: {}, dependencies: [], installed_at: null, last_error_type: null,
});

interface Seen { method: string; path: string; csrf: string | null; body: unknown }
const seen: Seen[] = [];
const record = async (request: Request) => {
  let body: unknown = null;
  try { body = await request.clone().json(); } catch { /* multipart or empty */ }
  seen.push({ method: request.method, path: decodeURIComponent(new URL(request.url).pathname), csrf: request.headers.get("x-csrf-token"), body });
};

function baseHandlers(over: Partial<{ toolsStatus: number; installed: ReturnType<typeof plugin>[] }> = {}) {
  const installed = over.installed ?? [plugin("otel", true), plugin("recall-off", false)];
  return [
    http.get("/v1/console/api/v1/marketplace/stats", () => HttpResponse.json({ total_plugins: 3, by_category: {}, by_tier: {}, generated_at: "2026-09-07T16:14:31Z" })),
    http.get("/v1/console/api/v1/marketplace/plugins", () => HttpResponse.json(INDEX)),
    http.post("/v1/console/api/v1/marketplace/plugins/:id/install", async ({ request, params }) => {
      await record(request);
      return HttpResponse.json({ status: "completed", job_id: "install_1", plugin_id: params.id, registry_id: "recall", version: "1.0.0" });
    }),
    http.get("/v1/console/plugins", () => HttpResponse.json({ plugins: installed, total: installed.length, lifecycle_enabled: true })),
    http.get("/v1/console/plugins/health", () => HttpResponse.json({ monitoring_enabled: false, breakers: {} })),
    http.post("/v1/console/plugins/:id/enable", async ({ request, params }) => { await record(request); return HttpResponse.json(plugin(String(params.id), true)); }),
    http.post("/v1/console/plugins/:id/disable", async ({ request, params }) => { await record(request); return HttpResponse.json(plugin(String(params.id), false)); }),
    http.delete("/v1/console/plugins/:id", async ({ request, params }) => { await record(request); return HttpResponse.json({ uninstalled: params.id, audit_retained: true }); }),
    http.get("/v1/console/packages", () => HttpResponse.json({ packages: [
      { package_id: "adscale-ldd", version: "0.6.0", display_name: "adscale LDD", description: "d", author: "a", installed_at: "2026-09-08T00:00:00Z", tenant_id: "_default" },
    ], total: 1 })),
    http.delete("/v1/console/packages/:id", async ({ request }) => { await record(request); return HttpResponse.json({ ok: true }); }),
    http.get("/v1/console/mcp-plugins", () => over.toolsStatus === 503
      ? HttpResponse.json({ detail: "MCP Plugin Manager not available on this installation." }, { status: 503 })
      : HttpResponse.json({ tenant_id: "_default", count: 0, tools: [], active: {} })),
    http.post("/v1/console/mcp-plugins/install", async ({ request }) => {
      await record(request);
      return HttpResponse.json({ ok: true, tool: { id: "npm-x", source: "npm:x", installed_at: null, runtime: "npx", compliance: {}, secrets: [], active: false, active_scopes: [], sha256: null } });
    }),
  ];
}

function LocationProbe() {
  const loc = useLocation(); const nav = useNavigationType();
  return <div data-testid="probe" data-search={loc.search} data-nav={nav} />;
}

function renderAt(path: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}>
        <Routes><Route path="/app/marketplace" element={<><MarketplacePage /><LocationProbe /></>} /></Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => { cleanup(); seen.length = 0; });

describe("Marketplace — tab ↔ URL", () => {
  it("renders the marker and opens the tab named in the URL", async () => {
    server.use(...baseHandlers());
    renderAt("/app/marketplace?tab=packages");
    expect(screen.getByText(MARKER_HEADER)).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Packages" })).toHaveAttribute("aria-selected", "true");
    await screen.findByText(/skill packages installed for this tenant/);
  });
  it("rewrites a bogus tab to browse with REPLACE", async () => {
    server.use(...baseHandlers());
    renderAt("/app/marketplace?tab=bogus");
    await waitFor(() => expect(screen.getByTestId("probe").getAttribute("data-search")).toBe("?tab=browse"));
    expect(screen.getByTestId("probe").getAttribute("data-nav")).toBe("REPLACE");
  });
});

describe("Browse", () => {
  it("installs an installable entry with CSRF and renders the outcome; a blocked entry has no button", async () => {
    server.use(...baseHandlers());
    renderAt("/app/marketplace?tab=browse");
    const recall = await screen.findByTestId("index-card-plugin:buildin-memory-recall");
    fireEvent.click(recall.querySelector("button.w-full") as HTMLButtonElement);
    await screen.findByText(/Installed — disabled until you enable it on the Installed tab/);
    expect(seen[0]).toMatchObject({ method: "POST", path: "/v1/console/api/v1/marketplace/plugins/plugin:buildin-memory-recall/install", csrf: "csrf-test", body: { version: "1.0.0" } });
    const slack = screen.getByTestId("index-card-plugin:contributor-integration-slack");
    expect(slack.textContent).toMatch(/Not installable on this build: only builtin plugins install locally/);
    expect(slack.querySelector("button.w-full")).toBeNull();
    const otel = screen.getByTestId("index-card-plugin:buildin-observability-otel");
    expect(otel.textContent).toMatch(/Manage on the Installed tab/);
    expect(screen.getByTestId("browse-summary").textContent).toMatch(/3 of 3 entries shown · 2 installable on this build · 1 not installable/);
  });
});

describe("Installed", () => {
  it("disables with CSRF; Uninstall is blocked while enabled; a disabled plugin uninstalls after confirm", async () => {
    server.use(...baseHandlers());
    renderAt("/app/marketplace?tab=installed");
    const otel = await screen.findByTestId("installed-row-otel");
    const uninstallOtel = Array.from(otel.querySelectorAll("button")).find((b) => b.textContent === "Uninstall") as HTMLButtonElement;
    expect(uninstallOtel).toBeDisabled();
    fireEvent.click(Array.from(otel.querySelectorAll("button")).find((b) => b.textContent === "Disable") as HTMLButtonElement);
    await screen.findByText("Disabled — audited.");
    expect(seen[0]).toMatchObject({ method: "POST", path: "/v1/console/plugins/otel/disable", csrf: "csrf-test" });

    const off = screen.getByTestId("installed-row-recall-off");
    fireEvent.click(Array.from(off.querySelectorAll("button")).find((b) => b.textContent === "Uninstall") as HTMLButtonElement);
    fireEvent.click(screen.getByRole("button", { name: /Confirm: remove recall-off/ }));
    await screen.findByText(/Uninstalled — the record and its instance directory are gone/);
    expect(seen[1]).toMatchObject({ method: "DELETE", path: "/v1/console/plugins/recall-off", csrf: "csrf-test" });
  });
});

describe("Packages", () => {
  it("uninstalls with CSRF after a two-click confirm", async () => {
    server.use(...baseHandlers());
    renderAt("/app/marketplace?tab=packages");
    const card = await screen.findByTestId("package-card-adscale-ldd");
    fireEvent.click(Array.from(card.querySelectorAll("button")).find((b) => b.textContent === "Uninstall") as HTMLButtonElement);
    fireEvent.click(screen.getByRole("button", { name: /Confirm: delete adscale-ldd/ }));
    await screen.findByText("Uninstalled adscale-ldd — audited.");
    expect(seen[0]).toMatchObject({ method: "DELETE", path: "/v1/console/packages/adscale-ldd", csrf: "csrf-test" });
  });
});

describe("MCP tools", () => {
  it("renders the not-available state on 503 — never an empty catalogue", async () => {
    server.use(...baseHandlers({ toolsStatus: 503 }));
    renderAt("/app/marketplace?tab=tools");
    await screen.findByText(/The MCP Plugin Manager is not available on this build/);
    expect(screen.queryByText(/tools in this tenant's catalogue/)).toBeNull();
  });
  it("installs a tool from a source with CSRF", async () => {
    server.use(...baseHandlers());
    renderAt("/app/marketplace?tab=tools");
    const input = await screen.findByLabelText("Tool source");
    fireEvent.change(input, { target: { value: "npm:x" } });
    fireEvent.click(screen.getByRole("button", { name: "Install" }));
    await screen.findByText(/Installed npm-x — inactive until you activate a scope/);
    expect(seen[0]).toMatchObject({ method: "POST", path: "/v1/console/mcp-plugins/install", csrf: "csrf-test", body: { source: "npm:x", allow_unpin: false } });
  });
});
