/**
 * ADR-2324 - the per-chat cost / efficiency dashboard.
 *
 * Renders the REAL <ChatEfficiencyPanel> against a mocked HTTP boundary (msw) and
 * asserts what the operator sees: collapsed by default with ZERO requests, the
 * request on expand, real figures, and "not available" (em dash) - never 0 - for
 * every figure the server could not compute.
 */
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { server } from "../fixtures/server";
import { ChatEfficiencyPanel } from "@/components/chat/ChatEfficiencyPanel";
import { buildNotes, buildTiles, costBars, fmtPct, fmtUsd, NA } from "@/lib/chat-efficiency";
import type { ChatEfficiency } from "@/lib/api/chat";

function payload(over: Partial<ChatEfficiency> = {}): ChatEfficiency {
  return {
    sid: "s1", schema: 1,
    window: { scope: "chat_lifetime", first_ts: 1, last_ts: 2, history_truncated: false },
    coverage: { turns: 3, measured: 3, priced: 3, unpriced: 0, without_usage: 0, incomplete_usage: 0,
      diff_withheld: 0, diff_truncated: 0, line_turns: 3, code_turns: 1 },
    tokens: { input: 300, output: 6000, cache_read: 9000, cache_write: 1000 },
    cost: { total_usd: 0.1234, per_turn_usd: 0.0411, per_1k_output_tokens_usd: 0.0206, cache_saving_usd: 0.05 },
    cache: { hit_rate: 0.8738 },
    code: { lines_added: 30, lines_removed: 10, lines_changed: 40, lines_lower_bound: false,
      output_tokens_per_changed_line: 150, changed_lines_per_1k_output_tokens: 6.7, cost_per_changed_line_usd: 0.0031 },
    models: [{ model: "claude-sonnet-5", turns: 3, priced_turns: 3, cost_usd: 0.1234, output_tokens: 6000 }],
    unpriced_models: [], includes_delegated_workers: false,
    price_basis: "First-party list price.",
    rows: [
      { ts: 1, model: "claude-sonnet-5", input_tokens: 100, output_tokens: 2000, cache_read_input_tokens: 3000,
        cache_creation_input_tokens: 300, cost_usd: 0.02, lines_added: 0, lines_removed: 0, lines_lower_bound: false },
      { ts: 2, model: "claude-sonnet-5", input_tokens: 100, output_tokens: 4000, cache_read_input_tokens: 6000,
        cache_creation_input_tokens: 700, cost_usd: 0.1, lines_added: 30, lines_removed: 10, lines_lower_bound: false },
    ],
    ...over,
  };
}

function renderPanel(streaming = false) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><ChatEfficiencyPanel sid="s1" streaming={streaming} /></QueryClientProvider>);
}
afterEach(() => cleanup());

