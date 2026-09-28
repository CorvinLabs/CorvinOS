/**
 * Reproduces the reported bug end-to-end through the real component tree
 * (not a direct call into persistent-state.ts): "skill generieren, tab
 * wechseln, zurück -> Fortschritt weg" (analysis 2026-09-28).
 *
 * Before the fix, AutonomousForgePanel held `forkRun` in plain
 * React.useState — unmounting the panel (exactly what react-router does on
 * every tab/panel switch, see registry.tsx / App.tsx) threw it away, and
 * with it the ONLY client-side record of which run_id was in flight
 * (getForgeStatus() reports forks_in_flight by skill_id, not run_id), so
 * the "Follow an operator fork run" polling effect had nothing to resume.
 *
 * This test drives the real flow — click "Forge candidate", assert the
 * progress banner, unmount (= simulated tab switch), remount (= switch
 * back) — and asserts the banner reappears without any further click.
 */
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { server } from "../fixtures/server";
import { AutonomousForgePanel } from "@/components/forge/AutonomousForgePanel";
import { clearPersistentState } from "@/lib/persistent-state";

const STATUS_URL = "/v1/console/autonomous-forge/status";
const HISTORY_URL = "/v1/console/autonomous-forge/history";
const FORK_URL = "/v1/console/autonomous-forge/fork";

function forgeStatusFixture() {
  return {
    autopilot: {
      enabled: false,
      last_tick: null,
      last_actions: [],
      interval_s: 300,
      loss_rule: { threshold: 0.5, min_outcomes: 5, window_days: 7 },
    },
    canaries: [],
    skills: [
      {
        skill_id: "demo-skill",
        description: "A demo skill",
        outcome_n: 5,
        outcome_mean: 0.8,
        usage_n: 10,
        loss_signal: false,
        canary_status: null,
      },
    ],
    forks_in_flight: [],
    fork_runs: [],
  };
}

function renderPanel(qc: QueryClient) {
  return render(
    <QueryClientProvider client={qc}>
      <AutonomousForgePanel />
    </QueryClientProvider>,
  );
}

describe("AutonomousForgePanel — state persistence across tab switch", () => {
  beforeEach(() => {
    sessionStorage.clear();
    clearPersistentState("skill-forge.autonomous.selected");
    clearPersistentState("skill-forge.autonomous.message");
    clearPersistentState("skill-forge.autonomous.forkRun");
    server.use(
      http.get(STATUS_URL, () => HttpResponse.json(forgeStatusFixture())),
      http.get(HISTORY_URL, () => HttpResponse.json({ attempts: [], total_count: 0 })),
      http.post(FORK_URL, () => HttpResponse.json({ run_id: "run-123" })),
    );
  });

  afterEach(() => {
    cleanup();
    sessionStorage.clear();
    clearPersistentState("skill-forge.autonomous.selected");
    clearPersistentState("skill-forge.autonomous.message");
    clearPersistentState("skill-forge.autonomous.forkRun");
  });

  it("keeps the forge-in-progress banner visible after a simulated tab switch", async () => {
    const user = userEvent.setup();
    // QueryClientProvider lives at the app root in the real console (App.tsx)
    // and is NOT remounted on a panel/tab switch — only the panel itself is.
    // Reusing one QueryClient across the unmount/remount below mirrors that.
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });

    const first = renderPanel(qc);

    await screen.findByText("demo-skill");
    await user.click(screen.getByRole("button", { name: /forge candidate/i }));

    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: /forge candidate/i }));

    await waitFor(() => {
      expect(screen.getByText(/forging a candidate for/i)).toBeInTheDocument();
    });
    expect(screen.getAllByText("demo-skill").length).toBeGreaterThan(0);

    // Simulate the tab switch: react-router unmounts the panel's component
    // tree. The registry (module-level Map / sessionStorage) is untouched.
    first.unmount();
    expect(screen.queryByText(/forging a candidate for/i)).not.toBeInTheDocument();

    // Simulate switching back: a fresh component instance mounts.
    renderPanel(qc);

    await waitFor(() => {
      expect(screen.getByText(/forging a candidate for/i)).toBeInTheDocument();
    });
    expect(screen.getAllByText("demo-skill").length).toBeGreaterThan(0);
  });
});
