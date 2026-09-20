/**
 * ADR-0764 — the Routing tab's task card and the Learning tab's per-tier
 * ranking render the SAME learned (tier, model) confidence, so they must apply
 * the SAME evidence bar.
 *
 * They did not. Until 2026-09-20 `MIN_SAMPLES_TO_RECOMMEND` was a private const
 * in learning.tsx: that tab printed "recommendation withheld — fewer than 5
 * samples" for COMPLEX at n=3 while this card, one tab away, presented the
 * identical score as a green "✓ Learned confidence: 68%" over 2 samples.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { server } from "../fixtures/server";
import { http, HttpResponse } from "msw";

vi.mock("@/lib/auth", () => ({
  useAuth: () => ({
    session: { tenant_id: "_default", csrf_token: "csrf-test", tier: "owner" },
    loading: false, refresh: vi.fn(), logout: vi.fn(),
  }),
}));

import { TaskTypeCard } from "@/pages/models/components/engine-parts";
import { MIN_SAMPLES_TO_RECOMMEND } from "@/pages/models/hooks/use-cost-derived";

const cfg = (run_count: number) => ({
  task_type: "COMPLEX" as const,
  selected_model: "anthropic/claude-opus-5",
  provider: null,
  alternatives: [],
  confidence_score: 0.68,
  run_count,
  is_converged: false,
  classified_count: run_count,
});

function renderCard(run_count: number) {
  server.use(
    http.get("/v1/console/v1/engine/claude-models", () =>
      HttpResponse.json({ tenant_id: "_default", models: [], count: 0, sources: [], default_model_id: null })),
    http.get("/v1/console/settings/engine/providers", () => HttpResponse.json({})),
    http.get("/v1/console/v1/engine/model-usage", () =>
      HttpResponse.json({ tenant_id: "_default", chain_path_resolved: true, chain_readable: true,
        models: [], providers: [], roles: [], totals: {}, window: { active: false } })),
    http.get("/v1/console/settings/engine/detect", () =>
      HttpResponse.json({ results: [], recommended_engine: null, needs_bootstrap: false })),
  );
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <TaskTypeCard config={cfg(run_count)} totalClassified={40} saving={false} onSave={vi.fn()} />
    </QueryClientProvider>,
  );
}

afterEach(cleanup);

describe("TaskTypeCard — evidence bar matches the Learning tab", () => {
  it("withholds the recommendation below the shared threshold", async () => {
    renderCard(MIN_SAMPLES_TO_RECOMMEND - 1);

    expect(
      await screen.findByText(new RegExp(`Recommendation withheld below ${MIN_SAMPLES_TO_RECOMMEND} samples`)),
    ).toBeTruthy();
    // The score is still shown — withholding it entirely would hide that
    // learning has started — but never as a settled "✓ Learned confidence".
    expect(screen.getByText(/Learning has started: 68%/)).toBeTruthy();
    expect(screen.queryByText(/✓ Learned confidence/)).toBeNull();
    // ...and the header badge, which reads as a verdict, stays off.
    expect(screen.queryByText(/68% confident/)).toBeNull();
  });

  it("presents it as a finding at or above the threshold", async () => {
    renderCard(MIN_SAMPLES_TO_RECOMMEND);

    expect(await screen.findByText(/✓ Learned confidence: 68%/)).toBeTruthy();
    expect(screen.getByText(/68% confident/)).toBeTruthy();
    expect(screen.queryByText(/Recommendation withheld/)).toBeNull();
  });

  it("still separates 'no samples at all' from 'too few samples'", async () => {
    renderCard(0);

    expect(await screen.findByText(/No learned confidence yet/)).toBeTruthy();
    expect(screen.queryByText(/Learning has started/)).toBeNull();
  });
});
