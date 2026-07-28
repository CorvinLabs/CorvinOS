// test_operator_root_resolution.js — the Python CLIs must be reachable from a
// daemon that runs out of the RUNTIME dir, not only from a git checkout.
//
// The defect this pins (found 2026-07-28, present since the runtime-dir split):
// `bridge_manager.start_channel_detached` spawns the daemon with
// `cwd=<corvin_home>/bridges/<channel>/` and `_materialise_shared_js()` mirrors
// ONLY `shared/js/*.{js,mjs,cjs,json}` beside it. Every `*_CLI` constant in
// in_chat_commands.js was `path.resolve(__dirname, '..', 'x.py')`, which from
// the mirrored copy points at `<corvin_home>/bridges/shared/` — a directory
// holding inbox/, outbox/ and js/, and no Python whatsoever. So `/new`,
// `/reset`, `/engine`, `/role`, `/grant`, `/quota`, `/audit`, `/consent`,
// `/join`, `/pass`, `/goal`, `/objective`, `/propose`, `/dialectic*`, `/ps`,
// `/kill`, `/lang`, `/profile`, `/settings` and `/a2a` all failed with ENOENT
// on every wheel install — and every one of them worked in a checkout, where
// `__dirname/..` happens to BE the source tree. That asymmetry is why no test
// and no dev session ever caught it.
//
// Run: node operator/bridges/shared/js/test_operator_root_resolution.js
'use strict';

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');

const SRC_JS = __dirname;                                  // <op>/bridges/shared/js
const OPERATOR = path.resolve(SRC_JS, '..', '..', '..');   // <op>

let failures = 0;
function check(name, fn) {
  try {
    fn();
    console.log(`PASS: ${name}`);
  } catch (e) {
    failures++;
    console.error(`FAIL: ${name}\n      ${e.message}`);
  }
}

// ── 1. Source layout (a git checkout / bridge.sh) resolves without any env ──
check('source layout finds the shared Python CLIs with no env hint', () => {
  delete process.env.CORVIN_BRIDGE_OPERATOR_ROOT;
  delete require.cache[require.resolve('./bridge_paths')];
  const bp = require('./bridge_paths');
  assert.strictEqual(bp.operatorRoot(), OPERATOR);
  for (const f of ['session_reset.py', 'roles.py', 'consent.py', 'quota.py',
                   'disclosure.py', 'goal.py', 'settings_view.py']) {
    assert.ok(fs.existsSync(bp.bridgeSharedPy(f)), `${f} not found`);
  }
  for (const f of ['lang_cli.py', 'profile_cli.py', 'corvin_a2a.py']) {
    assert.ok(fs.existsSync(bp.voiceScript(f)), `voice/scripts/${f} not found`);
  }
});

// ── 2. Runtime layout — the wheel-install shape that was broken ─────────────
check('runtime layout finds them through CORVIN_BRIDGE_OPERATOR_ROOT', () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'corvin-rt-'));
  const mirrored = path.join(tmp, 'bridges', 'shared', 'js');
  fs.mkdirSync(mirrored, { recursive: true });
  fs.mkdirSync(path.join(tmp, 'bridges', 'shared', 'inbox'), { recursive: true });
  fs.mkdirSync(path.join(tmp, 'bridges', 'discord'), { recursive: true });
  // Exactly what _materialise_shared_js copies: the .js files, nothing else.
  for (const f of fs.readdirSync(SRC_JS)) {
    if (f.endsWith('.js')) fs.copyFileSync(path.join(SRC_JS, f), path.join(mirrored, f));
  }

  const mirroredPaths = path.join(mirrored, 'bridge_paths.js');

  // Without the hint this is the bug: it resolves into the runtime tree.
  delete process.env.CORVIN_BRIDGE_OPERATOR_ROOT;
  delete require.cache[require.resolve(mirroredPaths)];
  const broken = require(mirroredPaths);
  assert.ok(
    !fs.existsSync(broken.bridgeSharedPy('session_reset.py')),
    'the runtime mirror must NOT contain session_reset.py — if it does, this ' +
    'test no longer reproduces the shape it exists to guard',
  );

  // With the hint bridge_manager exports, the CLI is found again.
  process.env.CORVIN_BRIDGE_OPERATOR_ROOT = OPERATOR;
  delete require.cache[require.resolve(mirroredPaths)];
  const fixed = require(mirroredPaths);
  assert.strictEqual(fixed.operatorRoot(), OPERATOR);
  assert.ok(fs.existsSync(fixed.bridgeSharedPy('session_reset.py')));
  assert.ok(fs.existsSync(fixed.voiceScript('lang_cli.py')));

  fs.rmSync(tmp, { recursive: true, force: true });
  delete process.env.CORVIN_BRIDGE_OPERATOR_ROOT;
});

// ── 3. A bogus hint must not win over a working source layout ──────────────
check('an unusable env hint falls back instead of breaking a good layout', () => {
  process.env.CORVIN_BRIDGE_OPERATOR_ROOT = path.join(os.tmpdir(), 'does-not-exist-xyz');
  delete require.cache[require.resolve('./bridge_paths')];
  const bp = require('./bridge_paths');
  assert.strictEqual(bp.operatorRoot(), OPERATOR);
  delete process.env.CORVIN_BRIDGE_OPERATOR_ROOT;
});

// ── 4. No CLI constant may go back to walking up from __dirname ────────────
check('in_chat_commands.js resolves no .py through __dirname', () => {
  const src = fs.readFileSync(path.join(SRC_JS, 'in_chat_commands.js'), 'utf8');
  const bad = src.match(/path\.resolve\(__dirname,[^)]*\.py'\)/g) || [];
  assert.deepStrictEqual(
    bad, [],
    'these resolve into the runtime dir on a wheel install; use ' +
    'bridgePaths.bridgeSharedPy() / bridgePaths.voiceScript()',
  );
});

if (failures) {
  console.error(`\n${failures} check(s) failed`);
  process.exit(1);
}
console.log('\nALL PASS');
