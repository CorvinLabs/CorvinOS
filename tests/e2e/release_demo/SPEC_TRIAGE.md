# Playwright spec and config triage (W-0)

Status: measured on the WP-00 branch (base `3029088f8`, follow-ups `18c7efb9c` and the second review
fix-up). Purpose: every Playwright spec, config and CI workflow that touches the console origin is
classified, so no test run reaches the live console (`:8765`) by default.

- **keep-live**: the spec only means something against the live console, host, gateway or engine
  (its header says so). It runs only with the explicit opt-in `CORVIN_E2E_ALLOW_LIVE=1`; the default
  config excludes it (`LIVE_ONLY_SPEC_GLOBS`, `testIgnore`). Without the opt-in it resolves to the sandbox.
- **sandbox**: runs against the sandbox origin `http://127.0.0.1:8799` (isolated backend,
  `web-next/scripts/start-isolated-e2e-backend.sh`) or against a backend its own config starts with a
  throwaway home.
- **delete**: an orphan no config, script or workflow selects. The five found were removed (see
  "Removed").

## Enforcement (not just this table)

- `core/console/corvin_console/web-next/tests/e2e/sandbox-origin.ts`: the only place an origin is resolved.
  `:8765` is refused at config, global-setup or module load unless `CORVIN_E2E_ALLOW_LIVE=1`.
- `tests/e2e/test_e2e_console_origin.py` scans `tests/**/*.py`: no Playwright test file (one that uses
  `page.goto` or imports Playwright) may name `:8765` in a `page.goto`, `base_url` or `BASE_URL` line.
- `web-next/tests/unit/e2e-static-origin-scan.test.ts` (vitest) fails on any executable `8765` literal and
  on any direct read of an origin variable (`process.env.CONSOLE_BASE_URL`, `BASE_URL`, `FORGE_LIVE_URL`, …)
  in a Playwright spec, helper, config or global setup anywhere in the repo, outside `sandbox-origin.ts`.
  Comment lines are allowed. Run against the pre-fix tree, it reported all 21 bypasses fixed here.
- The same file keeps `LIVE_ONLY_SPEC_GLOBS` in sync with the spec headers (`LIVE_HEADER_PATTERN`;
  incidental matches are in `LIVE_HEADER_NOT_LIVE` with a reason).
- `mayReuseDevServer()`: a sandbox run never reuses an already running `:5173` dev server.
- Python Playwright tests: `tests/e2e/_console_origin.py` (same policy), proven by
  `tests/e2e/test_e2e_console_origin.py`.

## Selection rules

1. `web-next/tests/e2e/**` is selected by the default config. **keep-live** when its leading comment
   matches `LIVE_HEADER_PATTERN` and the match is not incidental (28 specs); otherwise **sandbox**. The pattern also matches a literal live URL (`127.0.0.1:8765`, `localhost:8765`), "port 8765" and "running gateway", so a spec whose header names a backend on :8765 as its prerequisite is keep-live.
2. `web-next/tests/e2e-live/**`: the forge-live and live suites. **keep-live**.
3. `web-next/tests/e2e-fresh/**`: `playwright.fresh-install.config.ts` (own backend). **sandbox**.
4. Repo-root `scripts/*.spec.ts`: the root `playwright.config.ts`, driven by `scripts/run-e2e-tests.sh`
   (documented in `docs/E2E_TESTS.md`), which starts its own standalone console with a throwaway home.
   **sandbox**. (The first triage called these orphans; that was wrong, a script runs them.)
5. Repo-root `tests/e2e/*.spec.ts`: `tests/e2e/playwright.config.ts` (own standalone, throwaway home) or
   `playwright.e2e.config.ts` (an already running console; `:8765` only with the opt-in). **sandbox**,
   except `github-integration.spec.ts` and `learning_dashboard.spec.ts`, whose headers name the live console:
   **keep-live**; `tests/e2e/playwright.config.ts` applies the same `testIgnore` as the default config.
6. The header rule is applied repo-wide and enforced by `tests/unit/e2e-static-origin-scan.test.ts` for every
   scanned spec except `tests/e2e-live/` (keep-live by directory).

## Config triage (12 Playwright configs)

