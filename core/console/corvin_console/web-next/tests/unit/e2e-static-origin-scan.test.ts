// @vitest-environment node
/**
 * W-0 static gate: no Playwright file can reach the live console except through
 * tests/e2e/sandbox-origin.ts.
 *
 * Scans every Playwright spec, helper, config and global setup in the repo
 * (any *.spec.ts, playwright*.config.ts, global-setup*, and every .ts under a
 * tests/e2e* or tests/fixtures directory) and fails on
 *   - an executable `8765` literal (comment lines are allowed), or
 *   - a direct read of a console-origin variable (process.env.CONSOLE_BASE_URL,
 *     BASE_URL, FORGE_LIVE_URL, ...), which bypasses the live refusal.
 * sandbox-origin.ts itself is the one exception. Vitest files (*.test.ts) are
 * not Playwright files and are not scanned.
 *
 * Also keeps LIVE_ONLY_SPEC_GLOBS in sync with the spec headers.
 *
 * E2E_SCAN_ROOT overrides the scanned repo root (used to show the scan is red on
 * a pre-fix tree).
 */
import { describe, it, expect } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { LIVE_HEADER_NOT_LIVE, LIVE_HEADER_PATTERN, LIVE_ONLY_SPEC_GLOBS } from '../e2e/sandbox-origin';

