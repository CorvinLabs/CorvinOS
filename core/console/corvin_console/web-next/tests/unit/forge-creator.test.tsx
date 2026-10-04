/**
 * Tool Forge / Plugin Forge + Marketplace "Forged" (ADR-2217) against MSW.
 *
 *  - Plugin Forge starts a run with X-CSRF-Token, polls status, and links the
 *    staged result to Marketplace → Forged.
 *  - Tool Forge shows the sandbox test results of a registered tool.
 *  - Forged lists generated plugins as NOT installed / unsigned, previews the
 *    panel in an iframe sandboxed to exactly `allow-scripts`, and deletes with
 *    CSRF after a two-click confirm.
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

import ForgeCreatorPanel from "@/components/forge/ForgeCreatorPanel";
import { ForgedTab } from "@/pages/marketplace/tabs/forged";

interface Seen { method: string; path: string; csrf: string | null; body: unknown }
const seen: Seen[] = [];
const record = async (request: Request) => {
  let body: unknown = null;
  try { body = await request.clone().json(); } catch { /* empty */ }
  seen.push({ method: request.method, path: new URL(request.url).pathname, csrf: request.headers.get("x-csrf-token"), body });
};

const SUMMARY = {
  dirname: "community_weather_digest", plugin_id: "community.weather-digest", display_name: "Weather Digest",
  kind: "integration", tier: "B", quality: 1, review_skipped: false, risk_flags: [], created_at: 1, has_panel: true,
  installed: false, origin_on_install: "community", signed: false,
};

function wrap(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(<QueryClientProvider client={qc}><MemoryRouter>{ui}</MemoryRouter></QueryClientProvider>);
}

afterEach(() => { cleanup(); seen.length = 0; });

describe("Plugin Forge", () => {
  it("starts a CSRF-signed run, polls it and links the staged plugin to Forged", async () => {
    server.use(
      http.post("/v1/console/forge-creator/plugin/generate", async ({ request }) => {
        await record(request);
        return HttpResponse.json({ status: "accepted", run_id: "run-1", kind: "plugin", message: "" }, { status: 202 });
      }),
      http.get("/v1/console/forge-creator/status/run-1", () => HttpResponse.json({
        run_id: "run-1", kind: "plugin", status: "success", phase: "staging",
        phases: ["planning", "classification", "generation", "checks", "review", "staging"], progress: 100,
        message: "Plugin 'community.weather-digest' is staged under Marketplace → Forged. It is not installed.",
        engine: "claude_code", error: null, tool: null,
        plugin: { plugin_id: "community.weather-digest", dirname: "community_weather_digest", display_name: "Weather Digest",
                  kind: "integration", tier: "B", quality: 1, findings: [], files: ["plugin.py"], panel: null, review_skipped: false },
      })),
    );
    wrap(<ForgeCreatorPanel kind="plugin" />);
    fireEvent.change(screen.getByTestId("plugin-request"), { target: { value: "a plugin that summarises the weather" } });
    fireEvent.change(screen.getByTestId("plugin-panel-request"), { target: { value: "show today" } });
    fireEvent.click(screen.getByTestId("plugin-submit"));
    await waitFor(() => expect(screen.getByTestId("plugin-result")).toBeTruthy());
    expect(seen[0]).toMatchObject({ method: "POST", csrf: "csrf-test",
      body: { user_request: "a plugin that summarises the weather", panel_request: "show today" } });
    expect(screen.getByText("Not installed")).toBeTruthy();
    expect(screen.getByRole("link", { name: /Marketplace → Forged/ }).getAttribute("href")).toBe("/app/marketplace?tab=forged");
  });
});

