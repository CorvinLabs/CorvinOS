/**
 * Models console (ADR-0885) — tab ↔ URL contract and the CSRF invariant.
 *
 * Renders the REAL <ModelsPage/> (real router, real Radix tabs, real query
 * client) with the four tab bodies mocked, MSW answering the header's three
 * reads, and a probe route that exposes the current search string and the
 * navigation type — history length is not observable under MemoryRouter.
 *
 *  - `?tab=catalog` opens the Catalog tab (aria-selected);
 *  - `?tab=bogus` is rewritten to `routing` with REPLACE;
 *  - clicking a tab is a PUSH (browser back returns to the previous tab);
 *  - every POST/PUT the page sends carries X-CSRF-Token (an MSW handler fails
 *    the request otherwise) — the invariant behind the old cost panel's broken
 *    writes.
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
    loading: false,
    refresh: vi.fn(),
    logout: vi.fn(),
  }),
}));
vi.mock("@/pages/models/tabs/routing", () => ({ RoutingTab: () => <div data-testid="tab-routing" /> }));
vi.mock("@/pages/models/tabs/usage-cost", () => ({ UsageCostTab: () => <div data-testid="tab-usage" /> }));
vi.mock("@/pages/models/tabs/learning", () => ({ LearningTab: () => <div data-testid="tab-learning" /> }));
vi.mock("@/pages/models/tabs/catalog", () => ({ CatalogTab: () => <div data-testid="tab-catalog" /> }));
// The auth card queries the engine probe; keep the header's own reads to three.
vi.mock("@/pages/models/components/engine-parts", () => ({
  ClaudeCodeAuthStatus: () => <div data-testid="auth-status" />,
}));

import { ModelsPage } from "@/pages/models";
import { MARKER_HEADER } from "@/pages/models/tabs";

function LocationProbe() {
  const loc = useLocation();
  const nav = useNavigationType();
  return <div data-testid="probe" data-search={loc.search} data-nav={nav} />;
}

const csrfSeen: string[] = [];

function handlers() {
  return [
    http.get("/v1/console/settings/engine", () =>
      HttpResponse.json({
        default_engine: "claude_code",
        valid_engines: ["claude_code"],
        engine_models: { claude_code: { os_model: "claude-sonnet-5", worker_model: null, provider: null } },
        compliance_warnings: [],
      })),
    http.get("/v1/console/learning/model-cost-optimizer/status", () =>
      HttpResponse.json({
        converged_count: 0, total_count: 3, thresholds: [], cost_savings_percent: 0,
        cost_baseline_usd: 0, cost_current_usd: 0, cost_data_available: false, cost_history: [],
        cost_model_mix: {}, cost_os_model_pin: "claude-sonnet-5", acs_cost_actual_usd: 0,
        acs_cost_baseline_usd: 0, acs_model_mix: {}, acs_data_available: false,
        acs_worker_model_pin: null, accuracy_percent: 0, last_updated: new Date().toISOString(),
        window: { active: false, epoch_ts: null, since_iso: null, reason: "" },
      })),
    http.post("/v1/console/learning/model-cost-optimizer/usage-epoch", ({ request }) => {
      const token = request.headers.get("x-csrf-token");
      csrfSeen.push(token ?? "");
      if (!token) return HttpResponse.json({ detail: "missing CSRF token" }, { status: 403 });
      return HttpResponse.json({ ok: true });
    }),
  ];
}

function renderAt(path: string) {
  server.use(...handlers());
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/app/models" element={<><ModelsPage /><LocationProbe /></>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => { cleanup(); csrfSeen.length = 0; });

describe("Models console — tab ↔ URL", () => {
  it("renders the header marker and opens the tab named in the URL", async () => {
    renderAt("/app/models?tab=catalog");
    expect(screen.getByText(MARKER_HEADER)).toBeInTheDocument();
    const tab = screen.getByRole("tab", { name: "Catalog" });
    expect(tab).toHaveAttribute("aria-selected", "true");
    expect(screen.getByTestId("tab-catalog")).toBeInTheDocument();
  });

  it("rewrites a bogus tab to routing with REPLACE", async () => {
    renderAt("/app/models?tab=bogus");
    await waitFor(() => {
      expect(screen.getByTestId("probe").getAttribute("data-search")).toBe("?tab=routing");
    });
    expect(screen.getByTestId("probe").getAttribute("data-nav")).toBe("REPLACE");
    expect(screen.getByRole("tab", { name: "Routing" })).toHaveAttribute("aria-selected", "true");
  });

  it("switches tabs with a PUSH so browser back returns", async () => {
    renderAt("/app/models?tab=routing");
    fireEvent.mouseDown(screen.getByRole("tab", { name: "Learning" }));
    fireEvent.click(screen.getByRole("tab", { name: "Learning" }));
    await waitFor(() => {
      expect(screen.getByTestId("probe").getAttribute("data-search")).toBe("?tab=learning");
    });
    expect(screen.getByTestId("probe").getAttribute("data-nav")).toBe("PUSH");
    expect(screen.getByTestId("tab-learning")).toBeInTheDocument();
    // The Routing content stays mounted (hidden) so a half-edited form survives.
    expect(screen.getByTestId("tab-routing")).toBeInTheDocument();
  });

  it("sends X-CSRF-Token on the window reset (two-click confirm)", async () => {
    renderAt("/app/models?tab=routing");
    const btn = await screen.findByRole("button", { name: "Reset counters to now" });
    fireEvent.click(btn);
    fireEvent.click(screen.getByRole("button", { name: "Click again to confirm" }));
    await waitFor(() => expect(csrfSeen.length).toBe(1));
    expect(csrfSeen[0]).toBe("csrf-test");
  });
});
