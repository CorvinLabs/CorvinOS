/**
 * Quality Gates page (ADR-0688 amendment, 2026-09-20) against MSW: the marker
 * renders; before any run every gate shows "not run" and "—" (never 0 %) and
 * the trend says there is nothing to draw; "Run all gates" POSTs with CSRF,
 * the progress bar is the job's own artifacts_done/total, and the finished
 * run's counts land in the message; failures show the validator's reason.
 * The chart itself is not asserted — happy-dom has no layout — only its caption.
 */
import type React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { server } from "../fixtures/server";

// happy-dom has no layout and the setup's ResizeObserver stub is not a
// constructor; recharts' ResponsiveContainer needs both. The chart is a
// black box here — only its caption is asserted.
vi.mock("recharts", async (importOriginal) => {
  const mod = await importOriginal<typeof import("recharts")>();
  return { ...mod, ResponsiveContainer: ({ children }: { children: React.ReactNode }) => <div data-testid="chart">{children}</div> };
});
vi.mock("@/lib/auth", () => ({
  useAuth: () => ({ session: { tenant_id: "_default", csrf_token: "csrf-test", tier: "owner" }, loading: false, refresh: vi.fn(), logout: vi.fn() }),
}));

import QualityGatesPanel, { MARKER_QUALITY } from "@/pages/quality";

const B = "/v1/console/api/quality/gates";
const win = (p: number, w: number, f: number) => ({ pass: p, warn: w, fail: f, total: p + w + f, pass_percentage: p + w + f ? Math.round((p / (p + w + f)) * 1000) / 10 : null });
const gate = (a: ReturnType<typeof win>, b = a) => ({ last_24h: a, last_7d: b, last_verdict: a.total ? (a.fail ? "fail" : "pass") : null, last_timestamp: null });

const EMPTY = {
  tenant_id: "_default", timestamp: "2026-09-20T10:00:00.000000Z", gates_total: 4, events_24h: 0, events_total: 0, source_root: "/srv/Corvin-ADR", last_run: null,
  summary: { ADRGate: gate(win(0, 0, 0)), ConceptGate: gate(win(0, 0, 0)), ImplementationPlanGate: gate(win(0, 0, 0)), IdeaGate: gate(win(0, 0, 0)) },
};
const AFTER = {
  ...EMPTY, events_24h: 1091, events_total: 1091,
  last_run: { run_id: "r1", status: "completed", started_at: "2026-09-20T10:00:00.000000Z", completed_at: "2026-09-20T10:00:03.000000Z", artifacts_total: 1091, artifacts_done: 1091, error: null },
  summary: { ADRGate: gate(win(267, 0, 738)), ConceptGate: gate(win(1, 0, 66)), ImplementationPlanGate: gate(win(11, 0, 7)), IdeaGate: gate(win(0, 0, 1)) },
};

interface Seen { csrf: string | null; body: unknown }
const seen: Seen[] = [];

function renderIt() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(<QueryClientProvider client={qc}><QualityGatesPanel /></QueryClientProvider>);
}
afterEach(() => { cleanup(); seen.length = 0; });

