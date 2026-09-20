/**
 * /app/video-quality-metrics — folded into the Video Producer studio
 * (2026-09-20). The old page rendered one hard-coded record ("1080p",
 * "7200k", three scenes) for ANY job id, and this spec used to assert those
 * literals. The route now only redirects a ?job_id= deep link into
 * /app/video-producer?job=…&tab=quality, where the Quality tab shows what
 * ffprobe measured on the job's real artifacts.
 */
import { test, expect } from "@playwright/test";

test.describe("Video Quality deep link", () => {
  const BASE_URL = "http://localhost:8765";

  test("redirects into the Video Producer studio's Quality tab", async ({ page }) => {
    await page.goto(`${BASE_URL}/console/app/video-quality-metrics?job_id=job_2626f1e8`, { waitUntil: "networkidle" });
    await expect(page).toHaveURL(/\/app\/video-producer\?(?=.*job=job_2626f1e8)(?=.*tab=quality)/);
    await expect(page.locator("h1", { hasText: /Video Producer/ })).toBeVisible();
  });

  test("without a job id it still lands on the studio", async ({ page }) => {
    await page.goto(`${BASE_URL}/console/app/video-quality-metrics`, { waitUntil: "networkidle" });
    await expect(page).toHaveURL(/\/app\/video-producer\?tab=quality/);
  });
});
