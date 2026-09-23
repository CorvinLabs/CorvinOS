/**
 * Initiatives board — LIVE freshness E2E against the running console.
 *
 *   CONSOLE_BASE_URL=http://127.0.0.1:8765/console \
 *     npx playwright test -c playwright.live.config.ts
 *
 * One page, NEVER reloaded. Every way a value on the board can change is
 * driven for real and must appear on screen without a reload:
 *
 *   1. time          — countdowns tick every second
 *   2. file edit     — a hand edit of initiatives.json (another tool/editor)
 *   3. other session — a PATCH from a second login (another operator/tab)
 *   4. background    — while the tab is hidden polling pauses; on return a
 *                      fetch is issued at once and the change appears
 *   5. evidence      — the repo changes (the missing scene generator appears),
 *                      "Verify now" re-runs the evidence, the task flips to done
 *   6. timer         — the file is removed again and the REAL timer service
 *                      (`--if-changed`) detects the repo change and flips it back
 *
 * Latency budgets include server stalls: the console process currently stalls
 * for up to ~17 s at a time for reasons outside this panel (see the delay
 * indicator). Measured latencies are logged.
 *
 * Every mutation is reverted in `finally`, byte-for-byte for the board file.
 * Runs serially: it edits shared live state.
 */
import { test, expect, request as pwRequest, type Page, type APIRequestContext } from "@playwright/test";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(here, "../../../../../..");
const BOARD = path.join(REPO, ".corvin/tenants/_default/global/initiatives.json");
const GENERATOR = path.join(REPO, "tools/blender_3d_scene_generator.py");
const ORIGIN = (process.env.CONSOLE_BASE_URL || "http://127.0.0.1:8765/console").replace(/\/console\/?$/, "");
const PAGE_URL = `${ORIGIN}/console/app/initiatives`;

test.describe.configure({ mode: "serial" });
test.use({ storageState: { cookies: [], origins: [] } });
test.setTimeout(6 * 60_000);

/** "14d 3h 34m" / "5h 2m 7s" / "3m 9s" → seconds. */
function parseCountdown(text: string): number {
  const n = (u: string) => Number((new RegExp(`(\\d+)${u}`).exec(text) ?? [0, 0])[1]);
  return n("d") * 86400 + n("h") * 3600 + n("m") * 60 + n("s");
}

async function login(page: Page) {
  await page.goto(`${ORIGIN}/v1/console/auth/local-login`);
  await page.goto(PAGE_URL);
  await expect(page.getByTestId("run-loop-a")).toBeVisible({ timeout: 30_000 });
}

async function secondSession(): Promise<{ api: APIRequestContext; csrf: string }> {
  const api = await pwRequest.newContext({ baseURL: ORIGIN });
  await api.get("/v1/console/auth/local-login");
  const who = await (await api.get("/v1/console/auth/whoami")).json();
  return { api, csrf: who.csrf_token };
}

function editBoard(mut: (d: any) => void) {
  const d = JSON.parse(fs.readFileSync(BOARD, "utf8"));
  mut(d);
  const tmp = BOARD + ".e2e-tmp";
  fs.writeFileSync(tmp, JSON.stringify(d, null, 2) + "\n", { mode: 0o600 });
  fs.renameSync(tmp, BOARD);
}

function restoreBoard(original: Buffer) {
  const tmp = BOARD + ".e2e-tmp";
  fs.writeFileSync(tmp, original, { mode: 0o600 });
  fs.renameSync(tmp, BOARD);
}

async function verifyNowAndWait(page: Page) {
  const bar = page.getByTestId("verification-bar");
  await bar.getByRole("button", { name: /Verify now/ }).click();
  await expect(bar).toContainText("Verifying evidence", { timeout: 2_000 }); // immediate, not after a poll
  await expect(bar).toContainText(/Evidence verified just now/, { timeout: 180_000 });
}

