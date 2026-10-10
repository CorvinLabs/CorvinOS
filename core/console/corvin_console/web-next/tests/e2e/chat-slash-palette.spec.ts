/**
 * Slash-command palette in BOTH chat variants (session chat + peer chat), in the
 * real browser: it must open on "/", be OPAQUE (not see-through), and a `/` line
 * typed in the peer chat must go to the peer-thread dispatcher, never to the peer.
 */
import { test, expect, type Page } from "@playwright/test";

const PEER = "peer-e2e-slash";
const NOW = () => Math.floor(Date.now() / 1000);

async function opaque(page: Page) {
  const palette = page.getByTestId("slash-palette");
  await expect(palette).toBeVisible();
  const bg = await palette.evaluate((el) => getComputedStyle(el).backgroundColor);
  // rgba(...,0) / alpha < 1 / "transparent" would all be see-through.
  expect(bg).not.toMatch(/transparent|rgba\(.*,\s*0(\.\d+)?\)$/);
  const alpha = bg.startsWith("rgba") ? Number(bg.replace(/.*,\s*([\d.]+)\)/, "$1")) : 1;
  expect(alpha).toBe(1);
}

test("peer chat: palette opens, is opaque, and a / line hits the dispatcher", async ({ page }) => {
  await page.route(/\/v1\/console\/a2a\/feed(\?.*)?$/, (route) => route.fulfill({ json: {
    tenant_id: "_default", ts: NOW(), retention_days: 30, has_more: false, last_seq: 0,
    peers: [{ peer_id: PEER, label: "E2E Peer", state: "ACTIVE", can_send: true, can_receive: true,
      enabled: true, presence: "online", task_capacity: "ok" }],
    messages: [], stages: {},
  } }));
  await page.route(/\/v1\/console\/peer-thread\/commands$/, (route) => route.fulfill({ json: {
    commands: [{ cmd: "/ask @mine", args: "<question>", desc: "Ask your own agent" }],
  } }));
  let dispatched = "";
  let sentToPeer = false;
  await page.route(/\/v1\/console\/peer-thread\/[^/]+\/command$/, (route) => {
    dispatched = route.request().postDataJSON().line;
    return route.fulfill({ json: { executed: true, kind: "ask_mine", text: "ok" } });
  });
  await page.route(/\/v1\/console\/a2a\/send/, (route) => { sentToPeer = true; return route.fulfill({ json: {} }); });

  await page.goto(`/console/app/chat/peer/${PEER}`);
  const box = page.getByLabel("Message to agent");
  await box.fill("/");
  await opaque(page);
  // ONLY the server's table: session commands would be refused by the dispatcher.
  await expect(page.getByTestId("slash-palette").locator("button")).toHaveText([/\/ask @mine/]);
  await box.fill("/ask @mine hello");
  await box.press("Escape");
  await box.press("Enter");
  await expect.poll(() => dispatched).toBe("/ask @mine hello");
  expect(sentToPeer).toBe(false);
});

test("session chat: palette opens and is opaque", async ({ page }) => {
  await page.goto("/console/app/chat");
  const box = page.locator("textarea").first();
  await box.fill("/");
  await opaque(page);
  await expect(page.getByTestId("slash-palette").getByText("/help", { exact: true })).toBeVisible();
});