| Config | Origin / server | Class | Selected by |
|---|---|---|---|
| `web-next/playwright.config.ts` | `resolveConsoleBaseUrl()` → `:8799`; `testIgnore` keep-live unless opt-in; dev server `CORVIN_GATEWAY_URL` pinned, `reuseExistingServer: mayReuseDevServer()` | sandbox | `npm run test:e2e` (`test.yml`, `e2e-playwright.yml`, `e2e-nightly.yml`) |
| `web-next/playwright.live.config.ts` | **no baseURL, no globalSetup, no base spread, no `testIgnore`**: it selects `tasks-live.spec.ts` only, and the only guard is spec-level (`tasks-live` resolves its origin through `resolveConsoleBaseUrl()`, refusing `:8765` without the opt-in; the static scan keeps it that way). Without env it lists the spec against the sandbox origin | keep-live | none (manual) |
| `web-next/playwright.forge-live.config.ts` | specs use `BASE` = `resolveOptionalOrigin('FORGE_LIVE_URL')`; its own empty-install backend from the forge-system harness | keep-live | none (manual) |
| `web-next/playwright.adr0124.config.ts` | dev server pinned + `mayReuseDevServer()`; global setup guarded | sandbox | `adr-0124-*.spec.ts`: 1 file, 21 tests (measured with `playwright test --list`) |
| `web-next/playwright.forge-system.config.ts` | own harness backends (`isolated_forge_backend.py`) | sandbox | none (manual) |
| `web-next/playwright.video-quality.config.ts` | own harness backend | sandbox | none (manual) |
| `web-next/playwright.video-lifecycle.config.ts` | own harness backend | sandbox | none (manual) |
| `web-next/playwright.fresh-install.config.ts` | own backend (`start-fresh-install-backend.sh`) | sandbox | none (manual) |
| `web-next/playwright.isolated.config.ts` | `:8799` isolated backend; its dev server entry `mayReuseDevServer()` | sandbox | none (manual) |
| `playwright.config.ts` (repo root, `testDir ./scripts`) | baseURL/port from `resolveConsoleBaseUrl()`; webServer `env -u FORGE_ROOT python -m corvin_console.standalone` with `sandboxServerEnv('root-scripts')` | sandbox | `scripts/run-e2e-tests.sh` |
| `playwright.e2e.config.ts` (repo root, `tests/e2e`) | `resolveOptionalOrigin('BASE_URL')`; no server | keep-live | manual (`tests/e2e/learning_dashboard.spec.ts` header) |
| `tests/e2e/playwright.config.ts` (root tests/e2e) | as the root config, `sandboxServerEnv('root-tests-e2e')` | sandbox | none (manual) |

## webServer backends: what each one can write

| Backend | CORVIN_HOME | XDG_CONFIG_HOME | anchor key (absolute) | FORGE_ROOT | HOME |
|---|---|---|---|---|---|
| root `playwright.config.ts`, `tests/e2e/playwright.config.ts` (`sandboxServerEnv`) | `$TMPDIR/corvin-e2e-<name>/home` | throwaway | yes | unset (`env -u`) | throwaway |
| `web-next/scripts/start-isolated-e2e-backend.sh` (default sandbox `:8799`) | `/tmp/corvin-e2e-home` | throwaway | yes | unset | throwaway |
| `web-next/scripts/start-fresh-install-backend.sh` | throwaway | throwaway | yes | unset | **real** (not changed) |
| `forge-system`, `video-quality`, `video-lifecycle` harnesses (`isolated_*_backend.py`) | throwaway | throwaway | yes | popped | **real** (not changed) |
| Vite dev server (`npm run dev`) | — (serves the frontend; proxies `/v1` to the pinned gateway) | — | — | — | — |

The last two rows keep the real `HOME` on purpose: those suites were not run in WP-00 and may need the
engine CLI's credentials under `HOME`. They already meet the repo's test-sandbox standard (CORVIN_HOME,
XDG_CONFIG_HOME, absolute anchor key, FORGE_ROOT unset).

## CI workflow triage (the 4 workflows that mention Playwright, plus the new one)

| Workflow | After WP-00 | Class | Note |
|---|---|---|---|
| `.github/workflows/test.yml` | runner gateway on `:8799` (the sandbox default) | sandbox | the only workflow that starts a backend |
| `.github/workflows/e2e-playwright.yml` | `CONSOLE_BASE_URL=http://127.0.0.1:8799/console` | sandbox | **pre-existing gap:** starts no backend (before and after) |
| `.github/workflows/e2e-nightly.yml` | default origin (`:8799`) | sandbox | **pre-existing gap:** starts no backend |
| `.github/workflows/voice-e2e.yml` | unchanged (Playwright only in a comment) | — | not affected |
| `.github/workflows/release-inventory.yml` (new) | inventory + Python origin pytest (`--noconftest`, pytest-only runner); origin vitest + static scan | — | W-0 / W-0b gates |

## Removed (class delete)

No config, script or workflow selected these, and no doc referenced them except this table:
`web-next/tests/pages/skill-settings/test_all_admin_panels.spec.ts`,
`web-next/tests/pages/skill-feedback/test_all_feedback_forms.spec.ts` (both defaulted to `:8765`),
`core/console/tests/github-integration.spec.ts` (`:8765` literal), `core/console/tests/github-unit-tests.spec.ts`,
`tests/ui/inspector-panel.spec.ts`.

## Spec table (145 specs: 43 keep-live, 102 sandbox)

Column 2 counts `8765` hits at the WP-00 base `3029088f8` (executable / comment). After WP-00 no Playwright
file has an executable hit (static scan).

