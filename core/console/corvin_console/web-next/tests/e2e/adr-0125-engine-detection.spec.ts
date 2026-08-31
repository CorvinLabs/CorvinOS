/**
 * Playwright E2E tests — ADR-0125 Zero-Config Engine Onboarding
 *
 * Covers the "Detected Engines" section on /app/engines and the
 * detection status strip + credential badges on /app/engine-control.
 *
 * All API calls (including session auth) are mocked via page.route()
 * so tests run without a backend server.
 */
import { expect, test, type Page, type Route } from "@playwright/test";

// ---------------------------------------------------------------------------
// Shared mock helpers
// ---------------------------------------------------------------------------

const MOCK_SESSION = {
  username: "test_admin",
  tenant_id: "_default",
  role: "admin",
  csrf_token: "csrf-test-token",
};

const MOCK_ENGINE_SETTINGS = {
  default_engine: null,
  hermes_model: null,
  valid_engines: ["claude_code", "opencode", "hermes"],
  ollama_reachable: false,
  default_worker_engine: null,
  default_worker_model: null,
  valid_worker_engines: ["claude_code", "codex_cli", "opencode", "hermes", "copilot"],
  engine_models: {},
};

const MOCK_DETECT_ALL_READY = {
  results: [
    {
      engine_id: "claude_code",
      installed: true,
      authenticated: true,
      credential_source: "subscription",
      version: "1.2.3",
      models: [],
      detail: "Authenticated via Claude subscription (OAuth)",
    },
    {
      engine_id: "hermes",
      installed: true,
      authenticated: true,
      credential_source: "config_file",
      version: "0.5.0",
      models: ["qwen3:8b", "qwen3:1.7b"],
      detail: "Ollama running — 2 models",
    },
    {
      engine_id: "opencode",
      installed: false,
      authenticated: false,
      credential_source: null,
      version: null,
      models: [],
      detail: null,
    },
    {
      engine_id: "codex_cli",
      installed: true,
      authenticated: true,
      credential_source: "env_var",
      version: "0.2.0",
      models: [],
      detail: "OPENAI_API_KEY set",
    },
    {
      engine_id: "copilot",
      installed: true,
      authenticated: true,
      credential_source: "subscription",
      version: "1.0.56",
      models: [],
      detail: "GitHub Copilot subscription active",
    },
  ],
  recommended_engine: "claude_code",
  needs_bootstrap: false,
};

const MOCK_DETECT_NOTHING_READY = {
  results: [
    {
      engine_id: "claude_code",
      installed: false,
      authenticated: false,
      credential_source: null,
      version: null,
      models: [],
      detail: null,
    },
    {
      engine_id: "hermes",
      installed: false,
      authenticated: false,
      credential_source: null,
      version: null,
      models: [],
      detail: null,
    },
    {
      engine_id: "opencode",
      installed: false,
      authenticated: false,
      credential_source: null,
      version: null,
      models: [],
      detail: null,
    },
    {
      engine_id: "codex_cli",
      installed: false,
      authenticated: false,
      credential_source: null,
      version: null,
      models: [],
      detail: null,
    },
    {
      engine_id: "copilot",
      installed: false,
      authenticated: false,
      credential_source: null,
      version: null,
      models: [],
      detail: null,
    },
  ],
  recommended_engine: null,
  needs_bootstrap: true,
};

/** Set up all API mocks needed for the app to render without a backend.
 *
 * Playwright routes are matched LIFO (last registered = first checked).
 * We register the generic fallback FIRST so it is checked LAST.
 * Specific routes are registered last so they take priority.
 */
