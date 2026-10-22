/**
 * Ready tab (readiness-panel): the report rows, the synthetic-run notice, the acceptance
 * editor and the reason-gated "Mark ready anyway". The API layer is mocked; the helpers in
 * lib/workflow-readiness are exercised for real.
 */
import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import * as React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ReadinessPanel } from "@/components/workflows/ReadinessPanel";
import { firstBlocker, parseAcceptance, verdict } from "@/lib/workflow-readiness";
import type { ReadinessReport } from "@/lib/api/workflows";

const api = vi.hoisted(() => ({
  getReadiness: vi.fn(),
  putAcceptance: vi.fn(),
  postReadyOverride: vi.fn(),
}));

vi.mock("@/lib/api/workflows", () => api);

const REPORT: ReadinessReport = {
  ready: false,
  synthetic: true,
  run_state: "complete",
  elapsed_ms: 12,
  phase: "detailing",
  acceptance: null,
  notice: "n",
  nodes: [],
  signals: [
    { name: "validator", status: "pass", detail: "R1-R11 hold", value: null },
    { name: "reachability", status: "pass", detail: "3 node(s) ran, 0 skipped by a branch", value: 3 },
    { name: "acceptance", status: "unmeasured", detail: "no acceptance case stored", value: null },
    { name: "external_exposure", status: "info", detail: "2 engine node(s) are stubbed", value: 2 },
    { name: "repairs", status: "pass", detail: "0 designer repair(s), ceiling 3", value: 0 },
  ],
};

function mount(phase = "detailing", onPhaseChange = vi.fn()) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <ReadinessPanel wid="wf1" csrf="tok" phase={phase} onPhaseChange={onPhaseChange} />
    </QueryClientProvider>,
  );
  return { onPhaseChange };
}

beforeEach(() => {
  api.getReadiness.mockResolvedValue(REPORT);
  api.putAcceptance.mockResolvedValue({ ok: true });
  api.postReadyOverride.mockResolvedValue({ ok: true, phase: "ready" });
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("ReadinessPanel", () => {
  it("shows every signal, says the run is synthetic, and does not style 'not measured' as a pass", async () => {
    mount();
    expect(screen.getByTestId("readiness-notice").textContent).toContain("not real execution");
    await screen.findByTestId("readiness-signal-acceptance");
    expect(screen.getAllByTestId(/^readiness-signal-/)).toHaveLength(5);
    const acceptance = screen.getByTestId("readiness-signal-acceptance");
    expect(acceptance.textContent).toContain("Not measured");
    expect(acceptance.innerHTML).not.toContain("emerald");
    expect(screen.getByTestId("readiness-verdict").textContent).toContain("Not ready");
    expect(screen.getByTestId("readiness-verdict").textContent).toContain("acceptance case");
  });

  it("saves a parsed acceptance case and refuses invalid JSON without calling the server", async () => {
    mount();
    await screen.findByTestId("readiness-signals");
    const ta = screen.getByTestId("readiness-acceptance-text");
    fireEvent.change(ta, { target: { value: "{nope" } });
    fireEvent.click(screen.getByTestId("readiness-save-case"));
    expect((await screen.findByTestId("readiness-case-error")).textContent).toContain("valid JSON");
    expect(api.putAcceptance).not.toHaveBeenCalled();
    fireEvent.change(ta, { target: { value: JSON.stringify({ expect: { state: "complete" } }) } });
    fireEvent.click(screen.getByTestId("readiness-save-case"));
    await waitFor(() => expect(api.putAcceptance).toHaveBeenCalledWith("wf1", { expect: { state: "complete" } }, "tok"));
  });

  it("'Mark ready anyway' needs a reason before it can be confirmed", async () => {
    const { onPhaseChange } = mount();
    await screen.findByTestId("readiness-signals");
    fireEvent.click(screen.getByTestId("readiness-override-open"));
    const confirm = screen.getByTestId("readiness-override-confirm") as HTMLButtonElement;
    expect(confirm.disabled).toBe(true);
    fireEvent.change(screen.getByTestId("readiness-override-reason"), { target: { value: "  " } });
    expect(confirm.disabled).toBe(true);
    fireEvent.change(screen.getByTestId("readiness-override-reason"), { target: { value: "manual review done" } });
    expect(confirm.disabled).toBe(false);
    fireEvent.click(confirm);
    await waitFor(() => expect(api.postReadyOverride).toHaveBeenCalledWith("wf1", "manual review done", "tok"));
    await waitFor(() => expect(onPhaseChange).toHaveBeenCalledWith("ready"));
  });

  it("shows the server's refusal of an override and keeps the phase", async () => {
    api.postReadyOverride.mockRejectedValue(new Error("the draft does not validate and cannot be marked ready"));
    const { onPhaseChange } = mount();
    await screen.findByTestId("readiness-signals");
    fireEvent.click(screen.getByTestId("readiness-override-open"));
    fireEvent.change(screen.getByTestId("readiness-override-reason"), { target: { value: "x" } });
    fireEvent.click(screen.getByTestId("readiness-override-confirm"));
    expect((await screen.findByTestId("readiness-override-error")).textContent).toContain("does not validate");
    expect(onPhaseChange).not.toHaveBeenCalled();
  });

  it("a draft that is already ready offers no override", async () => {
    mount("ready");
    await screen.findByTestId("readiness-signals");
    expect(screen.queryByTestId("readiness-override-open")).toBeNull();
  });

  it("a failed check says it could not run instead of showing stale rows", async () => {
    api.getReadiness.mockRejectedValue(new Error("HTTP 429"));
    mount();
    expect((await screen.findByTestId("readiness-error")).textContent).toContain("could not run");
    expect(screen.queryByTestId("readiness-signals")).toBeNull();
  });
});

describe("workflow-readiness helpers", () => {
  it("picks the first failing or unmeasured signal and words the verdict from it", () => {
    expect(firstBlocker(REPORT.signals)?.name).toBe("acceptance");
    expect(verdict({ ...REPORT, ready: true })).toBe("Ready: every line passes.");
  });
  it("requires an expect object", () => {
    expect(parseAcceptance("{}").ok).toBe(false);
    expect(parseAcceptance('{"expect":{}}').ok).toBe(false);
    expect(parseAcceptance('{"expect":{"state":"complete"}}').ok).toBe(true);
  });
});
