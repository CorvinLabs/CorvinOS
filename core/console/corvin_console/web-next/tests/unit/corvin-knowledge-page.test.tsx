/**
 * Knowledge Graph panel (ADR-0892 — contributor plugin corvin_knowledge) against
 * MSW: the marker renders; an empty graph is an honest empty state naming the
 * configured path (never a blank canvas); Sync POSTs with X-CSRF-Token and
 * reports the backend's result; the settings form POSTs the edited config with
 * CSRF. The canvas itself (vis-network) is not exercised here — happy-dom has
 * no layout engine; the live spec opens the real panel.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { server } from "../fixtures/server";

vi.mock("@/lib/auth", () => ({
  useAuth: () => ({ session: { tenant_id: "_default", csrf_token: "csrf-test", tier: "owner" }, loading: false, refresh: vi.fn(), logout: vi.fn() }),
}));
vi.mock("vis-network", () => ({ Network: class { on() {} destroy() {} } }));
vi.mock("vis-network/styles/vis-network.min.css", () => ({}));

import { CorvinKnowledgePage, MARKER_KNOWLEDGE } from "@/pages/corvin-knowledge";

interface Seen { method: string; path: string; csrf: string | null; body: unknown }
const seen: Seen[] = [];
const CONFIG = { repo_path: "~/.corvin-knowledge/", remote_url: "https://github.com/CorvinLabs/Corvin-Knowledge.git", auto_sync_on_query: true, consistency_level: "warn" };

function handlers(graph: { entities: unknown[]; relations: unknown[] }) {
  return [
    http.get("/v1/console/plugins/corvin-knowledge/graph", () => HttpResponse.json(graph)),
    http.get("/v1/console/plugins/corvin-knowledge/config", () => HttpResponse.json(CONFIG)),
    http.post("/v1/console/plugins/corvin-knowledge/sync", async ({ request }) => {
      seen.push({ method: "POST", path: "sync", csrf: request.headers.get("x-csrf-token"), body: await request.json() });
      return HttpResponse.json({ status: "success", message: "Sync operation 'pull' completed", timestamp: "", conflicts: [], errors: [] });
    }),
    http.post("/v1/console/plugins/corvin-knowledge/config", async ({ request }) => {
      const body = await request.json();
      seen.push({ method: "POST", path: "config", csrf: request.headers.get("x-csrf-token"), body });
      return HttpResponse.json({ status: "saved", config: body });
    }),
  ];
}

function renderIt() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(<QueryClientProvider client={qc}><CorvinKnowledgePage /></QueryClientProvider>);
}
afterEach(() => { cleanup(); seen.length = 0; });

describe("Knowledge Graph panel", () => {
  it("renders the marker, an honest empty state, and syncs with CSRF", async () => {
    server.use(...handlers({ entities: [], relations: [] }));
    renderIt();
    expect(screen.getByText(MARKER_KNOWLEDGE)).toBeInTheDocument();
    await screen.findByTestId("knowledge-empty");
    expect(screen.getByTestId("knowledge-empty").textContent).toMatch(/No entities at ~\/.corvin-knowledge\/\/graph/);
    fireEvent.click(screen.getByRole("button", { name: "Pull" }));
    await screen.findByText(/Sync sync operation 'pull' completed — the graph was reloaded/);
    expect(seen[0]).toMatchObject({ path: "sync", csrf: "csrf-test", body: { sync_type: "pull" } });
  });

  it("counts entities by status and filters them", async () => {
    server.use(...handlers({ entities: [
      { id: "ADR-0001", type: "decision", title: "First", status: "accepted", tags: ["a"] },
      { id: "CONCEPT-0001", type: "concept", title: "Second", status: "proposed", tags: ["b"] },
    ], relations: [{ from_id: "CONCEPT-0001", to_id: "ADR-0001", relation: "relates_to" }] }));
    renderIt();
    await screen.findByText("2 of 2 entities shown");
    fireEvent.change(screen.getByLabelText("Status"), { target: { value: "accepted" } });
    await screen.findByText("1 of 2 entities shown");
  });

  it("saves the edited settings with CSRF", async () => {
    server.use(...handlers({ entities: [], relations: [] }));
    renderIt();
    fireEvent.mouseDown(screen.getByRole("tab", { name: "Settings" }));
    fireEvent.click(screen.getByRole("tab", { name: "Settings" }));
    const repo = await screen.findByLabelText("Repository path");
    fireEvent.change(repo, { target: { value: "/srv/knowledge" } });
    fireEvent.click(screen.getByLabelText(/^strict/));
    fireEvent.click(screen.getByRole("button", { name: "Save settings" }));
    await screen.findByText("Settings saved.");
    await waitFor(() => expect(seen.find((s) => s.path === "config")).toMatchObject({
      csrf: "csrf-test", body: { repo_path: "/srv/knowledge", consistency_level: "strict", auto_sync_on_query: true },
    }));
  });
});
