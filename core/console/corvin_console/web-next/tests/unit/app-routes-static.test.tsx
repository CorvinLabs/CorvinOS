/**
 * Regression guard: the console must BOOT — <App /> must render without throwing.
 *
 * react-router's <Routes> walks its children statically (createRoutesFromChildren)
 * and throws an invariant for any child whose element type is not <Route> or
 * <Fragment>. On 2026-09-03 a `<ManifestPanelRoutes />` component element was
 * placed inside the /app <Route>; the production bundle then died on first render
 * with a message-less "Uncaught Error" (the invariant text is stripped in prod),
 * leaving a blank console. No test rendered <App /> at all, so nothing caught it.
 *
 * This test renders the real <App /> (real router, real registry, real
 * AuthProvider) with the backend answering 401, exactly like a cold tab before
 * login — the shape that crashed in the browser.
 */
import { describe, it, expect, afterEach } from "vitest";
import { render, cleanup, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { server } from "../fixtures/server";
import App from "@/App";

afterEach(cleanup);

function mount(path: string) {
  // Unauthenticated backend: every API call answers 401 (cold tab, no session).
  server.use(http.all("*", () => HttpResponse.json({ detail: "no session" }, { status: 401 })));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}>
        <App />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("App boots (static <Routes> tree is valid)", () => {
  it("renders the root route without throwing", () => {
    expect(() => mount("/")).not.toThrow();
  });

  it("renders a nested /app panel path without throwing and shows the session gate", async () => {
    expect(() => mount("/app/vibe-engineering")).not.toThrow();
    // RequireAuth's loading gate is the first thing a cold tab shows — proves the
    // route tree was built and matched, not just that nothing exploded.
    expect(await screen.findByText(/Loading session…|Loading…/)).toBeTruthy();
  });
});
