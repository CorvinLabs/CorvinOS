/**
 * Bridges page — QR code correctness tests.
 *
 * Tests what CAN be verified without authentication:
 * 1. The page route loads (login page or bridges page)
 * 2. The built JavaScript bundle contains the right QR-code URLs
 *    (mobileSetupUrl values are correct after the fix)
 * 3. The qrcode.react import is present in the bundle
 * 4. WhatsApp QR probe URL is correct in the backend source
 *
 * For authenticated wizard behavior, see bridges-qr.authenticated.spec.ts
 * (requires a live session — run manually with `bridge.sh console`).
 */

import { test, expect } from '@playwright/test';
import * as fs from 'fs';
import { fileURLToPath } from 'url';
import * as path from 'path';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const DIST_DIR = path.join(__dirname, '../../dist');

// ── Bundle-level checks (no auth needed) ─────────────────────────────────

test.describe('Bridges QR — bundle correctness', () => {

  test('dist directory exists and has JS chunks', () => {
    expect(fs.existsSync(DIST_DIR)).toBe(true);
    const files = fs.readdirSync(DIST_DIR + '/assets');
    const jsFiles = files.filter(f => f.endsWith('.js'));
    expect(jsFiles.length).toBeGreaterThan(0);
  });

  test('qrcode.react is bundled (QRCodeSVG import present)', () => {
    const assets = fs.readdirSync(DIST_DIR + '/assets');
    const jsFiles = assets.filter(f => f.endsWith('.js'));

    let foundQr = false;
    for (const jsFile of jsFiles) {
      const content = fs.readFileSync(path.join(DIST_DIR, 'assets', jsFile), 'utf-8');
      // qrcode.react renders QR codes; look for characteristic SVG generation code
      if (content.includes('QRCodeSVG') || content.includes('qrcode') || content.includes('ErrorCorrectionLevel')) {
        foundQr = true;
        break;
      }
    }
    expect(foundQr).toBe(true);
  });

  test('t.me BotFather deep-link is in bundle (Telegram mobile QR correct)', () => {
    const assets = fs.readdirSync(DIST_DIR + '/assets');
    const jsFiles = assets.filter(f => f.endsWith('.js'));

    let found = false;
    for (const jsFile of jsFiles) {
      const content = fs.readFileSync(path.join(DIST_DIR, 'assets', jsFile), 'utf-8');
      if (content.includes('t.me/BotFather')) {
        found = true;
        break;
      }
    }
    expect(found).toBe(true);
  });

  test('discord.com/developers is NOT used as mobileSetupUrl (desktop-only portal removed)', () => {
    // In the JS bundle, the mobileSetupUrl for discord was removed.
    // The setupUrl (desktop portal link) should still appear, but only once per channel
    // (not duplicated as mobileSetupUrl).
    // We verify by checking the ChannelMeta objects in the source:
    const bridgesSource = fs.readFileSync(
      path.join(__dirname, '../../src/pages/bridges.tsx'),
      'utf-8',
    );

    // discord mobileSetupUrl should be gone
    const discordSection = bridgesSource.slice(
      bridgesSource.indexOf('discord: {'),
      bridgesSource.indexOf('slack: {'),
    );
    expect(discordSection).not.toContain('mobileSetupUrl: "https://discord.com');

    // slack mobileSetupUrl should be gone
    const slackSection = bridgesSource.slice(
      bridgesSource.indexOf('slack: {'),
      bridgesSource.indexOf('whatsapp: {'),
    );
    expect(slackSection).not.toContain('mobileSetupUrl: "https://api.slack.com');
  });

  test('signal.org/download is NOT used as mobileSetupUrl (CLI cannot be set up from phone)', () => {
    const bridgesSource = fs.readFileSync(
      path.join(__dirname, '../../src/pages/bridges.tsx'),
      'utf-8',
    );

    const signalSection = bridgesSource.slice(
      bridgesSource.indexOf('signal: {'),
      bridgesSource.indexOf('teams: {'),
    );
    expect(signalSection).not.toContain('mobileSetupUrl:');
  });

  test('Teams aka.ms link is NOT used as mobileSetupUrl (docs page, not a portal)', () => {
    const bridgesSource = fs.readFileSync(
      path.join(__dirname, '../../src/pages/bridges.tsx'),
      'utf-8',
    );

    const teamsSection = bridgesSource.slice(
      bridgesSource.indexOf('teams: {'),
      bridgesSource.indexOf('};', bridgesSource.indexOf('teams: {')),
    );
    expect(teamsSection).not.toContain('mobileSetupUrl:');
  });

  test('Email app-passwords URL kept as mobileSetupUrl (valid mobile flow)', () => {
    const bridgesSource = fs.readFileSync(
      path.join(__dirname, '../../src/pages/bridges.tsx'),
      'utf-8',
    );

    const emailSection = bridgesSource.slice(
      bridgesSource.indexOf('email: {'),
      bridgesSource.indexOf('signal: {'),
    );
    expect(emailSection).toContain('mobileSetupUrl: "https://myaccount.google.com/apppasswords"');
  });

  test('WhatsApp QR probe targets /qr.png not / (daemon root may return 404)', () => {
    const setupPy = fs.readFileSync(
      path.join(__dirname, '../../../routes/setup.py'),
      'utf-8',
    );

    // Must probe /qr.png, not just /
    expect(setupPy).toContain('127.0.0.1:7891/qr.png');
    // Must NOT still be probing the root endpoint
    const rootProbePattern = /urlopen\("http:\/\/127\.0\.0\.1:7891\/".*timeout=1\)/;
    expect(rootProbePattern.test(setupPy)).toBe(false);
  });

});

// ── Page-level smoke test (no auth needed) ────────────────────────────────

test.describe('Bridges page — route smoke test', () => {

  test('Bridges route responds (login redirect or bridges content)', async ({ page }) => {
    await page.goto('/console/app/bridges');
    await page.waitForLoadState('load');
    await page.waitForTimeout(1000);

    const content = await page.content();
    // Page must have some content — either login form or bridges page
    expect(content.length).toBeGreaterThan(100);
    // Must not be a blank page or 500 error
    expect(content).not.toContain('Internal Server Error');
    expect(content).not.toContain('Cannot GET');
  });

});
