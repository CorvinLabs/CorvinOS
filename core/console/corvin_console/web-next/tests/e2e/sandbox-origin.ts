/**
 * Console origin policy for Playwright (W-0, release readiness).
 *
 * The live console listens on :8765. Test runs must never reach it by default:
 *   - the default origin is the isolated sandbox backend on :8799
 *     (scripts/start-isolated-e2e-backend.sh, throwaway CORVIN_HOME);
 *   - any URL on port 8765 is refused unless CORVIN_E2E_ALLOW_LIVE=1 is set
 *     explicitly in the environment of the run.
 *
 * Every Playwright config, every global setup and every spec that needs a
 * console origin resolves it through this module, so the refusal is one check
 * and cannot be bypassed by a spec that hardcodes the port.
 */

import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

/** Sandbox console origin (isolated backend, throwaway CORVIN_HOME). */
export const SANDBOX_CONSOLE_ORIGIN = 'http://127.0.0.1:8799';

/** Port of the live console. Never a default. */
export const LIVE_CONSOLE_PORT = '8765';

/** Environment variable that explicitly opts a run into the live console. */
export const LIVE_OPT_IN_ENV = 'CORVIN_E2E_ALLOW_LIVE';

/**
 * Specs whose header says they run against the live console, host, gateway or
 * engine (keep-live class, SPEC_TRIAGE.md). The default run excludes them; they
 * run only with CORVIN_E2E_ALLOW_LIVE=1. tests/unit/e2e-static-origin-scan.test.ts
 * keeps this list and the headers in sync (LIVE_HEADER_PATTERN).
 */
export const LIVE_ONLY_SPEC_GLOBS: string[] = [
  '**/aco-observatory.spec.ts',
  '**/aco-self-repair.spec.ts',
  '**/adr-0177-nervous-system.spec.ts',
  '**/bridges-qr.spec.ts',
  '**/ccc-live-e2e.spec.ts',
  '**/chat-btw-steer.spec.ts',
  '**/chat-unified-panel.spec.ts',
  '**/console-command-center.spec.ts',
  '**/complex-chained-flows.spec.ts',
  '**/critical-flows/real-backend.spec.ts',
  '**/data-sources-e2e.spec.ts',
  '**/demo-spotify-analysis.spec.ts',
  '**/forge-run-survives-reload.spec.ts',
  '**/knowledge-graph-explorer.spec.ts',
  '**/ldd-active.spec.ts',
  '**/marketplace-console.spec.ts',
  '**/models-console.spec.ts',
  '**/models-redirects.spec.ts',
  '**/nordtech-command-center.spec.ts',
  '**/panel-datahub-unified.spec.ts',
  '**/peer-live-status-symbols.spec.ts',
  '**/skill-forge-live.spec.ts',
  '**/skill-forge.spec.ts',
  '**/tasks-live.spec.ts',
  '**/vibe-engineering-panel.spec.ts',
  '**/workflow-composer.spec.ts',
  '**/workflow-peer-agent.spec.ts',
  '**/workflow-readiness.spec.ts',
  // repo-root tests/e2e (tests/e2e/playwright.config.ts applies the same testIgnore)
  '**/tests/e2e/github-integration.spec.ts',
  '**/tests/e2e/learning_dashboard.spec.ts',
];

/**
 * Header text that marks a spec as keep-live (matched against the leading comment
 * block). Specs that match only incidentally are listed in LIVE_HEADER_NOT_LIVE.
 */
export const LIVE_HEADER_PATTERN =
  /\bLIVE\b|live host|live console|running console|real console|live install|live gateway|live engine|live session|live demo|live wiring|live E2E|production bundle|on :8765|the live one|(?:127\.0\.0\.1|localhost):8765\b|port 8765|running (?:CorvinOS )?gateway/i;

/** Specs whose header matches LIVE_HEADER_PATTERN but that do not need the live console, with the reason. */
export const LIVE_HEADER_NOT_LIVE: Record<string, string> = {
  'chat-stt-composer.spec.ts': '"live SpeechRecognition" is a browser API instance, not the console',
  'compliance-audit.spec.ts': '"live event feed" names a UI card',
  'forge-tab-order.spec.ts': '"facts that live in different places" (verb)',
  'plugins.spec.ts': 'header says it runs "without needing a live" console',
  '98-live-chain-detector.spec.ts': 'feeds a synthetic "live chain" to the detector on the isolated forge-system backend',
  '99-isolation-proof.spec.ts': 'proves the LIVE install is untouched, from the isolated forge-system backend',
};

type EnvLike = Record<string, string | undefined>;

function portOf(raw: string): string {
  const u = new URL(raw);
  if (u.port) return u.port;
  return u.protocol === 'https:' ? '443' : '80';
}

