/**
 * E2E proof: Data Sources page — add PostgreSQL, MySQL, SQLite connections
 * via UI button clicks (Connect Database wizard) and verify delete.
 *
 * Infrastructure required:
 *   - FastAPI console at http://localhost:8765  (running via bridge.sh console)
 *   - Vite dev server at http://localhost:5173  (proxy → 8765)
 *   - Mock Instance Agent at http://127.0.0.1:8766 (for BYOK vault store)
 *   - PostgreSQL test DB: localhost:5433 / spotify_dsi / corvin (trust auth)
 *   - MySQL test DB:      127.0.0.1:3307 / spotify_dsi / corvin:corvin_test
 *   - SQLite file: ~/projects/claude-playground/spotify-dsi-test/spotify_charts.db
 */

import { test, expect, BrowserContext, Page } from "@playwright/test";
import path from "path";
import os from "os";

const BASE_URL = "http://localhost:5173/console";
const API_BASE  = "http://localhost:8765/v1/console";

// Screenshots go directly to the session outputs directory
const OUTDIR = path.join(
  os.homedir(),
  "projects/CorvinOS/.corvin/tenants/_default/sessions/voice/discord/1502103856740302964/outputs",
);

const SQLITE_PATH = path.join(
  os.homedir(),
  "projects/claude-playground/spotify-dsi-test/spotify_charts.db",
);

const PG_CONN     = "test-pg-spotify";
const MYSQL_CONN  = "test-mysql-spotify";
const SQLITE_CONN = "test-sqlite-spotify";

// ── Auth helpers ──────────────────────────────────────────────────────────────

test.describe.configure({ mode: "serial" });

let ctx: BrowserContext;
let page: Page;

// License-tier flags — set in beforeAll; some adapters require a paid tier.
let _pgAvailable = true;
let _mysqlAvailable = true;

async function login(_context: BrowserContext): Promise<void> {
  // Session is provided via storageState; nothing to do here.
}

async function deleteConn(context: BrowserContext, name: string): Promise<void> {
  const whoami = await context.request.get(`${API_BASE}/auth/whoami`);
  const csrf = (await whoami.json()).csrf_token as string;
  await context.request.delete(`${API_BASE}/data-sources/${name}`, {
    headers: { "X-CSRF-Token": csrf },
  });
}

// ── Wizard helper ─────────────────────────────────────────────────────────────

async function addConnection(
  p: Page,
  opts: {
    dbTypeId: string;
    connName: string;
    configFields?: Record<string, string>;
    credentials?: Array<{ envVar: string; value: string }>;
  },
): Promise<void> {
  // 1. Open wizard
  await p.click('[data-testid="connect-database-btn"]');
  await p.waitForTimeout(500);

  // 2. Select DB type
  await p.click(`[data-testid="db-type-${opts.dbTypeId}"]`);
  // Wait for the connection form to appear after DB type selection (may take a moment)
  await p.waitForSelector('[data-testid="conn-name-input"]', { timeout: 15000 });

  // 3. Fill connection name
  await p.fill('[data-testid="conn-name-input"]', opts.connName);

  // 4. Fill config fields (host, port, database, ssl_mode, path, format…)
  for (const [key, value] of Object.entries(opts.configFields ?? {})) {
    const sel = `[data-testid="config-${key}"]`;
    const el  = p.locator(sel);
    const tag = await el.evaluate((n) => n.tagName.toLowerCase()).catch(() => "input");
    if (tag === "select") {
      await el.selectOption(value);
    } else {
      await el.fill(value);
    }
  }

  // 5. Fill credentials
  for (const { envVar, value } of opts.credentials ?? []) {
    await p.fill(`[data-testid="cred-${envVar}"]`, value);
  }

  // 6. Submit
  const submitBtn = p.locator('[data-testid="conn-submit-btn"]');
  await expect(submitBtn).toBeEnabled({ timeout: 5000 });
  await submitBtn.click();

  // 7. Wait for wizard to close (dialog disappears on success)
  await p.waitForTimeout(3500);

  // 8. Assert no form-level error
  const errEl = p.locator("form p.text-destructive");
  const hasErr = await errEl.isVisible().catch(() => false);
  if (hasErr) {
    throw new Error(`Wizard error for "${opts.connName}": ${await errEl.textContent()}`);
  }
}