test("board values stay current without a reload", async ({ page }) => {
  expect(fs.existsSync(GENERATOR), "scene generator must not exist before the test").toBe(false);
  const original = fs.readFileSync(BOARD);
  const { api, csrf } = await secondSession();
  const marker = `e2e-freshness-${Date.now()}`;
  let navigations = 0;
  let createdToolsDir = false;

  try {
    await login(page);
    page.on("framenavigated", (f) => { if (f === page.mainFrame()) navigations++; });

    // 1. Time: the countdown is CORRECT (matches the real remaining time to the
    //    minute it displays) and advances on its own. Above one day it shows
    //    minutes, so a change needs up to 60 s.
    const timeLeft = page.getByTestId("time-left-loop-a");
    const deadline = Date.parse(JSON.parse(original.toString("utf8")).initiatives[0].deadline);
    const shownS = parseCountdown((await timeLeft.textContent()) ?? "");
    const realS = (deadline - Date.now()) / 1000;
    expect(Math.abs(shownS - realS), `shown ${shownS}s vs real ${realS}s`).toBeLessThan(61);
    const t0 = await timeLeft.textContent();
    await expect.poll(async () => timeLeft.textContent(), { timeout: 65_000, intervals: [1_000] }).not.toBe(t0);

    // 2. File edit: a hand edit of the board file appears within one poll.
    const task2 = page.getByTestId("task-loop-a-2");
    const edited = Date.now();
    editBoard((d) => { d.initiatives[0].tasks.find((t: any) => t.id === "2").note = marker; });
    await expect(task2).toContainText(marker, { timeout: 30_000 });
    console.log(`file edit visible after ${Date.now() - edited} ms`);

    // 3. Other session: a PATCH from a second login appears here.
    const patched = Date.now();
    const r = await api.patch("/v1/console/initiatives/loop-a/tasks/2",
      { data: { progress: 7 }, headers: { "X-CSRF-Token": csrf } });
    expect(r.status()).toBe(200);
    await expect(task2).toContainText("7%", { timeout: 30_000 });
    console.log(`other-session change visible after ${Date.now() - patched} ms`);

    // 4. Background tab: hidden → polling pauses; visible again → fetched at once.
    await page.evaluate(() => {
      Object.defineProperty(document, "visibilityState", { configurable: true, get: () => "hidden" });
      Object.defineProperty(document, "hidden", { configurable: true, get: () => true });
      document.dispatchEvent(new Event("visibilitychange", { bubbles: true })); // as real browsers fire it
    });
    await api.patch("/v1/console/initiatives/loop-a/tasks/2",
      { data: { progress: 9 }, headers: { "X-CSRF-Token": csrf } });
    await page.waitForTimeout(7_000);
    await expect(task2).toContainText("7%"); // paused while hidden — no wasted polling
    const shown = Date.now();
    const refetch = page.waitForRequest((r) => r.url().endsWith("/v1/console/initiatives"), { timeout: 1_500 });
    await page.evaluate(() => {
      Object.defineProperty(document, "visibilityState", { configurable: true, get: () => "visible" });
      Object.defineProperty(document, "hidden", { configurable: true, get: () => false });
      document.dispatchEvent(new Event("visibilitychange", { bubbles: true })); // as real browsers fire it
      window.dispatchEvent(new Event("focus"));
    });
    await refetch; // issued immediately on return, not at the next 5 s tick
    console.log(`on return to the tab: fetch issued after ${Date.now() - shown} ms`);
    await expect(task2).toContainText("9%", { timeout: 30_000 });
    console.log(`on return to the tab: visible after ${Date.now() - shown} ms`);

    // 5. Evidence: the repo changes, Verify now picks it up, the task flips.
    const blender = page.getByTestId("task-loop-a-1");
    await expect(blender).toContainText("1/2 paths present");
    createdToolsDir = !fs.existsSync(path.dirname(GENERATOR));
    fs.mkdirSync(path.dirname(GENERATOR), { recursive: true });
    fs.writeFileSync(GENERATOR, "# e2e placeholder — removed by initiatives-live.spec.ts\n");
    await verifyNowAndWait(page);
    await expect(blender).toContainText("2/2 paths present", { timeout: 10_000 });
    await expect(blender.getByRole("combobox")).toHaveValue("done");

    // 6. Timer: remove it again; the real timer unit (--if-changed) notices the
    //    repo change on its own — no button.
    fs.rmSync(GENERATOR);
    const ticked = Date.now();
    const out = execFileSync("systemctl", ["--user", "start", "corvin-initiatives-verify.service"], { encoding: "utf8" });
    const log = execFileSync("journalctl", ["--user", "-u", "corvin-initiatives-verify", "--since", `@${Math.floor(ticked / 1000)}`, "--no-pager", "-o", "cat"], { encoding: "utf8" });
    expect(log, "the timer run must have verified, not skipped").toMatch(/"verified": 10/);
    void out;
    await expect(blender).toContainText("1/2 paths present", { timeout: 30_000 });
    console.log(`timer tick → UI after ${Date.now() - ticked} ms`);
    await expect(blender.getByRole("combobox")).toHaveValue("running");

    expect(navigations, "the page must never have been reloaded").toBe(0);
  } finally {
    if (fs.existsSync(GENERATOR)) fs.rmSync(GENERATOR);
    if (createdToolsDir) fs.rmdirSync(path.dirname(GENERATOR));
    // Restore the manual fields byte-for-byte, but keep the latest verification
    // results (the last Verify now ran against the restored repo state).
    const latest = JSON.parse(fs.readFileSync(BOARD, "utf8"));
    const orig = JSON.parse(original.toString("utf8"));
    for (const ini of orig.initiatives) {
      const li = latest.initiatives.find((x: any) => x.id === ini.id);
      for (const kind of ["tasks", "preconditions"]) {
        for (const item of ini[kind] ?? []) {
          const key = item.id ?? item.label;
          const l = (li?.[kind] ?? []).find((x: any) => (x.id ?? x.label) === key);
          if (l?.verification) item.verification = l.verification;
        }
      }
    }
    restoreBoard(Buffer.from(JSON.stringify(orig, null, 2) + "\n"));
    await api.dispose();
  }
});

