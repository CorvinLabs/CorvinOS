/**
 * DataHub panel, driven in a real browser against the running console.
 *
 * Until 2026-10-01 this panel posted to /v1/datahub/ingest and /v1/datahub/create,
 * which do not exist, so every click answered 404; the previous version of this
 * spec only checked that the page rendered.
 */
import fs from 'fs';
import os from 'os';
import path from 'path';
// Plain @playwright/test on purpose: panel-fixtures mocks /auth/whoami without a
// csrf_token, under which no raw-fetch mutation can succeed. This spec uses the real
// session from the global setup and the real API.
import { test, expect } from '@playwright/test';

const PANEL = `${process.env.CONSOLE_BASE_URL || 'http://127.0.0.1:8765/console'}/app/datahub-unified`;

test.describe('DataHub panel', () => {
  test('analyze → create → list → delete through the real API', async ({ page }) => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'datahub-e2e-'));
    const src = path.join(dir, 'orders.json');
    fs.writeFileSync(src, JSON.stringify([{ name: 'a', score: 1 }, { name: 'b', score: 2 }]));
    const name = `e2e-${Date.now()}`;

    await page.goto(PANEL);
    await page.getByPlaceholder('/home/me/data/orders.json').fill(src);
    await page.getByRole('button', { name: 'Analyze' }).click();

    const analysis = page.getByTestId('datahub-analysis');
    await expect(analysis).toContainText('Rows: 2');
    await expect(analysis).toContainText('name (string), score (number)');
    await expect(analysis).toContainText('0 secret(s)');

    await page.getByPlaceholder('artifact_name').fill(name);
    await page.getByPlaceholder('What is this artifact for?').fill('Browser E2E artifact');
    await page.getByRole('button', { name: 'Create artifact' }).click();

    const created = page.getByTestId('datahub-created');
    await expect(created).toContainText(name);
    await expect(created).toContainText('from 2 row(s)');
    await expect(created).toContainText('Based on analysis of 2 rows');

    const row = page.locator('li', { hasText: name });
    await expect(row).toHaveCount(1);
    await row.getByRole('button', { name: 'Delete' }).click();
    await expect(page.locator('li', { hasText: name })).toHaveCount(0);

    fs.rmSync(dir, { recursive: true, force: true });
  });

  test('a file with a secret is blocked before creation', async ({ page }) => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'datahub-e2e-'));
    const src = path.join(dir, 'leaky.json');
    fs.writeFileSync(src, JSON.stringify([{ note: 'ghp_' + 'a'.repeat(36) }]));

    await page.goto(PANEL);
    await page.getByPlaceholder('/home/me/data/orders.json').fill(src);
    await page.getByRole('button', { name: 'Analyze' }).click();
    await expect(page.getByTestId('datahub-analysis')).toContainText('1 secret(s)');
    await expect(page.getByText('This file contains secrets')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Create artifact' })).toHaveCount(0);

    fs.rmSync(dir, { recursive: true, force: true });
  });
});
