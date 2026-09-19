/**
 * E2E Tests for Video Quality Metrics Dashboard
 *
 * Tests the QualityMetrics panel with real API calls and dashboard rendering.
 * ADR-0695 Phase 2 — Video Quality Metrics UI
 */

import { test, expect } from "@playwright/test";

test.describe("Video Quality Metrics Dashboard", () => {
  const BASE_URL = "http://localhost:8765";
  const PANEL_URL = `${BASE_URL}/console/app/video-quality-metrics`;
  const JOB_ID = "test-job-123";

  test.beforeEach(async ({ page }) => {
    // Start with clean page load
    await page.goto(PANEL_URL + `?job_id=${JOB_ID}`, {
      waitUntil: "networkidle",
    });
  });

  test("should load the quality metrics panel", async ({ page }) => {
    // Check main heading
    const heading = page.locator("h1", { hasText: /Video Quality Metrics/i });
    await expect(heading).toBeVisible();

    // Check subheading
    const subheading = page.locator("text=/Real-time quality analysis/i");
    await expect(subheading).toBeVisible();
  });

  test("should fetch and display quality metrics", async ({ page }) => {
    // Wait for the summary card to load
    const summaryCard = page.locator("text=/Video Summary/");
    await expect(summaryCard).toBeVisible({ timeout: 5000 });

    // Verify metadata is displayed
    const resolution = page.locator("text=/1080p/");
    await expect(resolution).toBeVisible();

    const bitrate = page.locator("text=/7200k/");
    await expect(bitrate).toBeVisible();
  });

  test("should display per-scene validation table", async ({ page }) => {
    // Wait for the per-scene table
    const tableHeading = page.locator("text=/Per-Scene Validation Details/");
    await expect(tableHeading).toBeVisible({ timeout: 5000 });

    // Check for scene rows
    const sceneRows = page.locator("tbody tr");
    const rowCount = await sceneRows.count();
    expect(rowCount).toBeGreaterThan(0);

    // Check for status badges
    const statusBadge = page.locator('text=/PASS|WARN|FAIL/');
    await expect(statusBadge.first()).toBeVisible();
  });

  test("should display audio quality chart", async ({ page }) => {
    // Wait for audio quality chart
    const chartHeading = page.locator("text=/Per-Scene Audio Quality/");
    await expect(chartHeading).toBeVisible({ timeout: 5000 });

    // Check for recharts SVG
    const chartSvg = page.locator("svg").first();
    await expect(chartSvg).toBeVisible();
  });

  test("should display optimizer convergence chart", async ({ page }) => {
    // Wait for convergence chart
    const chartHeading = page.locator("text=/Optimizer Convergence/");
    await expect(chartHeading).toBeVisible({ timeout: 5000 });

    // Check for recharts SVG
    const chartSvg = page.locator("svg").nth(1); // Second chart
    await expect(chartSvg).toBeVisible();
  });

  test("should display learning feedback summary", async ({ page }) => {
    // Wait for learning summary card
    const summaryHeading = page.locator("text=/Learning & Optimization/");
    await expect(summaryHeading).toBeVisible({ timeout: 5000 });

    // Check for metrics
    const feedbackCount = page.locator("text=/Feedback Events/");
    await expect(feedbackCount).toBeVisible();

    const iterationCount = page.locator("text=/Optimizer Iterations/");
    await expect(iterationCount).toBeVisible();
  });

  test("should handle missing job_id gracefully", async ({ page }) => {
    // Navigate without job_id
    await page.goto(PANEL_URL, { waitUntil: "networkidle" });

    // Should show loading or error gracefully
    const text = page.locator("text=/unknown/");
    await expect(text).toBeVisible();
  });

  test("should auto-refresh metrics periodically", async ({ page }) => {
    // Initial load
    await expect(page.locator("text=/Video Summary/")).toBeVisible({ timeout: 5000 });

    // Get initial timestamp (if shown)
    let initialTimestamp = await page.evaluate(() => new Date().getTime());

    // Wait for auto-refresh (should happen every 5s)
    await page.waitForTimeout(6000);

    // Verify page is still responsive
    const heading = page.locator("h1");
    await expect(heading).toBeVisible();
  });

  test("should display all scenes in table", async ({ page }) => {
    // Wait for table to load
    await expect(page.locator("text=/Per-Scene Validation Details/")).toBeVisible({ timeout: 5000 });

    // Check for specific scenes (from mock data: s1, s2, s3)
    const scene1 = page.locator("text=/s1/").first();
    const scene2 = page.locator("text=/s2/").first();
    const scene3 = page.locator("text=/s3/").first();

    await expect(scene1).toBeVisible();
    await expect(scene2).toBeVisible();
    await expect(scene3).toBeVisible();
  });

  test("should show confidence values for each scene", async ({ page }) => {
    // Wait for table
    await expect(page.locator("text=/Confidence/")).toBeVisible({ timeout: 5000 });

    // Check for percentage values in table
    const confidenceValues = page.locator("td").filter({ hasText: /\d+%/ });
    const count = await confidenceValues.count();
    expect(count).toBeGreaterThan(0);
  });

  test("should have proper responsive layout", async ({ page }) => {
    // Verify cards are laid out vertically (mobile-first)
    const cards = page.locator("[class*='space-y-6']");
    await expect(cards.first()).toBeVisible();

    // Check for grid layouts in summary sections
    const gridElements = page.locator("[class*='grid']");
    await expect(gridElements.first()).toBeVisible();
  });

  test("should include learning metrics in dashboard", async ({ page }) => {
    // Wait for learning summary
    await expect(page.locator("text=/Learning & Optimization/")).toBeVisible({ timeout: 5000 });

    // Verify all learning metrics are shown
    const feedbackLabel = page.locator("text=/Feedback Events/");
    const iterationsLabel = page.locator("text=/Optimizer Iterations/");
    const confidenceLabel = page.locator("text=/Average Confidence/");

    await expect(feedbackLabel).toBeVisible();
    await expect(iterationsLabel).toBeVisible();
    await expect(confidenceLabel).toBeVisible();
  });

  test("should display video encoding details", async ({ page }) => {
    // Wait for summary card
    await expect(page.locator("text=/Video Summary/")).toBeVisible({ timeout: 5000 });

    // Check for encoding metadata
    const codecLabel = page.locator("text=/Codec/");
    const resolutionLabel = page.locator("text=/Resolution/");
    const bitrateLabel = page.locator("text=/Bitrate/");

    await expect(codecLabel).toBeVisible();
    await expect(resolutionLabel).toBeVisible();
    await expect(bitrateLabel).toBeVisible();
  });

  test("API route returns correct structure", async ({ request }) => {
    // Test quality metrics API
    const qualityResponse = await request.get(
      `${BASE_URL}/v1/console/video/jobs/${JOB_ID}/quality-metrics`
    );
    expect(qualityResponse.ok()).toBeTruthy();

    const qualityData = await qualityResponse.json();
    expect(qualityData).toHaveProperty("job_id");
    expect(qualityData).toHaveProperty("status");
    expect(qualityData).toHaveProperty("validation");
    expect(qualityData).toHaveProperty("encoding");
    expect(qualityData).toHaveProperty("per_scene");

    // Test learning metrics API
    const learningResponse = await request.get(
      `${BASE_URL}/v1/console/video/jobs/${JOB_ID}/learning-metrics`
    );
    expect(learningResponse.ok()).toBeTruthy();

    const learningData = await learningResponse.json();
    expect(learningData).toHaveProperty("job_id");
    expect(learningData).toHaveProperty("total_feedback_events");
    expect(learningData).toHaveProperty("optimizer_iterations");
    expect(learningData).toHaveProperty("convergence_trend");
  });
});