describe("Tool Forge", () => {
  it("shows the sandbox test results of a registered tool", async () => {
    server.use(
      http.post("/v1/console/forge-creator/tool/generate", async ({ request }) => {
        await record(request);
        return HttpResponse.json({ status: "accepted", run_id: "run-2", kind: "tool", message: "" }, { status: 202 });
      }),
      http.get("/v1/console/forge-creator/status/run-2", () => HttpResponse.json({
        run_id: "run-2", kind: "tool", status: "success", phase: "promotion",
        phases: ["planning", "validation", "sandbox_test", "review", "promotion"], progress: 100,
        message: "Tool 'assistant.word_count' passed its sandbox tests and is registered.", engine: "claude_code", error: null,
        plugin: null,
        tool: { name: "assistant.word_count", description: "Counts the words in a text.", input_schema: {},
                tests: { passed: 1, total: 1, cases: [{ index: 0, input: { text: "a b" }, expect: { words: 2 }, output: { words: 2 }, passed: true, sandbox: "bwrap" }] },
                iterations: 1, quality: 1, findings: [], sandbox: ["bwrap"] },
      })),
    );
    const onCreated = vi.fn();
    wrap(<ForgeCreatorPanel kind="tool" onCreated={onCreated} />);
    expect(screen.queryByTestId("plugin-panel-request")).toBeNull();
    fireEvent.change(screen.getByTestId("tool-request"), { target: { value: "count the words in a text" } });
    fireEvent.click(screen.getByTestId("tool-submit"));
    await waitFor(() => expect(screen.getByTestId("tool-result")).toBeTruthy());
    expect(seen[0]).toMatchObject({ csrf: "csrf-test", body: { user_request: "count the words in a text" } });
    expect(screen.getByText("1/1 sandbox tests")).toBeTruthy();
    expect(onCreated).toHaveBeenCalled();
  });
});

describe("Marketplace → Forged", () => {
  it("lists forged plugins as not installed and previews the panel with scripts-only sandbox", async () => {
    server.use(
      http.get("/v1/console/forge-creator/plugins", () => HttpResponse.json({ plugins: [SUMMARY], count: 1 })),
      http.get("/v1/console/forge-creator/plugins/:dirname", () => HttpResponse.json({
        ...SUMMARY, request: "weather", findings: [], warnings: [], egress_hosts: ["api.open-meteo.com"], engine: "claude_code",
        panel: { title: "Weather Digest", entry: "panel/index.html", sandbox: ["allow-scripts"] },
        panel_html: "<h1>Status</h1>", files: [{ path: "plugin.py", size: 10, content: "print(1)" }],
      })),
    );
    wrap(<ForgedTab />);
    await waitFor(() => expect(screen.getByTestId("forged-row")).toBeTruthy());
    expect(screen.getByText("Not installed")).toBeTruthy();
    expect(screen.getByText("Unsigned · community")).toBeTruthy();
    fireEvent.click(screen.getByTestId("forged-toggle"));
    const frame = await screen.findByTestId("forged-panel-preview");
    expect(frame.getAttribute("sandbox")).toBe("allow-scripts");
    expect(frame.getAttribute("srcdoc")).toBe("<h1>Status</h1>");
  });

  it("deletes with CSRF only after confirming", async () => {
    server.use(
      http.get("/v1/console/forge-creator/plugins", () => HttpResponse.json({ plugins: [SUMMARY], count: 1 })),
      http.delete("/v1/console/forge-creator/plugins/:dirname", async ({ request }) => { await record(request); return HttpResponse.json({ ok: true }); }),
    );
    wrap(<ForgedTab />);
    fireEvent.click(await screen.findByTestId("forged-delete"));
    expect(seen).toHaveLength(0);
    fireEvent.click(screen.getByTestId("forged-delete-confirm"));
    await waitFor(() => expect(seen).toHaveLength(1));
    expect(seen[0]).toMatchObject({ method: "DELETE", path: "/v1/console/forge-creator/plugins/community_weather_digest", csrf: "csrf-test" });
  });

  it("says so when nothing is forged", async () => {
    server.use(http.get("/v1/console/forge-creator/plugins", () => HttpResponse.json({ plugins: [], count: 0 })));
    wrap(<ForgedTab />);
    expect(await screen.findByTestId("forged-empty")).toBeTruthy();
  });
});
