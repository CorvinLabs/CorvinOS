/**
 * E2E: a Forge generation run survives a hard refresh and a console outage.
 *
 * REAL SPA in a real browser (the console is the live one). MOCKED, stated: the
 * /forge-creator generate + status endpoints — a real run costs engine minutes;
 * the server side (persistence, resume after a restart) is proven against the
 * real router in core/console/tests/test_forge_creator_e2e.py. What this spec
 * proves is the browser half: the run id outlives a reload, a dropped
 * connection / 5xx does not abandon the run, and only a 404 frees the form.
 */
import { test, expect } from "@playwright/test";

const RUN = "run-0123456789ab";
const STATUS = new RegExp(`/v1/console/forge-creator/status/${RUN}`);
const URL = "/console/app/forge?tab=generator&sub=tool";

function body(status: "running" | "success", message: string) {
  return {
    run_id: RUN, kind: "tool", status, phase: "generation",
    phases: ["planning", "generation", "promotion"], progress: 40, message,
    engine: "claude_code", error: null, tool: null, plugin: null,
  };
}

test.use({ storageState: "./tests/e2e/auth-state.json" });

test("a tool run re-attaches after a hard refresh and rides out an outage", async ({ page }) => {
  let mode: "ok" | "down" | "gone" = "ok";
  let message = "Generating";
  await page.route("**/v1/console/forge-creator/tool/generate", (r) =>
    r.fulfill({ status: 202, json: { status: "accepted", run_id: RUN, kind: "tool", message: "started" } }));
  await page.route(STATUS, (r) => {
    if (mode === "down") return r.abort("connectionrefused");
    if (mode === "gone") return r.fulfill({ status: 404, json: { detail: `Run not found: ${RUN}` } });
    return r.fulfill({ json: body("running", message) });
  });

  await page.goto(URL);
  await page.getByTestId("tool-request").fill("a tool that counts words in a text");
  await page.getByTestId("tool-submit").click();
  await expect(page.getByTestId("tool-run-message")).toHaveText("Generating");

  // Hard refresh mid-run: the form must come back attached, not reset.
  message = "Still generating after the reload";
  await page.reload();
  await expect(page.getByTestId("tool-run-message")).toHaveText("Still generating after the reload");
  await expect(page.getByTestId("tool-submit")).toBeDisabled();

  // The console goes away for a while: the run must not be abandoned.
  mode = "down";
  await page.waitForTimeout(4500);
  await expect(page.getByRole("alert")).toHaveCount(0);
  await expect(page.getByTestId("tool-submit")).toBeDisabled();
  mode = "ok";
  message = "Back after the outage";
  await expect(page.getByTestId("tool-run-message")).toHaveText("Back after the outage", { timeout: 15_000 });

  // Only a real 404 frees the form.
  mode = "gone";
  await expect(page.getByRole("alert")).toContainText("no longer known", { timeout: 15_000 });
  // The reload emptied the textarea, so the button waits for a new request.
  await expect(page.getByTestId("tool-request")).toBeEnabled();
  await page.getByTestId("tool-request").fill("a tool that counts words in a text");
  await expect(page.getByTestId("tool-submit")).toBeEnabled();
});
