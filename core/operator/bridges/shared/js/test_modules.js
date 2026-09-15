// test_modules.js — Smoke-Tests for die shared/js/ Bridge-Runtime-modulee.
//
// Pure Node.js, keine externen Dependencies. Sandboxed in /tmp.
//   node operator/bridges/shared/js/test_modules.js
//
// Was getestet wed:
//   1. outbox.js: Strict-Channel-Filter — fehlender channel → drop+log,
//                 falscher channel → continue (file bleibt), eigener channel → send.
//   2. settings.js: Hot-Reload via mtime, last-good-on-error.
//   3. auth.js: Whitelist-Match, Rate-Limit (perHour Cap), DEV-Mode.

const fs   = require('fs');
const os   = require('os');
const path = require('path');

const { makeLogger }            = require('./logger');
const { makeSettingsAccessor }  = require('./settings');
const { makeAuth }              = require('./auth');
const { startOutboxPoller }     = require('./outbox');

let pass = 0, fail = 0;
function ok(msg)   { console.log(`PASS: ${msg}`); pass++; }
function bad(msg)  { console.log(`FAIL: ${msg}`); fail++; }
function eq(a, b, msg) { (JSON.stringify(a) === JSON.stringify(b)) ? ok(msg) : bad(`${msg} — expected ${JSON.stringify(b)}, got ${JSON.stringify(a)}`); }

function tmpDir(prefix) {
  return fs.mkdtempSync(path.join(os.tmpdir(), prefix));
}

async function testOutboxStrictChannel() {
  console.log('\n=== outbox: strict channel filter ===');
  const dir = tmpDir('outbox-test-');
  const logs = [];
  const sent = [];
  const log = (...args) => logs.push(args.join(' '));

  // 3 files: one valid for our channel, one for a foreign channel, one without channel.
  fs.writeFileSync(path.join(dir, '01_ours.json'),
    JSON.stringify({ channel: 'telegram', chat_id: 1, text: 'A' }));
  fs.writeFileSync(path.join(dir, '02_foreign.json'),
    JSON.stringify({ channel: 'discord', chat_id: 2, text: 'B' }));
  fs.writeFileSync(path.join(dir, '03_missing.json'),
    JSON.stringify({ chat_id: 3, text: 'C' /* no channel */ }));
  fs.writeFileSync(path.join(dir, '04_badjson.json'), 'this is not json');

  const handle = startOutboxPoller({
    outboxDir: dir, channel: 'telegram',
    sendFn: async (payload) => { sent.push(payload); },
    logger: log, intervalMs: 50,
  });

  await new Promise(res => setTimeout(res, 300));
  handle.stop();

  // Our channel got delivered + file removed.
  const remaining = fs.readdirSync(dir).filter(f => f.endsWith('.json')).sort();

  eq(sent.length, 1, 'sendFn called exactly once');
  eq(sent[0]?.text, 'A', 'sendFn received our channel payload');
  // Foreign channel: file MUST remain (so the discord daemon can still pick it up).
  eq(remaining.includes('02_foreign.json'), true, 'foreign-channel file is preserved');
  // Missing channel: file MUST be dropped (the bug from Phase 1 — was previously
  // routed to "whatsapp" by default).
  eq(remaining.includes('03_missing.json'), false, 'missing-channel file is dropped');
  // Bad JSON: file dropped after log.
  eq(remaining.includes('04_badjson.json'), false, 'bad-json file is dropped');
  // Drop log was emitted.
  const droppedLog = logs.some(l => l.includes("missing 'channel'"));
  eq(droppedLog, true, 'log line emitted for missing-channel drop');

  fs.rmSync(dir, { recursive: true, force: true });
}

async function testOutboxRetainsOnError() {
  console.log('\n=== outbox: retain on send error ===');
  const dir = tmpDir('outbox-retain-');
  const logs = [];
  const log = (...args) => logs.push(args.join(' '));

  fs.writeFileSync(path.join(dir, '01.json'),
    JSON.stringify({ channel: 'telegram', text: 'fail-me' }));

  let calls = 0;
  const handle = startOutboxPoller({
    outboxDir: dir, channel: 'telegram',
    sendFn: async () => { calls++; throw new Error('simulated send failure'); },
    logger: log, intervalMs: 50,
  });
  await new Promise(res => setTimeout(res, 300));
  handle.stop();

  // File MUST still exist so a later tick can retry.
  const remaining = fs.readdirSync(dir).filter(f => f.endsWith('.json'));
  eq(remaining.length, 1, 'failed-send file is retained for retry');
  eq(calls >= 2, true, `sendFn retried (got ${calls} call(s))`);
  const errLog = logs.some(l => l.includes('simulated send failure'));
  eq(errLog, true, 'send failure was logged');

  fs.rmSync(dir, { recursive: true, force: true });
}