/** True when the URL points at the live console port (any host: fail closed). */
export function isLiveConsoleUrl(raw: string): boolean {
  try {
    return portOf(raw) === LIVE_CONSOLE_PORT;
  } catch {
    return false;
  }
}

/**
 * Throws unless the URL is not the live console or the run carries the explicit
 * opt-in. `what` names the variable or setting that supplied the URL.
 */
export function assertNotLiveConsole(raw: string, what = 'console origin', env: EnvLike = process.env): void {
  if (!isLiveConsoleUrl(raw)) return;
  if (env[LIVE_OPT_IN_ENV] === '1') return;
  throw new Error(
    `refusing to run: ${what} = ${raw} targets the live console (port ${LIVE_CONSOLE_PORT}). ` +
      `Tests default to the sandbox (${SANDBOX_CONSOLE_ORIGIN}). ` +
      `To run against the live console on purpose, set ${LIVE_OPT_IN_ENV}=1 explicitly.`,
  );
}

/** Console base URL (including /console) for the run; refuses :8765 without opt-in. */
export function resolveConsoleBaseUrl(env: EnvLike = process.env): string {
  const raw = env.CONSOLE_BASE_URL || `${SANDBOX_CONSOLE_ORIGIN}/console`;
  assertNotLiveConsole(raw, 'CONSOLE_BASE_URL', env);
  return raw;
}

/** Origin (scheme + host + port) of the run's console; refuses :8765 without opt-in. */
export function resolveConsoleOrigin(env: EnvLike = process.env): string {
  return new URL(resolveConsoleBaseUrl(env)).origin;
}

/**
 * May Playwright reuse an already-running Vite dev server (:5173)?
 *
 * A reused server keeps whatever CORVIN_GATEWAY_URL it was started with — the
 * live :8765 by default (vite.config.ts) — so the webServer `env` pin does not
 * apply to it. Only an explicit live run (CORVIN_E2E_ALLOW_LIVE=1, outside CI)
 * may reuse one. A sandbox run always spawns its own pinned server; if :5173 is
 * already taken, Playwright fails instead of silently proxying to the live gateway.
 */
export function mayReuseDevServer(env: EnvLike = process.env): boolean {
  if (env.CI) return false;
  if (env[LIVE_OPT_IN_ENV] !== '1') return false;
  // The opt-in alone is not enough: a run whose origin is still the sandbox must spawn its own server.
  return isLiveConsoleUrl(resolveConsoleBaseUrl(env));
}

/**
 * Resolve an optional, spec- or config-specific URL variable (e.g. BASE_URL,
 * FORGE_LIVE_URL). Falls back to the sandbox origin; refuses :8765 without opt-in.
 */
export function resolveOptionalOrigin(varName: string, env: EnvLike = process.env): string {
  return resolveUrlVar(varName, SANDBOX_CONSOLE_ORIGIN, env);
}

/**
 * Resolve a URL variable with a spec-specific non-live fallback (e.g. the Vite dev
 * server). The variable, if set, passes the same live-console refusal.
 */
export function resolveUrlVar(varName: string, fallback: string, env: EnvLike = process.env): string {
  const raw = env[varName] || fallback;
  assertNotLiveConsole(raw, varName, env);
  return raw;
}

/**
 * Environment for a console backend that a Playwright `webServer` entry starts.
 * Everything the backend can write points into a throwaway directory under the OS
 * temp dir — never the operator's ~/.corvin, ~/.config or the sibling
 * Corvin-Knowledge checkout:
 *   CORVIN_HOME, XDG_CONFIG_HOME, HOME, an ABSOLUTE CORVIN_AUDIT_ANCHOR_KEY
 *   (a relative one is refused by the audit writer), CORVIN_KB_REPO -> nowhere.
 * FORGE_ROOT must be UNSET for the backend (the boot tripwire refuses a redirect);
 * the command prefix SANDBOX_COMMAND_PREFIX does that.
 */
export function sandboxServerEnv(name: string): Record<string, string> {
  const base = path.join(os.tmpdir(), `corvin-e2e-${name}`);
  const dirs = { home: path.join(base, 'home'), xdg: path.join(base, 'xdg'), user: path.join(base, 'user-home') };
  for (const d of Object.values(dirs)) fs.mkdirSync(d, { recursive: true });
  return {
    CORVIN_HOME: dirs.home,
    XDG_CONFIG_HOME: dirs.xdg,
    HOME: dirs.user,
    CORVIN_AUDIT_ANCHOR_KEY: path.join(dirs.home, 'audit-anchor.key'),
    CORVIN_KB_REPO: path.join(base, 'no-knowledge-base'),
    CORVIN_TELEMETRY_OPTIN: 'false',
  };
}

/** Prefix for a webServer command that must not inherit FORGE_ROOT (POSIX `env -u`). */
export const SANDBOX_COMMAND_PREFIX = 'env -u FORGE_ROOT';
