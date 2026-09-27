/**
 * skill-learning-dashboard.tsx against the REAL backend contract of
 * routes/skill_learning_routes.py: 404 for the metrics and
 * `{available: false}` + empty lists for feedback/proposals. The page used to
 * read `metrics.accuracy` off the 404 body and crash the render.
 */
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { server } from "../fixtures/server";
import SkillLearningDashboard, { loadSkillLearning } from "@/pages/skill-learning-dashboard";

const backendToday = [
  http.get("/v1/console/skills/:id/learning", () =>
    HttpResponse.json({ detail: "skill learning metrics are not available on this build" }, { status: 404 }),
  ),
  http.get("/v1/console/skills/:id/feedback/history", ({ params }) =>
    HttpResponse.json({ skill_id: params.id, total_feedback_items: 0, recent: [], available: false }),
  ),
  http.get("/v1/console/skills/:id/optimization/proposals", ({ params }) =>
    HttpResponse.json({ skill_id: params.id, proposals: [], available: false }),
  ),
];

describe("skill learning dashboard", () => {
  it("classifies the backend's 404 as unavailable", async () => {
    server.use(...backendToday);
    const state = await loadSkillLearning("os.delegation_router");
    expect(state.kind).toBe("unavailable");
  });

  it("renders an honest English empty state instead of crashing on 404", async () => {
    server.use(...backendToday);
    render(<SkillLearningDashboard skillId="os.delegation_router" />);
    expect(await screen.findByText(/not available on this build/i)).toBeInTheDocument();
    expect(screen.queryByText(/Accuracy/)).not.toBeInTheDocument();
  });

  it("treats {available:false} lists as unavailable, not as an empty measurement", async () => {
    server.use(
      http.get("/v1/console/skills/:id/learning", ({ params }) =>
        HttpResponse.json({
          skill_id: params.id, version: "1", total_executions: 4, correct_outcomes: 3,
          accuracy: 0.75, avg_latency_ms: 12, error_rate: 0.25, avg_cost_usd: 0,
          confidence_score: 0.5, last_updated: "2026-09-27T00:00:00Z",
        }),
      ),
      ...backendToday.slice(1),
    );
    const state = await loadSkillLearning("s1");
    expect(state).toMatchObject({ kind: "ok", feedback: null, proposals: null });
    render(<SkillLearningDashboard skillId="s1" />);
    expect(await screen.findByText("75.0%")).toBeInTheDocument();
    expect(screen.getByText(/Feedback history is not available on this build/)).toBeInTheDocument();
    expect(screen.getByText(/Optimization proposals are not available on this build/)).toBeInTheDocument();
  });
});
