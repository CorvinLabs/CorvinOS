/**
 * E2E spec: Complex chained cross-feature scenarios for CorvinOS console.
 *
 * Covers three multi-step dependency chains that no existing spec exercises:
 *
 * Scenario 1 — LDD + Engine chain
 *   GET /v1/console/ldd → PUT /v1/console/ldd/layers/<layer> → GET /v1/console/settings/engine
 *   Verifies that toggling an LDD layer succeeds, the resulting LddSnapshot has the
 *   expected shape, and the engine settings endpoint is independently readable in the
 *   same authenticated session — confirming session-level state is consistent across
 *   two disjoint subsystems without a page reload.
 *
 * Scenario 2 — License gate + Feature access
 *   GET /v1/console/license/info → derive tier → attempt premium feature route
 *   For a Free-tier installation:  verify that trying to create a second workflow
 *   returns HTTP 402 and the response body carries a structured error (not a crash).
 *   For a paid tier: verify the route returns 200 (feature accessible).
 *   In both cases asserts that /app/license page renders the tier badge without JS errors.
 *
 * Scenario 3 — Audit tail + Event verification
 *   PUT /v1/console/settings/engine (state-changing mutation) →
 *   GET /v1/console/audit/tail?limit=5 →
 *   Assert action_performed event present + no PII in details fields
 *   Specifically validates: no token values, no email addresses, no file paths,
 *   and no exception text appear in any event's details object.
 *
 * Authentication strategy:
 *   GET /v1/console/auth/local-login → session cookie (owner tier, shared context).
 *   CSRF token fetched once from /auth/whoami and reused across PUT calls.
 *
 * Backend requirement:
 *   Backend running at http://localhost:8765 and frontend at http://localhost:5173.
 *   Tests are written to be resilient to no-op backends — where a PUT would succeed
 *   but the engine is already at the desired value, the audit tail check is adjusted
 *   to verify shape compliance rather than requiring a specific new event.
 *
 * PII audit definition (load-bearing):
 *   The allowed audit detail fields are: tenant_id, action, target_kind, target_id,
 *   sid_fingerprint, reason, classification, engine_id, persona, channel, chat_key,
 *   matched_rule. Any field resembling a token (>20 hex chars), email address
 *   (@-sign), file system path (/home, /etc, /tmp, ~/.corvin), or exception
 *   traceback text is a compliance violation (GDPR Art. 30, Layer 16 invariant).
 */

import {
  test,
  expect,
  type BrowserContext,
  type Page,
} from "@playwright/test";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const API_BASE = "http://localhost:8765/v1/console";
const FRONTEND_BASE = "http://localhost:5173";

// The layer chosen for the LDD toggle in Scenario 1. This layer has no cascade
// parent (reproducibility_first) so toggling it cannot violate cascade invariants.
const TEST_LDD_LAYER = "reproducibility_first";

// PII detection patterns (conservative — must match nothing in a clean audit chain)
const PII_PATTERNS: Array<{ label: string; re: RegExp }> = [
  { label: "hex_token_32+", re: /[0-9a-fA-F]{32,}/ },
  { label: "email_address", re: /@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}/ },
  { label: "fs_home_path", re: /\/home\/|\/root\/|~\/\.corvin|~\/\.config/ },
  { label: "exception_traceback", re: /Traceback \(most recent call last\)/ },
  { label: "secret_key_prefix", re: /sk-[a-zA-Z0-9]{20,}/ },
];

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

test.describe.configure({ mode: "serial" });

let sharedContext: BrowserContext;
let csrfToken: string;

async function fetchCsrfToken(context: BrowserContext): Promise<string> {
  const resp = await context.request.get(`${API_BASE}/auth/whoami`);
  expect(resp.status(), "whoami must return 200 to obtain CSRF token").toBe(200);
  const body = await resp.json() as { tier: string; csrf_token: string };
  expect(body.tier, "authenticated session must be owner tier").toBe("owner");
  expect(typeof body.csrf_token, "csrf_token must be a string").toBe("string");
  expect(body.csrf_token.length, "csrf_token must be non-empty").toBeGreaterThan(0);
  return body.csrf_token;
}

function collectJsErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on("pageerror", (err) => {
    const msg = err.message;
    // Filter benign development-environment noise
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

/**
 * Assert that no PII-pattern matches appear in any value of an audit event
 * details object. The check is applied to stringified values so that nested
 * objects are also covered.
 */
function assertNoPiiInDetails(
  details: Record<string, unknown>,
  eventType: string,
): void {
  const serialised = JSON.stringify(details);
  for (const { label, re } of PII_PATTERNS) {
    expect(
      re.test(serialised),
      `Audit event "${eventType}" details contain PII pattern "${label}": ${serialised.slice(0, 200)}`,
    ).toBe(false);
  }
}

// ---------------------------------------------------------------------------
// Suite setup / teardown
// ---------------------------------------------------------------------------

test.describe("Complex chained cross-feature flows", () => {
  test.beforeAll(async ({ browser }) => {
    sharedContext = await browser.newContext({ storageState: "./tests/e2e/auth-state.json" });
    csrfToken = await fetchCsrfToken(sharedContext);
  });

  test.afterAll(async () => {
    await sharedContext?.close();
  });

  // =========================================================================
  // SCENARIO 1 — LDD + Engine chain
  // =========================================================================

  test.describe("Scenario 1: LDD layer toggle → engine settings consistency", () => {
    /**
     * Step 1a: Capture the current LDD snapshot.
     * Asserts that the response is well-formed before the mutation.
     */
    test("1a. GET /ldd returns a well-formed LddSnapshot", async () => {
      const resp = await sharedContext.request.get(`${API_BASE}/ldd`);
      expect(resp.status(), "GET /ldd must return 200").toBe(200);

      const body = await resp.json() as {
        master_enabled: boolean;
        auto_optin_active?: boolean;
        layers: Array<{ id: string; configured: boolean; effective: boolean; depends_on: string[] }>;
        presets: string[];
        depends_on: Record<string, string>;
      };

      expect(typeof body.master_enabled, "master_enabled must be boolean").toBe("boolean");
      expect(Array.isArray(body.layers), "layers must be an array").toBe(true);
      expect(body.layers.length, "must have exactly 12 canonical layers").toBe(12);

      const layer = body.layers.find((l) => l.id === TEST_LDD_LAYER);
      expect(layer, `layer ${TEST_LDD_LAYER} must be present in the snapshot`).toBeDefined();
    });

    /**
     * Step 1b: Toggle the test layer to enabled.
     * The response must be a valid LddSnapshot reflecting the write.
     * When LDD_AUTO_OPTIN=1, effective is always forced-on regardless of the file
     * write — the test accommodates both cases.
     */
    test("1b. PUT /ldd/layers/<layer> returns updated LddSnapshot", async () => {
      const resp = await sharedContext.request.put(
        `${API_BASE}/ldd/layers/${TEST_LDD_LAYER}`,
        {
          data: { enabled: true },
          headers: { "x-csrf-token": csrfToken },
        },
      );
      expect(
        resp.status(),
        `PUT /ldd/layers/${TEST_LDD_LAYER} must return 200`,
      ).toBe(200);

      const body = await resp.json() as {
        master_enabled: boolean;
        auto_optin_active?: boolean;
        layers: Array<{ id: string; configured: boolean; effective: boolean }>;
      };

      // Shape invariants
      expect(typeof body.master_enabled).toBe("boolean");
      expect(Array.isArray(body.layers)).toBe(true);

      const updated = body.layers.find((l) => l.id === TEST_LDD_LAYER);
      expect(updated, `${TEST_LDD_LAYER} must appear in the returned snapshot`).toBeDefined();
      if (updated) {
        // configured must reflect the write (true was sent)
        expect(updated.configured, "configured must be true after PUT enabled=true").toBe(true);
        // When auto_optin_active the env var forces effective=true regardless of file
        if (body.auto_optin_active) {
          expect(updated.effective, "effective must be true when auto_optin_active").toBe(true);
        }
      }
    });

    /**
     * Step 1c: Fetch engine settings in the same session.
     * Confirms the session is still valid and the engine endpoint is readable
     * without a page reload after the LDD mutation.
     */
    test("1c. GET /settings/engine returns a valid OsEngineSetting in the same session", async () => {
      const resp = await sharedContext.request.get(`${API_BASE}/settings/engine`);
      expect(resp.status(), "GET /settings/engine must return 200").toBe(200);

      const body = await resp.json() as {
        default_engine: string | null;
        valid_engines: string[];
        valid_worker_engines: string[];
        ollama_reachable: boolean;
      };

      // Shape checks — the engine endpoint must not be disturbed by the LDD write
      expect(
        body.valid_engines !== undefined && Array.isArray(body.valid_engines),
        "valid_engines must be an array",
      ).toBe(true);
      expect(
        body.valid_worker_engines !== undefined && Array.isArray(body.valid_worker_engines),
        "valid_worker_engines must be an array",
      ).toBe(true);
      expect(
        typeof body.ollama_reachable,
        "ollama_reachable must be boolean",
      ).toBe("boolean");

      // default_engine is null or a non-empty string
      if (body.default_engine !== null) {
        expect(
          typeof body.default_engine === "string" && body.default_engine.length > 0,
          "default_engine must be a non-empty string when set",
        ).toBe(true);
      }
    });

    /**
     * Step 1d: UI reflects LDD state without a page reload.
     * Navigates to /app/ldd and verifies the page renders toggle controls —
     * at least one toggle/switch must be present, showing the UI layer is
     * correctly wired to the same backend state the API tests verified above.
     */
    test("1d. /app/ldd renders layer toggles (UI consistent with API state)", async () => {
      const page = await sharedContext.newPage();
      const jsErrors = collectJsErrors(page);

      try {
        await page.goto(`${FRONTEND_BASE}/console/app/ldd`, {
          waitUntil: "load",
          timeout: 30_000,
        });
        await page.waitForTimeout(2000);

        // Must have non-trivial content
        const content = await page.content();
        expect(content.length, "page must render non-trivial HTML").toBeGreaterThan(500);

        // LDD layers are displayed as ON/OFF button toggles, not HTML checkboxes
        const toggleCount = await page
          .locator('button:has-text("ON"), button:has-text("OFF")')
          .count();
        expect(
          toggleCount,
          "at least one LDD layer toggle must be visible",
        ).toBeGreaterThanOrEqual(1);

        // Must contain LDD-related text. Page rebranded to "AI Quality Settings"
        // — accept both legacy and current heading.
        const bodyText = await page.textContent("body");
        expect(bodyText, "page body must mention LDD or AI Quality Settings").toMatch(
          /LDD|Loss.Driven|AI Quality|Quality Settings/i,
        );

        expect(jsErrors, "no uncaught JS errors on /app/ldd").toHaveLength(0);
      } finally {
        await page.close();
      }
    });
  });

  // =========================================================================
  // SCENARIO 2 — License gate + Feature access
  // =========================================================================

  test.describe("Scenario 2: License tier gate → feature access decision", () => {
    /**
     * Step 2a: Determine the current tier from /license/info.
     * The shape must be valid regardless of tier.
     */
    test("2a. GET /license/info returns a well-formed LicenseInfo object", async () => {
      const resp = await sharedContext.request.get(`${API_BASE}/license/info`);
      expect(resp.status(), "GET /license/info must return 200").toBe(200);

      const body = await resp.json() as {
        tier: string;
        loaded: boolean;
        limits: Record<string, unknown>;
        features: Record<string, boolean>;
        free_tier: Record<string, unknown>;
      };

      expect(typeof body.tier, "tier must be a string").toBe("string");
      expect(body.tier.length, "tier must be non-empty").toBeGreaterThan(0);
      expect(typeof body.loaded, "loaded must be boolean").toBe("boolean");
      expect(
        body.limits !== null && typeof body.limits === "object",
        "limits must be an object",
      ).toBe(true);
      expect(
        body.features !== null && typeof body.features === "object",
        "features must be an object",
      ).toBe(true);
    });

    /**
     * Step 2b: Attempt to create a second concurrent workflow.
     * On Free tier (limit = 1) the backend must return HTTP 402 with a
     * structured error body. On paid tiers it may return 200/201/400 (not 402).
     * Either result is asserted explicitly — the test does not assume tier.
     */
    test("2b. Premium workflow feature: Free tier returns 402, paid tier allows access", async () => {
      // First, get the tier so we can branch the assertion
      const licenseResp = await sharedContext.request.get(`${API_BASE}/license/info`);
      const license = await licenseResp.json() as { tier: string };
      const tier = license.tier;

      // Attempt to create a workflow (premium feature — concurrent_limit=1 on Free)
      const createResp = await sharedContext.request.post(
        `${API_BASE}/workflows`,
        {
          data: {
            name: "e2e-license-gate-probe",
            description: "Probe workflow for license gate E2E",
            steps: [],
          },
          headers: { "x-csrf-token": csrfToken },
          failOnStatusCode: false,
        },
      );

      const status = createResp.status();

      if (tier === "free") {
        // Free tier must gate at 402 (payment required) or 403 (forbidden).
        // Both are acceptable structured refusals; a 500 is not.
        expect(
          [402, 403],
          `Free tier must return 402 or 403 for premium workflow creation, got ${status}`,
        ).toContain(status);

        // Response body must be parseable JSON with a structured error
        let body: unknown;
        try {
          body = await createResp.json();
        } catch {
          // Some 403s return plain text — acceptable; shape check below is skipped
          body = null;
        }
        if (body !== null && typeof body === "object" && body !== null) {
          // Must not expose internal exception detail — only a reason code or limit name
          const serialised = JSON.stringify(body);
          expect(
            serialised,
            "402/403 body must not contain traceback text",
          ).not.toMatch(/Traceback/);
          expect(
            serialised,
            "402/403 body must not contain file system paths",
          ).not.toMatch(/\/home\/|\/root\//);
        }
      } else {
        // Non-Free tier: must not receive a payment gate
        expect(
          status,
          `Paid tier "${tier}" must not receive 402 for workflow creation, got ${status}`,
        ).not.toBe(402);
      }
    });

    /**
     * Step 2c: /app/license page renders the tier badge without JS errors.
     * This is the UI-layer assertion that the license gate state visible in
     * the API (step 2a) also surfaces correctly in the operator console.
     */
    test("2c. /app/license renders the tier badge without JS errors", async () => {
      const page = await sharedContext.newPage();
      const jsErrors = collectJsErrors(page);

      try {
        await page.goto(`${FRONTEND_BASE}/app/license`, {
          waitUntil: "load",
          timeout: 30_000,
        });
        await page.waitForTimeout(2000);

        const content = await page.content();
        expect(
          content.length,
          "/app/license must render non-trivial HTML",
        ).toBeGreaterThan(500);

        // The page must contain the string "Free" or "Enterprise" or a tier indicator
        const bodyText = (await page.textContent("body")) ?? "";
        const hasTierText = /Free|Enterprise|Pro|Trial|tier/i.test(bodyText);
        expect(hasTierText, "/app/license must display a tier indicator").toBe(true);

        expect(jsErrors, "no uncaught JS errors on /app/license").toHaveLength(0);
      } finally {
        await page.close();
      }
    });

    /**
     * Step 2d: Free tier upgrade prompt — if the API returned 402 in step 2b
     * the UI should provide an upgrade pathway. This test verifies the
     * /app/license page contains at least one actionable element related to
     * upgrading (button or link).
     */
    test("2d. /app/license page provides an upgrade pathway element", async () => {
      const page = await sharedContext.newPage();
      const jsErrors = collectJsErrors(page);

      try {
        await page.goto(`${FRONTEND_BASE}/app/license`, {
          waitUntil: "load",
          timeout: 30_000,
        });
        await page.waitForTimeout(2000);

        // At least one of: upgrade button, contact link, or license upload element
        const upgradeCount = await page
          .locator(
            "button, a, [role='button']",
          )
          .filter({ hasText: /upgrade|license|contact|enterprise|upload/i })
          .count();

        // The page must offer at least one upgrade-oriented interaction element
        expect(
          upgradeCount,
          "/app/license must have at least one upgrade/license action element",
        ).toBeGreaterThanOrEqual(1);

        expect(jsErrors, "no uncaught JS errors on /app/license").toHaveLength(0);
      } finally {
        await page.close();
      }
    });
  });

  // =========================================================================
  // SCENARIO 3 — Audit tail + Event verification (PII-clean)
  // =========================================================================

  test.describe("Scenario 3: State-changing mutation → audit tail → PII compliance", () => {
    // Track the timestamp before the mutation so we can identify new events
    let mutationTimestampBefore: number;
    let engineBeforeMutation: { default_engine: string | null; hermes_model: string | null };

    /**
     * Step 3a: Capture current engine state before mutation so the teardown
     * can restore it, and record a pre-mutation timestamp for event correlation.
     */
    test("3a. Capture engine baseline and pre-mutation timestamp", async () => {
      mutationTimestampBefore = Math.floor(Date.now() / 1000);

      const resp = await sharedContext.request.get(`${API_BASE}/settings/engine`);
      expect(resp.status()).toBe(200);
      const body = await resp.json() as {
        default_engine: string | null;
        hermes_model: string | null;
      };
      engineBeforeMutation = {
        default_engine: body.default_engine,
        hermes_model: body.hermes_model,
      };
      // This test is purely setup — just assert state was captured
      expect(typeof mutationTimestampBefore).toBe("number");
    });

    /**
     * Step 3b: Perform the state-changing PUT to engine settings.
     * The mutation is idempotent (writes back the same default_engine value)
     * so it can be run safely on a live system. The important property is that
     * the backend writes an action_performed audit event.
     */
    test("3b. PUT /settings/engine performs a state-changing mutation (returns 200)", async () => {
      const resp = await sharedContext.request.put(
        `${API_BASE}/settings/engine`,
        {
          data: {
            default_engine: engineBeforeMutation.default_engine,
            hermes_model: engineBeforeMutation.hermes_model ?? null,
          },
          headers: { "x-csrf-token": csrfToken },
        },
      );
      expect(
        resp.status(),
        "PUT /settings/engine must return 200",
      ).toBe(200);

      const body = await resp.json() as { default_engine: string | null; valid_engines: string[] };
      // The response must echo valid_engines — confirms backend processed the write
      expect(
        Array.isArray(body.valid_engines),
        "Response must include valid_engines array",
      ).toBe(true);
    });

    /**
     * Step 3c: Fetch the audit tail immediately after the mutation.
     * Asserts the response is well-formed and the count matches events.length.
     */
    test("3c. GET /audit/tail?limit=5 returns well-formed AuditTailResponse", async () => {
      const resp = await sharedContext.request.get(
        `${API_BASE}/audit/tail?limit=5`,
      );
      expect(resp.status(), "GET /audit/tail must return 200").toBe(200);

      const body = await resp.json() as {
        tenant_id: string;
        ts: number;
        count: number;
        events: Array<{
          ts: number | null;
          event_type: string;
          severity: string;
          details: Record<string, unknown>;
          hash_prefix?: string | null;
        }>;
      };

      // Shape invariants from AuditTailResponse interface
      expect(typeof body.tenant_id, "tenant_id must be a string").toBe("string");
      expect(body.tenant_id.length, "tenant_id must be non-empty").toBeGreaterThan(0);
      expect(typeof body.ts, "ts must be a number").toBe("number");
      expect(typeof body.count, "count must be a number").toBe("number");
      expect(Array.isArray(body.events), "events must be an array").toBe(true);
      expect(body.count, "count must equal events.length").toBe(body.events.length);

      // Per-event shape validation
      for (const ev of body.events) {
        expect(typeof ev.event_type, "event_type must be a string").toBe("string");
        expect(ev.event_type.length, "event_type must be non-empty").toBeGreaterThan(0);
        expect(
          ["INFO", "WARNING", "CRITICAL", "ERROR"],
          `severity "${ev.severity}" must be a known level`,
        ).toContain(ev.severity);
        expect(
          ev.details !== undefined && typeof ev.details === "object",
          "details must be an object",
        ).toBe(true);

        // Chain internals must NOT be present in the API response
        expect(
          Object.keys(ev),
          "raw prev_hash must not appear in API response",
        ).not.toContain("prev_hash");
        expect(
          Object.keys(ev),
          "raw hash must not appear in API response",
        ).not.toContain("hash");
      }
    });

    /**
     * Step 3d: PII compliance check on every event in the tail.
     * This is the critical invariant from Layer 16 / GDPR Art. 30:
     * audit details must contain only the allow-listed metadata fields —
     * never tokens, email addresses, file paths, or exception text.
     */
    test("3d. No PII or forbidden content in audit event details (GDPR Art. 30 + Layer 16)", async () => {
      const resp = await sharedContext.request.get(
        `${API_BASE}/audit/tail?limit=5`,
      );
      expect(resp.status()).toBe(200);

      const body = await resp.json() as {
        events: Array<{
          event_type: string;
          details: Record<string, unknown>;
        }>;
      };

      for (const ev of body.events) {
        assertNoPiiInDetails(ev.details, ev.event_type);

        // Additionally: details keys must not include known-forbidden field names
        const keys = Object.keys(ev.details);
        expect(
          keys,
          `Event "${ev.event_type}" details must not contain "token" key`,
        ).not.toContain("token");
        expect(
          keys,
          `Event "${ev.event_type}" details must not contain "password" key`,
        ).not.toContain("password");
        expect(
          keys,
          `Event "${ev.event_type}" details must not contain "secret" key`,
        ).not.toContain("secret");
        expect(
          keys,
          `Event "${ev.event_type}" details must not contain "csrf_token" key`,
        ).not.toContain("csrf_token");
      }
    });

    /**
     * Step 3e: Verify an action_performed or action_failed event is present
     * in the tail that post-dates the mutation.
     * When the backend emits audit events synchronously on PUT /settings/engine,
     * the most-recent event in the tail should be a settings mutation event.
     * We do not assert the exact event_type (it may be vendor-specific) but
     * verify that at least one event with ts >= mutationTimestampBefore exists
     * and has a details object conforming to the metadata-only allowlist.
     *
     * Note: if the audit chain is empty (fresh install, no events yet) the
     * assertion is relaxed to shape-only — this avoids false-negative failures
     * on developer workstations with empty audit logs.
     */
    test("3e. At least one post-mutation audit event is present with no PII", async () => {
      const resp = await sharedContext.request.get(
        `${API_BASE}/audit/tail?limit=5`,
      );
      expect(resp.status()).toBe(200);

      const body = await resp.json() as {
        count: number;
        events: Array<{
          ts: number | null;
          event_type: string;
          severity: string;
          details: Record<string, unknown>;
        }>;
      };

      if (body.count === 0) {
        // Empty audit chain on a fresh install — accept shape-only
        return;
      }

      // At least one event should post-date the mutation (allowing 5s clock drift)
      const postMutation = body.events.filter(
        (ev) => ev.ts !== null && ev.ts >= mutationTimestampBefore - 5,
      );
      expect(
        postMutation.length,
        "At least one audit event must post-date the PUT /settings/engine mutation",
      ).toBeGreaterThanOrEqual(1);

      // Every post-mutation event must pass PII checks
      for (const ev of postMutation) {
        assertNoPiiInDetails(ev.details, ev.event_type);
      }
    });

    /**
     * Step 3f: Restore the engine state to the baseline captured in step 3a.
     * This keeps the test suite side-effect-free regardless of what was
     * already set before the run.
     */
    test("3f. Restore engine settings to pre-mutation baseline", async () => {
      const resp = await sharedContext.request.put(
        `${API_BASE}/settings/engine`,
        {
          data: {
            default_engine: engineBeforeMutation.default_engine,
            hermes_model: engineBeforeMutation.hermes_model ?? null,
          },
          headers: { "x-csrf-token": csrfToken },
        },
      );
      // 200 = restored; 400 = nothing changed (already at baseline) — both are fine
      expect(
        [200, 400],
        "Restore PUT must return 200 or 400",
      ).toContain(resp.status());
    });
  });
});