| Spec (repo path) | HEAD `:8765` refs (exec / comment) | Class | Selected by |
|---|---|---|---|
| `core/console/corvin_console/web-next/tests/e2e-fresh/default-plugins.spec.ts` | 0 | sandbox | playwright.fresh-install.config.ts (own backend port); no workflow runs it |
| `core/console/corvin_console/web-next/tests/e2e-fresh/knowledge-graph-default.spec.ts` | 0 | sandbox | playwright.fresh-install.config.ts (own backend port); no workflow runs it |
| `core/console/corvin_console/web-next/tests/e2e-fresh/video-producer-lifecycle.spec.ts` | 0 | sandbox | playwright.fresh-install.config.ts (own backend port); no workflow runs it |
| `core/console/corvin_console/web-next/tests/e2e-live/console-nav-live.spec.ts` | 1 / 1 | keep-live | live config playwright.forge-live.config.ts / live suite; needs CORVIN_E2E_ALLOW_LIVE=1 |
| `core/console/corvin_console/web-next/tests/e2e-live/forge-live/10-robustness.spec.ts` | 1 / 0 | keep-live | live config playwright.forge-live.config.ts / live suite; needs CORVIN_E2E_ALLOW_LIVE=1 |
| `core/console/corvin_console/web-next/tests/e2e-live/forge-live/15-composer.spec.ts` | 0 | keep-live | live config playwright.forge-live.config.ts / live suite; needs CORVIN_E2E_ALLOW_LIVE=1 |
| `core/console/corvin_console/web-next/tests/e2e-live/forge-live/16-run-feed.spec.ts` | 0 | keep-live | live config playwright.forge-live.config.ts / live suite; needs CORVIN_E2E_ALLOW_LIVE=1 |
| `core/console/corvin_console/web-next/tests/e2e-live/forge-live/17-layer-runs.spec.ts` | 0 | keep-live | live config playwright.forge-live.config.ts / live suite; needs CORVIN_E2E_ALLOW_LIVE=1 |
| `core/console/corvin_console/web-next/tests/e2e-live/forge-live/18-runs-history.spec.ts` | 0 | keep-live | live config playwright.forge-live.config.ts / live suite; needs CORVIN_E2E_ALLOW_LIVE=1 |
| `core/console/corvin_console/web-next/tests/e2e-live/forge-live/19-intake.spec.ts` | 0 | keep-live | live config playwright.forge-live.config.ts / live suite; needs CORVIN_E2E_ALLOW_LIVE=1 |
| `core/console/corvin_console/web-next/tests/e2e-live/forge-live/20-create-live.spec.ts` | 0 | keep-live | live config playwright.forge-live.config.ts / live suite; needs CORVIN_E2E_ALLOW_LIVE=1 |
| `core/console/corvin_console/web-next/tests/e2e-live/forge-live/21-intake-live.spec.ts` | 0 | keep-live | live config playwright.forge-live.config.ts / live suite; needs CORVIN_E2E_ALLOW_LIVE=1 |
| `core/console/corvin_console/web-next/tests/e2e-live/forge-live/22-intake-document-live.spec.ts` | 0 | keep-live | live config playwright.forge-live.config.ts / live suite; needs CORVIN_E2E_ALLOW_LIVE=1 |
| `core/console/corvin_console/web-next/tests/e2e-live/forge-live/23-intake-layer-live.spec.ts` | 0 | keep-live | live config playwright.forge-live.config.ts / live suite; needs CORVIN_E2E_ALLOW_LIVE=1 |
| `core/console/corvin_console/web-next/tests/e2e-live/forge-live/24-intake-existing-live.spec.ts` | 0 | keep-live | live config playwright.forge-live.config.ts / live suite; needs CORVIN_E2E_ALLOW_LIVE=1 |
| `core/console/corvin_console/web-next/tests/e2e-live/forge-live/30-fresh-install.spec.ts` | 0 | keep-live | live config playwright.forge-live.config.ts / live suite; needs CORVIN_E2E_ALLOW_LIVE=1 |
| `core/console/corvin_console/web-next/tests/e2e/aco-observatory.spec.ts` | 1 / 1 | keep-live | header claims a live console/host/engine; default config excludes it unless CORVIN_E2E_ALLOW_LIVE=1 (testIgnore) |
| `core/console/corvin_console/web-next/tests/e2e/aco-self-repair.spec.ts` | 1 / 0 | keep-live | header claims a live console/host/engine; default config excludes it unless CORVIN_E2E_ALLOW_LIVE=1 (testIgnore) |
| `core/console/corvin_console/web-next/tests/e2e/adr-0124-extensibility.spec.ts` | 0 | sandbox | web-next default config (`npm run test:e2e`, testDir tests/e2e) |
| `core/console/corvin_console/web-next/tests/e2e/adr-0177-nervous-system.spec.ts` | 1 / 0 | keep-live | header claims a live console/host/engine; default config excludes it unless CORVIN_E2E_ALLOW_LIVE=1 (testIgnore) |
| `core/console/corvin_console/web-next/tests/e2e/adversarial-github.spec.ts` | 1 / 0 | sandbox | web-next default config (`npm run test:e2e`, testDir tests/e2e) |
| `core/console/corvin_console/web-next/tests/e2e/bridges-qr.spec.ts` | 0 | keep-live | header claims a live console/host/engine; default config excludes it unless CORVIN_E2E_ALLOW_LIVE=1 (testIgnore) |
| `core/console/corvin_console/web-next/tests/e2e/ccc-live-e2e.spec.ts` | 0 | keep-live | header claims a live console/host/engine; default config excludes it unless CORVIN_E2E_ALLOW_LIVE=1 (testIgnore) |
| `core/console/corvin_console/web-next/tests/e2e/chat-attachments.spec.ts` | 1 / 0 | sandbox | web-next default config (`npm run test:e2e`, testDir tests/e2e) |
| `core/console/corvin_console/web-next/tests/e2e/chat-audit-graphs.spec.ts` | 1 / 0 | sandbox | web-next default config (`npm run test:e2e`, testDir tests/e2e) |
| `core/console/corvin_console/web-next/tests/e2e/chat-bg-activity.spec.ts` | 0 | sandbox | web-next default config (`npm run test:e2e`, testDir tests/e2e) |
| `core/console/corvin_console/web-next/tests/e2e/chat-btw-steer.spec.ts` | 0 / 1 | keep-live | header claims a live console/host/engine; default config excludes it unless CORVIN_E2E_ALLOW_LIVE=1 (testIgnore) |
| `core/console/corvin_console/web-next/tests/e2e/chat-composer-autosize.spec.ts` | 0 | sandbox | web-next default config (`npm run test:e2e`, testDir tests/e2e) |
| `core/console/corvin_console/web-next/tests/e2e/chat-group-a2a-send.spec.ts` | 1 / 0 | sandbox | web-next default config (`npm run test:e2e`, testDir tests/e2e) |
| `core/console/corvin_console/web-next/tests/e2e/chat-readability.spec.ts` | 0 | sandbox | web-next default config (`npm run test:e2e`, testDir tests/e2e) |
| `core/console/corvin_console/web-next/tests/e2e/chat-slash-palette.spec.ts` | 0 | sandbox | web-next default config (`npm run test:e2e`, testDir tests/e2e) |
| `core/console/corvin_console/web-next/tests/e2e/chat-stt-composer.spec.ts` | 0 | sandbox | web-next default config (`npm run test:e2e`, testDir tests/e2e) |
| `core/console/corvin_console/web-next/tests/e2e/chat-unified-panel.spec.ts` | 1 / 0 | keep-live | header claims a live console/host/engine; default config excludes it unless CORVIN_E2E_ALLOW_LIVE=1 (testIgnore) |
| `core/console/corvin_console/web-next/tests/e2e/complete-feature-coverage.spec.ts` | 0 | sandbox | web-next default config (`npm run test:e2e`, testDir tests/e2e) |
| `core/console/corvin_console/web-next/tests/e2e/complex-chained-flows.spec.ts` | 1 / 1 | keep-live | header claims a live console/host/engine; default config excludes it unless CORVIN_E2E_ALLOW_LIVE=1 (testIgnore) |
| `core/console/corvin_console/web-next/tests/e2e/compliance-audit.spec.ts` | 1 / 0 | sandbox | web-next default config (`npm run test:e2e`, testDir tests/e2e) |
| `core/console/corvin_console/web-next/tests/e2e/compliance-critical-pages.spec.ts` | 1 / 0 | sandbox | web-next default config (`npm run test:e2e`, testDir tests/e2e) |
| `core/console/corvin_console/web-next/tests/e2e/comprehensive-ui-integration.spec.ts` | 0 | sandbox | web-next default config (`npm run test:e2e`, testDir tests/e2e) |
| `core/console/corvin_console/web-next/tests/e2e/comprehensive-workflow.spec.ts` | 1 / 0 | sandbox | web-next default config (`npm run test:e2e`, testDir tests/e2e) |
| `core/console/corvin_console/web-next/tests/e2e/compute-awpkg-export.spec.ts` | 0 | sandbox | web-next default config (`npm run test:e2e`, testDir tests/e2e) |
| `core/console/corvin_console/web-next/tests/e2e/console-command-center.spec.ts` | 0 / 1 | keep-live | header claims a live console/host/engine; default config excludes it unless CORVIN_E2E_ALLOW_LIVE=1 (testIgnore) |
| `core/console/corvin_console/web-next/tests/e2e/console-release-0-10-34-core.spec.ts` | 0 | sandbox | web-next default config (`npm run test:e2e`, testDir tests/e2e) |
| `core/console/corvin_console/web-next/tests/e2e/console-ui-ux-release-0-10-34.spec.ts` | 0 | sandbox | web-next default config (`npm run test:e2e`, testDir tests/e2e) |
| `core/console/corvin_console/web-next/tests/e2e/console-unification-critical.spec.ts` | 0 | sandbox | web-next default config (`npm run test:e2e`, testDir tests/e2e) |
| `core/console/corvin_console/web-next/tests/e2e/critical-flows/real-backend.spec.ts` | 1 / 2 | keep-live | header claims a live console/host/engine; default config excludes it unless CORVIN_E2E_ALLOW_LIVE=1 (testIgnore) |
| `core/console/corvin_console/web-next/tests/e2e/custom-provider.spec.ts` | 0 | sandbox | web-next default config (`npm run test:e2e`, testDir tests/e2e) |
| `core/console/corvin_console/web-next/tests/e2e/data-sources-e2e.spec.ts` | 1 / 2 | keep-live | header claims a live console/host/engine; default config excludes it unless CORVIN_E2E_ALLOW_LIVE=1 (testIgnore) |
| `core/console/corvin_console/web-next/tests/e2e/datasource-license-gate.spec.ts` | 1 / 0 | sandbox | web-next default config (`npm run test:e2e`, testDir tests/e2e) |
| `core/console/corvin_console/web-next/tests/e2e/debug-test.spec.ts` | 1 / 0 | sandbox | web-next default config (`npm run test:e2e`, testDir tests/e2e) |
| `core/console/corvin_console/web-next/tests/e2e/demo-spotify-analysis.spec.ts` | 0 / 1 | keep-live | header claims a live console/host/engine; default config excludes it unless CORVIN_E2E_ALLOW_LIVE=1 (testIgnore) |
| `core/console/corvin_console/web-next/tests/e2e/e2e-console-workflows.spec.ts` | 1 / 0 | sandbox | web-next default config (`npm run test:e2e`, testDir tests/e2e) |
| `core/console/corvin_console/web-next/tests/e2e/edge-cases-and-failures.spec.ts` | 0 | sandbox | web-next default config (`npm run test:e2e`, testDir tests/e2e) |
| `core/console/corvin_console/web-next/tests/e2e/flow-creator.spec.ts` | 0 | sandbox | web-next default config (`npm run test:e2e`, testDir tests/e2e) |
| `core/console/corvin_console/web-next/tests/e2e/forge-run-survives-reload.spec.ts` | 0 | keep-live | header claims a live console/host/engine; default config excludes it unless CORVIN_E2E_ALLOW_LIVE=1 (testIgnore) |
| `core/console/corvin_console/web-next/tests/e2e/forge-system/01-console-roundtrip.spec.ts` | 0 | sandbox | web-next default config (`npm run test:e2e`, testDir tests/e2e) |
| `core/console/corvin_console/web-next/tests/e2e/forge-system/02-layer-forge.spec.ts` | 0 | sandbox | web-next default config (`npm run test:e2e`, testDir tests/e2e) |
| `core/console/corvin_console/web-next/tests/e2e/forge-system/03-plugin-forge.spec.ts` | 0 | sandbox | web-next default config (`npm run test:e2e`, testDir tests/e2e) |
| `core/console/corvin_console/web-next/tests/e2e/forge-system/04-skill-and-tool-forge.spec.ts` | 0 | sandbox | web-next default config (`npm run test:e2e`, testDir tests/e2e) |
| `core/console/corvin_console/web-next/tests/e2e/forge-system/05-malformed-bundles.spec.ts` | 0 | sandbox | web-next default config (`npm run test:e2e`, testDir tests/e2e) |
| `core/console/corvin_console/web-next/tests/e2e/forge-system/06-races-and-timeouts.spec.ts` | 0 | sandbox | web-next default config (`npm run test:e2e`, testDir tests/e2e) |
| `core/console/corvin_console/web-next/tests/e2e/forge-system/07-adversarial.spec.ts` | 0 | sandbox | web-next default config (`npm run test:e2e`, testDir tests/e2e) |
| `core/console/corvin_console/web-next/tests/e2e/forge-system/09-layer-forge-plan-phase.spec.ts` | 0 | sandbox | web-next default config (`npm run test:e2e`, testDir tests/e2e) |
| `core/console/corvin_console/web-next/tests/e2e/forge-system/98-live-chain-detector.spec.ts` | 0 | sandbox | web-next default config (`npm run test:e2e`, testDir tests/e2e) |
| `core/console/corvin_console/web-next/tests/e2e/forge-system/99-isolation-proof.spec.ts` | 0 | sandbox | web-next default config (`npm run test:e2e`, testDir tests/e2e) |
| `core/console/corvin_console/web-next/tests/e2e/forge-tab-order.spec.ts` | 0 | sandbox | web-next default config (`npm run test:e2e`, testDir tests/e2e) |
| `core/console/corvin_console/web-next/tests/e2e/github-sync.spec.ts` | 1 / 0 | sandbox | web-next default config (`npm run test:e2e`, testDir tests/e2e) |
| `core/console/corvin_console/web-next/tests/e2e/knowledge-graph-explorer.spec.ts` | 1 / 0 | keep-live | header claims a live console/host/engine; default config excludes it unless CORVIN_E2E_ALLOW_LIVE=1 (testIgnore) |
| `core/console/corvin_console/web-next/tests/e2e/ldd-active.spec.ts` | 1 / 1 | keep-live | header claims a live console/host/engine; default config excludes it unless CORVIN_E2E_ALLOW_LIVE=1 (testIgnore) |
| `core/console/corvin_console/web-next/tests/e2e/license-gates-comprehensive.spec.ts` | 1 / 0 | sandbox | web-next default config (`npm run test:e2e`, testDir tests/e2e) |
| `core/console/corvin_console/web-next/tests/e2e/marketplace-console.spec.ts` | 1 / 0 | keep-live | header claims a live console/host/engine; default config excludes it unless CORVIN_E2E_ALLOW_LIVE=1 (testIgnore) |
| `core/console/corvin_console/web-next/tests/e2e/models-console.spec.ts` | 3 / 0 | keep-live | header claims a live console/host/engine; default config excludes it unless CORVIN_E2E_ALLOW_LIVE=1 (testIgnore) |
| `core/console/corvin_console/web-next/tests/e2e/models-redirects.spec.ts` | 2 / 0 | keep-live | header claims a live console/host/engine; default config excludes it unless CORVIN_E2E_ALLOW_LIVE=1 (testIgnore) |
| `core/console/corvin_console/web-next/tests/e2e/nordtech-command-center.spec.ts` | 1 / 1 | keep-live | header claims a live console/host/engine; default config excludes it unless CORVIN_E2E_ALLOW_LIVE=1 (testIgnore) |
| `core/console/corvin_console/web-next/tests/e2e/panel-api-keys.spec.ts` | 0 | sandbox | web-next default config (`npm run test:e2e`, testDir tests/e2e) |
| `core/console/corvin_console/web-next/tests/e2e/panel-bridges.spec.ts` | 0 | sandbox | web-next default config (`npm run test:e2e`, testDir tests/e2e) |
| `core/console/corvin_console/web-next/tests/e2e/panel-chat.spec.ts` | 0 | sandbox | web-next default config (`npm run test:e2e`, testDir tests/e2e) |
| `core/console/corvin_console/web-next/tests/e2e/panel-compliance.spec.ts` | 0 | sandbox | web-next default config (`npm run test:e2e`, testDir tests/e2e) |
| `core/console/corvin_console/web-next/tests/e2e/panel-compute.spec.ts` | 0 | sandbox | web-next default config (`npm run test:e2e`, testDir tests/e2e) |
| `core/console/corvin_console/web-next/tests/e2e/panel-connectors.spec.ts` | 0 | sandbox | web-next default config (`npm run test:e2e`, testDir tests/e2e) |
| `core/console/corvin_console/web-next/tests/e2e/panel-custom-provider.spec.ts` | 0 | sandbox | web-next default config (`npm run test:e2e`, testDir tests/e2e) |
| `core/console/corvin_console/web-next/tests/e2e/panel-dashboard.spec.ts` | 0 | sandbox | web-next default config (`npm run test:e2e`, testDir tests/e2e) |
| `core/console/corvin_console/web-next/tests/e2e/panel-data-sources.spec.ts` | 0 | sandbox | web-next default config (`npm run test:e2e`, testDir tests/e2e) |
| `core/console/corvin_console/web-next/tests/e2e/panel-datahub-unified.spec.ts` | 1 / 0 | keep-live | header claims a live console/host/engine; default config excludes it unless CORVIN_E2E_ALLOW_LIVE=1 (testIgnore) |
| `core/console/corvin_console/web-next/tests/e2e/panel-files.spec.ts` | 0 | sandbox | web-next default config (`npm run test:e2e`, testDir tests/e2e) |
| `core/console/corvin_console/web-next/tests/e2e/panel-forge.spec.ts` | 0 | sandbox | web-next default config (`npm run test:e2e`, testDir tests/e2e) |
| `core/console/corvin_console/web-next/tests/e2e/panel-github.spec.ts` | 0 | sandbox | web-next default config (`npm run test:e2e`, testDir tests/e2e) |
| `core/console/corvin_console/web-next/tests/e2e/panel-layer-forge.spec.ts` | 0 | sandbox | web-next default config (`npm run test:e2e`, testDir tests/e2e) |
| `core/console/corvin_console/web-next/tests/e2e/panel-ldd.spec.ts` | 0 | sandbox | web-next default config (`npm run test:e2e`, testDir tests/e2e) |
| `core/console/corvin_console/web-next/tests/e2e/panel-license.spec.ts` | 0 | sandbox | web-next default config (`npm run test:e2e`, testDir tests/e2e) |
| `core/console/corvin_console/web-next/tests/e2e/panel-licensing-audit.spec.ts` | 0 | sandbox | web-next default config (`npm run test:e2e`, testDir tests/e2e) |
| `core/console/corvin_console/web-next/tests/e2e/panel-memory.spec.ts` | 0 | sandbox | web-next default config (`npm run test:e2e`, testDir tests/e2e) |
| `core/console/corvin_console/web-next/tests/e2e/panel-orgs.spec.ts` | 0 | sandbox | web-next default config (`npm run test:e2e`, testDir tests/e2e) |
| `core/console/corvin_console/web-next/tests/e2e/panel-otel-telemetry.spec.ts` | 0 | sandbox | web-next default config (`npm run test:e2e`, testDir tests/e2e) |
| `core/console/corvin_console/web-next/tests/e2e/panel-people.spec.ts` | 0 | sandbox | web-next default config (`npm run test:e2e`, testDir tests/e2e) |
| `core/console/corvin_console/web-next/tests/e2e/panel-quality.spec.ts` | 0 | sandbox | web-next default config (`npm run test:e2e`, testDir tests/e2e) |
| `core/console/corvin_console/web-next/tests/e2e/panel-rag-hub.spec.ts` | 0 | sandbox | web-next default config (`npm run test:e2e`, testDir tests/e2e) |
| `core/console/corvin_console/web-next/tests/e2e/panel-rag.spec.ts` | 0 | sandbox | web-next default config (`npm run test:e2e`, testDir tests/e2e) |
| `core/console/corvin_console/web-next/tests/e2e/panel-settings.spec.ts` | 0 | sandbox | web-next default config (`npm run test:e2e`, testDir tests/e2e) |
| `core/console/corvin_console/web-next/tests/e2e/panel-skill-forge-generator.spec.ts` | 0 | sandbox | web-next default config (`npm run test:e2e`, testDir tests/e2e) |
| `core/console/corvin_console/web-next/tests/e2e/panel-skills.spec.ts` | 0 | sandbox | web-next default config (`npm run test:e2e`, testDir tests/e2e) |
| `core/console/corvin_console/web-next/tests/e2e/panel-sync-monitor.spec.ts` | 0 | sandbox | web-next default config (`npm run test:e2e`, testDir tests/e2e) |
| `core/console/corvin_console/web-next/tests/e2e/panel-video-producer.spec.ts` | 0 | sandbox | web-next default config (`npm run test:e2e`, testDir tests/e2e) |
| `core/console/corvin_console/web-next/tests/e2e/panel-video-quality-metrics.spec.ts` | 1 / 0 | sandbox | web-next default config (`npm run test:e2e`, testDir tests/e2e) |
| `core/console/corvin_console/web-next/tests/e2e/panel-voice.spec.ts` | 0 | sandbox | web-next default config (`npm run test:e2e`, testDir tests/e2e) |
| `core/console/corvin_console/web-next/tests/e2e/panel-workflows.spec.ts` | 0 | sandbox | web-next default config (`npm run test:e2e`, testDir tests/e2e) |
| `core/console/corvin_console/web-next/tests/e2e/parallel-tasks.spec.ts` | 0 | sandbox | web-next default config (`npm run test:e2e`, testDir tests/e2e) |
| `core/console/corvin_console/web-next/tests/e2e/peer-chat-readability.spec.ts` | 0 | sandbox | web-next default config (`npm run test:e2e`, testDir tests/e2e) |
| `core/console/corvin_console/web-next/tests/e2e/peer-live-status-symbols.spec.ts` | 1 / 0 | keep-live | header claims a live console/host/engine; default config excludes it unless CORVIN_E2E_ALLOW_LIVE=1 (testIgnore) |
| `core/console/corvin_console/web-next/tests/e2e/peer-message-status-symbols.spec.ts` | 0 | sandbox | web-next default config (`npm run test:e2e`, testDir tests/e2e) |
| `core/console/corvin_console/web-next/tests/e2e/plugins.spec.ts` | 0 | sandbox | web-next default config (`npm run test:e2e`, testDir tests/e2e) |
| `core/console/corvin_console/web-next/tests/e2e/rag-license-gate.spec.ts` | 1 / 0 | sandbox | web-next default config (`npm run test:e2e`, testDir tests/e2e) |
| `core/console/corvin_console/web-next/tests/e2e/security-session-auth.spec.ts` | 1 / 0 | sandbox | web-next default config (`npm run test:e2e`, testDir tests/e2e) |
| `core/console/corvin_console/web-next/tests/e2e/sidebar-build-group.spec.ts` | 0 | sandbox | web-next default config (`npm run test:e2e`, testDir tests/e2e) |
| `core/console/corvin_console/web-next/tests/e2e/simple-vibe-test.spec.ts` | 0 | sandbox | web-next default config (`npm run test:e2e`, testDir tests/e2e) |
| `core/console/corvin_console/web-next/tests/e2e/skill-forge-live.spec.ts` | 1 / 0 | keep-live | header claims a live console/host/engine; default config excludes it unless CORVIN_E2E_ALLOW_LIVE=1 (testIgnore) |
| `core/console/corvin_console/web-next/tests/e2e/skill-forge.spec.ts` | 0 | keep-live | header claims a live console/host/engine; default config excludes it unless CORVIN_E2E_ALLOW_LIVE=1 (testIgnore) |
| `core/console/corvin_console/web-next/tests/e2e/tasks-live.spec.ts` | 1 / 1 | keep-live | header claims a live console/host/engine; default config excludes it unless CORVIN_E2E_ALLOW_LIVE=1 (testIgnore) |
| `core/console/corvin_console/web-next/tests/e2e/test-panels-p0.spec.ts` | 0 | sandbox | web-next default config (`npm run test:e2e`, testDir tests/e2e) |
| `core/console/corvin_console/web-next/tests/e2e/test-panels-p1.spec.ts` | 0 | sandbox | web-next default config (`npm run test:e2e`, testDir tests/e2e) |
| `core/console/corvin_console/web-next/tests/e2e/test-panels-p2.spec.ts` | 0 | sandbox | web-next default config (`npm run test:e2e`, testDir tests/e2e) |
| `core/console/corvin_console/web-next/tests/e2e/test-panels-p3.spec.ts` | 0 | sandbox | web-next default config (`npm run test:e2e`, testDir tests/e2e) |
| `core/console/corvin_console/web-next/tests/e2e/vibe-dashboard.spec.ts` | 1 / 0 | sandbox | web-next default config (`npm run test:e2e`, testDir tests/e2e) |
| `core/console/corvin_console/web-next/tests/e2e/vibe-engineering-panel.spec.ts` | 0 / 1 | keep-live | header claims a live console/host/engine; default config excludes it unless CORVIN_E2E_ALLOW_LIVE=1 (testIgnore) |
| `core/console/corvin_console/web-next/tests/e2e/vibe-engineering.spec.ts` | 0 | sandbox | web-next default config (`npm run test:e2e`, testDir tests/e2e) |
| `core/console/corvin_console/web-next/tests/e2e/vibe-engineering/audit-graph.spec.ts` | 2 / 0 | sandbox | web-next default config (`npm run test:e2e`, testDir tests/e2e) |
| `core/console/corvin_console/web-next/tests/e2e/vibe-engineering/context-inspector.spec.ts` | 4 / 0 | sandbox | web-next default config (`npm run test:e2e`, testDir tests/e2e) |
| `core/console/corvin_console/web-next/tests/e2e/video-lifecycle/data-lifecycle.spec.ts` | 0 | sandbox | web-next default config (`npm run test:e2e`, testDir tests/e2e) |
| `core/console/corvin_console/web-next/tests/e2e/video-quality/quality-tab.spec.ts` | 0 | sandbox | web-next default config (`npm run test:e2e`, testDir tests/e2e) |
| `core/console/corvin_console/web-next/tests/e2e/worker-engine-graph.spec.ts` | 1 / 0 | sandbox | web-next default config (`npm run test:e2e`, testDir tests/e2e) |
| `core/console/corvin_console/web-next/tests/e2e/workflow-composer.spec.ts` | 1 / 1 | keep-live | header claims a live console/host/engine; default config excludes it unless CORVIN_E2E_ALLOW_LIVE=1 (testIgnore) |
| `core/console/corvin_console/web-next/tests/e2e/workflow-peer-agent.spec.ts` | 1 / 1 | keep-live | header claims a live console/host/engine; default config excludes it unless CORVIN_E2E_ALLOW_LIVE=1 (testIgnore) |
| `core/console/corvin_console/web-next/tests/e2e/workflow-readiness.spec.ts` | 1 / 1 | keep-live | header claims a live console/host/engine; default config excludes it unless CORVIN_E2E_ALLOW_LIVE=1 (testIgnore) |
| `core/console/corvin_console/web-next/tests/e2e/your-agents.spec.ts` | 0 | sandbox | web-next default config (`npm run test:e2e`, testDir tests/e2e) |
| `scripts/e2e-task-graph-verification.spec.ts` | 1 / 0 | sandbox | root playwright.config.ts (testDir ./scripts) via scripts/run-e2e-tests.sh; starts its own standalone with a throwaway home |
| `scripts/e2e-test-full-console.spec.ts` | 0 | sandbox | root playwright.config.ts (testDir ./scripts) via scripts/run-e2e-tests.sh; starts its own standalone with a throwaway home |
| `scripts/test-token-metrics.spec.ts` | 4 / 0 | sandbox | root playwright.config.ts (testDir ./scripts) via scripts/run-e2e-tests.sh; starts its own standalone with a throwaway home |
| `tests/e2e/console_comprehensive_ui.spec.ts` | 0 | sandbox | tests/e2e/playwright.config.ts (own standalone, throwaway home) or playwright.e2e.config.ts (running console; :8765 needs the opt-in) |
| `tests/e2e/console_ui_exhaustive_quick.spec.ts` | 3 / 0 | sandbox | tests/e2e/playwright.config.ts (own standalone, throwaway home) or playwright.e2e.config.ts (running console; :8765 needs the opt-in) |
| `tests/e2e/github-integration.spec.ts` | 1 / 1 | keep-live | header names the live console (:8765); tests/e2e/playwright.config.ts excludes it unless CORVIN_E2E_ALLOW_LIVE=1 |
| `tests/e2e/learning_dashboard.spec.ts` | 0 / 2 | keep-live | header names the live console (:8765); tests/e2e/playwright.config.ts excludes it unless CORVIN_E2E_ALLOW_LIVE=1 |
| `tests/e2e/test_api_endpoints.spec.ts` | 0 | sandbox | tests/e2e/playwright.config.ts (own standalone, throwaway home) or playwright.e2e.config.ts (running console; :8765 needs the opt-in) |
| `tests/e2e/test_component_rendering.spec.ts` | 0 | sandbox | tests/e2e/playwright.config.ts (own standalone, throwaway home) or playwright.e2e.config.ts (running console; :8765 needs the opt-in) |
| `tests/e2e/test_interactions.spec.ts` | 0 | sandbox | tests/e2e/playwright.config.ts (own standalone, throwaway home) or playwright.e2e.config.ts (running console; :8765 needs the opt-in) |
| `tests/e2e/test_performance_accessibility.spec.ts` | 1 / 0 | sandbox | tests/e2e/playwright.config.ts (own standalone, throwaway home) or playwright.e2e.config.ts (running console; :8765 needs the opt-in) |
| `tests/e2e/test_responsive_dark_error.spec.ts` | 0 | sandbox | tests/e2e/playwright.config.ts (own standalone, throwaway home) or playwright.e2e.config.ts (running console; :8765 needs the opt-in) |

