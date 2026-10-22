/**
 * Workflow Ready tab (readiness-panel) - real browser against the live console.
 *
 * Run: CONSOLE_BASE_URL=http://127.0.0.1:8765/console npx playwright test workflow-readiness
 *
 * A two-step workflow is created through the API (id prefix qa-wf-) and deleted afterwards. No
 * design message is sent (that would spawn a paid engine turn); the report is a synthetic run.
 */
import { test, expect, type APIRequestContext } from "@playwright/test";

const ORIGIN = (process.env.CONSOLE_BASE_URL || "http://127.0.0.1:8765/console").replace(/\/console\/?$/, "");
const API = `${ORIGIN}/v1/console`;
const WID = `qa-wf-ready-${Date.now().toString(36)}`;

const YAML = `awp: "1.0.0"
workflow:
  name: qa_ready
  description: readiness e2e
orchestration:
  engine: dag
  graph:
    - id: fetch
      type: agent
      agent: assistant
      depends_on: []
      instructions: fetch
    - id: summarise
      type: agent
      agent: assistant
      depends_on: [fetch]
      instructions: summarise
`;

async function csrfOf(request: APIRequestContext): Promise<string> {
  // whoami can answer a transient non-200 while the host is busy (observed once right after a
  // restart); a short retry keeps the cleanup in afterAll from leaving a qa-wf- workflow behind.
  let status = 0;
  for (let i = 0; i < 4; i++) {
    const who = await request.get(`${API}/auth/whoami`);
    status = who.status();
    if (status === 200) return (await who.json()).csrf_token as string;
    await new Promise((r) => setTimeout(r, 1000 * (i + 1)));
  }
  expect(status).toBe(200);
  return "";
}

test.beforeAll(async ({ playwright }) => {
  const ctx = await playwright.request.newContext({ storageState: "./tests/e2e/auth-state.json" });
  const csrf = await csrfOf(ctx);
  const h = { "X-CSRF-Token": csrf };
  const r = await ctx.post(`${API}/workflows`, { headers: h, data: { id: WID, title: "QA readiness" } });
  expect(r.status(), await r.text()).toBeLessThan(300);
  const y = await ctx.put(`${API}/workflows/${WID}/yaml`, { headers: h, data: { yaml: YAML } });
  expect(y.status(), await y.text()).toBe(200);
  await ctx.dispose();
});

test.afterAll(async ({ playwright }) => {
  const ctx = await playwright.request.newContext({ storageState: "./tests/e2e/auth-state.json" });
  const csrf = await csrfOf(ctx);
  await ctx.delete(`${API}/workflows/${WID}`, { headers: { "X-CSRF-Token": csrf } });
  await ctx.dispose();
});

test("the Ready tab shows the measured lines and asks for a reason before marking ready", async ({ page }) => {
  await page.goto(`/console/app/workflows/${WID}`);
  await page.getByTestId("inspector-tab-ready").click();
  const panel = page.getByTestId("readiness-panel");
  await expect(panel).toBeVisible();
  await expect(page.getByTestId("readiness-notice")).toContainText("not real execution");
  await expect(page.getByTestId("readiness-signal-validator")).toContainText("Pass");
  await expect(page.getByTestId("readiness-signal-reachability")).toContainText("Pass");
  await expect(page.getByTestId("readiness-signal-acceptance")).toContainText("Not measured");
  await expect(page.getByTestId("readiness-verdict")).toContainText("Not ready");

  // Storing a holding acceptance case turns the last open line green.
  await page.getByTestId("readiness-acceptance-text").fill(
    JSON.stringify({ expect: { state: "complete", nodes_reached: ["fetch", "summarise"] } }),
  );
  await page.getByTestId("readiness-save-case").click();
  await expect(page.getByTestId("readiness-signal-acceptance")).toContainText("Pass");
  await expect(page.getByTestId("readiness-verdict")).toContainText("Ready");

  // The override asks for a reason.
  await page.getByTestId("readiness-override-open").click();
  await expect(page.getByTestId("readiness-override-confirm")).toBeDisabled();
  await page.getByTestId("readiness-override-reason").fill("checked by hand");
  await expect(page.getByTestId("readiness-override-confirm")).toBeEnabled();
});

test("the editor's former Dry-run button is the Synthetic check", async ({ page }) => {
  await page.goto(`/console/app/workflows/${WID}`);
  // The toolbar renders after the lazily loaded editor chunk and the workflow detail arrive;
  // under four parallel browsers that took longer than the 5 s default (empty <main> in the
  // failing run's DOM snapshot), so wait for the page like the composer spec does.
  await expect(page.getByRole("button", { name: "Synthetic check" })).toBeVisible({ timeout: 20_000 });
  await expect(page.getByRole("button", { name: "Dry-run" })).toHaveCount(0);
});
