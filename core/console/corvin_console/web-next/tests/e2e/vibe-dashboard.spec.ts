import { test, expect } from '@playwright/test';

test('Vibe Dashboard Phase 3+4a', async ({ page }) => {
  await page.goto('http://127.0.0.1:8765/console/', { waitUntil: 'networkidle' });
  
  // 1. Hexagon Radar visible
  const svg = page.locator('svg').first();
  await expect(svg).toBeVisible({ timeout: 3000 });
  console.log('✓ Hexagon Radar');
  
  // 2. Tab navigation
  await page.locator('button:has-text("Summary")').click();
  await expect(page.locator('text=Overall Confidence')).toBeVisible({ timeout: 2000 });
  console.log('✓ Summary Tab');
  
  await page.locator('button:has-text("Patterns")').click();
  await expect(page.locator('text=Routing Decisions')).toBeVisible({ timeout: 2000 });
  console.log('✓ Patterns Tab');
  
  // 3. Anomaly Alerts (in Summary)
  await page.locator('button:has-text("Summary")').click();
  const alerts = page.locator('[role="alert"]');
  const alertCount = await alerts.count();
  console.log(`✓ Anomalies (${alertCount} alerts)`);
  
  console.log('\n✅ Phase 3+4a E2E PASSED');
});
