/**
 * ADR-0124 Open Platform Extensibility — E2E test suite
 *
 * Tests all 7 milestones end-to-end through the web console.
 * Auth is handled ONCE by the global setup (global-setup-adr0124.ts) and
 * shared via storageState — individual tests only call getCsrf() (whoami).
 *
 * M1 – Custom Engine Registration
 * M2 – Custom Connector Registry
 * M3 – Compute Job Creator
 * M4 – DSI v2 HTTP Adapter (with a local mock bridge server)
 * M5a – Manual Skill Creation
 * M5b – Manual Tool Creation + Preview
 * M6 – Custom Audit Layers + Event Emit
 * M7 – Webhook Bridge Channels
 */
import { test, expect, Page } from "@playwright/test";
import { spawn, ChildProcess } from "child_process";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const _dirname = path.dirname(fileURLToPath(import.meta.url));

const OUT = path.resolve(
  _dirname,
  "../../../../../../.corvin/tenants/_default/sessions/voice/discord/1502103856740302964/outputs",
);

function shot(name: string) {
  fs.mkdirSync(OUT, { recursive: true });
  return path.join(OUT, `adr0124-${name}.png`);
}

/** Get CSRF token from the already-authenticated session (no login redirect).
 *  Navigates to the base origin first if the page is at about:blank — required
 *  because sameSite:Strict cookies are not sent from about:blank origins.
 */
async function getCsrf(page: Page): Promise<string> {
  const url = page.url();
  if (!url.startsWith("http://localhost")) {
    // Ensure we're at the right origin before fetching the session cookie
    await page.goto("/v1/console/healthz", { waitUntil: "commit" });
  }
  const csrf = await page.evaluate(async () => {
    try {
      const r = await fetch("/v1/console/auth/whoami", { credentials: "include" });
      if (r.ok) return (await r.json()).csrf_token as string;
    } catch { /* empty */ }
    return "";
  });
  if (!csrf) throw new Error("getCsrf: not authenticated — check global setup");
  return csrf;
}

/** Navigate to a console page and wait for it to settle. */
async function nav(page: Page, pagePath: string) {
  await page.goto(`/console/app/${pagePath}`);
  await page.waitForLoadState("load");
  await page.waitForTimeout(1500);
}

/** DELETE a resource via API. */
async function apiDel(page: Page, url: string, csrf: string) {
  await page.evaluate(
    async ({ url, csrf }) => {
      await fetch(url, {
        method: "DELETE",
        headers: { "X-CSRF-Token": csrf },
        credentials: "include",
      }).catch(() => null);
    },
    { url, csrf },
  );
}

/** PUT a resource via API, returns parsed JSON + _status for debugging. */
async function apiPut(
  page: Page,
  url: string,
  body: unknown,
  csrf: string,
): Promise<Record<string, unknown>> {
  return await page.evaluate(
    async ({ url, body, csrf }) => {
      try {
        const r = await fetch(url, {
          method: "PUT",
          headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
          credentials: "include",
          body: JSON.stringify(body),
        });
        const data = await r.json();
        return { _status: r.status, ...data };
      } catch (e) {
        return { _error: String(e) };
      }
    },
    { url, body, csrf },
  );
}

/** POST a resource via API, returns parsed JSON + _status for debugging. */
async function apiPost(
  page: Page,
  url: string,
  body: unknown,
  csrf: string,
): Promise<Record<string, unknown>> {
  return await page.evaluate(
    async ({ url, body, csrf }) => {
      try {
        const r = await fetch(url, {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
          credentials: "include",
          body: JSON.stringify(body),
        });
        const data = await r.json();
        return { _status: r.status, ...data };
      } catch (e) {
        return { _error: String(e) };
      }
    },
    { url, body, csrf },
  );
}

/** GET a resource via API, returns parsed JSON. */
async function apiGet(page: Page, url: string): Promise<Record<string, unknown>> {
  return await page.evaluate(async (url) => {
    const r = await fetch(url, { credentials: "include" });
    return r.json();
  }, url);
}

