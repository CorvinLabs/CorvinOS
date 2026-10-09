/**
 * Peer chat: a status symbol sticks to EVERY message, in the real browser, on the
 * bundle the console actually serves (not jsdom).
 *
 * The feed is stubbed at the network boundary (page.route) so every state — queued,
 * sent, delivered, working, done, refused, unconfirmed — can be shown deterministically;
 * the page, bundle, router, query layer and renderer are the real ones.
 */
import { test, expect } from "@playwright/test";

const PEER = "peer-e2e-1";
const NOW = () => Math.floor(Date.now() / 1000);

function msg(id: string, task: string, over: Record<string, unknown>) {
  return {
    id, seq: Number(id.replace(/\D/g, "")) || 1, ts: NOW() - 20, direction: "out", kind: "task",
    peer_id: PEER, peer_label: "E2E Peer", task_id: task, status: "sent", text: `message ${id}`,
    data: {}, attachments: [], duration_ms: null, error: null, thread_ref: null, ...over,
  };
}

test("every message carries a status symbol and the chain follows the peer's stage", async ({ page }) => {
  const feed = {
    tenant_id: "_default", ts: NOW(), retention_days: 30, has_more: false, last_seq: 9,
    peers: [{ peer_id: PEER, label: "E2E Peer", state: "ACTIVE", can_send: true, can_receive: true,
      enabled: true, presence: "online" }],
    messages: [
      msg("1", "t-queued", { status: "queued" }),
      msg("2", "t-sent", { status: "sent" }),
      msg("3", "t-work", { status: "sent" }),
      msg("4", "t-done", { status: "sent" }),
      msg("5", "t-done", { kind: "response", direction: "in", status: "ok", text: "pong" }),
      msg("6", "t-refused", { status: "sent" }),
      msg("7", "t-unconf", { status: "sent" }),
      msg("8", "t-unconf", { kind: "response", direction: "in", status: "unconfirmed",
        error: "Delivery unconfirmed — the peer may have received and run it." }),
      msg("9", "t-in", { direction: "in", status: "received" }),
    ],
    stages: {
      "t-work": { stage: "processing", stage_seq: 3, ts: NOW() - 6, reason: "" },
      "t-refused": { stage: "rejected", stage_seq: 2, ts: NOW() - 3, reason: "busy" },
      "t-unconf": { stage: "processing", stage_seq: 2, ts: NOW() - 40, reason: "" },
    },
  };
  // Regex, not a glob: "?" is a wildcard in Playwright globs, so "feed?*" never matched
  // the real "feed?peer_id=…" request and the page showed the (empty) live feed.
  await page.route(/\/v1\/console\/a2a\/feed(\?.*)?$/, (route) => route.fulfill({ json: feed }));

  await page.goto(`/console/app/chat/peer/${PEER}`); // absolute: baseURL carries /console, a bare "/app" would drop it
  const rows = page.getByTestId("peer-message");
  await expect(rows).toHaveCount(9);

  // The symbol is on every row, including the quiet ones.
  const symbols = rows.getByTestId("peer-message-status");
  await expect(symbols).toHaveCount(9);
  const keys = await symbols.evaluateAll((els) => els.map((e) => e.getAttribute("data-status")));
  expect(keys).toEqual(["queued", "sent", "working", "done", "reply-ok", "failed", "working", "reply-unconfirmed", "in-received"]);

  // The working message shows the spinner label, the six-step chain at step 5, and its age.
  const working = rows.nth(2).getByTestId("peer-message-status");
  await expect(working).toContainText("Agent working");
  const states = await working.getByTestId("peer-message-chain").locator("span")
    .evaluateAll((els) => els.map((e) => e.getAttribute("data-state")));
  expect(states).toEqual(["done", "done", "done", "done", "current", "pending"]);
  await expect(working).toContainText(/ago|just now/);

  // A refusal is a failure with the reason in the tooltip; an unconfirmed send with a known
  // stage shows that stage rather than a dead end.
  await expect(rows.nth(5).getByTestId("peer-message-status")).toHaveAttribute("title", /busy with other tasks/);
  await expect(rows.nth(6).getByTestId("peer-message-status")).toHaveAttribute("title", /last known state/);

  // The refused message (peer busy) offers Resend; the unconfirmed one must NOT (it may have run).
  await expect(rows.nth(5).getByTestId("peer-message-resend")).toBeVisible();
  await expect(rows.nth(6).getByTestId("peer-message-resend")).toHaveCount(0);
  await expect(page.getByTestId("peer-message-resend")).toHaveCount(1);

  // It must stay readable: nothing clipped to zero width, symbol inside the viewport.
  const box = await working.boundingBox();
  expect(box && box.width > 20 && box.height > 8).toBeTruthy();
  await page.screenshot({ path: "test-results/peer-message-status-symbols.png", fullPage: false });
});
