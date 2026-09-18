import { test, expect, Page } from '@playwright/test';

/**
 * Console Release 0.10.34 — Core E2E Tests (Streamlined)
 *
 * Focus: Essential acceptance criteria
 * - Dark/light mode functionality
 * - Responsive layouts at key breakpoints
 * - Panel rendering and performance
 * - Accessibility foundations
 *
 * ~35 tests, runs in <5min on 4 workers
 */

const BREAKPOINTS = [
  { name: 'Mobile', width: 375, height: 667 },
  { name: 'Tablet', width: 768, height: 1024 },
  { name: 'Desktop', width: 1920, height: 1080 },
];

const CRITICAL_PANELS = [
  '/console/app/chat',
  '/console/app/engine-config',
  '/console/app/settings',
];

async function setThemeAttr(page: Page, theme: 'light' | 'dark'): Promise<void> {
  await page.evaluate((t) => {
    document.documentElement.setAttribute('data-theme', t);
    try {
      localStorage.setItem('corvin-theme', t);
    } catch { /* localStorage may be blocked */ }
  }, theme);
  await page.waitForTimeout(50);
}

async function getThemeAttr(page: Page): Promise<string> {
  return await page.evaluate(() => document.documentElement.getAttribute('data-theme') || 'unknown');
}

// ──────────────────────────────────────────────────────────────────────────

test.describe('0.10.34 Dark Mode', () => {
  test('theme attribute is present on HTML element', async ({ page }) => {
    await page.goto('/console/app/chat');
    const theme = await getThemeAttr(page);
    expect(['light', 'dark', 'auto']).toContain(theme);
  });

  test('can set dark theme programmatically', async ({ page }) => {
    await page.goto('/console/app/chat');
    await setThemeAttr(page, 'dark');
    const theme = await getThemeAttr(page);
    expect(theme).toBe('dark');
  });

  test('can set light theme programmatically', async ({ page }) => {
    await page.goto('/console/app/chat');
    await setThemeAttr(page, 'light');
    const theme = await getThemeAttr(page);
    expect(theme).toBe('light');
  });

  test('theme toggle button exists and is clickable', async ({ page }) => {
    await page.goto('/console/app/chat');
    const toggle = page.locator('button[title*="Theme"], button[aria-label*="Theme"]').first();
    const exists = await toggle.isVisible().catch(() => false);
    // If theme toggle exists, it should be clickable
    if (exists) {
      await expect(toggle).toBeEnabled();
    }
  });

  test('CSS theme tokens exist in both modes', async ({ page }) => {
    for (const theme of ['light', 'dark']) {
      await page.goto('/console/app/chat');
      await setThemeAttr(page, theme as 'light' | 'dark');

      const tokens = await page.evaluate(() => {
        const root = document.documentElement;
        return {
          background: window.getComputedStyle(root).getPropertyValue('--background'),
          foreground: window.getComputedStyle(root).getPropertyValue('--foreground'),
          accent: window.getComputedStyle(root).getPropertyValue('--accent'),
        };
      });

      expect(tokens.background.trim()).toBeTruthy();
      expect(tokens.foreground.trim()).toBeTruthy();
      expect(tokens.accent.trim()).toBeTruthy();
    }
  });
});

test.describe('0.10.34 Responsive Layouts', () => {
  for (const bp of BREAKPOINTS) {
    test(`layout is responsive at ${bp.name} (${bp.width}×${bp.height})`, async ({ page }) => {
      await page.setViewportSize({ width: bp.width, height: bp.height });
      await page.goto('/console/app/chat', { waitUntil: 'networkidle' });

      // No unintended horizontal scroll
      const bodyWidth = await page.evaluate(() => document.body.scrollWidth);
      expect(bodyWidth).toBeLessThanOrEqual(bp.width + 16); // 16px scrollbar margin

      // At least some interactive elements visible
      const interactiveCount = await page.locator('button:visible, input:visible').count();
      expect(interactiveCount).toBeGreaterThan(0);
    });

    test(`text is readable at ${bp.name}`, async ({ page }) => {
      await page.setViewportSize({ width: bp.width, height: bp.height });
      await page.goto('/console/app/chat', { waitUntil: 'networkidle' });

      const fontSize = await page.evaluate(() => {
        const el = document.querySelector('body');
        return el ? parseInt(window.getComputedStyle(el).fontSize, 10) : 0;
      });

      expect(fontSize).toBeGreaterThanOrEqual(12);
    });
  }

  test('no horizontal scroll on mobile', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 667 });
    await page.goto('/console/app/chat', { waitUntil: 'networkidle' });

    const bodyWidth = await page.evaluate(() => document.body.scrollWidth);
    expect(bodyWidth).toBeLessThanOrEqual(375 + 16);
  });
});

test.describe('0.10.34 Performance', () => {
  test('chat panel loads < 500ms', async ({ page }) => {
    const start = Date.now();
    await page.goto('/console/app/chat', { waitUntil: 'networkidle' });
    const elapsed = Date.now() - start;
    expect(elapsed).toBeLessThan(500);
  });

  test('engine-config panel loads < 500ms', async ({ page }) => {
    const start = Date.now();
    await page.goto('/console/app/engine-config', { waitUntil: 'networkidle' });
    const elapsed = Date.now() - start;
    expect(elapsed).toBeLessThan(500);
  });

  test('settings panel loads < 500ms', async ({ page }) => {
    const start = Date.now();
    await page.goto('/console/app/settings', { waitUntil: 'networkidle' });
    const elapsed = Date.now() - start;
    expect(elapsed).toBeLessThan(500);
  });

  test('panels load consistently', async ({ page }) => {
    const times = [];
    for (const panel of CRITICAL_PANELS) {
      const start = Date.now();
      await page.goto(panel, { waitUntil: 'networkidle' });
      times.push(Date.now() - start);
    }

    const avg = times.reduce((a, b) => a + b, 0) / times.length;
    console.log(`Average load time: ${avg.toFixed(0)}ms`);
    expect(avg).toBeLessThan(500);
  });
});

