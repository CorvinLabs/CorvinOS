/**
 * E2E: Knowledge Graph explorer (CONCEPT-0099 / ADR-2237) against the REAL console and the
 * real Corvin-Knowledge checkout (read-only: nothing here writes config, sync or the KB).
 *
 *  1. The plugin panel is listed in the sidebar's Marketplace group (a manifest entry)
 *  2. /app/corvin-knowledge opens with a document already shown (newest decision) and a graph
 *  3. The graph canvas is real and zoomable (the mouse wheel changes the view scale)
 *  4. Opening a node by search shows its Markdown, and the URL carries ?node=
 *  5. Clicking an id inside the document moves to that node (URL + document change)
 *  6. Browser Back returns to the previous node
 *  7. Focus mode draws fewer nodes than All mode
 *  8. A path that escapes the repository is refused by the real route
 *  9. No uncaught page errors throughout
 */
import { test, expect, type BrowserContext, type Page } from "@playwright/test";

const ORIGIN = (process.env.CONSOLE_BASE_URL || "http://127.0.0.1:8765/console").replace(/\/console\/?$/, "");
const BASE_URL = `${ORIGIN}/console`;
const API = `${ORIGIN}/v1/console/plugins/corvin-knowledge`;

test.describe.configure({ mode: "serial" });

let ctx: BrowserContext;
let page: Page;
const errors: string[] = [];

test.beforeAll(async ({ browser }) => {
  ctx = await browser.newContext({ storageState: "./tests/e2e/auth-state.json", viewport: { width: 1600, height: 1000 } });
  page = await ctx.newPage();
  page.on("pageerror", (e) => { if (!/ResizeObserver loop|ChunkLoadError/.test(e.message)) errors.push(e.message); });
});
test.afterAll(async () => { await ctx.close(); });

const shown = async () => Number(/^(\d+) of/.exec((await page.getByTestId("knowledge-summary").innerText()) ?? "")?.[1] ?? NaN);

test("the Knowledge Graph entry sits in the sidebar's Marketplace group", async () => {
  await page.goto(`${BASE_URL}/app/dashboard`);
  const link = page.locator('nav a[href$="/app/corvin-knowledge"]:visible');
  await expect(link).toHaveCount(1, { timeout: 20_000 });
  await expect(link).toHaveText(/Knowledge Graph/);
  // The group section that holds it is headed "Marketplace", next to the Marketplace entry.
  const group = link.locator("xpath=ancestor::div[.//button[starts-with(normalize-space(.), 'Marketplace')]][1]");
  await expect(group.getByRole("button", { name: /^Marketplace/ })).toBeVisible();
  await expect(group.locator('a[href$="/app/marketplace"]')).toHaveCount(1);
  // ... and it is the ONLY group that holds it (no duplicate in the Data group)
  await expect(page.locator('nav:visible a[href$="/app/corvin-knowledge"]')).toHaveCount(1);
});

test("opens on a document and a real graph canvas", async () => {
  await page.locator('nav a[href$="/app/corvin-knowledge"]:visible').click();
  await expect(page).toHaveURL(/\/app\/corvin-knowledge/);
  await expect(page.getByTestId("knowledge-hint")).toContainText("Click a node to read its document.");
  await expect(page.getByTestId("knowledge-doc-body")).not.toBeEmpty({ timeout: 20_000 });
  await expect(page.getByTestId("knowledge-doc-id")).toContainText(/^ADR-\d{4}$/);
  await expect(page.getByTestId("knowledge-graph-canvas").locator("canvas")).toHaveCount(1);
});

test("mouse-wheel zoom changes the graph view", async () => {
  const canvas = page.getByTestId("knowledge-graph-canvas");
  const box = (await canvas.boundingBox())!;
  const snap = async () => (await canvas.locator("canvas").screenshot()).toString("base64");
  // Wait until the picture is stable (layout settled), so a difference after the wheel is the zoom.
  let before = await snap();
  await expect.poll(async () => { const now = await snap(); const same = now === before; before = now; return same; },
    { timeout: 15_000, intervals: [700] }).toBe(true);
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.wheel(0, -600);
  await page.waitForTimeout(500);
  expect(await snap()).not.toBe(before);
});

test("a node found by search opens its Markdown and puts it in the URL", async () => {
  await page.getByLabel("Find a node").fill("ADR-2206");
  await page.getByTestId("knowledge-search-hits").getByRole("button").first().click();
  await expect(page).toHaveURL(/[?&]node=/);
  await expect(page.getByTestId("knowledge-doc-id")).toHaveText("ADR-2206");
  await expect(page.getByTestId("knowledge-doc-body")).toContainText(/Knowledge Graph/i);
});

let first = "";
test("clicking an id in the text opens that document; Back returns", async () => {
  first = page.url();
  const link = page.getByTestId("knowledge-doc-body").locator("a[data-internal-link]").first();
  await expect(link).toBeVisible();
  const target = (await link.innerText()).trim();
  expect(target).toMatch(/^[A-Z]+-\d+$/);
  await link.click();
  await expect(page.getByTestId("knowledge-doc-id")).toHaveText(target);
  expect(page.url()).not.toBe(first);
  await page.goBack();
  await expect(page.getByTestId("knowledge-doc-id")).toHaveText("ADR-2206");
  expect(page.url()).toBe(first);
});

test("Focus mode shows a neighbourhood, All mode the whole graph", async () => {
  const focus = await shown();
  await page.getByRole("button", { name: "All", exact: true }).click();
  await expect.poll(shown).toBeGreaterThan(focus);
  expect(await shown()).toBeGreaterThan(500);
  await page.getByRole("button", { name: "Focus", exact: true }).click();
  await expect.poll(shown).toBe(focus);
});

test("a hand-written link with a human id opens that node; an unknown id says so", async () => {
  await page.goto(`${BASE_URL}/app/corvin-knowledge?node=ADR-2206`);
  await expect(page.getByTestId("knowledge-doc-id")).toHaveText("ADR-2206", { timeout: 20_000 });
  expect(await shown()).toBeLessThan(200);   // Focus mode, not the whole graph
  await page.goto(`${BASE_URL}/app/corvin-knowledge?node=ADR-9999`);
  await expect(page.getByTestId("knowledge-unknown-node")).toContainText("ADR-9999");
  await expect(page.getByTestId("knowledge-doc-id")).toContainText(/^ADR-\d{4}$/);
});

test("the route refuses a key that is not in the graph and never reads outside the repo", async () => {
  const miss = await ctx.request.get(`${API}/doc/ADR-9999`);
  expect(miss.status()).toBe(404);
  const traversal = await ctx.request.get(`${API}/doc/${encodeURIComponent("../../etc/passwd")}`);
  expect(traversal.status()).toBe(404);
  expect(await traversal.text()).not.toContain("root:");
});

test("no uncaught page errors", async () => { expect(errors).toEqual([]); });
