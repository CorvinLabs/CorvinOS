/**
 * ADR-0764 — the Routing tab's task card and the Learning tab's per-tier
 * ranking render the SAME learned (tier, model) confidence, so they must apply
 * the SAME evidence bar.
 *
 * They did not. Until 2026-09-20 `MIN_SAMPLES_TO_RECOMMEND` was a private const
 * in learning.tsx: that tab printed "recommendation withheld — fewer than 5
 * samples" for COMPLEX at n=3 while this card, one tab away, presented the
 * identical score as a green "✓ Learned confidence: 68%" over 2 samples.
 *
 * Review c120 (s5_completeness-4, 2026-10-27): the score's only inputs are
 * proxies (0.85 / 0.2 per turn that ended without / with an error), so the card
 * labels it "no-error score (proxy)" and never calls it confidence.
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
    expect(screen.queryByText(/✓ No-error score/)).toBeNull();
    // ...and the header badge, which reads as a verdict, stays off.
    expect(screen.queryByText(/^68% no-error score \(proxy\)$/)).toBeNull();
  });

  it("presents it as a finding at or above the threshold", async () => {
    renderCard(MIN_SAMPLES_TO_RECOMMEND);

    expect(await screen.findByText(/✓ No-error score \(proxy\): 68%/)).toBeTruthy();
    expect(screen.getByText(/^68% no-error score \(proxy\)$/)).toBeTruthy();
    expect(screen.queryByText(/Recommendation withheld/)).toBeNull();
  });

  it("labels the score as the proxy it is, never as confidence", async () => {
    // Its only inputs are 0.85 / 0.2 per turn that ended without / with an error.
    for (const n of [MIN_SAMPLES_TO_RECOMMEND - 1, MIN_SAMPLES_TO_RECOMMEND]) {
      const { container, unmount } = renderCard(n);
      expect((await screen.findAllByText(/no-error score \(proxy\)/i)).length).toBeGreaterThan(0);
      expect(container.textContent ?? "").toMatch(/no-error score \(proxy\)/i);
      expect(container.textContent ?? "").not.toMatch(/confident|learned confidence/i);
      unmount();
    }
  });

  it("still separates 'no samples at all' from 'too few samples'", async () => {
    renderCard(0);

    expect(await screen.findByText(/No no-error score \(proxy\) yet/)).toBeTruthy();
    expect(screen.queryByText(/Learning has started/)).toBeNull();
  });
});
