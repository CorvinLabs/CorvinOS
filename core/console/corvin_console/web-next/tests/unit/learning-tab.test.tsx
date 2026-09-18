/**
 * Learning tab (ADR-0885) — the real component against MSW: feedback copy for
 * 200 / 409 / 404 / 503, the ONE sequential reset (confidence store, then
 * thresholds; second-half failure keeps the first's result and offers a retry
 * that skips the first call), Import posting the export document as the whole
 * body, and every write carrying X-CSRF-Token.
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
vi.mock("@/pages/models/components/engine-parts", () => ({ fmtInt: (n: number) => String(n) }));

import { LearningTab } from "@/pages/models/tabs/learning";

const status = {
  converged_count: 1, total_count: 3, thresholds: [], cost_savings_percent: 0,
  cost_baseline_usd: 0, cost_current_usd: 0, cost_data_available: false, cost_history: [],
  cost_model_mix: {}, cost_os_model_pin: null, acs_cost_actual_usd: 0, acs_cost_baseline_usd: 0,
  acs_model_mix: {}, acs_data_available: false, accuracy_percent: 0,
  last_updated: new Date().toISOString(), window: { active: false, epoch_ts: null, since_iso: null, reason: "" },
};
const RECENT = { tenant_id: "_default", windowed: false, items: [
  { record_hash: "0123456789abcdef", ts: 1_800_000_000, task_type: "MEDIUM", model: "claude-sonnet-5", confidence: 0.7 },
  { record_hash: "fedcba9876543210", ts: 1_799_999_000, task_type: "SIMPLE", model: "claude-haiku-4-5-20251001", confidence: 0.6 },
]};

interface Seen { path: string; csrf: string | null; body: unknown }
const seen: Seen[] = [];

function baseHandlers(feedback: (hash: string) => Response) {
  return [
    http.get("/v1/console/learning/model-cost-optimizer/status", () => HttpResponse.json(status)),
    http.get("/v1/console/v1/engine/config", () => HttpResponse.json({ total_samples: 12, models: {}, total_learned_samples: 0, learning_status: "idle", last_learning_update: null, tenant_id: "_default", last_updated: "" })),
    http.get("/v1/console/v1/engine/analytics", () => HttpResponse.json({ timestamp: "", tenant_id: "_default", total_samples: 3, models: [], top_model: null, top_confidence: null })),
    http.get("/v1/console/v1/engine/analytics/task-type/:tt", ({ params }) =>
      HttpResponse.json({ task_type: params.tt, timestamp: "", models: params.tt === "MEDIUM" ? [
        { model: "claude-sonnet-5", confidence: 0.8, n_samples: 7, mean_quality: 0.8, variance: 0.01, is_converged: false, posterior_mean: 0.75, input_usd_per_1k: 0.002, output_usd_per_1k: 0.01, priced: true },
        { model: "claude-haiku-4-5-20251001", confidence: 0.5, n_samples: 2, mean_quality: 0.5, variance: 0.1, is_converged: false, posterior_mean: 0.5, input_usd_per_1k: 0.001, output_usd_per_1k: 0.005, priced: true },
      ] : [] })),
    http.get("/v1/console/v1/engine/analytics/recent", () => HttpResponse.json(RECENT)),
    http.post("/v1/console/v1/engine/analytics/feedback", async ({ request }) => {
      const body = (await request.json()) as { record_hash: string };
      seen.push({ path: "feedback", csrf: request.headers.get("x-csrf-token"), body });
      return feedback(body.record_hash);
    }),
  ];
}

function renderTab() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(<QueryClientProvider client={qc}><LearningTab /></QueryClientProvider>);
}

afterEach(() => { cleanup(); seen.length = 0; });

describe("Learning tab — feedback", () => {
  it("records a Good rating with the CSRF header and shows the new confidence", async () => {
    server.use(...baseHandlers(() => HttpResponse.json({
      record_hash: "0123456789abcdef", task_type: "MEDIUM", model: "claude-sonnet-5",
      confidence: 0.82, n_samples: 8, is_converged: false,
    })));
    renderTab();
    const good = (await screen.findAllByRole("button", { name: /Good/ }))[0];
    fireEvent.click(good);
    await screen.findByText(/recorded — claude-sonnet-5 now 82% over 8 samples/);
    expect(seen[0].csrf).toBe("csrf-test");
    expect(seen[0].body).toEqual({ record_hash: "0123456789abcdef", rating: "good" });
  });

  it.each([
    [409, "already rated"],
    [404, "no longer available to rate"],
    [503, "audit chain unavailable — not recorded"],
  ])("maps %s to fixed copy", async (code, copy) => {
    server.use(...baseHandlers(() => HttpResponse.json({ detail: "server text must not be rendered" }, { status: code })));
    renderTab();
    fireEvent.click((await screen.findAllByRole("button", { name: /Poor/ }))[0]);
    await screen.findByText(copy);
    expect(screen.queryByText(/server text must not be rendered/)).toBeNull();
  });

  it("renders ranking rows as stored and withholds below 5 samples", async () => {
    server.use(...baseHandlers(() => HttpResponse.json({}, { status: 500 })));
    renderTab();
    await screen.findAllByText("claude-sonnet-5");
    expect(screen.getAllByText("claude-haiku-4-5-20251001").length).toBeGreaterThan(0);
    expect(screen.getAllByText(/recommendation withheld — fewer than 5 samples/).length).toBe(1);
    expect(screen.getByText("Outcome samples — lifetime")).toBeInTheDocument();
    expect(screen.getByText("Shadow classifications — in window")).toBeInTheDocument();
  });
});

describe("Learning tab — ONE reset", () => {
  it("resets confidence store then thresholds, both with CSRF", async () => {
    const calls: string[] = [];
    server.use(
      ...baseHandlers(() => HttpResponse.json({}, { status: 500 })),
      http.post("/v1/console/v1/engine/analytics/reset", ({ request }) => { calls.push("confidence:" + request.headers.get("x-csrf-token")); return HttpResponse.json({ status: "success" }); }),
      http.post("/v1/console/learning/model-cost-optimizer/reset", ({ request }) => { calls.push("thresholds:" + request.headers.get("x-csrf-token")); return HttpResponse.json({ status: "ok" }); }),
    );
    renderTab();
    fireEvent.click(await screen.findByRole("button", { name: "Reset learning" }));
    fireEvent.click(screen.getByRole("button", { name: /Confirm: clear the confidence store and the learned thresholds/ }));
    await screen.findByText("Both stores cleared.");
    expect(calls).toEqual(["confidence:csrf-test", "thresholds:csrf-test"]);
  });

  it("second-half failure keeps the first's result, offers a retry that skips the first call, and a Cancel", async () => {
    const calls: string[] = [];
    let thresholdOk = false;
    server.use(
      ...baseHandlers(() => HttpResponse.json({}, { status: 500 })),
      http.post("/v1/console/v1/engine/analytics/reset", () => { calls.push("confidence"); return HttpResponse.json({ status: "success" }); }),
      http.post("/v1/console/learning/model-cost-optimizer/reset", () => { calls.push("thresholds"); return thresholdOk ? HttpResponse.json({ status: "ok" }) : HttpResponse.json({ detail: "boom" }, { status: 500 }); }),
    );
    renderTab();
    fireEvent.click(await screen.findByRole("button", { name: "Reset learning" }));
    fireEvent.click(screen.getByRole("button", { name: /Confirm: clear/ }));
    await screen.findByText(/Confidence store cleared; threshold reset failed — the threshold store is unchanged/);
    expect(screen.getByRole("button", { name: "Cancel" })).toBeInTheDocument();
    thresholdOk = true;
    fireEvent.click(screen.getByRole("button", { name: "Retry threshold reset" }));
    await screen.findByText("Both stores cleared.");
    expect(calls).toEqual(["confidence", "thresholds", "thresholds"]);
  });

  it("first-half failure changes nothing and says so", async () => {
    const calls: string[] = [];
    server.use(
      ...baseHandlers(() => HttpResponse.json({}, { status: 500 })),
      http.post("/v1/console/v1/engine/analytics/reset", () => { calls.push("confidence"); return HttpResponse.json({ detail: "x" }, { status: 503 }); }),
      http.post("/v1/console/learning/model-cost-optimizer/reset", () => { calls.push("thresholds"); return HttpResponse.json({ status: "ok" }); }),
    );
    renderTab();
    fireEvent.click(await screen.findByRole("button", { name: "Reset learning" }));
    fireEvent.click(screen.getByRole("button", { name: /Confirm: clear/ }));
    await screen.findByText(/The confidence store could not be reset — nothing was changed/);
    expect(calls).toEqual(["confidence"]);
  });
});

describe("Learning tab — import", () => {
  it("posts the export document as the whole body and reports the count", async () => {
    let body: unknown = null;
    server.use(
      ...baseHandlers(() => HttpResponse.json({}, { status: 500 })),
      http.post("/v1/console/learning/model-cost-optimizer/import", async ({ request }) => {
        body = await request.json();
        expect(request.headers.get("x-csrf-token")).toBe("csrf-test");
        return HttpResponse.json({ status: "ok", imported_count: 2 });
      }),
    );
    const { container } = renderTab();
    await screen.findByRole("button", { name: "Reset learning" });
    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    const file = new File([JSON.stringify({ version: "1", thresholds: [] })], "t.json", { type: "application/json" });
    Object.defineProperty(input, "files", { value: [file] });
    fireEvent.change(input);
    await screen.findByText("Imported 2 thresholds.");
    expect(body).toEqual({ version: "1", thresholds: [] }); // NOT wrapped in {data}
  });
});
