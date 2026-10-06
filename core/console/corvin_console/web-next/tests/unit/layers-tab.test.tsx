/**
 * LayersTab against the route the backend actually serves
 * (core/console/corvin_console/routes/layer_forge.py, mounted with NO extra
 * prefix under the console router's "/v1/console", so its own
 * `@router.get("/layer-forge/definitions")` resolves to
 * "/v1/console/layer-forge/definitions").
 *
 * This is a regression test for a real bug found while moving
 * pages/layer-forge.tsx into this file (2026-10-06, live-browser 404
 * report): the original code declared a LOCAL `const BASE =
 * "/api/layer-forge"` and called `api(\`${BASE}/definitions\`)`. `api()`
 * (lib/api/client.ts) already prepends its own `BASE = "/v1/console"` to
 * every path — forge.tsx's calls are all `api('/forge/...')`, never
 * `api('/v1/console/forge/...')` — so the concatenation produced
 * "/v1/console/api/layer-forge/definitions", a path nothing serves. The list
 * silently rendered its 404/error state, never a crash, which is why it went
 * unnoticed from the day the panel shipped.
 *
 * The regression this guards against is specifically in the REQUEST PATH,
 * not in whether some function was called — a test that mocks `api()` (or
 * `fetch`) and merely asserts it was invoked would pass identically whether
 * the path is right or wrong, because the mock doesn't reproduce `api()`'s
 * own BASE-prepending. MSW is configured globally with
 * `onUnhandledRequest: 'error'` (tests/setup.ts); registering the handler
 * ONLY at the real, fully-resolved path means a reintroduced BASE bug makes
 * the request miss the handler and fails the test loudly, instead of the
 * component just rendering an empty/error state that happens to look like
 * "no data yet".
 */
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { server } from "../fixtures/server";
import LayersTab, { MARKER_LAYER_FORGE } from "@/components/forge/LayersTab";

afterEach(() => cleanup());

function wrap(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("LayersTab", () => {
  it("fetches from the real backend route (/v1/console/layer-forge/definitions), not /api/layer-forge", async () => {
    server.use(
      http.get("/v1/console/layer-forge/definitions", () =>
        HttpResponse.json({
          items: [
            {
              id: "L10",
              version: "1.0.0",
              status: "accepted",
              targets: [{ layer_id: "L10" }],
              quality_gates: [{ gate_id: "G1" }],
              enforcement_rules: [],
            },
          ],
          count: 1,
        }),
      ),
    );

    wrap(<LayersTab />);

    // Proves the request landed on the real route and the response was
    // actually parsed and rendered — not just that *a* request happened.
    expect(await screen.findByText("L10")).toBeInTheDocument();
    expect(screen.getByText(MARKER_LAYER_FORGE)).toBeInTheDocument();
  });

  it("surfaces the honest empty state when the backend has zero definitions", async () => {
    server.use(
      http.get("/v1/console/layer-forge/definitions", () =>
        HttpResponse.json({ items: [], count: 0 }),
      ),
    );

    wrap(<LayersTab />);

    expect(await screen.findByText("No layer definitions yet.")).toBeInTheDocument();
  });

  it("links to the Analytics dashboard for discoverability", async () => {
    server.use(
      http.get("/v1/console/layer-forge/definitions", () =>
        HttpResponse.json({ items: [], count: 0 }),
      ),
    );

    wrap(<LayersTab />);

    await screen.findByText("No layer definitions yet.");
    const link = screen.getByRole("link", { name: /View Analytics/i });
    expect(link).toHaveAttribute("href", "/app/layer-forge-analytics");
  });
});
