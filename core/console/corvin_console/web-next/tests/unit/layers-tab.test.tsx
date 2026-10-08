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
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { server } from "../fixtures/server";

vi.mock("@/lib/auth", () => ({
  useAuth: () => ({
    session: { tenant_id: "_default", csrf_token: "csrf-test", tier: "owner" },
    loading: false, refresh: vi.fn(), logout: vi.fn(),
  }),
}));

import LayersTab, { MARKER_LAYER_FORGE, ForgeLayerPanel } from "@/components/forge/LayersTab";

interface Seen { method: string; path: string; csrf: string | null; body: unknown }
const seen: Seen[] = [];
const record = async (request: Request) => {
  let body: unknown = null;
  try { body = await request.clone().json(); } catch { /* empty */ }
  seen.push({ method: request.method, path: new URL(request.url).pathname, csrf: request.headers.get("x-csrf-token"), body });
};

afterEach(() => { cleanup(); seen.length = 0; });

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

/**
 * "Forge a Layer" (ADR-2224/2225 LLM-PLAN phase) — the input field that was
 * missing entirely: the backend has supported POST /layer-forge/plan since
 * those ADRs landed, but nothing in the console ever called it. Lives in
 * Generator as a fourth sub-tab next to Skill/Tool/Plugin Forge (2026-10-06,
 * operator request) — see forge.tsx's GENERATOR_SUBTABS — rendered directly
 * here rather than through <LayersTab/>, which went back to a pure browse
 * surface once this moved out. These tests prove the real two-step flow
 * against the ACTUAL resolved routes (same onUnhandledRequest: 'error'
 * discipline as the BASE-path regression test above): plan() must hit
 * /v1/console/layer-forge/plan with a CSRF header, and create() must hit
 * /v1/console/layer-forge/definitions with the manifest plan() returned —
 * never a mock of api() itself, which would pass on a wrong path too.
 */
describe("ForgeLayerPanel", () => {
  const manifest = {
    id: "L34", version: "1.0.0",
    targets: [{ layer_id: "L10" }], quality_gates: [{ gate_id: "G1" }], enforcement_rules: [],
  };

  it("plans a layer, previews the manifest, then creates it via the real routes", async () => {
    const onCreated = vi.fn();
    server.use(
      http.post("/v1/console/layer-forge/plan", async ({ request }) => {
        await record(request);
        return HttpResponse.json({ status: "SUCCESS", manifest });
      }),
      http.post("/v1/console/layer-forge/definitions", async ({ request }) => {
        await record(request);
        return HttpResponse.json({ status: "SUCCESS" });
      }),
    );

    wrap(<ForgeLayerPanel onCreated={onCreated} />);

    fireEvent.change(screen.getByTestId("layer-id-input"), { target: { value: "L34" } });
    fireEvent.change(screen.getByTestId("layer-intent-input"), {
      target: { value: "audit downstream of L10 and enforce the boundary at build time" },
    });
    fireEvent.click(screen.getByTestId("plan-layer-button"));

    await waitFor(() => expect(screen.getByTestId("create-layer-button")).toBeTruthy());
    expect(seen[0]).toMatchObject({
      method: "POST", path: "/v1/console/layer-forge/plan", csrf: "csrf-test",
      body: { layer_id: "L34", intent: "audit downstream of L10 and enforce the boundary at build time" },
    });
    // The preview renders the generated manifest — nothing persisted yet.
    expect(screen.getByText(/"id": "L34"/)).toBeInTheDocument();
    expect(onCreated).not.toHaveBeenCalled();

    fireEvent.click(screen.getByTestId("create-layer-button"));

    await waitFor(() => expect(screen.getByTestId("create-layer-success")).toBeTruthy());
    expect(seen[1]).toMatchObject({
      method: "POST", path: "/v1/console/layer-forge/definitions", csrf: "csrf-test", body: manifest,
    });
    expect(screen.getByTestId("create-layer-success").textContent).toContain("L34@1.0.0");
    // onCreated fires AFTER a real create, not on plan — Generator uses this
    // to jump the operator to the Layers tab to see the new entry.
    expect(onCreated).toHaveBeenCalledTimes(1);
  });

  it("surfaces a plan failure without creating anything", async () => {
    server.use(
      http.post("/v1/console/layer-forge/plan", () =>
        HttpResponse.json({ status: "FAILED", error: "intent too vague", phase: "plan" }, { status: 422 }),
      ),
    );

    wrap(<ForgeLayerPanel />);

    fireEvent.change(screen.getByTestId("layer-id-input"), { target: { value: "L34" } });
    fireEvent.change(screen.getByTestId("layer-intent-input"), { target: { value: "do something" } });
    fireEvent.click(screen.getByTestId("plan-layer-button"));

    await waitFor(() => expect(screen.getByTestId("plan-layer-error")).toBeTruthy());
    expect(screen.queryByTestId("create-layer-button")).toBeNull();
  });
});
