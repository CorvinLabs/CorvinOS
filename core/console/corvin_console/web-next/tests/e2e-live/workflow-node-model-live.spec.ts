/**
 * Live-server E2E (ADR-2308) — the run view of a REAL workflow run shows, next to each model node,
 * the model the CLI reported and its token total. Read-only: it opens a run that already exists.
 *
 *   CORVIN_E2E_ALLOW_LIVE=1 CONSOLE_BASE_URL=http://127.0.0.1:8765 NM_WID=<wid> NM_RID=<rid> \
 *     NM_EXPECT='{"extract":"haiku","describe":"sonnet"}' \
 *     npx playwright test --config=playwright.live.config.ts tests/e2e-live/workflow-node-model-live.spec.ts
 */
import { test, expect } from "@playwright/test";
import { resolveOptionalOrigin } from "../e2e/sandbox-origin";

const BASE = resolveOptionalOrigin("CONSOLE_BASE_URL");
const WID = process.env.NM_WID ?? "";
const RID = process.env.NM_RID ?? "";
const EXPECT: Record<string, string> = JSON.parse(process.env.NM_EXPECT ?? "{}");

test.skip(!WID || !RID || Object.keys(EXPECT).length === 0, "needs NM_WID, NM_RID and NM_EXPECT");

test("the run view shows the reported model and tokens next to each model node", async ({ page }) => {
  await page.goto(`${BASE}/v1/console/auth/local-login`);
  await page.waitForURL(/\/console\//, { timeout: 20000 });
  await page.goto(`${BASE}/console/app/workflows/${encodeURIComponent(WID)}/runs/${encodeURIComponent(RID)}`);

  const badges = page.getByTestId("run-event-model");
  await expect(badges.first()).toBeVisible({ timeout: 20000 });

  for (const [nodeId, family] of Object.entries(EXPECT)) {
    // the summary row that names the node AND carries the usage badge
    const row = page.locator("div.flex.items-center", { hasText: nodeId }).filter({ has: page.getByTestId("run-event-model") }).first();
    await expect(row, `row of ${nodeId}`).toBeVisible();
    const badge = row.getByTestId("run-event-model");
    await expect(badge, `badge of ${nodeId}`).toContainText(family);
    await expect(badge, `tokens of ${nodeId}`).toContainText("tok");
  }
  await page.screenshot({ path: process.env.NM_SCREENSHOT ?? "/tmp/node_model_inventory/run-view.png", fullPage: true });
});