describe("Quality Gates page", () => {
  it("before any run: not-run badges, — instead of 0 %, nothing to draw", async () => {
    server.use(
      http.get(`${B}/status`, () => HttpResponse.json(EMPTY)),
      http.get(`${B}/trend`, () => HttpResponse.json({ points: [] })),
      http.get(`${B}/failures`, () => HttpResponse.json({ failures: [], total: 0 })),
    );
    renderIt();
    await screen.findByTestId("quality-gates");
    expect(screen.getByText(MARKER_QUALITY)).toBeInTheDocument();
    expect(screen.getAllByText("not run")).toHaveLength(4);
    expect(screen.getByTestId("gate-row-ADRGate").textContent).toContain("—");
    expect(screen.getByTestId("gate-row-ADRGate").textContent).not.toMatch(/0\.0 %/);
    expect(screen.getByTestId("trend-caption").textContent).toMatch(/nothing to draw/);
    expect(screen.getByText("No gate run in the last 24 hours.")).toBeInTheDocument();
    expect(screen.getByText("/srv/Corvin-ADR")).toBeInTheDocument();
  });

  it("run all gates: CSRF POST, progress from the job, counts in the message, failures with reason", async () => {
    let polls = 0;
    let ran = false;
    server.use(
      http.get(`${B}/status`, () => HttpResponse.json(ran ? AFTER : EMPTY)),
      http.get(`${B}/trend`, () => HttpResponse.json(ran ? { points: [{ date: "2026-09-20", pass: 279, warn: 0, fail: 812, total: 1091, pass_percentage: 25.6 }] } : { points: [] })),
      http.get(`${B}/failures`, () => HttpResponse.json(ran
        ? { total: 812, failures: [{ gate_name: "ADRGate", artifact_id: "ADR-0002", verdict: "fail", confidence: 1, reason: "ADR frontmatter incomplete", timestamp: "2026-09-20T10:00:01.000000Z", findings_count: 3, artifact_type: "ADR" }] }
        : { failures: [], total: 0 })),
      http.post(`${B}/run/all`, async ({ request }) => {
        seen.push({ csrf: request.headers.get("x-csrf-token"), body: await request.json() });
        return HttpResponse.json({ run_id: "r1", status: "running", artifacts_total: 1091 });
      }),
      http.get(`${B}/results/r1`, () => {
        polls += 1;
        if (polls < 2) {
          return HttpResponse.json({ run_id: "r1", status: "running", progress: 40, artifacts_total: 1091, artifacts_done: 436, gates_completed: 1, gates_total: 4, source_root: "/srv/Corvin-ADR", error: null, gates_results: [] });
        }
        ran = true;
        return HttpResponse.json({
          run_id: "r1", status: "completed", progress: 100, artifacts_total: 1091, artifacts_done: 1091, gates_completed: 4, gates_total: 4, source_root: "/srv/Corvin-ADR", error: null,
          gates_results: [
            { gate_name: "ADRGate", artifacts: 1005, pass: 267, warn: 0, fail: 738 }, { gate_name: "ConceptGate", artifacts: 67, pass: 1, warn: 0, fail: 66 },
            { gate_name: "ImplementationPlanGate", artifacts: 18, pass: 11, warn: 0, fail: 7 }, { gate_name: "IdeaGate", artifacts: 1, pass: 0, warn: 0, fail: 1 },
          ],
        });
      }),
    );
    renderIt();
    await screen.findByTestId("quality-gates");
    fireEvent.click(screen.getByTestId("run-all"));
    await screen.findByTestId("run-progress");
    expect(screen.getByTestId("run-progress").textContent).toMatch(/Judging 436 of 1091 artifacts/);
    await screen.findByText(/Run r1 judged 1091 artifacts — 812 failed/);
    expect(seen[0]).toMatchObject({ csrf: "csrf-test" });
    await waitFor(() => expect(screen.getByTestId("gate-row-ADRGate").textContent).toContain("26.6 %"));
    expect(screen.getByTestId("trend-caption").textContent).toMatch(/One day with a run \(2026-09-20, UTC\) — shown as a bar, not a trend/);
    await screen.findByTestId("failure-row");
    expect(screen.getByTestId("failure-row").textContent).toMatch(/ADRGate.*ADR: ADR-0002.*ADR frontmatter incomplete · 3 findings/);
    expect(screen.getByTestId("failures-caption").textContent).toMatch(/812 verdicts below pass — newest 1 shown/);
  });

  it("a 404 build says so instead of showing zeros", async () => {
    server.use(http.get(`${B}/status`, () => HttpResponse.json({ detail: "nope" }, { status: 404 })));
    renderIt();
    await screen.findByText("Quality gates are not available on this build.");
  });
});
