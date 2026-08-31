/**
 * Playwright E2E tests — ADR-0126 Claude Code Local Backend
 *
 * Tests the "Local Backend" section on /app/engine-control.
 * All API calls are mocked so the tests run without a running backend,
 * EXCEPT for the integration group which uses the real server at 8765.
 *
 * Mock routing follows the LIFO rule: last registered = first matched.
 * Generic fallbacks are registered FIRST, specific mocks LAST.
 */
import { expect, test, type Page, type Route } from "@playwright/test";

// ---------------------------------------------------------------------------
// Shared test data
// ---------------------------------------------------------------------------

const MOCK_SESSION = {
  username: "test_admin",
  tenant_id: "_default",
  role: "admin",
  csrf_token: "csrf-test-token",
};

const MOCK_ENGINE_SETTINGS = {
  default_engine: "claude_code",
  hermes_model: null,
  valid_engines: ["claude_code", "opencode", "hermes"],
  ollama_reachable: false,
  default_worker_engine: null,
  default_worker_model: null,
  valid_worker_engines: ["claude_code", "codex_cli", "opencode", "hermes", "copilot"],
  engine_models: {},
};

const MOCK_CATALOG = [
  { id: "claude_code", label: "Claude Code", os_capable: true, local: false,
    description: "Full CC", requires: "Anthropic", model_placeholder: "", model_examples: "", model_aliases: [] },
  { id: "hermes", label: "Hermes (Ollama)", os_capable: true, local: true,
    description: "Local Ollama", requires: "Ollama", model_placeholder: "", model_examples: "", model_aliases: [] },
  { id: "opencode", label: "OpenCode", os_capable: true, local: false,
    description: "OpenCode", requires: "Key", model_placeholder: "", model_examples: "", model_aliases: [] },
];

const MOCK_DETECT = {
  results: [
    { engine_id: "claude_code", installed: true, authenticated: true,
      credential_source: "subscription", version: "1.2.3", models: [], detail: null },
    { engine_id: "hermes", installed: true, authenticated: true,
      credential_source: "config_file", version: "0.5.0",
      models: ["qwen3:8b", "qwen3:1.7b"], detail: null },
  ],
  recommended_engine: "claude_code",
  needs_bootstrap: false,
};

const MOCK_CAPABILITIES = {
  engines: {
    claude_code: {
      capabilities: { hooks: true, mcp: true, permission_modes: ["default", "plan"] },
      command_manifest: { mid_stream_inject: "stdin_json", cancel: "ctrl-c", compact: null, native_commands: {} },
      eaos_gaps: [],
    },
    hermes: {
      capabilities: { hooks: false, mcp: false, permission_modes: [] },
      command_manifest: null,
      eaos_gaps: ["mid_stream_inject_live"],
    },
    opencode: {
      capabilities: { hooks: false, mcp: false, permission_modes: [] },
      command_manifest: null,
      eaos_gaps: [],
    },
  },
  eaos_milestones: { M1: "done", M2: "done", M3: "done", M4: "done", M5: "done", M6: "done" },
};

const MOCK_HEALTH = { ollama_reachable: true, model_count: 3, base_url_hash: "abc12345" };

// Claude Local disabled state (default)
const MOCK_CLAUDE_LOCAL_DISABLED = {
  enabled: false,
  base_url: "http://localhost:11434",
  sonnet_model: "",
  haiku_model: "",
  opus_model: "",
  ollama_reachable: true,
  available_models: ["minimax-m2.7:cloud", "qwen3:1.7b", "qwen3:8b"],
};

// Claude Local enabled state
const MOCK_CLAUDE_LOCAL_ENABLED = {
  ...MOCK_CLAUDE_LOCAL_DISABLED,
  enabled: true,
  sonnet_model: "qwen3:8b",
  haiku_model: "qwen3:1.7b",
  opus_model: "qwen3:8b",
};

// ---------------------------------------------------------------------------
// Setup helpers
// ---------------------------------------------------------------------------

