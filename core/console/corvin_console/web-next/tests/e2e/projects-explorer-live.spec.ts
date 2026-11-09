/**
 * The explorer with NOTHING stubbed (CONCEPT-0128, plan phase 3): a real chat of the running console is attached to a real project with
 * write access, a REAL turn is typed into the composer, the real CLI runs, and the explorer - fed by the real SSE stream - must show it.
 *
 * Needs a prepared project and chat (see the handover): EXPLORER_DEMO_PROJECT, EXPLORER_DEMO_SID, EXPLORER_DEMO_DIR.
 * Run: CORVIN_E2E_ALLOW_LIVE=1 CONSOLE_BASE_URL=http://127.0.0.1:8765/console EXPLORER_DEMO_PROJECT=explorer-demo \
 *      EXPLORER_DEMO_SID=<chat sid> EXPLORER_DEMO_DIR=/path/to/project npx playwright test -c playwright.config.ts projects-explorer-live
 * EXPLORER_KEEP=1 leaves the file the assistant wrote, so the feed shows it afterwards.
 *
 * Measured, not claimed: the time from the turn's own WebSocket `tool_use` frame to the visible row (target under 3 s), that every tool the turn
 * called appears as a row, and that nothing of the prompt or of the file's content is on the screen's explorer.
 */
import fs from "node:fs";
import path from "node:path";
import { test, expect } from "@playwright/test";

const PROJECT = process.env.EXPLORER_DEMO_PROJECT ?? "";
const SID = process.env.EXPLORER_DEMO_SID ?? "";
const DIR = process.env.EXPLORER_DEMO_DIR ?? "";
test.skip(!PROJECT || !SID || !DIR, "needs EXPLORER_DEMO_PROJECT, EXPLORER_DEMO_SID and EXPLORER_DEMO_DIR");

test("a real turn typed in the browser shows up live in the explorer, in order, within seconds, without its content", async ({ page }) => {
  test.setTimeout(240_000);
  const name = `hello-explorer-${Date.now().toString(36)}.txt`;
  const secret = `SECRET-${Math.random().toString(36).slice(2, 10)}`;
  const target = path.join(DIR, name);
  const toolFrames: { t: number; name: string }[] = [];
  page.on("websocket", (ws) => ws.on("framereceived", (f) => {
    const p = typeof f.payload === "string" ? f.payload : f.payload.toString();
    if (p.includes('"type":"tool_use"') || p.includes('"type": "tool_use"')) {
      try { const m = JSON.parse(p) as { type?: string; name?: string }; if (m.type === "tool_use") toolFrames.push({ t: Date.now(), name: m.name ?? "" }); } catch { /* not JSON */ }
    }
  }));

  await page.goto(`/console/app/chat/${SID}`);
  await page.getByTestId("project-menu-button").click();
  await page.getByTestId("project-menu-explorer").click();
  await expect(page.getByTestId("explorer-panel")).toBeVisible();
  await expect(page.getByTestId("explorer-status")).toHaveAttribute("data-conn", "live", { timeout: 15_000 });
  const rowsBefore = await page.getByTestId("explorer-row").count();

  const composer = page.getByTestId("chat-composer-textarea");
  await composer.click();
  // the absolute path: this test measures the explorer, not whether the model obeys "in the project" (it sometimes writes into its own folder)
  await composer.fill(`Create the file ${target} containing one friendly line that includes the code ${secret}. Then reply DONE.`);
  const sentAt = Date.now();
  await composer.press("Enter");

  // 1. the agent appears as working
  const working = page.locator('[data-testid="explorer-agent"][data-state="working"]');
  await expect(working).toBeVisible({ timeout: 30_000 });
  const workingAt = Date.now();
  expect(workingAt - sentAt, "Enter -> the Working card (the card must follow the turn's own start event, not the next reconnect)").toBeLessThan(5000);
  // 2. its tool call appears as a row, and a file row follows
  const toolRow = page.locator('[data-testid="explorer-row"][data-kind="tool"]', { hasText: /Wrote|Edited|Ran/ }).first();
  await expect(toolRow).toBeVisible({ timeout: 120_000 });
  const rowAt = Date.now();
  const fileRow = page.locator('[data-testid="explorer-row"][data-kind="file"]', { hasText: name }).first();
  await expect(fileRow).toBeVisible({ timeout: 30_000 });
  await expect(fileRow).toContainText("New file");
  // 3. the turn finishes: the working card goes idle and a "Finished" row is added
  // other chats attached to the same project show their own (idle) cards too: it is enough that one card reads "finished"
  await expect(page.locator('[data-testid="explorer-agent"][data-state="idle"]').filter({ hasText: "finished" }).first()).toBeVisible({ timeout: 120_000 });
  await expect(page.locator('[data-testid="explorer-row"][data-kind="turn"]', { hasText: "Finished" }).first()).toBeVisible();

  // measured: the turn's own tool_use frame -> the visible row
  const write = toolFrames.find((f) => ["Write", "Edit", "Bash"].includes(f.name)) ?? toolFrames[0];
  expect(write, "the browser saw no tool_use frame of the real turn").toBeTruthy();
  const latency = rowAt - write.t;
  console.log(`[explorer-live] working card ${workingAt - sentAt} ms after Enter; Write frame -> row: ${Math.max(0, latency)} ms; tool frames of the turn: ${toolFrames.map((f) => f.name).join(", ")}`);
  expect(Math.max(0, latency), "tool_use frame to visible row").toBeLessThan(3000);

  // 4. every tool the turn called is on screen as a row (collapsing aside), nothing else is
  const explorerText = (await page.getByTestId("explorer-feed").innerText()) + (await page.getByTestId("explorer-now").innerText());
  for (const n of new Set(toolFrames.map((f) => f.name))) {
    const word = n === "Write" ? "Wrote" : n === "Edit" ? "Edited" : n === "Read" ? "Read" : n === "Bash" ? "Ran" : "";
    if (word) expect(explorerText, `a row for ${n}`).toContain(word);
  }
  expect(await page.getByTestId("explorer-row").count()).toBeGreaterThan(rowsBefore);
  // 5. nothing of the prompt or the file's content is in the explorer
  for (const leak of [secret, "friendly line", "in the project containing"]) expect(explorerText).not.toContain(leak);
  expect(fs.existsSync(target), "the assistant really wrote the file").toBe(true);
  expect(fs.readFileSync(target, "utf8")).toContain(secret);

  await page.screenshot({ path: "/tmp/explorer-live.png" });
  if (!process.env.EXPLORER_KEEP) fs.rmSync(target, { force: true });
});
