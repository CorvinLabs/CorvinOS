/**
 * Tasks panel — LIVE E2E against the running console (Task-Tracking SSOT).
 *
 *   CONSOLE_BASE_URL=http://127.0.0.1:8765/console \
 *   SHOTS_DIR=/tmp/shots npx playwright test -c playwright.live.config.ts
 *
 * Proves the whole path browser → /v1/console/task-tracking → SQLite store:
 *   1. every view renders what the API returns for this tenant (no fixtures);
 *   2. a change made from a SECOND session through the real API appears in the
 *      open page within a poll, without a reload;
 *   3. the drawer's edit goes through the real PATCH (version + CSRF).
 * Mutations touch one existing item's assignee and are reverted in `finally`
 * (the audit chain keeps both records — it is append-only by design).
 * Optional screenshots (light + dark, every view) go to $SHOTS_DIR.
 */
import { test, expect, request as pwRequest, type APIRequestContext, type Page } from "@playwright/test";

const ORIGIN = (process.env.CONSOLE_BASE_URL || "http://127.0.0.1:8765/console").replace(/\/console\/?$/, "");
const PAGE_URL = `${ORIGIN}/console/app/initiatives`;
const SHOTS = process.env.SHOTS_DIR;

test.describe.configure({ mode: "serial" });
test.use({ storageState: { cookies: [], origins: [] }, viewport: { width: 1440, height: 900 } });
test.setTimeout(3 * 60_000);

async function apiSession(): Promise<{ api: APIRequestContext; csrf: string }> {
  const api = await pwRequest.newContext({ baseURL: ORIGIN });
  await api.get("/v1/console/auth/local-login");
  const who = await (await api.get("/v1/console/auth/whoami")).json();
  return { api, csrf: who.csrf_token };
}

async function open(page: Page, query = "") {
  await page.goto(`${ORIGIN}/v1/console/auth/local-login`);
  await page.goto(`${PAGE_URL}${query}`);
  await expect(page.getByTestId("tasks-page")).toBeVisible({ timeout: 30_000 });
}

async function shoot(page: Page, name: string) {
  if (!SHOTS) return;
  for (const theme of ["light", "dark"]) {
    await page.evaluate((t) => document.documentElement.setAttribute("data-theme", t), theme);
    await page.waitForTimeout(150);
    await page.screenshot({ path: `${SHOTS}/${name}-${theme}.png`, fullPage: true });
  }
}

test("every view renders the tenant's SSOT items", async ({ page }) => {
  const { api } = await apiSession();
  const body = await (await api.get("/v1/console/task-tracking/items")).json();
  expect(body.items.length, "the live store must hold items (import ran)").toBeGreaterThan(0);
  const root = body.items.find((i: { kind: string; parent_id: string | null }) => i.kind === "initiative" && !i.parent_id);

  await open(page);
  await expect(page.getByTestId("tree-view")).toBeVisible();
  await expect(page.getByTestId(`tree-row-${root.id}`)).toContainText(root.title);
  await expect(page.getByTestId("kpi-open")).toContainText(String(body.summary.open));
  await shoot(page, "tree");

  for (const v of ["board", "timeline", "table", "activity"] as const) {
    await page.getByTestId(`view-${v}`).click();
    await expect(page.getByTestId(`${v}-view`)).toBeVisible({ timeout: 30_000 });
    await shoot(page, v);
  }

  await page.getByTestId("view-tree").click();
  await page.getByTestId(`tree-row-${root.id}`).click();
  await expect(page.getByTestId("detail-drawer").getByLabel("Title")).toHaveValue(root.title);
  await expect(page.getByTestId("detail-drawer")).toContainText("History");
  await shoot(page, "drawer");
  await api.dispose();
});

test("a change from another session appears without a reload; the drawer edits through the real API", async ({ page }) => {
  const { api, csrf } = await apiSession();
  const items = (await (await api.get("/v1/console/task-tracking/items")).json()).items;
  const target = items.find((i: { kind: string; deleted_at: string | null }) => i.kind === "task" && !i.deleted_at);
  const original: string | null = target.assignee;
  const marker = `e2e ${Date.now() % 100000}`;
  let navigations = 0;
  try {
    await open(page, `?item=${target.id}`);
    page.on("framenavigated", (f) => { if (f === page.mainFrame()) navigations++; });
    const drawer = page.getByTestId("detail-drawer");
    await expect(drawer).toBeVisible();

    // 2. second session writes through the real API
    const cur = await (await api.get(`/v1/console/task-tracking/items/${target.id}`)).json();
    const r = await api.patch(`/v1/console/task-tracking/items/${target.id}`,
      { data: { version: cur.item.version, assignee: marker }, headers: { "X-CSRF-Token": csrf } });
    expect(r.status()).toBe(200);
    await expect(drawer.getByLabel("Assignee")).toHaveValue(marker, { timeout: 20_000 });

    // 3. the drawer writes back through PATCH
    const assignee = drawer.getByLabel("Assignee");
    await assignee.fill(original ?? "");
    const patched = page.waitForResponse((res) => res.url().includes(`/task-tracking/items/${target.id}`) && res.request().method() === "PATCH");
    await assignee.blur();
    const res = await patched;
    expect(res.status()).toBe(200);
    expect(res.request().headers()["x-csrf-token"]).toBeTruthy();
    expect(navigations).toBe(0);
  } finally {
    const cur = await (await api.get(`/v1/console/task-tracking/items/${target.id}`)).json();
    if (cur.item.assignee !== original) {
      await api.patch(`/v1/console/task-tracking/items/${target.id}`,
        { data: { version: cur.item.version, assignee: original }, headers: { "X-CSRF-Token": csrf } });
    }
    await api.dispose();
  }
});