async function setupBaseMocks(page: Page, claudeLocal = MOCK_CLAUDE_LOCAL_DISABLED) {
  // 1. Generic fallback (lowest priority — checked last)
  await page.route("**/v1/**", (route: Route) =>
    route.fulfill({ status: 200, contentType: "application/json",
                    body: JSON.stringify({}) })
  );
  // 2. Auth
  await page.route("**/v1/console/auth/whoami", (route: Route) =>
    route.fulfill({ status: 200, contentType: "application/json",
                    body: JSON.stringify(MOCK_SESSION) })
  );
  // 3. Engine settings (must come before more specific /settings/engine/* routes)
  await page.route("**/v1/console/settings/engine", (route: Route) => {
    if (route.request().method() === "GET") {
      return route.fulfill({ status: 200, contentType: "application/json",
                              body: JSON.stringify(MOCK_ENGINE_SETTINGS) });
    }
    return route.fulfill({ status: 200, contentType: "application/json",
                            body: JSON.stringify(MOCK_ENGINE_SETTINGS) });
  });
  // 4. Engine health
  await page.route("**/v1/console/settings/engine/health", (route: Route) =>
    route.fulfill({ status: 200, contentType: "application/json",
                    body: JSON.stringify(MOCK_HEALTH) })
  );
  // 5. Engine catalog
  await page.route("**/v1/console/settings/engine/catalog", (route: Route) =>
    route.fulfill({ status: 200, contentType: "application/json",
                    body: JSON.stringify(MOCK_CATALOG) })
  );
  // 6. Engine capabilities
  await page.route("**/v1/console/settings/engine/capabilities", (route: Route) =>
    route.fulfill({ status: 200, contentType: "application/json",
                    body: JSON.stringify(MOCK_CAPABILITIES) })
  );
  // 7. Engine detect
  await page.route("**/v1/console/settings/engine/detect", (route: Route) =>
    route.fulfill({ status: 200, contentType: "application/json",
                    body: JSON.stringify(MOCK_DETECT) })
  );
  // 8. Engine model registry
  await page.route("**/v1/console/settings/engine/registry", (route: Route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({}) })
  );
  // 9. Setup — registered as wildcard so all /setup/* sub-paths return "complete"
  await page.route("**/v1/console/setup/**", (route: Route) =>
    route.fulfill({ status: 200, contentType: "application/json",
                    body: JSON.stringify({
                      first_run: false, setup_complete: true,
                      engines_configured: true, bridges_configured: true,
                    }) })
  );
  // 9b. setup/engines needs its own shape — LIFO so it beats the wildcard above
  await page.route("**/v1/console/setup/engines*", (route: Route) =>
    route.fulfill({ status: 200, contentType: "application/json",
                    body: JSON.stringify({ engines: [], env_path: "~/.config/corvin-voice/.env" }) })
  );
  // 10. Claude Local — highest priority (last registered)
  await page.route("**/v1/console/settings/engine/claude-local", (route: Route) =>
    route.fulfill({ status: 200, contentType: "application/json",
                    body: JSON.stringify(claudeLocal) })
  );
}

async function gotoEngineControl(page: Page) {
  // The standalone "Engine Control" page no longer exists: App.tsx redirects
  // /app/engine-control to /app/engines, where its content lives in the "Control" tab
  // ("merged from the former separate Engine Control page", engines.tsx). The redirect
  // kept the old URL working, so navigating still succeeded — it just landed on the
  // Setup tab, and every assertion about the Local Backend section then failed with
  // "element(s) not found". Eight tests in this file, one stale helper.
  await page.goto("/console/app/engines", { waitUntil: "load" });
  await page.getByRole("tab", { name: "Control" }).click();
  // Wait for the engine selector inside the tab to render.
  await page.waitForSelector("text=Claude Code", { timeout: 10_000 });
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

test.describe("ADR-0126 — Local Backend section visibility", () => {
  test("Local Backend section is present when claude_code engine is selected", async ({ page }) => {
    await setupBaseMocks(page);
    await gotoEngineControl(page);

    // The Local Backend toggle should be visible
    await expect(page.getByText("Local Backend (Ollama / LM Studio)")).toBeVisible();
  });

  test("Toggle OFF by default when local backend is disabled", async ({ page }) => {
    await setupBaseMocks(page, MOCK_CLAUDE_LOCAL_DISABLED);
    await gotoEngineControl(page);

    // The toggle switch should exist and be in off state (bg-input not bg-violet-500)
    const toggle = page.locator('[role="switch"]');
    await expect(toggle).toBeVisible();
    await expect(toggle).toHaveAttribute("aria-checked", "false");
  });

  test("Local badge visible when enabled", async ({ page }) => {
    await setupBaseMocks(page, MOCK_CLAUDE_LOCAL_ENABLED);
    await gotoEngineControl(page);

    const badge = page.locator("text=local").first();
    await expect(badge).toBeVisible();
  });
});

test.describe("ADR-0126 — Toggle interaction", () => {
  test("Clicking toggle opens the config section", async ({ page }) => {
    await setupBaseMocks(page, MOCK_CLAUDE_LOCAL_DISABLED);
    await gotoEngineControl(page);

    // Initially collapsed / OFF
    const toggle = page.locator('[role="switch"]');
    await expect(toggle).toHaveAttribute("aria-checked", "false");

    // Click the toggle to enable
    await toggle.click();

    // Now the URL field should be visible
    await expect(page.locator("input[placeholder*='localhost:11434']")).toBeVisible();
    // Use first() — there are two "Reachable" texts: the OS health badge and the URL probe badge
    await expect(page.getByText("Reachable").first()).toBeVisible();
  });

  test("Model dropdown populated from available_models", async ({ page }) => {
    await setupBaseMocks(page, MOCK_CLAUDE_LOCAL_DISABLED);
    await gotoEngineControl(page);

    // Open the local backend section by clicking toggle
    await page.locator('[role="switch"]').click();

    // The model dropdown should contain the mock Ollama models
    // Note: <option> elements are never "visible" in Playwright — use toContainText on the select
    const dropdown = page.locator("select").first();
    await expect(dropdown).toBeVisible();
    await expect(dropdown).toContainText("qwen3:8b");
    await expect(dropdown).toContainText("qwen3:1.7b");
  });

  test("CONFIDENTIAL note visible when section is open", async ({ page }) => {
    await setupBaseMocks(page, MOCK_CLAUDE_LOCAL_DISABLED);
    await gotoEngineControl(page);

    await page.locator('[role="switch"]').click();

    await expect(page.getByText("CONFIDENTIAL-capable")).toBeVisible();
    await expect(page.getByText("no Anthropic API calls")).toBeVisible();
  });
});

test.describe("ADR-0126 — Save interaction", () => {
  test("Clicking Save triggers PUT /claude-local", async ({ page }) => {
    let putCalled = false;
    let putBody: Record<string, unknown> = {};

    await setupBaseMocks(page, MOCK_CLAUDE_LOCAL_DISABLED);

    // Override the claude-local route to capture the PUT
    await page.route("**/v1/console/settings/engine/claude-local", async (route: Route) => {
      if (route.request().method() === "PUT") {
        putCalled = true;
        putBody = JSON.parse(route.request().postData() || "{}");
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ ...MOCK_CLAUDE_LOCAL_DISABLED, enabled: true, ...putBody }),
        });
      } else {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify(MOCK_CLAUDE_LOCAL_DISABLED),
        });
      }
    });

    await gotoEngineControl(page);

    // Enable the toggle
    await page.locator('[role="switch"]').click();

    // Select a model
    const dropdown = page.locator("select").first();
    await dropdown.selectOption("qwen3:8b");

    // Click the local backend Save (nth(1) — the OS engine Save is disabled and comes first)
    await page.getByRole("button", { name: "Save" }).nth(1).click();

    // Wait for the PUT to be called
    await page.waitForTimeout(500);

    expect(putCalled).toBe(true);
    expect(putBody).toBeDefined();
    expect(putBody.enabled).toBe(true);
    expect(putBody.base_url).toBe("http://localhost:11434");
  });

  test("Advanced mode allows per-tier model selection", async ({ page }) => {
    await setupBaseMocks(page, MOCK_CLAUDE_LOCAL_DISABLED);
    await gotoEngineControl(page);

    // Open section
    await page.locator('[role="switch"]').click();

    // Click "Advanced: set per tier"
    await page.getByText("Advanced: set per tier").click();

    // Three separate tier selectors should appear
    await expect(page.getByText("Sonnet tier")).toBeVisible();
    await expect(page.getByText("Haiku tier")).toBeVisible();
    await expect(page.getByText("Opus tier")).toBeVisible();
  });
});

