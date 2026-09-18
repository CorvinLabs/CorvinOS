/**
 * Routing tab (ADR-0885) — the real component against MSW: "Save pins" sends
 * the FULL engine_models map (the route replaces it wholesale) with the CSRF
 * header; a saved pin the source no longer offers stays selectable as
 * "(configured)"; the Catalog hand-off applies once options loaded and is
 * consumed; a 422 renders fixed copy, never the server's sentence.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { server } from "../fixtures/server";

vi.mock("@/lib/auth", () => ({
  useAuth: () => ({
    session: { tenant_id: "_default", csrf_token: "csrf-test", tier: "owner" },
    loading: false, refresh: vi.fn(), logout: vi.fn(),
  }),
}));
// The classifier-override cards are the moved TaskTypeCard (covered by the
// engine-config real-data E2E); stub them so this test is about the pins.
vi.mock("@/pages/models/components/engine-parts", () => ({
  TaskTypeCard: ({ config }: { config: { task_type: string } }) => <div data-testid={`card-${config.task_type}`} />,
  ClaudeSourceLine: () => null,
  fmtInt: (n: number) => String(n),
  useAuthLabel: () => "",
  useClaudeModels: () => ({ data: { models: [
    { id: "claude-sonnet-5", label: "Sonnet 5" }, { id: "claude-haiku-4-5-20251001", label: "Haiku 4.5" },
  ], sources: [], count: 2, default_model_id: null } }),
  useProviders: () => ({ data: { anthropic: { label: "Anthropic", kind: "cloud", model_source: "anthropic", credential_env: "", base_url: "" },
                                 ollama_local: { label: "Ollama", kind: "local", model_source: "ollama", credential_env: "", base_url: "" } } }),
}));

import { RoutingTab } from "@/pages/models/tabs/routing";

const setting = (os: string | null, worker: string | null) => ({
  default_engine: "claude_code", valid_engines: ["claude_code"],
  engine_models: {
    claude_code: { os_model: os, worker_model: worker, provider: null },
    other_engine: { os_model: "keep-me", worker_model: null, provider: "ollama_local" },
  },
  compliance_warnings: [],
});
const config = { tenant_id: "_default", total_samples: 10, total_learned_samples: 0, learning_status: "idle", last_learning_update: null, last_updated: "",
  models: Object.fromEntries(["corvinOS", "SIMPLE", "MEDIUM", "COMPLEX"].map((t) => [t, { task_type: t, selected_model: "claude-sonnet-5", provider: null, alternatives: [], confidence_score: 0.5, run_count: 0, is_converged: false, classified_count: 2 }])) };

function handlers(os: string | null, worker: string | null, onPut: (body: unknown, csrf: string | null) => Response) {
  // Like the real route: a successful PUT changes what the next GET serves.
  let current = setting(os, worker);
  return [
    http.get("/v1/console/settings/engine", () => HttpResponse.json(current)),
    http.put("/v1/console/settings/engine", async ({ request }) => {
      const res = onPut(await request.json(), request.headers.get("x-csrf-token"));
      if (res.ok) current = (await res.clone().json()) as typeof current;
      return res;
    }),
    http.get("/v1/console/v1/engine/config", () => HttpResponse.json(config)),
    http.get("/v1/console/learning/model-cost-optimizer/status", () => HttpResponse.json({ window: { active: false } })),
  ];
}

function renderTab(props: Partial<React.ComponentProps<typeof RoutingTab>> = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const consumed = vi.fn();
  const utils = render(
    <QueryClientProvider client={qc}>
      <RoutingTab active preselect={null} preselectTurn={null} onPreselectConsumed={consumed} {...props} />
    </QueryClientProvider>,
  );
  return { ...utils, consumed };
}

afterEach(() => cleanup());

describe("Routing tab — turn pins", () => {
  it("Save pins sends the FULL map with the changed role and the CSRF header", async () => {
    let put: { body: unknown; csrf: string | null } | null = null;
    server.use(...handlers("claude-sonnet-5", null, (body, csrf) => { put = { body, csrf }; return HttpResponse.json(setting("claude-sonnet-5", "claude-haiku-4-5-20251001")); }));
    renderTab();
    const worker = await screen.findByLabelText("Worker turn model");
    fireEvent.change(worker, { target: { value: "claude-haiku-4-5-20251001" } });
    fireEvent.click(screen.getByRole("button", { name: "Save pins" }));
    await waitFor(() => expect(put).not.toBeNull());
    expect(put!.csrf).toBe("csrf-test");
    expect(put!.body).toEqual({
      default_engine: "claude_code",
      engine_models: {
        claude_code: { os_model: "claude-sonnet-5", worker_model: "claude-haiku-4-5-20251001", provider: null },
        other_engine: { os_model: "keep-me", worker_model: null, provider: "ollama_local" }, // untouched engine survives the REPLACE
      },
    });
    await screen.findByText("Saved — audited.", {}, { timeout: 4000 });
  });

  it("keeps a configured pin the source no longer offers selectable", async () => {
    server.use(...handlers("claude-retired-1", null, () => HttpResponse.json({})));
    renderTab();
    const os = (await screen.findByLabelText("OS turn model")) as HTMLSelectElement;
    await waitFor(() => expect(os.value).toBe("claude-retired-1"));
    expect(within(os).getByRole("option", { name: "claude-retired-1 (configured)" })).toBeInTheDocument();
  });

  it("applies a Catalog preselect once options exist, then consumes it", async () => {
    server.use(...handlers("claude-sonnet-5", null, () => HttpResponse.json({})));
    const { consumed } = renderTab({ preselect: "claude-haiku-4-5-20251001", preselectTurn: "worker" });
    const worker = (await screen.findByLabelText("Worker turn model")) as HTMLSelectElement;
    await waitFor(() => expect(worker.value).toBe("claude-haiku-4-5-20251001"));
    expect(consumed).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "Save pins" })).not.toBeDisabled(); // dirty
  });

  it("says so when the preselected model is not offered", async () => {
    server.use(...handlers("claude-sonnet-5", null, () => HttpResponse.json({})));
    const { consumed } = renderTab({ preselect: "gh", preselectTurn: "os" });
    await screen.findByText("gh is not offered by the pinned engine's model source.");
    expect(consumed).toHaveBeenCalledTimes(1);
  });

  it("renders fixed copy on 422, never the server sentence", async () => {
    server.use(...handlers("claude-sonnet-5", null, () => HttpResponse.json({ detail: "SERVER SENTENCE" }, { status: 422 })));
    renderTab();
    const worker = await screen.findByLabelText("Worker turn model");
    fireEvent.change(worker, { target: { value: "claude-haiku-4-5-20251001" } });
    fireEvent.click(screen.getByRole("button", { name: "Save pins" }));
    await screen.findByText(/Not saved — a pin names a model/);
    expect(screen.queryByText(/SERVER SENTENCE/)).toBeNull();
  });

  it("lists the native path once (the registry's anthropic entry is not a second option)", async () => {
    server.use(...handlers("claude-sonnet-5", null, () => HttpResponse.json({})));
    renderTab();
    const source = (await screen.findByLabelText("Model source")) as HTMLSelectElement;
    const labels = Array.from(source.options).map((o) => o.textContent);
    expect(labels).toEqual(["Claude (native)", "Ollama"]);
  });
});
