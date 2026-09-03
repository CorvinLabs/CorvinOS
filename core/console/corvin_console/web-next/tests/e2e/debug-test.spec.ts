import { test } from '@playwright/test';

test('debug: show actual URLs and setup', async ({ page, baseURL }) => {
  console.log(`[DEBUG] baseURL: ${baseURL}`);
  console.log(`[DEBUG] About to navigate to /app/vibe-engineering`);

  const fullUrl = new URL('/app/vibe-engineering', baseURL || 'http://127.0.0.1:8765/console').toString();
  console.log(`[DEBUG] Full URL: ${fullUrl}`);

  const response = await page.goto('/app/vibe-engineering', { waitUntil: 'domcontentloaded' });
  console.log(`[DEBUG] Response status: ${response?.status()}`);
  console.log(`[DEBUG] Response ok: ${response?.ok()}`);
  
  const body = await page.content();
  console.log(`[DEBUG] Response body (first 300 chars): ${body.substring(0, 300)}`);
});
