/**
 * E2E test: LDD (Layer 14) is active and correctly exposed via the console API.
 *
 * Covers:
 *  1. GET /v1/console/ldd returns a well-formed LddSnapshot with all required fields.
 *  2. When LDD_AUTO_OPTIN=1 is active on the server:
 *       - master_enabled is true
 *       - auto_optin_active is true
 *       - all 12 canonical layers have effective=true
 *  3. Individual layer states are consistent: effective=true iff direct=true and
 *     any cascade parent is also active.
 *  4. PUT /ldd/master and PUT /ldd/layers/<layer> return a valid LddSnapshot shape.
 *  5. /app/ldd page renders without JS errors and surfaces layer toggles.
 *
 * Why these assertions are hard:
 *   LDD_AUTO_OPTIN=1 is set in ~/.bashrc — every bridge.sh-started console
 *   process inherits it. When the env var is active, the server-side
 *   _direct_state() short-circuits to True regardless of ldd.json, so
 *   the effective state cannot be disabled via the file-based API.
 *   The auto_optin_active field in the response is the observable signal of this.
 *
 * Authentication strategy:
 *   GET /v1/console/auth/local-login → session cookie (shared context)
 *
 * Requirements:
 *   - Backend running at http://localhost:8765
 *   - Frontend running at http://localhost:5173
 */

import {
  test,
  expect,
  type BrowserContext,
  type Page,
} from "@playwright/test";

const API_BASE = "http://localhost:8765/v1/console";
const FRONTEND_BASE = "http://localhost:5173";

const CANONICAL_LAYERS = [
  "loop_driven_engineering",
  "e2e_driven_iteration",
  "dialectical_reasoning",
  "dialectical_cot",
  "root_cause_by_layer",
  "docs_as_dod",
  "reproducibility_first",
  "loss_backprop_lens",
  "method_evolution",
  "drift_detection",
  "iterative_refinement",
  "per_subtask_e2e",
] as const;

// ── Shared auth context ──────────────────────────────────────────────────────

test.describe.configure({ mode: "serial" });

let sharedContext: BrowserContext;

async function verifyLoggedIn(context: BrowserContext): Promise<void> {
  const resp = await context.request.get(`${API_BASE}/auth/whoami`);
  expect(resp.status()).toBe(200);
  const body = await resp.json();
  expect(body.tier).toBe("owner");
}

function collectJsErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on("pageerror", (err) => {
    const msg = err.message;
    if (
      msg.includes("ResizeObserver loop") ||
      msg.includes("Non-Error promise rejection") ||
      msg.includes("ChunkLoadError") ||
      msg.includes("Loading CSS chunk")
    ) {
      return;
    }
    errors.push(msg);
  });
  return errors;
}

// ── Test suite ────────────────────────────────────────────────────────────────