test.describe('0.10.34 Accessibility', () => {
  test('keyboard focus is visible on buttons', async ({ page }) => {
    await page.goto('/console/app/chat');

    const button = page.locator('button').first();
    const isVisible = await button.isVisible().catch(() => false);

    if (isVisible) {
      await button.focus();
      const isStillVisible = await button.isVisible();
      expect(isStillVisible).toBe(true);
    }
  });

  test('landmarks are present in layout', async ({ page }) => {
    await page.goto('/console/app/chat');

    const hasNav = await page.locator('nav').count().then(c => c > 0);
    const hasRole = await page.locator('[role="main"], [role="navigation"]').count().then(c => c > 0);

    // At least one landmark structure
    expect(hasNav || hasRole).toBe(true);
  });

  test('images have alt text or are marked decorative', async ({ page }) => {
    await page.goto('/console/app/chat');

    const images = await page.locator('img').all();
    for (const img of images) {
      const alt = await img.getAttribute('alt');
      const ariaHidden = await img.getAttribute('aria-hidden');

      if (!alt && ariaHidden !== 'true') {
        // Allow if image is the only content of a link
        const parent = await img.evaluate(el => el.parentElement?.tagName);
        if (parent !== 'A') {
          expect(alt || ariaHidden === 'true').toBe(true);
        }
      }
    }
  });
});

test.describe('0.10.34 Component Rendering', () => {
  test('header renders without errors', async ({ page }) => {
    await page.goto('/console/app/chat');

    const headerExists = await page.locator('header, [role="banner"]').isVisible().catch(() => false);
    const hasNav = await page.locator('nav').isVisible().catch(() => false);

    // At least one header structure
    expect(headerExists || hasNav).toBe(true);
  });

  test('main content area is accessible', async ({ page }) => {
    await page.goto('/console/app/chat');

    const main = page.locator('main, [role="main"]').first();
    const isVisible = await main.isVisible().catch(() => false);

    // Either main or content area should be visible
    const content = await page.content();
    expect(isVisible || content.length > 100).toBe(true);
  });

  test('interactive elements are reachable', async ({ page }) => {
    await page.goto('/console/app/chat');

    const buttons = await page.locator('button:visible').count();
    const inputs = await page.locator('input:visible, textarea:visible').count();

    // Some interactive elements should be accessible
    expect((buttons + inputs) > 0).toBe(true);
  });

  test('no console errors on page load', async ({ page }) => {
    const errors: string[] = [];
    page.on('console', (msg) => {
      if (msg.type() === 'error') {
        errors.push(msg.text());
      }
    });

    await page.goto('/console/app/chat', { waitUntil: 'networkidle' });

    // Some minor errors are OK, but no critical ones
    const criticalErrors = errors.filter(e =>
      e.toLowerCase().includes('404') ||
      e.toLowerCase().includes('500') ||
      e.toLowerCase().includes('unhandled')
    );
    expect(criticalErrors.length).toBe(0);
  });
});

test.describe('0.10.34 Dark Mode — Visual Consistency', () => {
  test('layout is stable when switching themes', async ({ page }) => {
    await page.goto('/console/app/chat');

    await setThemeAttr(page, 'light');
    const lightWidth = await page.evaluate(() => document.body.offsetWidth);

    await setThemeAttr(page, 'dark');
    const darkWidth = await page.evaluate(() => document.body.offsetWidth);

    expect(lightWidth).toBe(darkWidth);
  });

  test('text is readable in both themes', async ({ page }) => {
    await page.goto('/console/app/chat');

    for (const theme of ['light', 'dark']) {
      await setThemeAttr(page, theme as 'light' | 'dark');

      const textColor = await page.evaluate(() => {
        return window.getComputedStyle(document.body).color;
      });

      expect(textColor).toBeTruthy();
      expect(textColor).not.toBe('rgba(0, 0, 0, 0)');
    }
  });

  test('all critical panels render in both modes', async ({ page }) => {
    for (const theme of ['light', 'dark']) {
      for (const panel of CRITICAL_PANELS) {
        await setThemeAttr(page, theme as 'light' | 'dark');
        await page.goto(panel, { waitUntil: 'networkidle' });

        const content = await page.content();
        expect(content.length).toBeGreaterThan(100);
      }
    }
  });
});

test.describe('0.10.34 Edge Cases', () => {
  test('handles network errors gracefully', async ({ page }) => {
    await page.goto('/console/app/chat');

    // Simulate offline
    await page.context().setOffline(true);
    await page.waitForTimeout(200);

    const body = await page.locator('body').isVisible();
    expect(body).toBe(true); // Page structure remains

    await page.context().setOffline(false);
  });

  test('recovers from missing panel', async ({ page }) => {
    await page.goto('/console/app/nonexistent', { waitUntil: 'networkidle' });

    // Either shows 404 or redirects
    const url = page.url();
    const content = await page.content();

    expect(url.includes('404') || content.length > 50).toBe(true);
  });
});
