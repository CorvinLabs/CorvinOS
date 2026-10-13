/**
 * Peer-chat readability, in the real browser on the bundle the console serves: actor
 * glyphs, day separators, time-only stamps with a full-date tooltip — on a desktop and
 * on a phone-width viewport, in light and dark theme. The feed is stubbed at the
 * network boundary (page.route); page, bundle, router, query layer and renderer are real.
 */
import { test, expect, type Page } from "@playwright/test";

const PEER = "peer-e2e-read";

function noon(daysAgo: number): number {
  const d = new Date();
  return Math.floor(new Date(d.getFullYear(), d.getMonth(), d.getDate() - daysAgo, 12).getTime() / 1000);
}

function msg(id: number, task: string, ts: number, over: Record<string, unknown> = {}) {
  return {
    id: `m${id}`, seq: id, ts, direction: "out", kind: "task", peer_id: PEER, peer_label: "Read Peer",
    task_id: task, status: "sent", text: `message ${id}`, data: {}, attachments: [],
    duration_ms: null, error: null, thread_ref: null, ...over,
  };
}

async function openThread(page: Page) {
  const now = Math.floor(Date.now() / 1000);
  const feed = {
    tenant_id: "_default", ts: now, retention_days: 30, has_more: false, last_seq: 5,
    peers: [{ peer_id: PEER, label: "Read Peer", state: "ACTIVE", can_send: true, can_receive: true,
      enabled: true, presence: "online" }],
    messages: [
      msg(1, "a", noon(1)),
      msg(2, "a", noon(1) + 40, { kind: "response", direction: "in", status: "ok", text: "their agent answers" }),
      msg(3, "b", now - 120, { direction: "in", status: "received", text: "the peer asks" }),
      msg(4, "b", now - 60, { kind: "response", direction: "out", status: "ok", text: "your agent answers" }),
      msg(5, "c", now - 20, { status: "sent", text: "a longer message so the row has to wrap on a phone ".repeat(3) }),
    ],
    stages: {},
  };
  await page.route(/\/v1\/console\/a2a\/feed(\?.*)?$/, (route) => route.fulfill({ json: feed }));
  await page.goto(`/console/app/chat/peer/${PEER}`);
  await expect(page.getByTestId("peer-message")).toHaveCount(5);
}

test("actor glyphs, day separators and time tooltips render on the served bundle", async ({ page }) => {
  await openThread(page);
  const rows = page.getByTestId("peer-message");

  const marks = await rows.getByTestId("peer-message-role-mark").allTextContents();
  expect(marks).toEqual(["🧑", "🤖", "👤", "🤖", "🧑"]);
  await expect(rows.first().getByTestId("peer-message-role-mark")).toHaveAttribute("aria-hidden", "true");

  const seps = page.getByTestId("chat-day-separator");
  await expect(seps).toHaveCount(2);
  await expect(seps.nth(0)).toHaveText("Yesterday");
  await expect(seps.nth(1)).toHaveText("Today");

  const time = rows.first().getByTestId("peer-message-time");
  await expect(time).toHaveText(/^\d{1,2}:\d{2}\s?(AM|PM)$/);
  await expect(time).toHaveAttribute("title", new RegExp(String(new Date().getFullYear())));

  // The ADR-2242 status symbol is still the coloured icon with its words.
  const sym = rows.nth(4).getByTestId("peer-message-status");
  await expect(sym.locator("svg")).toHaveCount(1);
  await expect(sym).toBeVisible();
  await page.screenshot({ path: "test-results/peer-chat-readability-desktop.png" });
});

test("stays readable on a phone width in dark theme: nothing hidden, nothing overflowing", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await openThread(page);
  await page.evaluate(() => document.documentElement.setAttribute("data-theme", "dark"));
  const rows = page.getByTestId("peer-message");

  // Time, role and status words stay visible on a phone — they are the content, not decoration.
  for (let i = 0; i < 5; i++) {
    await expect(rows.nth(i).getByTestId("peer-message-time")).toBeVisible();
    await expect(rows.nth(i).getByTestId("peer-message-role")).toBeVisible();
    await expect(rows.nth(i).getByTestId("peer-message-status")).toBeVisible();
  }
  const overflow = await page.getByTestId("peer-conversation").evaluate((el) => el.scrollWidth - el.clientWidth);
  expect(overflow).toBeLessThanOrEqual(0);
  await page.screenshot({ path: "test-results/peer-chat-readability-mobile-dark.png" });
});