// ── Suite ─────────────────────────────────────────────────────────────────────

test.describe("Data Sources — wizard add + delete via UI buttons", () => {

  test.beforeAll(async ({ browser }) => {
    ctx  = await browser.newContext({ storageState: "./tests/e2e/auth-state.json" });
    await login(ctx);

    // Check which adapters the current license tier allows.
    // On free tier only "local_file" is in datasource_adapters_allowed.
    try {
      const licResp = await ctx.request.get(`${API_BASE}/license/info`);
      if (licResp.ok()) {
        const lic = await licResp.json() as Record<string, unknown>;
        // datasource_adapters_allowed is nested under lic.limits (not top-level)
        const limits = lic.limits as Record<string, unknown> | undefined;
        const allowed = (limits?.datasource_adapters_allowed as string[] | undefined) ?? null;
        if (allowed !== null) {
          _pgAvailable    = allowed.includes("postgresql");
          _mysqlAvailable = allowed.includes("mysql");
        }
      }
    } catch { /* keep defaults (true) so tests run if check is unavailable */ }

    console.log(`License check — pg: ${_pgAvailable}, mysql: ${_mysqlAvailable}`);

    // Purge any leftover test connections
    for (const n of [PG_CONN, MYSQL_CONN, SQLITE_CONN]) {
      await deleteConn(ctx, n).catch(() => {});
    }

    page = await ctx.newPage();
    // Accept the native window.confirm() shown by the delete-connection trash
    // icon. Registered here in beforeAll (not inside a test step) so it is
    // always active even when the PG/MySQL delete steps skip on a free license
    // tier — otherwise the SQLite delete step's confirm() is auto-dismissed and
    // the row never disappears.
    page.on("dialog", (d) => d.accept());
    await page.goto(`${BASE_URL}/app/data-sources`, { waitUntil: "load" });
    await page.waitForTimeout(1800);
  });

  test.afterAll(async () => {
    await ctx?.close();
  });

  // ── 1. Add PostgreSQL ─────────────────────────────────────────────────────

  test("1. Add PostgreSQL via wizard", async () => {
    if (!_pgAvailable) { test.skip(true, "PostgreSQL adapter locked on this license tier"); return; }
    await addConnection(page, {
      dbTypeId: "postgresql",
      connName: PG_CONN,
      configFields: {
        host:     "localhost",
        port:     "5433",
        database: "spotify_dsi",
        ssl_mode: "disable",
      },
      credentials: [
        { envVar: "PGUSER",     value: "corvin" },
        { envVar: "PGPASSWORD", value: "trust-no-pw" },
      ],
    });

    await page.screenshot({ path: `${OUTDIR}/ds-01-pg-added.png` });
  });

  test("2. PostgreSQL row appears in list", async () => {
    if (!_pgAvailable) { test.skip(true, "PostgreSQL adapter locked on this license tier"); return; }
    await page.goto(`${BASE_URL}/app/data-sources`, { waitUntil: "load" });
    await page.waitForTimeout(1500);

    await expect(page.locator(`[data-testid="conn-row-${PG_CONN}"]`))
      .toBeVisible({ timeout: 8000 });

    await page.screenshot({ path: `${OUTDIR}/ds-02-pg-in-list.png` });
  });

  // ── 2. Add MySQL ──────────────────────────────────────────────────────────

  test("3. Add MySQL via wizard", async () => {
    if (!_mysqlAvailable) { test.skip(true, "MySQL adapter locked on this license tier"); return; }
    await addConnection(page, {
      dbTypeId: "mysql",
      connName: MYSQL_CONN,
      configFields: {
        host:     "127.0.0.1",
        port:     "3307",
        database: "spotify_dsi",
        ssl:      "false",
      },
      credentials: [
        { envVar: "MYSQL_USER",     value: "corvin" },
        { envVar: "MYSQL_PASSWORD", value: "corvin_test" },
      ],
    });

    await page.screenshot({ path: `${OUTDIR}/ds-03-mysql-added.png` });
  });

  test("4. MySQL row appears in list", async () => {
    if (!_mysqlAvailable) { test.skip(true, "MySQL adapter locked on this license tier"); return; }
    await page.goto(`${BASE_URL}/app/data-sources`, { waitUntil: "load" });
    await page.waitForTimeout(1500);

    await expect(page.locator(`[data-testid="conn-row-${MYSQL_CONN}"]`))
      .toBeVisible({ timeout: 8000 });

    await page.screenshot({ path: `${OUTDIR}/ds-04-mysql-in-list.png` });
  });

  // ── 3. Add SQLite (Local File) ────────────────────────────────────────────

  test("5. Add SQLite (Local File) via wizard", async () => {
    await addConnection(page, {
      dbTypeId: "local_file",
      connName: SQLITE_CONN,
      configFields: {
        path:   SQLITE_PATH,
        format: "auto",
      },
    });

    await page.screenshot({ path: `${OUTDIR}/ds-05-sqlite-added.png` });
  });

  test("6. All available connections visible in list", async () => {
    await page.goto(`${BASE_URL}/app/data-sources`, { waitUntil: "load" });
    await page.waitForTimeout(1500);

    // Only check connections that are allowed by the current license tier.
    const toCheck = [
      ...(_pgAvailable    ? [PG_CONN]    : []),
      ...(_mysqlAvailable ? [MYSQL_CONN] : []),
      SQLITE_CONN,
    ];
    for (const name of toCheck) {
      await expect(page.locator(`[data-testid="conn-row-${name}"]`))
        .toBeVisible({ timeout: 8000 });
    }

    await page.screenshot({ path: `${OUTDIR}/ds-06-connections.png`, fullPage: true });
  });

  // ── 4. Delete all added connections ──────────────────────────────────────

  test("7. Delete PostgreSQL via trash icon", async () => {
    if (!_pgAvailable) { test.skip(true, "PostgreSQL adapter locked on this license tier"); return; }

    await page.locator(`[data-testid="delete-conn-${PG_CONN}"]`).click();
    await page.waitForTimeout(2000);

    await expect(page.locator(`[data-testid="conn-row-${PG_CONN}"]`))
      .not.toBeVisible({ timeout: 6000 });

    await page.screenshot({ path: `${OUTDIR}/ds-07-pg-deleted.png` });
  });

  test("8. Delete MySQL via trash icon", async () => {
    if (!_mysqlAvailable) { test.skip(true, "MySQL adapter locked on this license tier"); return; }
    await page.locator(`[data-testid="delete-conn-${MYSQL_CONN}"]`).click();
    await page.waitForTimeout(2000);

    await expect(page.locator(`[data-testid="conn-row-${MYSQL_CONN}"]`))
      .not.toBeVisible({ timeout: 6000 });

    await page.screenshot({ path: `${OUTDIR}/ds-08-mysql-deleted.png` });
  });

  test("9. Delete SQLite via trash icon", async () => {
    await page.locator(`[data-testid="delete-conn-${SQLITE_CONN}"]`).click();
    await page.waitForTimeout(2000);

    await expect(page.locator(`[data-testid="conn-row-${SQLITE_CONN}"]`))
      .not.toBeVisible({ timeout: 6000 });

    await page.screenshot({ path: `${OUTDIR}/ds-09-sqlite-deleted.png` });
  });

  test("10. Final state — no test connections in list", async () => {
    await page.goto(`${BASE_URL}/app/data-sources`, { waitUntil: "load" });
    await page.waitForTimeout(1500);

    for (const name of [PG_CONN, MYSQL_CONN, SQLITE_CONN]) {
      await expect(page.locator(`[data-testid="conn-row-${name}"]`))
        .not.toBeVisible();
    }

    await page.screenshot({ path: `${OUTDIR}/ds-10-final-empty.png`, fullPage: true });
  });
});
