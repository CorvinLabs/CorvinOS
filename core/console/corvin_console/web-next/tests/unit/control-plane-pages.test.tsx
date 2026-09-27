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
