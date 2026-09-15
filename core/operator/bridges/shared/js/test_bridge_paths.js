// ADR-0008 Phase 8.1 per-subtask E2E for bridgeRuntimeDir() in Node.
//
// Mirrors operator/bridges/shared/test_bridge_paths.py case-for-case
// so any future divergence between the Python and Node resolver shows up
// as a failing case here.

'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');

function cleanEnv() {
  for (const key of Object.keys(process.env)) {
    if (key.startsWith('CORVIN_BRIDGE') ||
        key === 'CORVIN_HOME' ||
        key === 'CORVIN_BRIDGES_HOME') {
      delete process.env[key];
    }
  }
}

function fresh() {
  // Resolver caches nothing — but re-require to be safe across cases.
  delete require.cache[require.resolve('./bridge_paths')];
  return require('./bridge_paths');
}

function mktmp() {
  return fs.mkdtempSync(path.join(os.tmpdir(), 'adr0008-'));
}

function assertEq(a, b, msg) {
  if (a !== b) throw new Error(`${msg || 'assertion'}: ${a} !== ${b}`);
}

function caseCorvinHomeRoot() {
  cleanEnv();
  const tmp = mktmp();
  process.env.CORVIN_HOME = tmp;
  const p = fresh();
  assertEq(p.bridgesHome(), path.join(tmp, 'bridges'));
  assertEq(p.bridgeChannelDir('discord'), path.join(tmp, 'bridges', 'discord'));
}

function caseKindResolution() {
  cleanEnv();
  const tmp = mktmp();
  process.env.CORVIN_HOME = tmp;
  const p = fresh();
  for (const kind of ['inbox', 'outbox', 'processed', 'attachments', 'auth', 'log']) {
    assertEq(
      p.bridgeRuntimeDir('telegram', kind),
      path.join(tmp, 'bridges', 'telegram', kind),
      kind,
    );
  }
  for (const alias of ['settings', 'root']) {
    assertEq(
      p.bridgeRuntimeDir('telegram', alias),
      path.join(tmp, 'bridges', 'telegram'),
      alias,
    );
  }
}

function caseConveniencePaths() {
  cleanEnv();
  const tmp = mktmp();
  process.env.CORVIN_HOME = tmp;
  const p = fresh();
  assertEq(p.bridgeSettingsPath('discord'),
    path.join(tmp, 'bridges', 'discord', 'settings.json'));
  assertEq(p.bridgeLogPath('email'),
    path.join(tmp, 'bridges', 'email', 'log', 'voice.log'));
}

function caseRootEnvOverride() {
  cleanEnv();
  const tmp = mktmp();
  process.env.CORVIN_HOME = '/should/not/be/used';
  process.env.CORVIN_BRIDGES_HOME = tmp;
  const p = fresh();
  assertEq(p.bridgeRuntimeDir('slack', 'inbox'),
    path.join(tmp, 'slack', 'inbox'));
}

function casePerLeafEnvOverride() {
  cleanEnv();
  process.env.CORVIN_HOME = '/should/not/be/used';
  process.env.CORVIN_BRIDGE_DISCORD_INBOX = '/sandbox/inbox-override';
  const p = fresh();
  assertEq(p.bridgeRuntimeDir('discord', 'inbox'), '/sandbox/inbox-override');
  if (p.bridgeRuntimeDir('discord', 'outbox') === '/sandbox/inbox-override') {
    throw new Error('per-leaf override leaked to a different kind');
  }
}

function caseInvalidChannelRejected() {
  cleanEnv();
  const p = fresh();
  for (const bad of ['Discord', 'bad/channel', '', 'a'.repeat(33), '9digits', 'with space']) {
    let rejected = false;
    try { p.bridgeRuntimeDir(bad, 'inbox'); } catch (e) { rejected = true; }
    if (!rejected) throw new Error(`should have rejected channel ${JSON.stringify(bad)}`);
  }
}

function caseInvalidKindRejected() {
  cleanEnv();
  const p = fresh();
  for (const bad of ['Bogus', 'INBOX', 'messages', '', 'settings.json']) {
    let rejected = false;
    try { p.bridgeRuntimeDir('discord', bad); } catch (e) { rejected = true; }
    if (!rejected) throw new Error(`should have rejected kind ${JSON.stringify(bad)}`);
  }
}

