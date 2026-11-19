/**
 * ADR-2029 control-plane pages against the routes the backend actually
 * serves. Replaces four test files (ff1a0e6e2) that imported pages which do
 * not exist (@/pages/control-overrides, @/components/ui/toast, ...), used
 * jest globals under vitest and asserted a UI that was never built — they
 * collected zero tests.
 */
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { http, HttpResponse } from "msw";
import { server } from "../fixtures/server";
import ControlIntentRouterPage from "@/pages/control-intent-router";
import ControlPlaneOverridesPage from "@/pages/control-plane-overrides";
import ControlPlanePluginsPage from "@/pages/control-plane-plugins";
import ControlPlaneSubsystemsPage from "@/pages/control-plane-subsystems";
import ControlPlaneSnapshotsPage from "@/pages/control-plane-snapshots";

function renderPage(el: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>{el}</MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => cleanup());

describe("Intent Router page", () => {
  it("renders 'not available on this build' on 404 and stops polling", async () => {
    let hits = 0;
    server.use(
      http.get("/v1/console/control-plane/intent-router/stats", () => {
        hits += 1;
        return HttpResponse.json({ detail: "Not Found" }, { status: 404 });
      }),
    );
    renderPage(<ControlIntentRouterPage />);
    expect(await screen.findByTestId("intent-router-unavailable")).toHaveTextContent(
      /not available on this build/i,
    );
    expect(screen.queryByText(/Failed to load/)).not.toBeInTheDocument();
    await new Promise((r) => setTimeout(r, 3500)); // one refetchInterval
    expect(hits).toBe(1);
  });

  it("offers no reset: no backend route resets router statistics", async () => {
    server.use(
      http.get("/v1/console/control-plane/intent-router/stats", () =>
        HttpResponse.json({ timestamp: "2026-11-19T00:00:00Z", uptime_seconds: 1, paths: [], recent_requests: [] }),
      ),
    );
    renderPage(<ControlIntentRouterPage />);
    expect(await screen.findByText(/Auto-Refresh/)).toBeInTheDocument();
    expect(screen.queryByText(/Reset Stats/)).not.toBeInTheDocument();
  });
});

// The three routers below answer 501 on every route (defused simulations). The
// pages used to turn that into an empty list: "No plugins found".
const NOT_IMPLEMENTED = { detail: { status: "not_implemented", reason: "defused" } };

describe("Control-plane pages on a 501 backend", () => {
  it("plugins: says 'not available on this build', never 'No plugins found'", async () => {
    server.use(
      http.get("/v1/console/control-plane/plugins", () =>
        HttpResponse.json(NOT_IMPLEMENTED, { status: 501 })),
    );
    renderPage(<ControlPlanePluginsPage />);
    expect(await screen.findByTestId("control-plane-plugins-unavailable")).toHaveTextContent(
      /not available on this build/i,
    );
    expect(screen.queryByText(/No plugins found/)).not.toBeInTheDocument();
  });

  it("subsystems: says so and stops polling", async () => {
    let hits = 0;
    server.use(
      http.get("/v1/console/control-plane/subsystems", () => {
        hits += 1;
        return HttpResponse.json(NOT_IMPLEMENTED, { status: 501 });
      }),
    );
    renderPage(<ControlPlaneSubsystemsPage />);
    expect(await screen.findByTestId("control-plane-subsystems-unavailable")).toHaveTextContent(
      /not available on this build/i,
    );
    expect(screen.queryByText(/No subsystems found/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Graceful Shutdown/)).not.toBeInTheDocument();
    await new Promise((r) => setTimeout(r, 5600)); // one refetchInterval
    expect(hits).toBe(1);
  }, 10_000);

  it("snapshots: says so and offers no create action", async () => {
    server.use(
      http.get("/v1/console/control-plane/snapshots", () =>
        HttpResponse.json(NOT_IMPLEMENTED, { status: 501 })),
    );
    renderPage(<ControlPlaneSnapshotsPage />);
    expect(await screen.findByTestId("control-plane-snapshots-unavailable")).toHaveTextContent(
      /not available on this build/i,
    );
    expect(screen.queryByText(/No snapshots yet/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Create Snapshot/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Atomicity Guarantee/)).not.toBeInTheDocument();
  });

  it("a real failure shows the server's reason, not the empty state", async () => {
    server.use(
      http.get("/v1/console/control-plane/plugins", () =>
        HttpResponse.json({ detail: "registry unreadable" }, { status: 500 })),
    );
    renderPage(<ControlPlanePluginsPage />);
    expect(await screen.findByText("registry unreadable")).toBeInTheDocument();
    expect(screen.queryByText(/No plugins found/)).not.toBeInTheDocument();
    expect(screen.queryByTestId("control-plane-plugins-unavailable")).not.toBeInTheDocument();
  });

  it("an empty list that WAS read still reads as empty", async () => {
    server.use(
      http.get("/v1/console/control-plane/plugins", () => HttpResponse.json([])),
    );
    renderPage(<ControlPlanePluginsPage />);
    expect(await screen.findByText(/No plugins found/)).toBeInTheDocument();
  });
});

describe("Overrides page", () => {
  it("lists pending overrides from GET /control-plane/overrides ({overrides, count})", async () => {
    server.use(
      http.get("/v1/console/control-plane/overrides", () =>
        HttpResponse.json({
          overrides: [
            { override_id: "ovr-1", override_type: "force_disable", target_id: "skill-router", created_at: "2026-09-27T00:00:00Z" },
          ],
          count: 1,
        }),
      ),
    );
    renderPage(<ControlPlaneOverridesPage />);
    expect(await screen.findByText("force_disable")).toBeInTheDocument();
    expect(screen.getByText("skill-router")).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByText(/No pending approvals/)).not.toBeInTheDocument());
  });
});
