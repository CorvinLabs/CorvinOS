/**
 * Video Producer data lifecycle in a real browser (T-0092): cancel a job that is being produced,
 * delete a finished video with confirmation (Keep first, then Delete — files gone from disk), the
 * storage quota in the overview, and a full store refusing a new production.
 *
 * Backend: harness/isolated_vp_lifecycle_backend.py — real gateway + console + built bundle, throwaway
 * CORVIN_HOME, the plugin's real ProcessJobRunner (stand-in job process). Session: local-login.
 */
import { test, expect, type Page } from "@playwright/test";
import fs from "fs";
import path from "path";

const HOME = process.env.VL_E2E_HOME ?? "/tmp/corvin-vl-e2e";
const seed = () => JSON.parse(fs.readFileSync(path.join(HOME, "seed.json"), "utf-8"));
const SHOTS = process.env.VL_SHOTS ?? "test-results/video-lifecycle";

async function open(page: Page, jobId?: string) {
  const r = await page.request.get("/v1/console/auth/local-login");
  expect(r.ok(), "local-login on the isolated console").toBeTruthy();
  await page.addInitScript(() => {
    localStorage.setItem("corvin-dark-default-v2", "1");
    localStorage.setItem("corvin-theme", "light");
  });
  await page.setViewportSize({ width: 1400, height: 1800 });
  await page.goto(`/console/app/video-producer${jobId ? `?job=${jobId}` : ""}`);
  await expect(page.getByTestId("video-producer")).toBeVisible();
}

test.describe.configure({ mode: "serial" });

test("cancel a job that is being produced", async ({ page }) => {
  const s = seed();
  await open(page, s.job_running);
  await expect(page.getByTestId("playback-pending")).toBeVisible();
  await page.getByTestId("cancel-job").click();
  await expect(page.getByTestId("stage-cancelled")).toBeVisible({ timeout: 30_000 });
  const rec = JSON.parse(fs.readFileSync(path.join(s.store, "jobs", `${s.job_running}.json`), "utf-8"));
  expect(rec.status).toBe("cancelled");
});

test("delete a finished video: confirmation, Keep keeps it, Delete removes every file", async ({ page }) => {
  const s = seed();
  const videoDir = path.join(s.store, "videos", s.job_delete);
  const record = path.join(s.store, "jobs", `${s.job_delete}.json`);
  expect(fs.existsSync(path.join(videoDir, "output.mp4"))).toBeTruthy();
  expect(fs.existsSync(path.join(videoDir, "report.json"))).toBeTruthy();

  await open(page, s.job_delete);
  // quota shown: what the store holds against the tenant's 1 GB quota
  await expect(page.getByTestId("storage-caption")).toContainText("of the 1.00 GB quota");
  const usedBefore = await page.getByTestId("storage-used").innerText();
  expect(usedBefore).toMatch(/\d+(\.\d+)? (KB|MB)/);

  // another tenant's job cannot be deleted through this session
  const csrf = (await (await page.request.get("/v1/console/auth/whoami")).json()).csrf_token;
  const cross = await page.request.delete(`/v1/console/video/jobs/${s.job_other_tenant}`, { headers: { "X-CSRF-Token": csrf } });
  expect(cross.status()).toBe(404);
  expect(fs.existsSync(path.join(HOME, "tenants", "tenant_b", "video_producer", "jobs", `${s.job_other_tenant}.json`))).toBeTruthy();
  // the running/cancelled job has no delete while running; a finished one does
  await expect(page.getByTestId(`delete-${s.job_delete}`)).toBeVisible();

  await page.getByTestId(`delete-${s.job_delete}`).click();
  const dialog = page.getByTestId(`delete-dialog-${s.job_delete}`);
  await expect(dialog).toContainText("Delete this video?");
  await expect(dialog).toContainText("Deletes the video, its scenes, audio and production report. This cannot be undone.");
  fs.mkdirSync(SHOTS, { recursive: true });
  await page.getByTestId("library").screenshot({ path: path.join(SHOTS, "delete-confirmation.png") });
  await page.getByTestId(`delete-keep-${s.job_delete}`).click();
  await expect(dialog).toHaveCount(0);
  expect(fs.existsSync(record)).toBeTruthy();

  await page.getByTestId(`delete-${s.job_delete}`).click();
  await page.getByTestId(`delete-confirm-${s.job_delete}`).click();
  await expect(page.getByTestId(`job-${s.job_delete}`)).toHaveCount(0);
  await expect(page.getByTestId(`job-${s.job_keep}`)).toBeVisible();
  // gone from disk: record, video directory (MP4, scenes, audio, report), owner entry
  expect(fs.existsSync(record)).toBeFalsy();
  expect(fs.existsSync(videoDir)).toBeFalsy();
  const owners = JSON.parse(fs.readFileSync(path.join(s.store, "owners.json"), "utf-8"));
  expect(owners[s.job_delete]).toBeUndefined();
  expect(owners[s.job_keep]).toBe("console-owner:_default");
  await expect(page.getByTestId("storage-used")).not.toHaveText(usedBefore);
  await page.screenshot({ path: path.join(SHOTS, "after-delete.png"), fullPage: true });
});

test("a full store is shown and refuses a new production", async ({ page }) => {
  const s = seed();
  fs.writeFileSync(s.tenant_yaml, "spec:\n  telemetry:\n    ping_enabled: false\n    healing_traces: false\n  video_producer:\n    storage_quota_gb: 0.000001\n", { mode: 0o600 });
  await open(page);
  await expect(page.getByTestId("storage-full")).toContainText("Video storage is full");
  await expect(page.getByTestId("storage-caption")).toContainText("100 % of the");
  await page.getByTestId("task-input").fill("Explain the storage quota in one scene.");
  await page.getByTestId("start-production").click();
  await expect(page.getByTestId("composer-error")).toContainText("Video storage is full");
  await expect(page.getByTestId("composer-error")).toContainText("Delete videos you no longer need");
  await page.getByTestId("overview-tiles").scrollIntoViewIfNeeded();
  await page.screenshot({ path: path.join(SHOTS, "storage-full.png"), fullPage: true });
  const jobs = fs.readdirSync(path.join(s.store, "jobs")).filter((f) => /^job_[0-9a-f]{8}\.json$/.test(f));
  expect(jobs.sort()).toEqual([`${s.job_running}.json`, `${s.job_keep}.json`].sort());
});