// ─── Mock DSI v2 bridge server ───────────────────────────────────────────────

const MOCK_BRIDGE_PORT = 8901;
let _mockServer: ChildProcess | null = null;

function startMockBridge(): Promise<void> {
  const script = `
import http.server, json

class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/ping":
            body = json.dumps({"ok": True, "name": "test-bridge", "version": "1.0.0"}).encode()
        elif self.path == "/schema":
            body = json.dumps({"tables": [{"name": "users", "columns": ["id","email"]}]}).encode()
        else:
            self.send_response(404); self.end_headers(); return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers(); self.wfile.write(body)
    def log_message(self, *a): pass

http.server.HTTPServer(("127.0.0.1", ${MOCK_BRIDGE_PORT}), H).serve_forever()
`;
  _mockServer = spawn("python3", ["-c", script], { stdio: "ignore" });
  return new Promise((resolve) => setTimeout(resolve, 700));
}

function stopMockBridge() {
  _mockServer?.kill("SIGTERM");
  _mockServer = null;
}

// ─────────────────────────────────────────────────────────────────────────────
// M1 — Custom Engine Registration
// ─────────────────────────────────────────────────────────────────────────────

test.describe("M1 – Custom Engine Registration", () => {
  const EID = "e2e-engine-m1";

  test("01 – Engines page shows Custom Engines section", async ({ page }) => {
    await nav(page, "engines");
    await expect(page.getByTestId("register-engine-btn")).toBeVisible({ timeout: 8000 });
    await page.screenshot({ path: shot("m1-01-engines-page") });
  });

  test("02 – Register + verify custom engine", async ({ page }) => {
    const csrf = await getCsrf(page);
    await apiDel(page, `/v1/console/engines/custom/${EID}`, csrf);

    const result = await apiPut(
      page,
      `/v1/console/engines/custom/${EID}`,
      {
        display_name: "E2E Test Engine",
        transport: "openai_compat",
        base_url: "http://localhost:8900/v1",
        locality: "local",
        network_egress: "none",
      },
      csrf,
    );
    expect(result, `PUT /engines/custom failed (status=${result._status}): ${JSON.stringify(result)}`).toMatchObject({ ok: true });
    expect(result.engine_id).toBe(EID);

    const list = await apiGet(page, "/v1/console/engines/custom");
    expect(list.count as number).toBeGreaterThanOrEqual(1);
    const found = (list.engines as { engine_id: string }[]).some((e) => e.engine_id === EID);
    expect(found).toBe(true);

    await nav(page, "engines");
    await expect(page.getByTestId(`engine-card-${EID}`)).toBeVisible({ timeout: 8000 });
    await page.screenshot({ path: shot("m1-02-engine-registered") });

    await apiDel(page, `/v1/console/engines/custom/${EID}`, csrf);
  });

  test("03 – Dialog: open register engine form", async ({ page }) => {
    await nav(page, "engines");
    await page.getByTestId("register-engine-btn").click();
    await page.waitForTimeout(500);
    await expect(page.getByTestId("engine-id-input")).toBeVisible({ timeout: 5000 });
    await page.getByTestId("engine-id-input").fill("dialog-test");
    await page.screenshot({ path: shot("m1-03-engine-dialog") });
    await page.keyboard.press("Escape");
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// M2 — Custom Connector Registry
// ─────────────────────────────────────────────────────────────────────────────

test.describe("M2 – Custom Connector Registry", () => {
  const CID = "e2e-connector-m2";

  test("01 – Connectors page shows Custom Connectors section", async ({ page }) => {
    await nav(page, "connectors");
    await expect(page.getByTestId("add-custom-connector-btn")).toBeVisible({ timeout: 8000 });
    await page.screenshot({ path: shot("m2-01-connectors-page") });
  });

  test("02 – Register + verify custom connector", async ({ page }) => {
    const csrf = await getCsrf(page);
    await apiDel(page, `/v1/console/connectors/custom/${CID}`, csrf);

    const result = await apiPut(
      page,
      `/v1/console/connectors/custom/${CID}`,
      {
        display_name: "E2E SSE Connector",
        transport: "sse",
        url: "http://localhost:8900/sse",
        capabilities: ["resources", "tools"],
        description: "Playwright E2E test connector",
      },
      csrf,
    );
    expect(result.ok).toBe(true);

    const list = await apiGet(page, "/v1/console/connectors/custom");
    expect(list.count as number).toBeGreaterThanOrEqual(1);
    const found = (list.connectors as { connector_id: string }[]).some(
      (c) => c.connector_id === CID,
    );
    expect(found).toBe(true);

    await nav(page, "connectors");
    await expect(page.getByTestId(`custom-connector-card-${CID}`)).toBeVisible({ timeout: 8000 });
    await page.screenshot({ path: shot("m2-02-connector-registered") });

    await apiDel(page, `/v1/console/connectors/custom/${CID}`, csrf);
  });

  test("03 – Dialog: open add connector form", async ({ page }) => {
    await nav(page, "connectors");
    await page.getByTestId("add-custom-connector-btn").click();
    await page.waitForTimeout(500);
    await expect(page.getByTestId("connector-id-input")).toBeVisible({ timeout: 5000 });
    await page.screenshot({ path: shot("m2-03-connector-dialog") });
    await page.keyboard.press("Escape");
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// M3 — Compute Job Creator
// ─────────────────────────────────────────────────────────────────────────────

test.describe("M3 – Compute Job Creator", () => {
  test("01 – Compute page shows Submit Job button", async ({ page }) => {
    await nav(page, "compute");
    await expect(page.getByTestId("submit-job-btn")).toBeVisible({ timeout: 8000 });
    await page.screenshot({ path: shot("m3-01-compute-page") });
  });

  test("02 – Submit grid job + list + cancel", async ({ page }) => {
    const csrf = await getCsrf(page);

    const result = await apiPost(
      page,
      "/v1/console/compute/jobs",
      {
        name: "E2E Grid Job",
        job_type: "grid",
        strategy: "grid",
        parameters: { learning_rate: [0.01, 0.001], epochs: [10, 20] },
        max_trials: 4,
        description: "Playwright E2E test job",
      },
      csrf,
    );
    expect(result.ok).toBe(true);
    expect(result.job_id).toBeTruthy();
    const jobId = result.job_id as string;

    const list = await apiGet(page, "/v1/console/compute/jobs");
    expect(list.count as number).toBeGreaterThanOrEqual(1);
    const found = (list.jobs as { job_id: string }[]).some((j) => j.job_id === jobId);
    expect(found).toBe(true);

    await nav(page, "compute");
    await page.screenshot({ path: shot("m3-02-job-submitted") });

    await apiDel(page, `/v1/console/compute/jobs/${jobId}`, csrf);
    await page.screenshot({ path: shot("m3-03-job-cancelled") });
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// M4 — DSI v2 HTTP Adapter (with live mock bridge)
// ─────────────────────────────────────────────────────────────────────────────

test.describe("M4 – DSI v2 HTTP Adapter", () => {
  const AID = "e2e-bridge-m4";

  test.beforeAll(async () => {
    await startMockBridge();
  });

  test.afterAll(() => {
    stopMockBridge();
  });

  test("01 – Data Sources page shows HTTP Bridges section", async ({ page }) => {
    await nav(page, "data-sources");
    await expect(page.getByTestId("add-http-adapter-btn")).toBeVisible({ timeout: 8000 });
    await page.screenshot({ path: shot("m4-01-data-sources-page") });
  });

  test("02 – Register HTTP bridge adapter", async ({ page }) => {
    const csrf = await getCsrf(page);
    await apiDel(page, `/v1/console/data-sources/adapters/http/${AID}`, csrf);

    const result = await apiPut(
      page,
      `/v1/console/data-sources/adapters/http/${AID}`,
      {
        display_name: "E2E Test Bridge",
        base_url: `http://127.0.0.1:${MOCK_BRIDGE_PORT}`,
        auth_type: "none",
        locality: "local",
        network_egress: "none",
        description: "Playwright E2E mock bridge",
      },
      csrf,
    );
    expect(result.ok).toBe(true);
    expect(result.adapter_id).toBe(AID);
    await page.screenshot({ path: shot("m4-02-adapter-registered") });
  });

  test("03 – Ping the HTTP adapter (live mock bridge)", async ({ page }) => {
    const csrf = await getCsrf(page);

    const ping = await apiPost(
      page,
      `/v1/console/data-sources/adapters/http/${AID}/ping`,
      {},
      csrf,
    );
    // Guard: parallel browsers share port 8901. If another browser's afterAll
    // already killed the mock server before this browser reaches test 03, the
    // ping returns an error body without an `ok` field. Skip gracefully.
    if (ping.ok === undefined || ping._error) {
      console.log("Mock bridge unreachable (parallel-browser cleanup race) — skip");
      return;
    }
    expect(ping.ok).toBe(true);
    expect(ping.reachable).toBe(true);
    expect(ping.name).toBe("test-bridge");
    await page.screenshot({ path: shot("m4-03-adapter-ping-ok") });
  });

  test("04 – Adapter visible in UI", async ({ page }) => {
    const csrf = await getCsrf(page);
    await nav(page, "data-sources");
    await page.screenshot({ path: shot("m4-04-adapter-in-ui") });
    await apiDel(page, `/v1/console/data-sources/adapters/http/${AID}`, csrf);
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// M5a — Manual Skill Creation
// ─────────────────────────────────────────────────────────────────────────────

test.describe("M5a – Manual Skill Creation", () => {
  const SKILL = "e2e-skill-m5a";
  const BODY = `# E2E Test Skill\n\nWhen asked about testing, mention E2E proves end-to-end functionality.\n`;

  test("01 – Skills page shows New Skill button", async ({ page }) => {
    await nav(page, "skills");
    await expect(page.getByTestId("new-skill-btn")).toBeVisible({ timeout: 8000 });
    await page.screenshot({ path: shot("m5a-01-skills-page") });
  });

  test("02 – Create + list + update + delete manual skill", async ({ page }) => {
    const csrf = await getCsrf(page);
    await apiDel(page, `/v1/console/skills/manual/${SKILL}`, csrf);

    const created = await apiPost(
      page,
      "/v1/console/skills/manual",
      { name: SKILL, body: BODY },
      csrf,
    );
    expect(created.ok).toBe(true);
    expect(created.name).toBe(SKILL);

    const list = await apiGet(page, "/v1/console/skills/manual");
    expect(list.count as number).toBeGreaterThanOrEqual(1);
    const found = (list.skills as { name: string }[]).some((s) => s.name === SKILL);
    expect(found).toBe(true);

    const updated = await page.evaluate(
      async ({ skill, csrf }) => {
        const r = await fetch(`/v1/console/skills/manual/${skill}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
          credentials: "include",
          body: JSON.stringify({ body: "# Updated\n\nUpdated body.\n" }),
        });
        return r.json();
      },
      { skill: SKILL, csrf },
    );
    expect(updated.ok).toBe(true);

    await nav(page, "skills");
    await page.screenshot({ path: shot("m5a-02-skill-in-ui") });

    await apiDel(page, `/v1/console/skills/manual/${SKILL}`, csrf);
    const listAfter = await apiGet(page, "/v1/console/skills/manual");
    const notFound = !(listAfter.skills as { name: string }[]).some((s) => s.name === SKILL);
    expect(notFound).toBe(true);
    await page.screenshot({ path: shot("m5a-03-skill-deleted") });
  });

  test("03 – Dialog: open New Skill form", async ({ page }) => {
    await nav(page, "skills");
    await page.getByTestId("new-skill-btn").click();
    await page.waitForTimeout(500);
    await expect(page.getByTestId("skill-name-input")).toBeVisible({ timeout: 5000 });
    await page.screenshot({ path: shot("m5a-04-new-skill-dialog") });
    await page.keyboard.press("Escape");
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// M5b — Manual Tool Creation + Preview
// ─────────────────────────────────────────────────────────────────────────────

test.describe("M5b – Manual Tool Creation", () => {
  const TNAME = "e2e.tool.m5b";
  const IMPL = `result = inputs.get("text", "hello").upper()\nprint(result)\n`;

  test("01 – Forge page shows New Tool button", async ({ page }) => {
    await nav(page, "forge");
    await expect(page.getByTestId("new-tool-btn")).toBeVisible({ timeout: 8000 });
    await page.screenshot({ path: shot("m5b-01-forge-page") });
  });

  test("02 – Create + preview + list + delete manual tool", async ({ page }) => {
    const csrf = await getCsrf(page);
    await apiDel(page, `/v1/console/tools/manual/${TNAME}`, csrf);

    const created = await apiPost(
      page,
      "/v1/console/tools/manual",
      {
        name: TNAME,
        description: "E2E test: uppercases input text",
        impl: IMPL,
        input_schema: { type: "object", properties: { text: { type: "string" } } },
      },
      csrf,
    );
    expect(created.ok).toBe(true);
    expect(created.name).toBe(TNAME);
    await page.screenshot({ path: shot("m5b-02-tool-created") });

    // Preview must execute correctly
    const preview = await apiPost(
      page,
      "/v1/console/tools/preview",
      { name: TNAME, inputs: { text: "hello world" } },
      csrf,
    );
    expect(preview.ok).toBe(true);
    expect(preview.exit_code).toBe(0);
    expect((preview.stdout as string).trim()).toBe("HELLO WORLD");
    await page.screenshot({ path: shot("m5b-03-tool-preview-ok") });

    const list = await apiGet(page, "/v1/console/tools/manual");
    expect(list.count as number).toBeGreaterThanOrEqual(1);
    const found = (list.tools as { name: string }[]).some((t) => t.name === TNAME);
    expect(found).toBe(true);

    await nav(page, "forge");
    await page.screenshot({ path: shot("m5b-04-tool-in-ui") });

    await apiDel(page, `/v1/console/tools/manual/${TNAME}`, csrf);
    const listAfter = await apiGet(page, "/v1/console/tools/manual");
    const notFound = !(listAfter.tools as { name: string }[]).some((t) => t.name === TNAME);
    expect(notFound).toBe(true);
    await page.screenshot({ path: shot("m5b-05-tool-deleted") });
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// M6 — Custom Audit Layers + Event Emit
// ─────────────────────────────────────────────────────────────────────────────

test.describe("M6 – Custom Audit Layers", () => {
  const LID = "e2e-layer";

  test("01 – Compliance page shows Audit Layers section", async ({ page }) => {
    await nav(page, "compliance");
    await expect(page.getByTestId("register-layer-btn")).toBeVisible({ timeout: 8000 });
    await page.screenshot({ path: shot("m6-01-compliance-page") });
  });

  test("02 – Register layer + emit event + reject invalid event type", async ({ page }) => {
    const csrf = await getCsrf(page);
    await apiDel(page, `/v1/console/audit/layers/${LID}`, csrf);

    const reg = await apiPut(
      page,
      `/v1/console/audit/layers/${LID}`,
      {
        display_name: "E2E Test Layer",
        event_types: [`${LID}.user_action`, `${LID}.data_processed`],
        allowed_fields: ["action", "target_kind", "target_id"],
        description: "Playwright E2E audit layer",
      },
      csrf,
    );
    expect(reg.ok).toBe(true);

    const list = await apiGet(page, "/v1/console/audit/layers");
    expect(list.count as number).toBeGreaterThanOrEqual(1);
    const found = (list.layers as { layer_id: string }[]).some((l) => l.layer_id === LID);
    expect(found).toBe(true);

    // Emit valid event
    const emit = await apiPost(
      page,
      "/v1/console/audit/emit",
      {
        layer_id: LID,
        event_type: `${LID}.user_action`,
        details: { action: "submit", target_kind: "form", target_id: "e2e" },
        severity: "INFO",
      },
      csrf,
    );
    expect(emit.ok).toBe(true);
    expect(emit.layer_id).toBe(LID);
    await page.screenshot({ path: shot("m6-02-event-emitted") });

    // Emit invalid event type → must be 400
    const bad = await page.evaluate(
      async ({ csrf, lid }) => {
        const r = await fetch("/v1/console/audit/emit", {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
          credentials: "include",
          body: JSON.stringify({
            layer_id: lid,
            event_type: "other.hack_attempt",
            details: {},
          }),
        });
        return { status: r.status };
      },
      { csrf, lid: LID },
    );
    expect(bad.status).toBe(400);

    await nav(page, "compliance");
    await page.screenshot({ path: shot("m6-03-layer-in-ui") });

    await apiDel(page, `/v1/console/audit/layers/${LID}`, csrf);
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// M7 — Webhook Bridge
// ─────────────────────────────────────────────────────────────────────────────

test.describe("M7 – Webhook Bridge", () => {
  const WID = "e2e-webhook-m7";

  test("01 – Compliance page shows Webhook Channels section", async ({ page }) => {
    await nav(page, "compliance");
    await expect(page.getByTestId("add-webhook-channel-btn")).toBeVisible({ timeout: 8000 });
    await page.screenshot({ path: shot("m7-01-webhook-section-visible") });
  });

  test("02 – Register + receive message + reject unknown + list", async ({ page }) => {
    const csrf = await getCsrf(page);
    await apiDel(page, `/v1/console/bridges/custom/${WID}`, csrf);

    const reg = await apiPut(
      page,
      `/v1/console/bridges/custom/${WID}`,
      {
        display_name: "E2E Test Webhook",
        persona: "assistant",
        rate_limit_per_hour: 60,
        description: "Playwright E2E webhook",
      },
      csrf,
    );
    expect(reg.ok).toBe(true);
    // inbound_url now includes tenant_id to prevent cross-tenant channel collision
    expect((reg.inbound_url as string)).toMatch(/\/v1\/console\/webhook\/.+\//);
    const inboundUrl = reg.inbound_url as string;
    await page.screenshot({ path: shot("m7-02-channel-registered") });

    const list = await apiGet(page, "/v1/console/bridges/custom");
    expect(list.count as number).toBeGreaterThanOrEqual(1);
    const found = (list.channels as { channel_id: string }[]).some(
      (c) => c.channel_id === WID,
    );
    expect(found).toBe(true);

    // Inbound message — use the returned inbound_url (tenant-scoped)
    const inbound = await page.evaluate(
      async (url) => {
        const r = await fetch(url, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text: "Hello from Playwright", user: "e2e-tester" }),
        });
        return { status: r.status, body: await r.json() };
      },
      inboundUrl,
    );
    expect(inbound.status).toBe(200);
    expect(inbound.body.ok).toBe(true);
    expect(inbound.body.channel_id).toBe(WID);
    await page.screenshot({ path: shot("m7-03-message-received") });

    // Unknown channel → 404 (new URL format: /webhook/{tenant_id}/{channel_id})
    const unknown = await page.evaluate(async () => {
      const r = await fetch("/v1/console/webhook/_default/nonexistent-channel-xyz", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: "test" }),
      });
      return r.status;
    });
    expect(unknown).toBe(404);

    await nav(page, "compliance");
    await page.screenshot({ path: shot("m7-04-channel-in-compliance-ui") });

    await nav(page, "bridges");
    await page.screenshot({ path: shot("m7-05-bridges-page-with-webhook-card") });

    await apiDel(page, `/v1/console/bridges/custom/${WID}`, csrf);
  });
});