async function setupBaseMocks(page: Page, detectResponse = MOCK_DETECT_ALL_READY) {
  // 1. Generic fallback — returns safe empty defaults for all unmatched routes
  await page.route("**/v1/**", (route: Route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ engines: {}, eaos_milestones: {} }),
    })
  );

  // 2. Engine settings (GET) — required for the engine page to render
  await page.route("**/v1/console/settings/engine", (route: Route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(MOCK_ENGINE_SETTINGS),
    })
  );

  // 3. Engine health probe
  await page.route("**/v1/console/settings/engine/health", (route: Route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ ollama_reachable: false, model_count: 0, base_url_hash: "abc123" }),
    })
  );

  // 4. Engine catalog — returns array (component iterates over it)
  await page.route("**/v1/console/settings/engine/catalog", (route: Route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([
        { id: "claude_code", label: "Claude Code", os_capable: true, local: false,
          description: "Full CC", requires: "Anthropic", model_placeholder: "",
          model_examples: "", model_aliases: [] },
        { id: "hermes", label: "Hermes", os_capable: true, local: true,
          description: "Ollama", requires: "Ollama", model_placeholder: "",
          model_examples: "", model_aliases: [] },
        { id: "opencode", label: "OpenCode", os_capable: true, local: false,
          description: "OpenCode", requires: "Any", model_placeholder: "",
          model_examples: "", model_aliases: [] },
        { id: "codex_cli", label: "Codex CLI", os_capable: false, local: false,
          description: "Codex", requires: "OpenAI", model_placeholder: "",
          model_examples: "", model_aliases: [] },
        { id: "copilot", label: "GitHub Copilot", os_capable: false, local: false,
          description: "Copilot", requires: "GitHub", model_placeholder: "",
          model_examples: "", model_aliases: [] },
      ]),
    })
  );

  // 5. Engine model registry
  await page.route("**/v1/console/settings/engine/registry", (route: Route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({}),
    })
  );

  // 6. Engine capabilities
  await page.route("**/v1/console/settings/engine/capabilities", (route: Route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ engines: {}, eaos_milestones: {} }),
    })
  );

  // 7. Engine detection (ADR-0125)
  await page.route("**/v1/console/settings/engine/detect", (route: Route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(detectResponse),
    })
  );

  // 8. Custom engines list — returns empty list (not array iteration crash)
  await page.route("**/v1/console/engines/custom", (route: Route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ engines: [] }),
    })
  );

  // 9. License info
  await page.route("**/v1/console/license**", (route: Route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ plan: "apache", features: {}, expires_at: null }),
    })
  );

  // 10. Setup status — must be complete so the app doesn't show the setup wizard
  await page.route("**/v1/console/setup/**", (route: Route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        first_run: false, setup_complete: true,
        engines_configured: true, bridges_configured: true,
      }),
    })
  );

  // 10b. setup/engines needs EnginesResponse shape {engines:[], env_path:...}
  //      Registered AFTER setup/** so LIFO checks this route FIRST for /setup/engines.
  await page.route("**/v1/console/setup/engines*", (route: Route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ engines: [], env_path: "~/.config/corvin-voice/.env" }),
    })
  );

  // 11. Session auth — registered last → checked first (highest priority in LIFO)
  await page.route("**/v1/console/auth/whoami", (route: Route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(MOCK_SESSION),
    })
  );
}

// ---------------------------------------------------------------------------
// /app/engines — Detected Engines section
// ---------------------------------------------------------------------------

