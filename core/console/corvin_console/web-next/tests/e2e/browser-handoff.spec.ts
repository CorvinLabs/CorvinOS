/**
 * Ad-hoc verification: chat's `/browser` command deep-links to the SAME
 * session the Browser page attaches to, instead of the page creating a
 * brand-new, disconnected (never-navigated, blank) session.
 */
import { test, expect } from "@playwright/test";

const API_BASE = "http://localhost:8765/v1/console";

test("Browser page attaches to an existing sid passed via ?sid=", async ({
  page,
  request,
}) => {
  const whoami = await request.get(`${API_BASE}/auth/whoami`);
  expect(whoami.status()).toBe(200);
  const csrfToken = (await whoami.json()).csrf_token as string;

  // Create a browser session directly (simulates what chat.py's
  // _handle_browser_command does via mgr.create()) and navigate it, so we
  // have real, non-blank content to verify the page actually shows.
  const create = await request.post(`${API_BASE}/browser/session`, {
    headers: { "X-CSRF-Token": csrfToken, "Content-Type": "application/json" },
    data: {},
  });
  expect(create.status()).toBe(200);
  const sid = (await create.json()).session as string;
  expect(sid).toBeTruthy();

  const nav = await request.post(`${API_BASE}/browser/${sid}/navigate`, {
    headers: { "X-CSRF-Token": csrfToken, "Content-Type": "application/json" },
    data: { url: "https://example.com" },
  });
  expect(nav.status()).toBe(200);

  // Now load the Browser page with ?sid=<that session> — it must attach
  // directly, WITHOUT calling POST /browser/session again.
  let createSessionCalls = 0;
  await page.route("**/v1/console/browser/session", (route) => {
    createSessionCalls++;
    route.continue();
  });

  await page.goto(`/console/app/browser?sid=${sid}`, { waitUntil: "load" });
  await page.waitForTimeout(1500); // let the frame-polling effect fire at least once

  const frameSrcSeen = await page.evaluate(() => {
    const imgs = Array.from(document.querySelectorAll("img"));
    return imgs.map((i) => i.src).find((s) => s.includes("/frame.jpg"));
  });
  console.log("frame img src used by the page:", frameSrcSeen);
  expect(frameSrcSeen).toContain(`/browser/${sid}/frame.jpg`);
  expect(createSessionCalls).toBe(0);

  await request.post(`${API_BASE}/browser/${sid}/close`, {
    headers: { "X-CSRF-Token": csrfToken },
  });
});
