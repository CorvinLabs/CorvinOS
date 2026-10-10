/**
 * Background activity strip in the real browser, on the bundle the console serves.
 * The chat WebSocket is stubbed at the network boundary (page.routeWebSocket) so the
 * `bg_status` stream can be driven deterministically; the page, registry, reducer and
 * component are the real ones. Asserts the strip shows what runs, ticks, finishes, and
 * never stops the operator from typing.
 */
import { test, expect } from "@playwright/test";

const SID = "bg-activity-e2e";

test("running shell / monitor / agent are listed live, finish, and typing stays possible", async ({ page }) => {
  let push: (evt: Record<string, unknown>) => void = () => {};
  await page.routeWebSocket(new RegExp(`/chat/sessions/${SID}/stream`), (ws) => {
    push = (evt) => ws.send(JSON.stringify(evt));
    ws.onMessage((raw) => {
      const msg = JSON.parse(String(raw));
      if (msg.type === "ping") return;
      push({ type: "delta", text: "Started a build and a monitor." });
      push({ type: "bg_status", open: 3, children: [
        { id: "a", kind: "bash", state: "running", age_s: 65, label: "npm run build" },
        { id: "b", kind: "monitor", state: "running", age_s: 12, label: "tail -f app.log" },
        { id: "c", kind: "agent", state: "running", age_s: 3, label: "Review the diff" },
      ] });
    });
  });

  await page.goto(`/console/app/chat/${SID}`);
  const box = page.locator("textarea").last();
  await box.fill("build it");
  await box.press("Enter");

  const strip = page.getByTestId("bg-activity");
  await expect(strip).toBeVisible();
  await expect(page.getByTestId("bg-activity-summary")).toHaveText("3 running in the background");
  const rows = page.getByTestId("bg-activity-row");
  await expect(rows).toHaveCount(3);
  await expect(rows.nth(0)).toContainText("Shell command");
  await expect(rows.nth(0)).toContainText("npm run build");
  await expect(rows.nth(1)).toContainText("Monitor");
  await expect(rows.nth(2)).toContainText("Background agent");
  await page.screenshot({ path: "test-results/bg-activity-running.png" });

  // the clock runs on by itself (1:05 → at least 1:07 after ~2 s) …
  await expect(rows.nth(0)).toContainText(/1:0[6-9]|1:1\d/, { timeout: 5000 });
  // … and the composer is still usable while tasks run
  await box.fill("one more thing");
  await expect(box).toHaveValue("one more thing");
  await expect(box).toBeEnabled();

  // one child ends, one fails
  push({ type: "bg_status", open: 1, children: [
    { id: "a", kind: "bash", state: "completed", age_s: 70, label: "npm run build" },
    { id: "b", kind: "monitor", state: "running", age_s: 17, label: "tail -f app.log" },
    { id: "c", kind: "agent", state: "failed", age_s: 8, label: "Review the diff" },
  ] });
  await expect(page.getByTestId("bg-activity-summary")).toHaveText("1 running in the background");
  await expect(rows.first()).toHaveAttribute("data-kind", "monitor"); // running first
  await expect(page.getByLabel("failed")).toBeVisible();
  await page.screenshot({ path: "test-results/bg-activity-partial.png" });

  // the turn ends: the strip goes away
  push({ type: "result", text: "done", final: true });
  push({ type: "done" });
  await expect(strip).toBeHidden();
});
