/**
 * LIVE (no stubs): on the running console, every message of a real peer chat carries a status
 * symbol. The feed is whatever the host really serves — real peers, real history — so this fails
 * when the page, the bundle or the host's `/a2a/feed` shape drifts apart.
 */
import { test, expect } from "@playwright/test";

const ORIGIN = (process.env.CONSOLE_BASE_URL || "http://127.0.0.1:8765/console").replace(/\/console\/?$/, "");
const API = `${ORIGIN}/v1/console`;

test("live peer chat: a symbol on every message, none without", async ({ page, request }) => {
  const res = await request.get(`${API}/a2a/feed?limit=200&include_former=true`);
  expect(res.status()).toBe(200);
  const feed = await res.json();
  const withHistory = new Set<string>((feed.messages ?? []).map((m: { peer_id: string }) => m.peer_id));
  const peer = (feed.peers ?? []).find((p: { peer_id: string }) => withHistory.has(p.peer_id));
  test.skip(!peer, "no peer with message history on this host");

  await page.goto(`/console/app/chat/peer/${peer.peer_id}`);
  const rows = page.getByTestId("peer-message");
  await expect(rows.first()).toBeVisible({ timeout: 20_000 });
  const n = await rows.count();
  expect(n).toBeGreaterThan(0);
  // Every row has exactly one symbol, and each carries a known state key.
  await expect(page.getByTestId("peer-message-status")).toHaveCount(n);
  const keys = await page.getByTestId("peer-message-status").evaluateAll((els) => els.map((e) => e.getAttribute("data-status")));
  const known = new Set(["queued", "sent", "working", "delivered", "accepted", "done", "failed", "unconfirmed",
    "reply-ok", "reply-failed", "reply-unconfirmed", "in-received", "in-answered", "in-refused"]);
  for (const k of keys) expect(known.has(String(k)), `unknown status key ${k}`).toBeTruthy();
  await page.screenshot({ path: "test-results/peer-live-status-symbols.png" });
});