async function testSettingsHotReload() {
  console.log('\n=== settings: hot-reload via mtime ===');
  const dir = tmpDir('settings-test-');
  const file = path.join(dir, 'settings.json');
  fs.writeFileSync(file, JSON.stringify({ foo: 'one', whitelist: ['a'] }));

  const logs = [];
  const log = (...a) => logs.push(a.join(' '));
  const { currentSettings, loadSettings, saveSettings } = makeSettingsAccessor(file, log);

  eq(currentSettings().foo, 'one', 'first read returns initial value');
  eq(loadSettings().whitelist, ['a'], 'loadSettings bypasses cache');

  // mtime resolution on some filesystems is 1s — wait + bump it.
  await new Promise(r => setTimeout(r, 1100));
  fs.writeFileSync(file, JSON.stringify({ foo: 'two', whitelist: ['a', 'b'] }));
  eq(currentSettings().foo, 'two', 'second read picks up updated file');
  eq(currentSettings().whitelist, ['a', 'b'], 'whitelist hot-reloaded');
  const reloadLogged = logs.some(l => l.includes('settings.json reloaded'));
  eq(reloadLogged, true, 'reload is logged');

  // Corrupt the file → should keep last good value.
  fs.writeFileSync(file, '{not valid');
  // mtime change — but parse fails → cache stays.
  await new Promise(r => setTimeout(r, 1100));
  fs.writeFileSync(file, '{not valid');
  eq(currentSettings().foo, 'two', 'corrupt file: last good value retained');

  // Write VALID changed value back — should reload.
  await new Promise(r => setTimeout(r, 1100));
  fs.writeFileSync(file, JSON.stringify({ foo: 'three' }));
  eq(currentSettings().foo, 'three', 'recovery: valid file loads after corruption');

  // saveSettings + read-back. 1100ms BEFORE save, damit mtime garantiert
  // einen neuen Sekunden-Tick bekommt (filesystems mit 1s-resolution würden
  // otherwise die "three"-Schreib- und die "four"-Schreib-mtime nicht trennen).
  await new Promise(r => setTimeout(r, 1100));
  saveSettings({ foo: 'four', extra: 1 });
  await new Promise(r => setTimeout(r, 1100));
  eq(currentSettings().foo, 'four', 'saveSettings round-trip');

  fs.rmSync(dir, { recursive: true, force: true });
}

function testAuth() {
  console.log('\n=== auth: whitelist + rate-limit + DEV mode ===');
  const dir = tmpDir('auth-test-');
  const file = path.join(dir, 'settings.json');
  fs.writeFileSync(file, JSON.stringify({ whitelist: ['user-1', 'user-2'] }));

  const { currentSettings, loadSettings } = makeSettingsAccessor(file);
  const logs = [];
  const log = (...a) => logs.push(a.join(' '));
  const { authOk, rateAllow } = makeAuth({
    settingsFile: file, currentSettings, loadSettings, logger: log,
  });

  eq(authOk('user-1', ''),    true,  'whitelisted user-1 accepted');
  eq(authOk('user-99', ''),   false, 'non-whitelisted rejected');
  eq(authOk('user-99', 'no-pin'), false, 'no PIN configured → /auth ignored');

  // Rate-limit: 3 calls, then deny.
  eq(rateAllow('user-1', 3), true, '1st under limit');
  eq(rateAllow('user-1', 3), true, '2nd under limit');
  eq(rateAllow('user-1', 3), true, '3rd hits exact limit');
  eq(rateAllow('user-1', 3), false, '4th over limit');
  // Other user has independent counter.
  eq(rateAllow('user-2', 3), true, 'rate-limit is per-user');

  // DEV mode: empty whitelist → accept everyone.
  fs.writeFileSync(file, JSON.stringify({ whitelist: [] }));
  // mtime change forces re-read in currentSettings.
  // Quick wait for fs mtime resolution.
  return new Promise(res => setTimeout(() => {
    eq(authOk('random-user', ''), true, 'DEV mode accepts all');
    fs.rmSync(dir, { recursive: true, force: true });
    res();
  }, 1100));
}

function testAuthAudienceOverride() {
  console.log('\n=== auth: per-chat audience override (/all on) ===');
  const dir = tmpDir('auth-aud-');
  const file = path.join(dir, 'settings.json');
  // Whitelist is restrictive — only `owner` may pass — unless the chat
  // has been opened up via chat_profiles.<chat>.audience = "all".
  fs.writeFileSync(file, JSON.stringify({
    whitelist: ['owner'],
    chat_profiles: { 'open-chat': { audience: 'all' } },
  }));

  const { currentSettings, loadSettings } = makeSettingsAccessor(file);
  const { authOk } = makeAuth({
    settingsFile: file, currentSettings, loadSettings,
  });

  eq(authOk('owner',    '', 'open-chat'),    true,  'owner always accepted');
  eq(authOk('stranger', '', 'open-chat'),    true,  'stranger accepted in open-chat');
  eq(authOk('stranger', '', 'closed-chat'),  false, 'stranger rejected in closed-chat');
  eq(authOk('stranger', '', null),           false, 'stranger rejected without chatKey');

  fs.rmSync(dir, { recursive: true, force: true });
}

(async () => {
  await testOutboxStrictChannel();
  await testOutboxRetainsOnError();
  await testSettingsHotReload();
  await testAuth();
  testAuthAudienceOverride();

  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
})();