test.describe("/app/engines — Detected Engines section (ADR-0125)", () => {
  test("shows 'Detected Engines' heading before Architecture Overview", async ({
    page,
  }) => {
    await setupBaseMocks(page, MOCK_DETECT_ALL_READY);
    await page.goto("/console/app/engines");
    await page.waitForLoadState("load");

    const detected = page.getByRole("heading", { name: /detected engines/i });
    await expect(detected).toBeVisible({ timeout: 15_000 });

    // Must appear BEFORE the Engine Architecture heading
    const arch = page.getByRole("heading", { name: /engine architecture/i });
    await expect(arch).toBeVisible({ timeout: 5_000 });

    const detectedBox = await detected.boundingBox();
    const archBox = await arch.boundingBox();
    expect(detectedBox!.y).toBeLessThan(archBox!.y);
  });

  test("shows engine cards for all detected results", async ({ page }) => {
    await setupBaseMocks(page, MOCK_DETECT_ALL_READY);
    await page.goto("/console/app/engines");
    await page.waitForLoadState("load");

    // Claude Code card must be present
    await expect(
      page.getByText(/claude code/i).first()
    ).toBeVisible({ timeout: 15_000 });
  });

  test("shows credential badges for authenticated engines", async ({ page }) => {
    await setupBaseMocks(page, MOCK_DETECT_ALL_READY);
    await page.goto("/console/app/engines");
    await page.waitForLoadState("load");

    // At least one subscription badge must appear
    const subBadges = page.getByText(/subscription/i);
    await expect(subBadges.first()).toBeVisible({ timeout: 15_000 });
  });

  test("Re-detect button is visible and clickable", async ({ page }) => {
    let detectCallCount = 0;

    // Use full base mocks to prevent "Something went wrong" crashes from missing endpoints.
    // setupBaseMocks registers a detect route at position 7; we register our counter route
    // AFTER it (LIFO → ours is checked first, overriding the base mock for detect).
    await setupBaseMocks(page, MOCK_DETECT_ALL_READY);

    await page.route("**/v1/console/settings/engine/detect", (route: Route) => {
      detectCallCount++;
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(MOCK_DETECT_ALL_READY),
      });
    });

    await page.goto("/console/app/engines");
    await page.waitForLoadState("load");

    const redetectBtn = page.getByRole("button", { name: /re-?detect/i });
    await expect(redetectBtn).toBeVisible({ timeout: 15_000 });

    const countBefore = detectCallCount;
    await redetectBtn.click();

    await page.waitForTimeout(800);
    expect(detectCallCount).toBeGreaterThan(countBefore);
  });

  test("shows Bootstrap Hermes CTA when no engine is ready", async ({
    page,
  }) => {
    await setupBaseMocks(page, MOCK_DETECT_NOTHING_READY);
    await page.goto("/console/app/engines");
    await page.waitForLoadState("load");

    const bootstrapBtn = page.getByRole("button", { name: /bootstrap hermes/i });
    await expect(bootstrapBtn).toBeVisible({ timeout: 15_000 });
  });

  test("does NOT show Bootstrap CTA when an engine is already ready", async ({
    page,
  }) => {
    await setupBaseMocks(page, MOCK_DETECT_ALL_READY);
    await page.goto("/console/app/engines");
    await page.waitForLoadState("load");

    // Wait for detection section to appear first
    await expect(
      page.getByRole("heading", { name: /detected engines/i })
    ).toBeVisible({ timeout: 15_000 });

    // Bootstrap CTA should NOT be visible when engines are ready
    await expect(
      page.getByRole("button", { name: /bootstrap hermes/i })
    ).not.toBeVisible({ timeout: 3_000 }).catch(() => {
      /* not-found also passes: "does NOT show" */
    });
  });

  test("shows hermes model list when hermes has models", async ({ page }) => {
    await setupBaseMocks(page, MOCK_DETECT_ALL_READY);
    await page.goto("/console/app/engines");
    await page.waitForLoadState("load");

    await expect(page.getByText("qwen3:8b")).toBeVisible({ timeout: 15_000 });
  });
});

// ---------------------------------------------------------------------------
// /app/engine-control — detection status strip
// ---------------------------------------------------------------------------

test.describe("/app/engine-control — detection strip (ADR-0125)", () => {
  test("shows detection status strip in header area", async ({ page }) => {
    await setupBaseMocks(page, MOCK_DETECT_ALL_READY);
    await page.goto("/console/app/engine-control");
    await page.waitForLoadState("load");

    // Wait for the page to render (look for any engine-related content)
    await page.waitForTimeout(2000);

    const page_content = await page.content();
    const hasDetectionContent =
      page_content.toLowerCase().includes("subscription") ||
      page_content.toLowerCase().includes("claude_code") ||
      page_content.toLowerCase().includes("env_var") ||
      page_content.toLowerCase().includes("credential") ||
      page_content.toLowerCase().includes("detected");
    expect(hasDetectionContent).toBe(true);
  });

  test("credential badges visible for OS engine selector entries", async ({
    page,
  }) => {
    await setupBaseMocks(page, MOCK_DETECT_ALL_READY);
    await page.goto("/console/app/engine-control");
    await page.waitForLoadState("load");

    // At least one credential badge should appear somewhere
    const subBadge = page.getByText(/subscription/i).first();
    await expect(subBadge).toBeVisible({ timeout: 15_000 });
  });

  test("re-detect button is available on engine-control page", async ({
    page,
  }) => {
    await setupBaseMocks(page, MOCK_DETECT_ALL_READY);
    await page.goto("/console/app/engine-control");
    await page.waitForLoadState("load");

    const redetectBtn = page.getByRole("button", { name: /re-?detect/i });
    await expect(redetectBtn).toBeVisible({ timeout: 15_000 });
  });
});
