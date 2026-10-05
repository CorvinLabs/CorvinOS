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
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
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

beforeEach(() => { window.sessionStorage.clear(); });
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
    const onOpenTools = vi.fn();
    wrap(<ForgeCreatorPanel kind="tool" onCreated={onCreated} onOpenTools={onOpenTools} />);
    expect(screen.queryByTestId("plugin-panel-request")).toBeNull();
    fireEvent.change(screen.getByTestId("tool-request"), { target: { value: "count the words in a text" } });
    fireEvent.click(screen.getByTestId("tool-submit"));
    await waitFor(() => expect(screen.getByTestId("tool-result")).toBeTruthy());
    expect(seen[0]).toMatchObject({ csrf: "csrf-test", body: { user_request: "count the words in a text" } });
    expect(screen.getByText("1/1 sandbox tests")).toBeTruthy();
    expect(onCreated).toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("open-tools"));
    expect(onOpenTools).toHaveBeenCalled();
  });

  it("shows the failing cases of a refused tool and offers no Tools link", async () => {
    server.use(
      http.post("/v1/console/forge-creator/tool/generate", () =>
        HttpResponse.json({ status: "accepted", run_id: "run-3", kind: "tool", message: "" }, { status: 202 })),
      http.get("/v1/console/forge-creator/status/run-3", () => HttpResponse.json({
        run_id: "run-3", kind: "tool", status: "failed", phase: "sandbox_test",
        phases: ["planning", "validation", "sandbox_test", "review", "promotion"], progress: 60,
        message: "Not registered: 1 of 1 sandbox test case(s) still fail after 3 iteration(s).",
        engine: "claude_code", error: "x", plugin: null,
        tool: { name: "assistant.word_count", description: "Counts.", input_schema: {},
                tests: { passed: 0, total: 1, cases: [{ index: 0, input: { text: "a" }, expect: { words: 1 }, output: { words: 42 }, error: "'words': expected 1, got 42", passed: false, sandbox: "bwrap" }] },
                iterations: 3, quality: 0.8, findings: [], sandbox: ["bwrap"] },
      })),
    );
    wrap(<ForgeCreatorPanel kind="tool" onOpenTools={vi.fn()} />);
    fireEvent.change(screen.getByTestId("tool-request"), { target: { value: "count the words in a text" } });
    fireEvent.click(screen.getByTestId("tool-submit"));
    await waitFor(() => expect(screen.getByText(/expected 1, got 42/)).toBeTruthy());
    expect(screen.queryByTestId("open-tools")).toBeNull();
  });

  it("frees the form when the console no longer knows the run (restart → 404)", async () => {
    window.sessionStorage.setItem("forge-creator-run:tool", "run-gone");
    server.use(http.get("/v1/console/forge-creator/status/run-gone", () =>
      HttpResponse.json({ detail: "Run not found: run-gone" }, { status: 404 })));
    wrap(<ForgeCreatorPanel kind="tool" />);
    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(screen.getByRole("alert").textContent).toMatch(/no longer known/);
    expect(window.sessionStorage.getItem("forge-creator-run:tool")).toBeNull();
  });

  it("rides out a transient 401 instead of abandoning a live run", async () => {
    window.sessionStorage.setItem("forge-creator-run:tool", "run-401");
    let calls = 0;
    server.use(http.get("/v1/console/forge-creator/status/run-401", () => {
      calls += 1;
      if (calls === 1) return HttpResponse.json({ detail: "Not authenticated" }, { status: 401 });
      return HttpResponse.json({
        run_id: "run-401", kind: "tool", status: "running", phase: "generation",
        phases: ["planning", "validation", "sandbox_test", "review", "promotion"], progress: 40,
        message: "Still generating…", engine: "claude_code", error: null, tool: null, plugin: null,
      });
    }));
    wrap(<ForgeCreatorPanel kind="tool" />);
    expect(await screen.findByText("Still generating…", {}, { timeout: 3000 })).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
    expect((screen.getByTestId("tool-submit") as HTMLButtonElement).disabled).toBe(true);
    expect(window.sessionStorage.getItem("forge-creator-run:tool")).toBe("run-401");
  });

  it("re-attaches to a run after the tab was left and re-entered", async () => {
    window.sessionStorage.setItem("forge-creator-run:plugin", "run-9");
    server.use(http.get("/v1/console/forge-creator/status/run-9", () => HttpResponse.json({
      run_id: "run-9", kind: "plugin", status: "running", phase: "review",
      phases: ["planning", "classification", "generation", "checks", "review", "staging"], progress: 75,
      message: "Adversarial review…", engine: "claude_code", error: null, tool: null, plugin: null,
    })));
    wrap(<ForgeCreatorPanel kind="plugin" />);
    expect(await screen.findByText("Adversarial review…")).toBeTruthy();
    expect((screen.getByTestId("plugin-submit") as HTMLButtonElement).disabled).toBe(true);
  });
});

describe("Marketplace → Forged", () => {
  it("lists forged plugins as not installed and previews the panel with scripts-only sandbox", async () => {
    server.use(
      http.get("/v1/console/forge-creator/plugins", () => HttpResponse.json({ plugins: [SUMMARY], count: 1 })),
      http.get("/v1/console/forge-creator/plugins/:dirname", () => HttpResponse.json({
        ...SUMMARY, request_chars: 7, findings: [], warnings: [], egress_hosts: ["api.open-meteo.com"], engine: "claude_code",
        panel: { title: "Weather Digest", entry: "panel/index.html" },
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
    const doc = frame.getAttribute("srcdoc") ?? "";
    expect(doc.endsWith("<h1>Status</h1>")).toBe(true);
    expect(doc).toContain("Content-Security-Policy");
    expect(doc).toContain("default-src 'none'");
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

describe("previewDocument", () => {
  it("keeps a leading doctype first so the preview stays in standards mode", async () => {
    const { previewDocument } = await import("@/lib/api/forge-creator");
    const out = previewDocument("<!doctype html><html><body>x</body></html>");
    expect(out.startsWith("<!doctype html><meta")).toBe(true);
  });
});