const WEB_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const REPO = process.env.E2E_SCAN_ROOT || path.resolve(WEB_ROOT, '../../../..');
const SKIP_DIRS = new Set(['node_modules', '.git', 'dist', 'dist.next', 'dist.prev', '.claude', 'playwright-report', 'test-results']);
const COMMENT = /^\s*(\*|\/\/|\/\*)/;
const ORIGIN_ENV = /process\.env(?:\.|\[['"])(CONSOLE_BASE_URL|BASE_URL|FORGE_LIVE_URL|PLAYWRIGHT_TEST_BASE_URL|CORVIN_GATEWAY_URL|GATEWAY_URL|API_BASE_URL|API_BASE)\b/;

function isPlaywrightFile(rel: string, name: string): boolean {
  if (!/\.tsx?$/.test(name) || /\.test\.tsx?$/.test(name) || name === 'sandbox-origin.ts') return false;
  return (
    name.includes('.spec.') ||
    (name.startsWith('playwright') && name.includes('.config.')) ||
    name.startsWith('global-setup') ||
    /(^|\/)tests\/e2e[^/]*\//.test(rel) ||
    /(^|\/)tests\/fixtures\//.test(rel)
  );
}

function walk(dir: string, out: string[] = []): string[] {
  for (const ent of fs.readdirSync(dir, { withFileTypes: true })) {
    if (ent.isDirectory()) {
      if (!SKIP_DIRS.has(ent.name)) walk(path.join(dir, ent.name), out);
    } else {
      const full = path.join(dir, ent.name);
      if (isPlaywrightFile(path.relative(REPO, full).split(path.sep).join('/'), ent.name)) out.push(full);
    }
  }
  return out;
}

/** Violations in one file's text: `<line>: <reason>: <code>`. */
export function scanText(text: string): string[] {
  const out: string[] = [];
  text.split('\n').forEach((line, i) => {
    if (COMMENT.test(line)) return;
    if (line.includes('8765')) out.push(`${i + 1}: 8765 literal: ${line.trim().slice(0, 120)}`);
    else if (ORIGIN_ENV.test(line)) out.push(`${i + 1}: direct origin env read: ${line.trim().slice(0, 120)}`);
  });
  return out;
}

const FILES = walk(REPO).sort();
const rel = (f: string) => path.relative(REPO, f).split(path.sep).join('/');

function header(text: string): string {
  const lines: string[] = [];
  for (const l of text.split('\n').slice(0, 40)) {
    const s = l.trim();
    if (s === '' || s.startsWith('/*') || s.startsWith('*') || s.startsWith('//')) lines.push(l);
    else break;
  }
  return lines.join('\n');
}

describe('static scan: Playwright files reach the console only through sandbox-origin.ts', () => {
  it('positive control: the scan reaches the files the policy covers', () => {
    const names = new Set(FILES.map(rel));
    for (const must of [
      'core/console/corvin_console/web-next/playwright.config.ts',
      'core/console/corvin_console/web-next/tests/e2e/global-setup-adr0124.ts',
      'core/console/corvin_console/web-next/tests/e2e-live/forge-live/10-robustness.spec.ts',
      'core/console/corvin_console/web-next/tests/e2e-live/forge-live/live.ts',
      'core/console/corvin_console/web-next/tests/fixtures/panel-fixtures.ts',
      'playwright.e2e.config.ts',
      'tests/e2e/fixtures.ts',
      'tests/e2e/test_performance_accessibility.spec.ts',
    ]) {
      expect(names.has(must), `${must} not reached by the scan (root ${REPO})`).toBe(true);
    }
    expect(FILES.length).toBeGreaterThan(150);
  });

  it('positive control: the scanner flags the shapes the review found', () => {
    expect(scanText("  const anon = async () => request.newContext({ baseURL: process.env.FORGE_LIVE_URL ?? 'http://127.0.0.1:8765' });")).toHaveLength(1);
    expect(scanText('const BASE_URL = process.env.BASE_URL || "http://localhost:5173/console";')).toHaveLength(1);
    expect(scanText("export const API_BASE = 'http://127.0.0.1:8765/api';")).toHaveLength(1);
    expect(scanText(' * Run: CORVIN_E2E_ALLOW_LIVE=1 CONSOLE_BASE_URL=http://127.0.0.1:8765/console')).toHaveLength(0);
    expect(scanText('// the live console on :8765 is never touched')).toHaveLength(0);
  });

  it('no executable :8765 literal or direct origin env read outside sandbox-origin.ts', () => {
    const violations: string[] = [];
    for (const f of FILES) {
      for (const v of scanText(fs.readFileSync(f, 'utf8'))) violations.push(`${rel(f)}:${v}`);
    }
    expect(violations, violations.join('\n')).toEqual([]);
  });
});

describe('keep-live list matches the spec headers (repo-wide)', () => {
  // Every Playwright spec in the repo, except tests/e2e-live/ (keep-live by directory: its configs
  // and helpers are the live suites).
  const specs = FILES.filter((f) => f.endsWith('.spec.ts') && !rel(f).includes('/tests/e2e-live/'));
  const listed = (f: string) => LIVE_ONLY_SPEC_GLOBS.some((g) => rel(f).endsWith(g.replace(/^\*\*\//, '')));

  it('every spec whose header claims a live console is keep-live or explicitly not-live', () => {
    const missing = specs.filter((f) => LIVE_HEADER_PATTERN.test(header(fs.readFileSync(f, 'utf8'))))
      .filter((f) => !listed(f) && !(path.basename(f) in LIVE_HEADER_NOT_LIVE))
      .map(rel);
    expect(missing, `header says live but not in LIVE_ONLY_SPEC_GLOBS:\n${missing.join('\n')}`).toEqual([]);
  });

  it('every keep-live entry names an existing spec whose header claims a live console', () => {
    const stale = LIVE_ONLY_SPEC_GLOBS.filter((g) => {
      const hit = specs.find((f) => rel(f).endsWith(g.replace(/^\*\*\//, '')));
      return !hit || !LIVE_HEADER_PATTERN.test(header(fs.readFileSync(hit, 'utf8')));
    });
    expect(stale).toEqual([]);
  });

  it('every not-live exception still names an existing spec', () => {
    const names = new Set(FILES.map((f) => path.basename(f)));
    expect(Object.keys(LIVE_HEADER_NOT_LIVE).filter((n) => !names.has(n))).toEqual([]);
  });
});
