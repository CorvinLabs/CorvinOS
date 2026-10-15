/**
 * Usage & Cost tab — the Day / Week / Month switch of the trend chart, driven
 * through the REAL <UsageCostTab/> (real status hook, MSW answering the
 * endpoint). jsdom gives recharts no width, so the assertions read the chart's
 * own caption and coverage line, which are derived from the folded rows.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { server } from "../fixtures/server";

vi.mock("@/lib/auth", () => ({
  useAuth: () => ({
    session: { tenant_id: "_default", csrf_token: "csrf-test", tier: "owner" },
    loading: false, refresh: vi.fn(), logout: vi.fn(),
  }),
}));
vi.mock("@/pages/models/components/engine-parts", () => ({ ModelUsagePanel: () => <div /> }));

import { UsageCostTab } from "@/pages/models/tabs/usage-cost";

const row = (date: string, usd: number) => ({
  date, actual_usd: usd, baseline_usd: usd * 5, counted_turns: 2, total_turns: 2,
  acs_actual_usd: null, acs_baseline_usd: null, acs_counted_turns: 0, acs_total_turns: 0,
});
const status = {
  converged_count: 0, total_count: 0, thresholds: [], cost_savings_percent: 80,
  cost_baseline_usd: 50, cost_current_usd: 10, cost_data_available: true,
  cost_counted_turns: 10, cost_total_turns: 10,
  // Wed, Fri, Sunday | Mon, Tue — two ISO weeks, two calendar months
  cost_history: [row("2026-09-30", 1), row("2026-10-02", 2), row("2026-10-04", 2),
                 row("2026-10-05", 3), row("2026-10-06", 4)],
  cost_model_mix: { "claude-haiku-4-5-20251001": 10 }, cost_os_model_pin: null,
  acs_cost_actual_usd: 0, acs_cost_baseline_usd: 0, acs_model_mix: {}, acs_data_available: false,
  rate_limit_windows: { observed_at: 1791500000, status: "allowed", windows: {
    five_hour: { utilization: 0.08, resets_at: 4102444800 },      // far future: never "passed"
    seven_day: { utilization: 0.16, resets_at: 4102448400 },
  } },
  accuracy_percent: 90, last_updated: "2026-10-13T12:00:00Z",
  window: { active: false, epoch_ts: null, since_iso: null, reason: "" },
};

// recharts' ResponsiveContainer does `new ResizeObserver(...)`; the shared
// setup stub is an arrow function and cannot be constructed.
vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });

afterEach(cleanup);

describe("Usage & Cost — trend resolution", () => {
  it("folds the same daily rows into weeks and months when switched", async () => {
    server.use(http.get("/v1/console/learning/model-cost-optimizer/status", () => HttpResponse.json(status)));
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={qc}><UsageCostTab /></QueryClientProvider>);

    expect((await screen.findByTestId("trend-caption")).textContent).toContain("5 days");
    expect(screen.getByTestId("daily-coverage-os").textContent).toContain("all 5 days");

    fireEvent.click(screen.getByTestId("trend-unit-week"));
    expect(screen.getByTestId("trend-caption").textContent).toContain("2 weeks");
    expect(screen.getByTestId("trend-caption").textContent).toContain("weeks start Monday (UTC)");
    expect(screen.getByTestId("daily-coverage-os").textContent).toContain("all 2 weeks");

    fireEvent.click(screen.getByTestId("trend-unit-month"));
    expect(screen.getByTestId("trend-caption").textContent).toContain("2 months");
    expect(screen.getByTestId("daily-coverage-os").textContent).toContain("all 2 months");
    expect(screen.getByTestId("trend-unit-month").getAttribute("aria-pressed")).toBe("true");
  });

  it("shows the provider reset times, and says so when none were observed", async () => {
    server.use(http.get("/v1/console/learning/model-cost-optimizer/status", () => HttpResponse.json(status)));
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const first = render(<QueryClientProvider client={qc}><UsageCostTab /></QueryClientProvider>);
    const seven = await screen.findByTestId("quota-seven_day");
    expect(seven.textContent).toContain("7-day window");
    expect(seven.textContent).toContain("16% used when last seen");
    expect(screen.getByTestId("quota-five_hour").textContent).toContain("5-hour window");
    first.unmount();

    server.use(http.get("/v1/console/learning/model-cost-optimizer/status",
      () => HttpResponse.json({ ...status, rate_limit_windows: {} })));
    render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><UsageCostTab /></QueryClientProvider>);
    expect((await screen.findByTestId("quota-windows")).textContent).toContain("not observed yet");
  });
});