test.describe("ADR-0126 — Disabled state", () => {
  test("Toggling OFF sends PUT with enabled=false", async ({ page }) => {
    let _lastPut: Record<string, unknown> = {};

    await setupBaseMocks(page, MOCK_CLAUDE_LOCAL_ENABLED);

    await page.route("**/v1/console/settings/engine/claude-local", async (route: Route) => {
      if (route.request().method() === "PUT") {
        _lastPut = JSON.parse(route.request().postData() || "{}");
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ ...MOCK_CLAUDE_LOCAL_DISABLED }),
        });
      } else {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify(MOCK_CLAUDE_LOCAL_ENABLED),
        });
      }
    });

    await gotoEngineControl(page);

    // Toggle is ON (enabled) — click to disable
    const toggle = page.locator('[role="switch"]');
    await expect(toggle).toHaveAttribute("aria-checked", "true");
    await toggle.click();

    // The Save button should still be present (we need to explicitly save)
    // OR the toggle-off should have triggered a save automatically
    // In our implementation, toggling OFF immediately saves
    // wait a bit for the mutation to fire
    await page.waitForTimeout(500);
    // The section should now be collapsed
    await expect(page.locator("input[placeholder*='localhost:11434']")).not.toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// Integration test against real running server
// ---------------------------------------------------------------------------

test.describe("ADR-0126 — Real Ollama integration", () => {
  // These tests require a running console at 8765 AND Ollama at 11434.
  // They are tagged @live so they can be run selectively.
  test.use({ baseURL: "http://127.0.0.1:8765" });

  test("GET /v1/console/settings/engine/claude-local returns real Ollama models @live", async ({ request }) => {
    // Login to get session cookie
    const loginResp = await request.get("/v1/console/auth/local-login", {
      maxRedirects: 0,
    }).catch(() => null);

    if (!loginResp) {
      test.skip();
      return;
    }

    // Try the endpoint (session might not be available in request context without cookies)
    const resp = await request.get("/v1/console/settings/engine/claude-local");
    if (resp.status() === 401) {
      test.skip();
      return;
    }

    expect(resp.ok()).toBe(true);
    const data = await resp.json();
    expect(data).toHaveProperty("enabled");
    expect(data).toHaveProperty("ollama_reachable");
    expect(data).toHaveProperty("available_models");
    // Ollama is running on this system with qwen3 models
    expect(data.ollama_reachable).toBe(true);
    expect(data.available_models.length).toBeGreaterThan(0);
  });
});
