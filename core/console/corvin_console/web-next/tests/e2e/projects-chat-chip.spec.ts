/**
 * The project chip in a chat's header (T-0364), on the served bundle with a real chat session of the live console.
 * Only the attachment lookup is stubbed: with a project attached the chip names it and links to its settings;
 * with none, no chip is rendered.
 */
import { test, expect } from "@playwright/test";

async function aSid(page: import("@playwright/test").Page): Promise<string> {
  const r = await page.request.get("/v1/console/chat/sessions");
  expect(r.ok()).toBeTruthy();
  const sessions = (await r.json()).sessions as { sid: string }[];
  test.skip(sessions.length === 0, "needs at least one chat session on the console under test");
  return sessions[0].sid;
}

test("an attached project shows as a chip that links to the project's settings", async ({ page }) => {
  const sid = await aSid(page);
  await page.route(/\/v1\/console\/projects\/chat-attachment/, (r) => r.fulfill({ json: { session_id: sid, project_id: "plumbline", title: "Plumbline explainer" } }));
  await page.goto(`/console/app/chat/${sid}`);
  const chip = page.getByTestId("project-chip");
  await expect(chip).toContainText("Plumbline explainer");
  await expect(chip).toHaveAttribute("href", /\/app\/projects\/plumbline\?tab=settings/);
});

test("with no project attached, or a failing lookup, no chip is rendered and the chat header still is", async ({ page }) => {
  const sid = await aSid(page);
  await page.route(/\/v1\/console\/projects\/chat-attachment/, (r) => r.fulfill({ json: { session_id: sid, project_id: null, title: null } }));
  await page.goto(`/console/app/chat/${sid}`);
  await expect(page.getByTestId("chat-header")).toBeVisible();
  await expect(page.getByTestId("project-chip")).toHaveCount(0);
  await page.unroute(/\/v1\/console\/projects\/chat-attachment/);
  await page.route(/\/v1\/console\/projects\/chat-attachment/, (r) => r.fulfill({ status: 500, json: { detail: "boom" } }));
  await page.reload();
  await expect(page.getByTestId("chat-header")).toBeVisible();
  await expect(page.getByTestId("project-chip")).toHaveCount(0);
});
