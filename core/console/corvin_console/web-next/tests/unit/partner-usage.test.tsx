/**
 * Per-partner usage of delegated tasks: real token sums from the peer's signed answers, priced with this
 * installation's rates, the counting window shown with the numbers, the peer's own figure labelled as declared.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { PartnerUsage } from "@/components/agent-conversations/PartnerUsage";
import { getPartnerUsage, type PartnerUsage as Row } from "@/lib/api/federation";

vi.mock("@/lib/api/federation", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api/federation")>("@/lib/api/federation");
  return { ...actual, getPartnerUsage: vi.fn() };
});

const row = (p: Partial<Row> = {}): Row => ({
  endpoint_id: "peer-B", peer_instance_id: "iid-B-1234", tasks: 3, tasks_with_usage: 3,
  input_tokens: 4300, output_tokens: 1040, cache_read_tokens: 20000, cache_write_tokens: 2000,
  models: [
    { model_id: "claude-haiku-4-5", tasks: 2, input_tokens: 4000, output_tokens: 1000, cache_read_tokens: 20000,
      cache_write_tokens: 2000, priced: true, cost_usd: 0.0123 },
    { model_id: "acme-local-7b", tasks: 1, input_tokens: 300, output_tokens: 40, cache_read_tokens: 0,
      cache_write_tokens: 0, priced: false, cost_usd: null },
  ],
  priced_tasks: 2, unpriced_tasks: 1, cost_usd: 0.0123,
  declared_by_peer: [{ agent_id: "haiku-cheap", cost_per_task_usd: 0.01 }],
  ...p,
});

function renderIt(peerId = "peer-B") {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><PartnerUsage peerId={peerId} /></QueryClientProvider>);
}

describe("PartnerUsage", () => {
  beforeEach(() => vi.resetAllMocks());

  it("shows the token sums, the cost at our rates and the unpriced model as tokens only", async () => {
    vi.mocked(getPartnerUsage).mockResolvedValue({
      window: { active: false, epoch_ts: null, since_iso: null, reason: "" }, partners: [row()] });
    renderIt();
    expect((await screen.findByTestId("partner-usage-tasks")).textContent).toBe("3 (3 with token data)");
    expect(screen.getByText("20,000")).toBeTruthy();
    const cost = screen.getByTestId("partner-usage-cost").textContent ?? "";
    expect(cost).toContain("$0.0123");
    expect(cost).toContain("tokens only");
    expect(screen.getByTestId("partner-usage-models").textContent).toContain("acme-local-7b: 1 task(s), 340 tokens (no rate here)");
    expect(screen.getByTestId("partner-usage-window").textContent).toBe("All time");
    const text = screen.getByTestId("partner-usage").textContent ?? "";
    expect(text).not.toMatch(/%|average|ADR-/i);
  });

  it("labels the peer's catalogue figure as declared and keeps it out of the cost", async () => {
    vi.mocked(getPartnerUsage).mockResolvedValue({
      window: { active: true, epoch_ts: 1, since_iso: "2026-10-01T00:00:00+00:00", reason: "" }, partners: [row()] });
    renderIt();
    const declared = await screen.findByTestId("partner-usage-declared");
    expect(declared.textContent).toContain("Declared by peer (not verified, not included above)");
    expect(declared.textContent).toContain("haiku-cheap $0.01 per task");
    expect(screen.getByTestId("partner-usage-window").textContent).toMatch(/^Since /);
  });

  it("a peer without delegated tasks in the window says so", async () => {
    vi.mocked(getPartnerUsage).mockResolvedValue({
      window: { active: false, epoch_ts: null, since_iso: null, reason: "" }, partners: [row()] });
    renderIt("someone-else");
    expect(await screen.findByTestId("partner-usage-empty")).toBeTruthy();
  });
});
