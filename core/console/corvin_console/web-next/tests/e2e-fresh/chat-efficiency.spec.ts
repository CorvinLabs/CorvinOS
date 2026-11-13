/**
 * Per-chat cost / token-efficiency dashboard, end to end in a real browser (ADR-2324).
 *
 *   seeded turn log (written by the real chat_runtime._append_turn)
 *     -> GET /v1/console/chat/sessions/{sid}/efficiency  (real backend, real rate card)
 *     -> the collapsed "Cost & efficiency" strip in the chat, opened by a click
 *
 * Needs a console whose CORVIN_HOME contains the chat created by
 * fixtures/chat_efficiency_seed.py; pass its sid and the origin:
 *
 *   CORVIN_EFF_SID=<sid> CORVIN_EFF_BASE=http://127.0.0.1:8799 \
 *   npx playwright test -c playwright.chat-efficiency.config.ts
 *
 * Token and line totals are asserted against the seed's own counts; the dollar figure is only
 * asserted to be positive here - the exact pricing is pinned by the backend suite
 * (core/console/tests/test_chat_efficiency_e2e.py) against the rate card.
 */
import { expect, test } from "@playwright/test";

const SID = process.env.CORVIN_EFF_SID ?? "";
const API = "/v1/console";

test.beforeAll(() => {
  if (!SID) throw new Error("CORVIN_EFF_SID is required (run fixtures/chat_efficiency_seed.py in the console's CORVIN_HOME)");
});

test.beforeEach(async ({ page }) => {
  const r = await page.request.get(`${API}/auth/local-login`, { maxRedirects: 0 });
  expect([200, 302]).toContain(r.status());
  await expect.poll(async () => (await page.request.get(`${API}/auth/whoami`)).status(), { timeout: 20_000 }).toBe(200);
});

test("API: figures follow the rate card and the unknown-is-not-zero rules", async ({ page }) => {
  const d = await (await page.request.get(`${API}/chat/sessions/${SID}/efficiency`)).json();
  expect(d.coverage).toMatchObject({ turns: 5, measured: 4, priced: 3, unpriced: 1, without_usage: 1 });
  expect(d.unpriced_models).toEqual(["mystery-model-9"]);
  expect(d.tokens.output).toBe(900 + 4200 + 9000 + 100);
  expect(d.code).toMatchObject({ lines_added: 42 + 120, lines_removed: 7, lines_lower_bound: true });
  expect(d.cost.total_usd).toBeGreaterThan(0);
  expect(d.includes_delegated_workers).toBe(false);
});

test("UI: collapsed by default, opens to real tiles, caveats and a chart", async ({ page }) => {
  const seen: string[] = [];
  page.on("request", (rq) => { if (rq.url().includes("/efficiency")) seen.push(rq.url()); });
  await page.goto(`/console/app/chat/${SID}`);
  const toggle = page.getByTestId("chat-efficiency-toggle");
  await expect(toggle).toBeVisible();
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
  await expect(page.getByTestId("chat-efficiency-body")).toHaveCount(0);
  await page.waitForTimeout(800);
  expect(seen, "a closed panel must not fetch").toHaveLength(0);

  await toggle.click();
  await expect(page.getByTestId("chat-efficiency-tiles")).toBeVisible();
  expect(seen.length).toBeGreaterThanOrEqual(1);
  await expect(page.getByTestId("eff-total-cost-value")).toHaveText(/^\$\d/);
  await expect(page.getByTestId("eff-lines-value")).toHaveText("≥ 169");   // truncated diff => lower bound
  await expect(page.getByTestId("eff-total-cost")).toContainText("3 of 5 turns priced");
  await expect(page.getByTestId("eff-cache-hit-value")).toHaveText(/%$/);
  const notes = page.getByTestId("chat-efficiency-notes");
  await expect(notes).toContainText("mystery-model-9");
  await expect(notes).toContainText("no complete token usage");
  await expect(notes).toContainText("lower bound");
  await expect(page.getByTestId("chat-efficiency-chart")).toBeVisible();
  await expect(page.getByTestId("chat-efficiency-models")).toContainText("claude-opus-5");

  await page.screenshot({ path: process.env.CORVIN_EFF_SHOT ?? "chat-efficiency.png", fullPage: false });

  // The conversation must stay usable: the open panel may not take more than ~40% of the viewport.
  const box = await page.getByTestId("chat-efficiency-body").boundingBox();
  expect(box!.height).toBeLessThanOrEqual(page.viewportSize()!.height * 0.4 + 1);
  if (process.env.CORVIN_EFF_SHOT_LIGHT) {
    await page.evaluate(() => document.documentElement.setAttribute("data-theme", "light"));
    await page.screenshot({ path: process.env.CORVIN_EFF_SHOT_LIGHT });
  }

  await toggle.click();   // way back: closes again
  await expect(page.getByTestId("chat-efficiency-body")).toHaveCount(0);
});

test("UI: a chat without replies says so instead of showing zeros", async ({ page }) => {
  const created = await page.request.post(`${API}/chat/sessions`, {
    data: { title: "empty" }, headers: { "x-csrf-token": (await (await page.request.get(`${API}/auth/whoami`)).json()).csrf_token },
  });
  expect(created.ok()).toBeTruthy();
  const sid = (await created.json()).session.sid as string;
  await page.goto(`/console/app/chat/${sid}`);
  await page.getByTestId("chat-efficiency-toggle").click();
  await expect(page.getByTestId("chat-efficiency-empty")).toBeVisible();
  await expect(page.getByTestId("chat-efficiency-tiles")).toHaveCount(0);
});