describe("ChatEfficiencyPanel", () => {
  it("is collapsed by default and requests nothing until opened", async () => {
    let hits = 0;
    server.use(http.get("/v1/console/chat/sessions/s1/efficiency", () => { hits++; return HttpResponse.json(payload()); }));
    renderPanel();
    expect(screen.getByTestId("chat-efficiency-toggle")).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByTestId("chat-efficiency-body")).toBeNull();
    await new Promise((r) => setTimeout(r, 50));
    expect(hits).toBe(0);
  });

  it("opens, fetches once and shows the real figures", async () => {
    let hits = 0;
    server.use(http.get("/v1/console/chat/sessions/s1/efficiency", () => { hits++; return HttpResponse.json(payload()); }));
    renderPanel();
    fireEvent.click(screen.getByTestId("chat-efficiency-toggle"));
    await screen.findByTestId("chat-efficiency-tiles");
    expect(hits).toBe(1);
    expect(screen.getByTestId("eff-total-cost-value")).toHaveTextContent("$0.12");
    expect(screen.getByTestId("eff-cache-hit-value")).toHaveTextContent("87.4%");
    expect(screen.getByTestId("eff-lines-value")).toHaveTextContent("40");
    expect(screen.getByTestId("eff-tok-per-line-value")).toHaveTextContent("150");
    expect(screen.getByTestId("chat-efficiency-chart")).toBeInTheDocument();
    expect(screen.getByTestId("chat-efficiency-summary")).toHaveTextContent("$0.12");
  });

  it("shows an em dash, never 0, for figures the server could not compute", async () => {
    server.use(http.get("/v1/console/chat/sessions/s1/efficiency", () => HttpResponse.json(payload({
      coverage: { turns: 2, measured: 0, priced: 0, unpriced: 0, without_usage: 2, incomplete_usage: 0,
        diff_withheld: 0, diff_truncated: 0, line_turns: 0, code_turns: 0 },
      cost: { total_usd: null, per_turn_usd: null, per_1k_output_tokens_usd: null, cache_saving_usd: null },
      cache: { hit_rate: null },
      code: { lines_added: 0, lines_removed: 0, lines_changed: 0, lines_lower_bound: false,
        output_tokens_per_changed_line: null, changed_lines_per_1k_output_tokens: null, cost_per_changed_line_usd: null },
      rows: [],
    }))));
    renderPanel();
    fireEvent.click(screen.getByTestId("chat-efficiency-toggle"));
    await screen.findByTestId("chat-efficiency-tiles");
    for (const id of ["total-cost", "cost-per-turn", "cost-per-1k-out", "cache-hit", "tok-per-line", "lines-per-1k", "cost-per-line"]) {
      expect(screen.getByTestId(`eff-${id}-value`)).toHaveTextContent(NA);
      expect(screen.getByTestId(`eff-${id}-value`).textContent).not.toMatch(/\$0\.00|^0/);
    }
    expect(screen.getByTestId("chat-efficiency-notes")).toHaveTextContent("reported no complete token usage");
    expect(screen.queryByTestId("chat-efficiency-chart")).toBeNull();
  });

  it("says so for a chat without replies", async () => {
    server.use(http.get("/v1/console/chat/sessions/s1/efficiency", () => HttpResponse.json(payload({
      coverage: { turns: 0, measured: 0, priced: 0, unpriced: 0, without_usage: 0, incomplete_usage: 0,
        diff_withheld: 0, diff_truncated: 0, line_turns: 0, code_turns: 0 }, rows: [] }))));
    renderPanel();
    fireEvent.click(screen.getByTestId("chat-efficiency-toggle"));
    await screen.findByTestId("chat-efficiency-empty");
    expect(screen.queryByTestId("chat-efficiency-tiles")).toBeNull();
  });

  it("shows an error with a working retry", async () => {
    let fail = true;
    server.use(http.get("/v1/console/chat/sessions/s1/efficiency", () =>
      fail ? new HttpResponse(null, { status: 500 }) : HttpResponse.json(payload())));
    renderPanel();
    fireEvent.click(screen.getByTestId("chat-efficiency-toggle"));
    await screen.findByTestId("chat-efficiency-error");
    fail = false;
    fireEvent.click(screen.getByText("Retry"));
    await screen.findByTestId("chat-efficiency-tiles");
  });

  it("reloads when a streaming reply ends", async () => {
    let hits = 0;
    server.use(http.get("/v1/console/chat/sessions/s1/efficiency", () => { hits++; return HttpResponse.json(payload()); }));
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const ui = (streaming: boolean) => (
      <QueryClientProvider client={qc}><ChatEfficiencyPanel sid="s1" streaming={streaming} /></QueryClientProvider>);
    const { rerender } = render(ui(true));
    fireEvent.click(screen.getByTestId("chat-efficiency-toggle"));
    await waitFor(() => expect(hits).toBe(1));
    rerender(ui(false));
    await waitFor(() => expect(hits).toBe(2));
  });
});

describe("chat-efficiency formatting", () => {
  it("formats null as not-available and tiny costs with enough digits", () => {
    expect(fmtUsd(null)).toBe(NA);
    expect(fmtUsd(0.0042)).toBe("$0.0042");
    expect(fmtUsd(1234.5)).toBe("$1,234.50");
    expect(fmtPct(null)).toBe(NA);
    expect(fmtPct(0.5)).toBe("50.0%");
  });

  it("marks a lower-bound line count and lists every caveat", () => {
    const d = payload({
      code: { ...payload().code, lines_lower_bound: true },
      coverage: { ...payload().coverage, diff_withheld: 2, without_usage: 1 },
      unpriced_models: ["mystery-9"], window: { ...payload().window, history_truncated: true },
    });
    expect(buildTiles(d).find((t) => t.id === "lines")!.value).toBe("≥ 40");
    const notes = buildNotes(d).join("\n");
    for (const s of ["no complete token usage", "mystery-9", "withheld", "lower bound", "oldest turns", "Delegated worker"])
      expect(notes).toContain(s);
  });

  it("scales bars to the max with a 2px floor for a real value", () => {
    const d = payload({ rows: [
      { ...payload().rows[0], cost_usd: 0.0001 }, { ...payload().rows[0], cost_usd: 1 },
      { ...payload().rows[0], cost_usd: null },
    ] });
    const bars = costBars(d, 40, 48);
    expect(bars.map((b) => b.height)).toEqual([2, 48]);
  });
});