## Load fixes made alongside (every config now lists with exit 0)

- `web-next/tests/e2e/test-panels-p0.spec.ts`, `test-panels-p3.spec.ts`, `console-ui-ux-release-0-10-34.spec.ts`:
  `8b9a8f672` renamed fixture keys to `_panelNav` / `_context`; restored with an alias (`panelNav: _panelNav`).
- `tests/e2e/test_responsive_dark_error.spec.ts`: `test.use({...devices[...]})` inside a describe group is
  refused by Playwright (the device sets `defaultBrowserType`); a local `device()` helper drops that key.
  `devices['iPad Pro']` does not exist in this Playwright version; it is `iPad Pro 11`.
- `tests/e2e/playwright.config.ts`: `testDir: './tests/e2e'` resolved to `tests/e2e/tests/e2e`; now `'.'`.

## Not done in WP-00 (measured residue)

- **Python tests and product code** outside the Python Playwright path still use `:8765`: about 20
  `tests/e2e/*.py` API tests and 7 `core/console/tests/*.py`, plus product code (installer, a2a, bridges).
  WP-00 applied the policy to the Python Playwright fixture (`tests/e2e/conftest.py` `playwright_config`)
  and `tests/e2e/test_output_rendering.py`; the rest is the next residue to classify.
- **Vitest mocks** (`tests/unit/csrf-fetch.test.ts`, `learning-tab.test.tsx`, `voice-summaries-page.test.tsx`)
  mention `:8765` as strings in mocks and make no request to it; vitest files are out of the scan.
- **Sandbox backend.** A default run needs the isolated backend on `:8799` already listening (start it with
  `scripts/start-isolated-e2e-backend.sh`, or use `playwright.isolated.config.ts`).
- **`tests/e2e/auth-state.json` is not tracked** (written by `global-setup-adr0124.ts`); nothing to regenerate.
- **E2E residue writers** (`fake`/`stub` turns, residue in the live install) are not measured here.
- **`env -u FORGE_ROOT`** in the root configs' webServer command is POSIX (Linux, macOS); on Windows those two
  root configs need WSL or Git Bash.
