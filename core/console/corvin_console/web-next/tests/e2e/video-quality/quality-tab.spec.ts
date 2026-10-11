/**
 * Video Producer Quality tab — the production signals of a job, in a real browser (T-0091).
 *
 * Backend: the isolated harness (harness/isolated_vp_backend.py) — real gateway + console + built
 * bundle, throwaway CORVIN_HOME, the report.json a REAL job wrote (fixture of 2026-10-11), a real MP4.
 * The session is the console's own local-login (loopback). Light AND dark theme are rendered and
 * screenshotted (the console is data-theme driven, so the theme is set via its stored preference).
 */
import { test, expect, type Page } from "@playwright/test";
import fs from "fs";
import path from "path";

const HOME = process.env.VQ_E2E_HOME ?? "/tmp/corvin-vq-e2e";
const seed = () => JSON.parse(fs.readFileSync(path.join(HOME, "seed.json"), "utf-8"));
const SHOTS = process.env.VQ_SHOTS ?? "test-results/video-quality";

async function login(page: Page) {
  const r = await page.request.get("/v1/console/auth/local-login");
  expect(r.ok(), "local-login on the isolated console").toBeTruthy();
}

async function openQuality(page: Page, jobId: string, theme: "light" | "dark") {
  // what the theme toggle stores: the choice plus the one-time dark-default migration marker
  // (index.html would otherwise turn a stored "light" into "dark" on a fresh profile)
  await page.addInitScript((t) => {
    localStorage.setItem("corvin-dark-default-v2", "1");
    localStorage.setItem("corvin-theme", t);
  }, theme);
  await page.goto(`/console/app/video-producer?job=${jobId}&tab=quality`);
  await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
}

for (const theme of ["light", "dark"] as const) {
  test(`signals of a real report: value, threshold, verdict, n of N (${theme})`, async ({ page }) => {
    const s = seed();
    await login(page);
    await openQuality(page, s.job_report, theme);

    const card = page.getByTestId("signals-card");
    await expect(card).toBeVisible();
    await expect(page.getByTestId("signals-summary")).toContainText("Recorded by the job itself when it finished");
    // one row per signal the job wrote, gate first
    const rows = page.getByTestId("signals-table").locator("tbody tr");
    await expect(rows).toHaveCount(s.fixture.signals.length);
    await expect(rows.first()).toHaveAttribute("data-testid", "signal-degradation_rate");

    // degradation_rate: 2 of 2 scenes degraded (TTS tier) - below 10 scenes, no percentage
    await expect(page.getByTestId("signal-value-degradation_rate")).toHaveText("2 of 2 scenes");
    await expect(page.getByTestId("signal-threshold-degradation_rate")).toContainText("at most 10 % of scenes");
    await expect(page.getByTestId("signal-verdict-degradation_rate")).toHaveText("Fail");
    await expect(page.getByTestId("signal-basis-degradation_rate")).toHaveText("2 of 2");
    await expect(page.getByTestId("signals-gate-note")).toBeVisible();
    // a gated signal it invalidated reads "not valid"
    await expect(page.getByTestId("signal-verdict-layout_clean")).toHaveText("Pass · not valid");
    await expect(page.getByTestId("signal-value-layout_clean")).toHaveText("0 collisions kept");
    // the former timing check, now information only
    await expect(page.getByTestId("signal-value-plan_estimate_error")).toHaveText("9.2 s narrated · 16 s planned");
    await expect(page.getByTestId("signal-threshold-plan_estimate_error")).toHaveText("information only");
    await expect(page.getByTestId("signal-verdict-plan_estimate_error")).toHaveText("Information");
    // not measured is said, never a number
    await expect(page.getByTestId("signal-value-style_fidelity")).toHaveText("not measured");
    await expect(page.getByTestId("signal-verdict-style_fidelity")).toContainText("Not measured");
    // ffprobe half still renders below, without timing/runtime checks
    await expect(page.getByTestId("quality-tab")).toContainText("Container readable");
    await expect(page.getByTestId("quality-tab")).not.toContainText("Runtime equals the sum of the scenes");

    fs.mkdirSync(SHOTS, { recursive: true });
    await expect(page.getByTestId("signal-note-degradation_rate")).toContainText("Scene share above 10 %.");
    // the whole card in view: a tall viewport, so the console's sticky header does not cover it
    await page.setViewportSize({ width: 1280, height: 2000 });
    await card.scrollIntoViewIfNeeded();
    await card.screenshot({ path: path.join(SHOTS, `quality-signals-${theme}.png`) });
  });
}

test("a job without a report says so and shows no signal", async ({ page }) => {
  const s = seed();
  await login(page);
  await openQuality(page, s.job_old, "light");
  await expect(page.getByTestId("signals-absent")).toContainText("No production report for this job");
  await expect(page.getByTestId("signals-table")).toHaveCount(0);
});

test("another tenant's job is not readable through the session's tenant", async ({ page }) => {
  const s = seed();
  await login(page);
  const r = await page.request.get(`/v1/console/video/jobs/${s.job_other_tenant}/quality-metrics`);
  expect(r.status()).toBe(404);
  expect(await r.text()).not.toContain("degradation_rate");
  const own = await page.request.get(`/v1/console/video/jobs/${s.job_report}/quality-metrics`);
  expect(own.status()).toBe(200);
  expect((await own.json()).report.state).toBe("present");
});
