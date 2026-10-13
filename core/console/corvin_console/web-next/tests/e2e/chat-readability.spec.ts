/**
 * Assistant chat readability, in the real browser on the bundle the console serves: actor
 * glyph + time per message, a Today/Yesterday separator across days, full date in the tooltip —
 * on desktop and on a 375 px phone in dark theme. The history is stubbed at the network
 * boundary (page.route); page, bundle, registry, router and renderer are the real ones.
 */
import { test, expect, type Page } from "@playwright/test";

const SID = "chat-readability-e2e";

function noon(daysAgo: number): number {
  const d = new Date();
  return Math.floor(new Date(d.getFullYear(), d.getMonth(), d.getDate() - daysAgo, 12).getTime() / 1000);
}
const turn = (role: string, ts: number, text: string) => ({ role, ts, parts: [{ kind: "text", text }] });

async function openChat(page: Page) {
  const now = Math.floor(Date.now() / 1000);
  const turns = [
    turn("user", noon(1), "question from yesterday"),
    turn("assistant", noon(1) + 30, "answer from yesterday"),
    turn("user", now - 120, "question today"),
    turn("assistant", now - 60, "answer today ".repeat(12)),
  ];
  await page.route(new RegExp(`/chat/sessions/${SID}/turns`), (r) =>
    r.fulfill({ json: { sid: SID, count: turns.length, turns } }));
  await page.goto(`/console/app/chat/${SID}`);
  await expect(page.getByTestId("chat-message-time")).toHaveCount(4);
}

test("each message shows glyph, name and time; days are separated; tooltip carries the date", async ({ page }) => {
  await openChat(page);
  expect(await page.getByTestId("chat-role-mark").allTextContents()).toEqual(["🧑", "🤖", "🧑", "🤖"]);
  expect(await page.getByTestId("chat-role").allTextContents()).toEqual(["You", "Corvin", "You", "Corvin"]);
  await expect(page.getByTestId("chat-role-mark").first()).toHaveAttribute("aria-hidden", "true");

  const seps = page.getByTestId("chat-day-separator");
  await expect(seps).toHaveCount(2);
  await expect(seps.nth(0)).toHaveText("Yesterday");
  await expect(seps.nth(1)).toHaveText("Today");

  const time = page.getByTestId("chat-message-time").first();
  await expect(time).toHaveText(/^\d{1,2}:\d{2}\s?(AM|PM)$/);
  await expect(time).toHaveAttribute("title", new RegExp(String(new Date().getFullYear())));
  await page.screenshot({ path: "test-results/chat-readability-desktop.png" });
});

test("phone width, dark theme: time and name stay visible, nothing overflows", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await openChat(page);
  await page.evaluate(() => document.documentElement.setAttribute("data-theme", "dark"));
  for (let i = 0; i < 4; i++) {
    await expect(page.getByTestId("chat-message-time").nth(i)).toBeVisible();
    await expect(page.getByTestId("chat-role").nth(i)).toBeVisible();
  }
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(0);
  await page.screenshot({ path: "test-results/chat-readability-mobile-dark.png" });
});
