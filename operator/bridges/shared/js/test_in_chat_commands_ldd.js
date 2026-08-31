// test_in_chat_commands_ldd.js — End-to-End round-trip tests for the
// /ldd-* slash-command family (Layer 14).
//
// Each test uses a temp CORVIN_HOME, dispatches the slash command via
// in_chat_commands, then reads the resulting ldd.json from disk to confirm
// the underlying Python CLI produced the expected on-disk state.
//
// Run: node operator/bridges/shared/js/test_in_chat_commands_ldd.js

const fs = require('fs');
const os = require('os');
const path = require('path');

const inChat = require('./in_chat_commands');

let pass = 0, fail = 0;
function ok(msg) { console.log(`PASS: ${msg}`); pass++; }
function bad(msg) { console.log(`FAIL: ${msg}`); fail++; }
function eq(a, b, msg) {
  if (JSON.stringify(a) === JSON.stringify(b)) ok(msg);
  else bad(`${msg} — expected ${JSON.stringify(b)}, got ${JSON.stringify(a)}`);
}
function contains(haystack, needle, msg) {
  if (typeof haystack === 'string' && haystack.includes(needle)) ok(msg);
  else bad(`${msg} — "${needle}" not found in: ${String(haystack).slice(0, 300)}`);
}
function truthy(v, msg) { v ? ok(msg) : bad(`${msg} — got ${JSON.stringify(v)}`); }

function fresh() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'ldd-cmd-'));
  process.env.CORVIN_HOME = dir;
  process.env.CORVIN_FORCE_SCOPE = 'user';
  return dir;
}

function readLddConfig(dir) {
  const p = path.join(dir, 'global', 'ldd.json');
  if (!fs.existsSync(p)) return null;
  return JSON.parse(fs.readFileSync(p, 'utf8'));
}

function tmpSettings() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'inchat-ldd-'));
  const f = path.join(dir, 'settings.json');
  fs.writeFileSync(f, JSON.stringify({}));
  return f;
}

const baseCtx = (settingsFile) => ({
  channel: 'telegram', chatKey: 'test-chat',
  isOwner: true, settingsFile,
});

// ── 1. /ldd-status: lists every layer ─────────────────────────────────────
console.log('\n=== /ldd-status: lists every layer ===');
{
  const dir = fresh();
  const f = tmpSettings();
  const r = inChat.dispatch({ ...baseCtx(f), text: '/ldd-status' });
  truthy(r, '/ldd-status returns a reply');
  // Default-OFF since the policy switch.
  contains(r && r.reply, 'enabled=False', '/ldd-status reports enabled=False');
  contains(r && r.reply, 'loop_driven_engineering', '/ldd-status lists loop_driven_engineering');
  contains(r && r.reply, 'dialectical_reasoning', '/ldd-status lists dialectical_reasoning');
  contains(r && r.reply, 'per_subtask_e2e', '/ldd-status lists per_subtask_e2e');
  // First call wrote the default config to disk.
  const cfg = readLddConfig(dir);
  truthy(cfg, 'ldd.json written on first invocation');
  eq(cfg.enabled, false, 'cfg.enabled defaults to false (default-off policy)');
}

// ── 2. /ldd-off and /ldd-on flip the master ───────────────────────────────
console.log('\n=== /ldd-off and /ldd-on flip the master ===');
{
  const dir = fresh();
  const f = tmpSettings();
  inChat.dispatch({ ...baseCtx(f), text: '/ldd-off' });
  let cfg = readLddConfig(dir);
  eq(cfg.enabled, false, '/ldd-off persists enabled=false');
  inChat.dispatch({ ...baseCtx(f), text: '/ldd-on' });
  cfg = readLddConfig(dir);
  eq(cfg.enabled, true, '/ldd-on flips it back to true');
}

// ── 3. /ldd-set <layer> <on|off> persists ─────────────────────────────────
console.log('\n=== /ldd-set persists per-layer toggles ===');
{
  const dir = fresh();
  const f = tmpSettings();
  inChat.dispatch({ ...baseCtx(f), text: '/ldd-set e2e_driven_iteration off' });
  let cfg = readLddConfig(dir);
  eq(cfg.layers.e2e_driven_iteration, false,
    '/ldd-set e2e_driven_iteration off persists');
  // Hyphenated form should also work — auto-canonicalized to underscore.
  inChat.dispatch({ ...baseCtx(f), text: '/ldd-set docs-as-definition-of-done off' });
  cfg = readLddConfig(dir);
  eq(cfg.layers.docs_as_dod, false,
    '/ldd-set docs-as-definition-of-done off canonicalizes to docs_as_dod');
  inChat.dispatch({ ...baseCtx(f), text: '/ldd-set e2e_driven_iteration on' });
  cfg = readLddConfig(dir);
  eq(cfg.layers.e2e_driven_iteration, true,
    '/ldd-set e2e_driven_iteration on flips back');
}

// ── 4. /ldd-set with unknown layer is rejected ───────────────────────────
console.log('\n=== /ldd-set rejects unknown layer ===');
{
  const dir = fresh();
  const f = tmpSettings();
  // Seed a known config to compare against.
  inChat.dispatch({ ...baseCtx(f), text: '/ldd-status' });
  const before = readLddConfig(dir);
  const r = inChat.dispatch({ ...baseCtx(f), text: '/ldd-set not_a_real_layer off' });
  contains(r && r.reply, 'unknown layer', '/ldd-set unknown layer reports unknown');
  const after = readLddConfig(dir);
  eq(after, before, 'ldd.json unchanged after unknown-layer rejection');
}

// ── 5. /ldd-preset writes the preset's layer-state ───────────────────────
console.log('\n=== /ldd-preset applies named presets ===');
{
  const dir = fresh();
  const f = tmpSettings();
  inChat.dispatch({ ...baseCtx(f), text: '/ldd-preset quick' });
  let cfg = readLddConfig(dir);
  eq(cfg.layers.e2e_driven_iteration, true, 'preset quick: e2e on');
  eq(cfg.layers.drift_detection, false, 'preset quick: drift_detection off');
  eq(cfg.layers.method_evolution, false, 'preset quick: method_evolution off');
  inChat.dispatch({ ...baseCtx(f), text: '/ldd-preset off' });
  cfg = readLddConfig(dir);
  eq(cfg.enabled, false, 'preset off: master disabled');
  inChat.dispatch({ ...baseCtx(f), text: '/ldd-preset default' });
  cfg = readLddConfig(dir);
  eq(cfg.enabled, true, 'preset default: master re-enabled');
  for (const layer of Object.keys(cfg.layers)) {
    if (cfg.layers[layer] !== true) {
      bad(`preset default: layer ${layer} should be true, got ${cfg.layers[layer]}`);
    }
  }
  ok('preset default: every layer back to on');
}

// ── 6. /ldd-preset with unknown name is rejected ─────────────────────────
console.log('\n=== /ldd-preset rejects unknown name ===');
{
  fresh();
  const f = tmpSettings();
  const r = inChat.dispatch({ ...baseCtx(f), text: '/ldd-preset blarg' });
  contains(r && r.reply, 'unknown preset', '/ldd-preset unknown reports unknown');
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail === 0 ? 0 : 1);