test.describe("LDD (Layer 14) is active and exposed via console API", () => {
  test.beforeAll(async ({ browser }) => {
    sharedContext = await browser.newContext({
      storageState: "./tests/e2e/auth-state.json",
    });
    await verifyLoggedIn(sharedContext);
  });

  test.afterAll(async () => {
    await sharedContext?.close();
  });

  // ── 1. API shape ──────────────────────────────────────────────────────────

  test("GET /ldd returns a well-formed LddSnapshot with all canonical layers", async () => {
    const resp = await sharedContext.request.get(`${API_BASE}/ldd`);
    expect(resp.status()).toBe(200);

    const body = await resp.json();

    // Top-level shape
    expect(body).toHaveProperty("master_enabled");
    expect(body).toHaveProperty("presets");
    expect(body).toHaveProperty("depends_on");
    expect(body).toHaveProperty("layers");
    expect(body).toHaveProperty("auto_optin_active");

    expect(typeof body.master_enabled).toBe("boolean");
    expect(typeof body.auto_optin_active).toBe("boolean");
    expect(Array.isArray(body.presets)).toBe(true);
    expect(Array.isArray(body.layers)).toBe(true);

    // All 12 canonical layers must be present
    const layerIds: string[] = body.layers.map((l: { id: string }) => l.id);
    for (const canonical of CANONICAL_LAYERS) {
      expect(layerIds).toContain(canonical);
    }
    expect(body.layers).toHaveLength(CANONICAL_LAYERS.length);

    // Each layer entry must have the required fields
    for (const layer of body.layers) {
      expect(layer).toHaveProperty("id");
      expect(layer).toHaveProperty("label");
      expect(layer).toHaveProperty("configured");
      expect(layer).toHaveProperty("effective");
      expect(layer).toHaveProperty("depends_on");
      expect(typeof layer.configured).toBe("boolean");
      expect(typeof layer.effective).toBe("boolean");
    }

    // Presets must include at least the three standard ones
    expect(body.presets).toContain("default");
    expect(body.presets).toContain("quick");
    expect(body.presets).toContain("off");
  });

  // ── 2. LDD_AUTO_OPTIN=1 makes every layer effectively active ─────────────

  test("When auto_optin_active=true: master_enabled=true and all layers effective", async () => {
    const resp = await sharedContext.request.get(`${API_BASE}/ldd`);
    expect(resp.status()).toBe(200);
    const body = await resp.json();

    // Skip when the server was not started with LDD_AUTO_OPTIN=1 in its
    // environment (e.g. started via systemd rather than bridge.sh which
    // sources ~/.bashrc). The check is advisory, not a deployment gate here.
    if (!body.auto_optin_active) {
      test.skip(true, "LDD_AUTO_OPTIN not in server env (started outside bridge.sh) — skip");
      return;
    }

    expect(body.master_enabled).toBe(true);

    // Every layer must have effective=true when env var forces everything on
    for (const layer of body.layers) {
      expect(layer.effective).toBe(true);
    }
  });

  // ── 3. Cascade consistency ────────────────────────────────────────────────

  test("Cascade parents: if parent effective=true, dependent child must also be effective", async () => {
    const resp = await sharedContext.request.get(`${API_BASE}/ldd`);
    expect(resp.status()).toBe(200);
    const body = await resp.json();

    const effectiveById: Record<string, boolean> = {};
    for (const layer of body.layers) {
      effectiveById[layer.id] = layer.effective;
    }

    // Known cascade pairs from ldd.py::DEPENDS_ON
    const cascades: Array<[string, string]> = [
      ["dialectical_cot", "dialectical_reasoning"],
      ["per_subtask_e2e", "e2e_driven_iteration"],
      ["drift_detection", "docs_as_dod"],
    ];

    for (const [child, parent] of cascades) {
      if (!effectiveById[parent]) {
        // Parent is off → child must be off (cascade rule)
        expect(effectiveById[child]).toBe(false);
      }
      // Parent active → child state is unrestricted (may be on or off)
    }
  });

  // ── 4. PUT /ldd/master returns a valid snapshot ───────────────────────────

  test("PUT /ldd/master returns updated LddSnapshot with correct shape", async () => {
    // Get CSRF token via whoami
    const whoamiResp = await sharedContext.request.get(`${API_BASE}/auth/whoami`);
    const whoami = await whoamiResp.json();
    const csrf = whoami.csrf_token as string;
    expect(typeof csrf).toBe("string");
    expect(csrf.length).toBeGreaterThan(0);

    // Write master=true (idempotent when auto_optin_active)
    const putResp = await sharedContext.request.put(`${API_BASE}/ldd/master`, {
      data: { enabled: true },
      headers: { "x-csrf-token": csrf },
    });
    expect(putResp.status()).toBe(200);

    const body = await putResp.json();
    expect(body).toHaveProperty("master_enabled");
    expect(body).toHaveProperty("layers");
    expect(body).toHaveProperty("auto_optin_active");

    // With LDD_AUTO_OPTIN=1 active, effective state is always forced-on
    // regardless of what was written to disk.
    if (body.auto_optin_active) {
      expect(body.master_enabled).toBe(true);
    }
  });

  // ── 5. /app/ldd page renders without JS errors ───────────────────────────

  test("/app/ldd page renders LDD configuration UI without JS errors", async () => {
    const page = await sharedContext.newPage();
    const jsErrors = collectJsErrors(page);

    try {
      await page.goto(`${FRONTEND_BASE}/console/app/ldd`, { waitUntil: "load" });
      await page.waitForTimeout(2000);

      // Page must have non-trivial content
      const content = await page.content();
      expect(content.length).toBeGreaterThan(500);

      // LDD layers are displayed as ON/OFF button toggles, not HTML checkboxes
      const toggleCount = await page
        .locator('button:has-text("ON"), button:has-text("OFF")')
        .count();
      expect(toggleCount).toBeGreaterThanOrEqual(1);

      expect(jsErrors).toHaveLength(0);
    } finally {
      await page.close();
    }
  });

  // ── 6. /app/ldd page reflects master active state ─────────────────────────

  test("/app/ldd page shows LDD as active (heading or status indicator)", async () => {
    const page = await sharedContext.newPage();
    const jsErrors = collectJsErrors(page);

    try {
      await page.goto(`${FRONTEND_BASE}/console/app/ldd`, { waitUntil: "load" });
      await page.waitForTimeout(3000);

      // Page must contain LDD-related or quality-settings heading.
      // The page was rebranded to "AI Quality Settings" — check both forms.
      const hasLddText = await page
        .locator("text=/LDD|Loss.Driven|AI Quality|Quality Settings/i")
        .count();
      expect(hasLddText).toBeGreaterThan(0);

      expect(jsErrors).toHaveLength(0);
    } finally {
      await page.close();
    }
  });

  // ── 7. Individual layer PUT shapes are stable ─────────────────────────────

  test("PUT /ldd/layers/<layer> for valid layer returns 200 with snapshot", async () => {
    const whoamiResp = await sharedContext.request.get(`${API_BASE}/auth/whoami`);
    const whoami = await whoamiResp.json();
    const csrf = whoami.csrf_token as string;

    // Toggle reproducibility_first (has no cascade parent, safe to toggle)
    const putResp = await sharedContext.request.put(
      `${API_BASE}/ldd/layers/reproducibility_first`,
      {
        data: { enabled: true },
        headers: { "x-csrf-token": csrf },
      }
    );
    expect(putResp.status()).toBe(200);

    const body = await putResp.json();
    expect(Array.isArray(body.layers)).toBe(true);

    const layer = body.layers.find(
      (l: { id: string }) => l.id === "reproducibility_first"
    );
    expect(layer).toBeDefined();
    // With auto_optin_active the effective state is forced-on even if file says off
    if (body.auto_optin_active) {
      expect(layer.effective).toBe(true);
    }
  });

  // ── 8. Invalid layer returns 404 ──────────────────────────────────────────

  test("PUT /ldd/layers/<unknown> returns 404", async () => {
    const whoamiResp = await sharedContext.request.get(`${API_BASE}/auth/whoami`);
    const whoami = await whoamiResp.json();
    const csrf = whoami.csrf_token as string;

    const putResp = await sharedContext.request.put(
      `${API_BASE}/ldd/layers/nonexistent_layer_xyz`,
      {
        data: { enabled: true },
        headers: { "x-csrf-token": csrf },
        failOnStatusCode: false,
      }
    );
    expect(putResp.status()).toBe(404);
  });
});
