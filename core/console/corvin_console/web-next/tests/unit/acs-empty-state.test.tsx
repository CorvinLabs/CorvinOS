/**
 * WdatAuditPanel's ACS empty state (ADR-0885 review R2-M1): it must never
 * assert a configuration state the API does not report. It names what is
 * known (the served worker pin, once the setting answered) and links to the
 * Routing tab where the worker turn pin actually lives.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { server } from "../fixtures/server";

vi.mock("reactflow", () => ({ default: () => null, Background: () => null, Controls: () => null, MarkerType: {}, Position: {}, Handle: () => null }));
vi.mock("reactflow/dist/style.css", () => ({}));

import { AcsEmptyStateForTest } from "@/components/WdatAuditPanel";

function renderIt() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}><MemoryRouter><AcsEmptyStateForTest onViewOs={() => {}} /></MemoryRouter></QueryClientProvider>,
  );
}
afterEach(() => cleanup());

describe("ACS empty state", () => {
  it("names the served worker pin and links to the Routing tab — never 'Configure a Worker Engine'", async () => {
    server.use(http.get("/v1/console/settings/engine", () => HttpResponse.json({
      default_engine: "claude_code", valid_engines: ["claude_code"],
      engine_models: { claude_code: { os_model: null, worker_model: "claude-opus-5", provider: null } }, compliance_warnings: [],
    })));
    renderIt();
    await screen.findByText(/Worker turn pin: claude-opus-5\./);
    const link = screen.getByRole("link", { name: /See the worker turn pin/ });
    expect(link).toHaveAttribute("href", "/app/models?tab=routing");
    expect(screen.queryByText(/Configure a Worker Engine/)).toBeNull();
    expect(screen.queryByText(/Engine Settings/)).toBeNull();
  });

  it("says 'engine default' for a null worker pin and claims nothing while the setting has not answered", async () => {
    server.use(http.get("/v1/console/settings/engine", () => HttpResponse.json({
      default_engine: "claude_code", valid_engines: ["claude_code"],
      engine_models: { claude_code: { os_model: null, worker_model: null, provider: null } }, compliance_warnings: [],
    })));
    renderIt();
    expect(screen.getByText(/no delegated worker run was recorded/)).toBeInTheDocument();
    await screen.findByText(/Worker turn: engine default \(no pin\)\./);
  });
});