function caseIdentityOnlyNoFsSideEffects() {
  cleanEnv();
  const tmp = mktmp();
  process.env.CORVIN_HOME = tmp;
  const p = fresh();
  for (const channel of ['telegram', 'discord', 'slack', 'whatsapp', 'email']) {
    for (const kind of ['inbox', 'outbox', 'processed', 'attachments', 'auth', 'log']) {
      const resolved = p.bridgeRuntimeDir(channel, kind);
      if (fs.existsSync(resolved)) {
        throw new Error(`resolver MUST NOT create ${resolved}`);
      }
    }
  }
  if (fs.existsSync(path.join(tmp, 'bridges'))) {
    throw new Error('bridgesHome MUST NOT be created');
  }
}

function caseLegacyPathPointsAtRepo() {
  cleanEnv();
  const p = fresh();
  const legacy = p.legacyBridgeRuntimeDir('email', 'attachments');
  if (!legacy || !path.isAbsolute(legacy)) {
    throw new Error('legacy path must be absolute');
  }
  // Accept both the new operator/bridges layout and the legacy plugins/voice/bridges
  // path — the function falls back to whichever exists.
  const newPath = path.join('operator', 'bridges', 'email', 'attachments');
  const legacyPath = path.join('plugins', 'voice', 'bridges', 'email', 'attachments');
  if (!legacy.includes(newPath) && !legacy.includes(legacyPath)) {
    throw new Error(`legacy path should be in repo tree: ${legacy}`);
  }
}

function caseWhitespaceOnlyEnvIsIgnored() {
  cleanEnv();
  process.env.CORVIN_HOME = '   \t\n  ';
  const p = fresh();
  // Must fall back to the repo-walk / homedir default, not resolve to
  // a bogus path like path.resolve('   ') relative to the cwd.
  const resolved = p.corvinHome();
  if (resolved === path.resolve('   \t\n  ')) {
    throw new Error('whitespace-only CORVIN_HOME must not be used verbatim');
  }
}

function caseExpandUserTilde() {
  cleanEnv();
  process.env.CORVIN_HOME = path.join('~', 'corvin-home-tilde-test');
  const p = fresh();
  const expected = path.join(os.homedir(), 'corvin-home-tilde-test');
  assertEq(p.corvinHome(), expected, 'tilde must expand to homedir');
}

function caseExpandVarsDollar() {
  cleanEnv();
  const tmp = mktmp();
  process.env.CORVIN_HOME_TEST_TARGET = tmp;
  process.env.CORVIN_HOME = '${CORVIN_HOME_TEST_TARGET}/nested';
  const p = fresh();
  assertEq(p.corvinHome(), path.join(tmp, 'nested'), '${VAR} must expand');
  delete process.env.CORVIN_HOME_TEST_TARGET;
}

function caseKnownChannelsAllAccepted() {
  cleanEnv();
  const tmp = mktmp();
  process.env.CORVIN_HOME = tmp;
  const p = fresh();
  for (const ch of ['telegram', 'discord', 'slack', 'whatsapp', 'email', 'shared']) {
    const resolved = p.bridgeRuntimeDir(ch, 'inbox');
    if (path.basename(path.dirname(resolved)) !== ch) {
      throw new Error(`channel resolution wrong for ${ch}: ${resolved}`);
    }
  }
}

const CASES = [
  caseCorvinHomeRoot,
  caseKindResolution,
  caseConveniencePaths,
  caseRootEnvOverride,
  casePerLeafEnvOverride,
  caseInvalidChannelRejected,
  caseInvalidKindRejected,
  caseIdentityOnlyNoFsSideEffects,
  caseLegacyPathPointsAtRepo,
  caseWhitespaceOnlyEnvIsIgnored,
  caseExpandUserTilde,
  caseExpandVarsDollar,
  caseKnownChannelsAllAccepted,
];

let failed = 0;
for (const c of CASES) {
  try {
    c();
    console.log(`PASS ${c.name}`);
  } catch (e) {
    console.log(`FAIL ${c.name}: ${e.message}`);
    failed += 1;
  }
}
if (failed) {
  console.log(`\n${failed} failure(s).`);
  process.exit(1);
}
console.log(`\nAll ${CASES.length} cases passed.`);
