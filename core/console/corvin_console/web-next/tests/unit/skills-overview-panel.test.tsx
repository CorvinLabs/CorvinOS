/**
 * SkillsOverviewPanel must call the skills status endpoint through the console
 * API base (/v1/console/api/skills/status). 2026-09-03: it fetched the bare
 * /api/skills/status, which 404s on the gateway and on the vite dev server, so
 * the OS-Skills panel showed "Failed to load skills — HTTP 404" every 5 s.
 * MSW runs with onUnhandledRequest:"error", so a wrong path fails this test.
 */
import { describe, it, expect, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { server } from "../fixtures/server";
import { SkillsOverviewPanel } from "@/components/SkillsOverviewPanel";

afterEach(cleanup);

describe("SkillsOverviewPanel", () => {
  it("loads from /v1/console/api/skills/status and renders the skills", async () => {
    let seenUrl = "";
    server.use(
      http.get("/v1/console/api/skills/status", ({ request }) => {
        seenUrl = new URL(request.url).pathname + new URL(request.url).search;
        return HttpResponse.json({
          tenant_id: "_default",
          timestamp: "2026-09-03T00:00:00Z",
          skills: [{ id: "os.delegation_router", version: "1.0.0", enabled: true, score: 0.9, runs_24h: 3, errors_24h: 0, last_run: null, status: "healthy" }],
        });
      }),
    );
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={qc}><SkillsOverviewPanel /></QueryClientProvider>);
    expect(await screen.findByText(/os\.delegation_router/)).toBeTruthy();
    expect(seenUrl).toBe("/v1/console/api/skills/status");
  });
});