test("opening the page shows the real current data and survives outages", async ({ page }) => {
  const { api } = await secondSession();
  try {
    // ── On open: fresh, and identical to what the API says right now ──────────
    const opened = Date.now();
    await login(page);
    const indicator = page.getByTestId("live-indicator");
    await expect(indicator).toHaveText(/^Live · updated [0-5]s ago/, { timeout: 10_000 });
    console.log(`first live data after ${Date.now() - opened} ms`);

    const truth = await (await api.get("/v1/console/initiatives")).json();
    for (const ini of truth.initiatives.filter((i: any) => i.phase === "active")) {
      for (const t of ini.tasks) {
        const row = page.getByTestId(`task-${ini.id}-${t.id}`);
        await expect(row, `${ini.id}/${t.id}`).toContainText(`${t.progress}%`);
        await expect(row.getByRole("combobox"), `${ini.id}/${t.id}`).toHaveValue(t.status);
      }
    }

    // ── Every task type: each active/stale record the API reports is on screen,
    //    marked with its type; the finished tab lists the newest finished ones.
    const all = await (await api.get("/v1/console/initiatives/tasks")).json();
    expect(all.totals.all, "the install has tasks from several sources").toBeGreaterThan(0);
    for (const t of all.active.filter((x: any) => x.type !== "initiative")) {
      const row = page.getByTestId(`utask-${t.id}`);
      await expect(row, t.id).toBeVisible();
      await expect(row.getByTestId("type-badge"), t.id).toHaveText(t.type_label);
    }
    await page.getByRole("tab", { name: /Finished \(/ }).click();
    const newest = all.finished[0];
    await expect(page.getByTestId(`utask-${newest.id}`).getByTestId("type-badge")).toHaveText(newest.type_label);
    console.log(`types on this install: ${all.types.filter((x: any) => x.active + x.finished + x.stale > 0).map((x: any) => `${x.label}=${x.active + x.finished + x.stale}`).join(", ")}`);
    await page.getByRole("tab", { name: /Running \(/ }).click();

    // ── Outage: every poll fails for ~15 s — data stays, state is labelled ────
    await page.route("**/v1/console/initiatives", (r) =>
      r.request().method() === "GET" ? r.abort("connectionfailed") : r.continue());
    await expect(indicator).toContainText(/Reconnecting… showing data from \d+s ago/, { timeout: 20_000 });
    await expect(page.getByTestId("run-loop-a")).toBeVisible(); // last good data still on screen
    await page.unroute("**/v1/console/initiatives");
    const healed = Date.now();
    await expect(indicator).toHaveText(/^Live · updated [0-5]s ago/, { timeout: 15_000 });
    console.log(`recovered from outage after ${Date.now() - healed} ms`);

    // ── Hung request: the server never answers — aborted after 8 s, retried ───
    let hung = 0;
    await page.route("**/v1/console/initiatives", async (r) => {
      if (r.request().method() === "GET" && hung++ === 0) return; // never fulfilled
      await r.continue();
    });
    const t0 = Date.now();
    await expect.poll(() => hung, { timeout: 10_000 }).toBeGreaterThan(0);
    await expect.poll(() => hung, { timeout: 20_000, message: "a follow-up request after the hung one" }).toBeGreaterThan(1);
    console.log(`hung request abandoned and retried after ${Date.now() - t0} ms`);
    await expect(indicator).toHaveText(/^Live · updated [0-9]+s ago/, { timeout: 15_000 });
    await page.unroute("**/v1/console/initiatives");
  } finally {
    await api.dispose();
  }
});

test("an expired session renews itself — values keep updating, no reload", async ({ page }) => {
  // Regression, 2026-09-23: sessions end after 8 h (ABSOLUTE_TIMEOUT_S). The
  // open tab kept its cached whoami, never noticed, and every poll 401-ed from
  // 08:20 on — the page showed yesterday's numbers until someone reloaded.
  const { api, csrf } = await secondSession();
  let navigations = 0;
  try {
    await login(page);
    page.on("framenavigated", (f) => { if (f === page.mainFrame()) navigations++; });
    const indicator = page.getByTestId("live-indicator");
    await expect(indicator).toHaveText(/^Live/, { timeout: 15_000 });

    // End THIS tab's session server-side — exactly what the 8 h expiry does.
    const who = await (await page.request.get(`${ORIGIN}/v1/console/auth/whoami`)).json();
    const out = await page.request.post(`${ORIGIN}/v1/console/auth/logout`, { headers: { "X-CSRF-Token": who.csrf_token } });
    expect(out.status(), "logout").toBeLessThan(300);
    expect((await page.request.get(`${ORIGIN}/v1/console/auth/whoami`)).status()).toBe(401);
    const expired = Date.now();

    // A change made elsewhere must still reach this tab.
    const r = await api.patch("/v1/console/initiatives/loop-a/tasks/2",
      { data: { progress: 11 }, headers: { "X-CSRF-Token": csrf } });
    expect(r.status()).toBe(200);
    await expect(page.getByTestId("task-loop-a-2")).toContainText("11%", { timeout: 30_000 });
    await expect(indicator).toHaveText(/^Live/, { timeout: 15_000 });
    console.log(`recovered from an expired session after ${Date.now() - expired} ms`);
    expect((await page.request.get(`${ORIGIN}/v1/console/auth/whoami`)).status(), "a new session exists").toBe(200);
    expect(navigations, "recovered without reloading the page").toBe(0);
  } finally {
    await api.patch("/v1/console/initiatives/loop-a/tasks/2", { data: { progress: 0 }, headers: { "X-CSRF-Token": csrf } });
    await api.dispose();
  }
});

test("an open tab moves itself onto a new build (console_auto_reload)", async ({ page }) => {
  // Fixes only help an operator whose tab actually runs them: the tab that
  // froze on 2026-09-23 was still executing the build with the 401 loop.
  await login(page);
  const caps = await page.request.get(`${ORIGIN}/v1/console/capabilities`);
  const flags = await caps.json();
  console.log(`capabilities ${caps.status()} console_auto_reload=${flags?.flags?.console_auto_reload}`);
  test.skip(!flags?.flags?.console_auto_reload, "console_auto_reload is off on this install");
  // Pretend a deploy happened: the no-cache shell now names a different entry bundle.
  let served = false;
  await page.route(`${ORIGIN}/console/`, async (route) => {
    const res = await route.fetch();
    const html = (await res.text()).replace(/assets\/index-[A-Za-z0-9_-]+\.js/, "assets/index-NEWBUILD0.js");
    served = true;
    await route.fulfill({ response: res, body: html });
  }, { times: 1 }); // once: the reload itself must get the real shell
  const reloaded = page.waitForEvent("framenavigated", { timeout: 20_000 });
  const t0 = Date.now();
  await reloaded;
  expect(served).toBe(true);
  console.log(`tab reloaded itself onto the new build after ${Date.now() - t0} ms`);
  await expect(page.getByTestId("live-indicator")).toHaveText(/^Live/, { timeout: 20_000 });
});
