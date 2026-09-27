/**
 * Unwired pages (no production caller, backend route not mounted) must say
 * "not available on this build" on a 404 — never an error loop, never a
 * placeholder state rendered as if it were measured — and must stop polling.
 *
 * Each case: MSW answers the page's data route with 404, the page renders its
 * honest notice, and the route is hit exactly once across a poll interval.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { server } from "../fixtures/server";

vi.mock("@/lib/auth", () => ({
  useAuth: () => ({ session: { tenant_id: "_default", csrf_token: "c", tier: "owner" }, status: "authenticated" }),
}));

import SkillsObservabilityPanel from "@/panels/skills_observability";
import RolloutMonitoringDashboard from "@/pages/monitoring-dashboard-extension";
import { MonitoringDashboardExtension } from "@/pages/orchestration/monitoring-dashboard-extension";
import { VideoStoryboardTimeline } from "@/components/VideoStoryboardTimeline";
import { LearningLoopsDashboard } from "@/pages/learning-loops-dashboard";
import { UnifiedLearningDashboard } from "@/pages/skills/unified-learning-dashboard";

afterEach(() => cleanup());

function count404(pattern: string) {
  const hits = { n: 0 };
  server.use(
    http.get(pattern, () => {
      hits.n += 1;
      return HttpResponse.json({ detail: "Not Found" }, { status: 404 });
    }),
  );
  return hits;
}

const wrap = (el: React.ReactElement) =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter>{el}</MemoryRouter>
    </QueryClientProvider>,
  );

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

describe("unwired pages on a build without their backend", () => {
  it("skills observability", async () => {
    const hits = count404("/v1/skills-observability/metrics/*");
    wrap(<SkillsObservabilityPanel />);
    expect(await screen.findByTestId("skills-observability-unavailable")).toHaveTextContent(/not available on this build/);
    await sleep(5200);
    expect(hits.n).toBe(4); // one round of the four endpoints, no 5 s poll after
  }, 10_000);

  it("rollout monitoring dashboard", async () => {
    const hits = count404("/v1/console/orchestration/status");
    wrap(<RolloutMonitoringDashboard />);
    expect(await screen.findByTestId("rollout-unavailable")).toHaveTextContent(/not available on this build/);
    expect(screen.queryByText(/API Connection Error/)).not.toBeInTheDocument();
    await sleep(5200);
    expect(hits.n).toBe(1);
  }, 10_000);

  it("orchestration monitoring extension never shows its placeholder state", async () => {
    const hits = count404("/v1/console/orchestration/status");
    wrap(<MonitoringDashboardExtension />);
    expect(await screen.findByTestId("orchestration-unavailable")).toHaveTextContent(/not available on this build/);
    expect(screen.queryByText(/✓ Valid/)).not.toBeInTheDocument();
    await sleep(2200);
    expect(hits.n).toBe(1);
  });

  it("orchestration monitoring extension on a 500 shows the error, not a fabricated 'Audit Chain: Valid'", async () => {
    server.use(http.get("/v1/console/orchestration/status", () => HttpResponse.json({}, { status: 500 })));
    wrap(<MonitoringDashboardExtension />);
    expect(await screen.findByRole("alert")).toHaveTextContent(/API error: 500/);
    expect(screen.queryByText(/Valid/)).not.toBeInTheDocument();
    expect(screen.queryByText("PHASE_1")).not.toBeInTheDocument();
  });

  it("video storyboard timeline", async () => {
    const hits = count404("/api/v1/timeline/state/:id");
    wrap(<VideoStoryboardTimeline taskId="t1" />);
    expect(await screen.findByTestId("storyboard-unavailable")).toHaveTextContent(/not available on this build/);
    await sleep(3200);
    expect(hits.n).toBe(1);
  });

  it("learning loops dashboard", async () => {
    count404("/v1/console/learning/loops");
    wrap(<LearningLoopsDashboard />);
    expect(await screen.findByTestId("learning-loops-unavailable")).toHaveTextContent(/not available on this build/);
    expect(screen.queryByText(/HTTP 404/)).not.toBeInTheDocument();
  });

  it("unified learning dashboard", async () => {
    count404("/v1/console/learning/dashboard");
    wrap(<UnifiedLearningDashboard />);
    expect(await screen.findByText(/learning dashboard is not available on this build/)).toBeInTheDocument();
    expect(screen.queryByText(/Failed to load dashboard/)).not.toBeInTheDocument();
  });
});
