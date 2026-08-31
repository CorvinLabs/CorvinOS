#!/usr/bin/env node
// test_engine_switch_dispatcher.js — Layer-29 companion: /engine slash-command.
//
// Covers engineReply from in_chat_commands.js, which shells out to
// operator/bridges/shared/engine_switch.py. The Python module is
// the single source of truth for validation + audit; this test verifies
// the JS wrapper:
//   * dispatches /engine to engineReply
//   * shows current pref + alias help on bare /engine
//   * passes the alias through to set / clear correctly
//   * refuses non-owner callers (delegation routing is privileged)
//   * surfaces Python-side validation errors with a soft prefix
//
// Each scenario sandboxes CORVIN_HOME via a tempdir so the test never
// touches the operator's live preference store.
//
// Run: node operator/bridges/shared/js/test_engine_switch_dispatcher.js

'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');

delete require.cache[require.resolve('./in_chat_commands')];
const inChat = require('./in_chat_commands');
const dispatch = inChat.dispatch;
const { engineReply } = inChat._internal;

const TMP = fs.mkdtempSync(path.join(os.tmpdir(), 'engine-disp-'));
const SETTINGS = path.join(TMP, 'settings.json');
fs.writeFileSync(SETTINGS, '{}');
process.env.CORVIN_HOME = path.join(TMP, 'corvin');

let pass = 0, fail = 0;
function ok(cond, label, extra) {
  console.log(`  ${cond ? 'PASS' : 'FAIL'}  ${label}` + (extra ? ` — ${extra}` : ''));
  if (cond) pass++; else fail++;
}

const CTX = (text, extra = {}) => ({
  text,
  channel: 'discord',
  chatKey: 'chat-1',
  uid: 'owner-uid',
  isOwner: true,
  settingsFile: SETTINGS,
  ...extra,
});

// ── 1: Dispatch routing — /engine reaches engineReply ────────────────
console.log('\n[dispatch routing]');
{
  const r = dispatch(CTX('/engine'));
  ok(r && r.kind === 'engine', '/engine → kind=engine', JSON.stringify(r && r.kind));
  ok(r && /worker-engine preference/i.test(r.reply || ''),
     'bare /engine shows help block');
}

// ── 2: Non-owner is refused ──────────────────────────────────────────
console.log('\n[non-owner refused]');
{
  const r = engineReply(CTX('', { isOwner: false }), 'claude');
  ok(r && /owner-only/i.test(r.reply || ''),
     'non-owner gets owner-only refusal', JSON.stringify(r && r.reply));
  ok(r && r.kind === 'engine', 'kind=engine on refusal');
}

// ── 3: Bare /engine shows current pref + supported aliases ───────────
console.log('\n[bare /engine renders help]');
{
  const r = engineReply(CTX(''), '');
  const reply = (r && r.reply) || '';
  ok(/current:/i.test(reply), 'help mentions current pref');
  ok(/claude/i.test(reply), 'help lists claude alias');
  ok(/codex/i.test(reply), 'help lists codex alias');
  ok(/opencode/i.test(reply), 'help lists opencode alias');
  ok(/cloud/i.test(reply), 'help lists cloud alias');
  ok(/no preference/i.test(reply),
     'fresh sandbox shows no preference');
}

// ── 4: /engine claude — set roundtrip ────────────────────────────────
console.log('\n[/engine claude — set roundtrip]');
{
  const r = engineReply(CTX(''), 'claude');
  const reply = (r && r.reply) || '';
  ok(/claude_code/.test(reply), 'set reply mentions claude_code', JSON.stringify(reply));
  // Verify the file landed on disk via Show.
  const r2 = engineReply(CTX(''), '');
  ok(/claude_code/.test(r2.reply), 'show after set reflects claude_code');
}

// ── 5: /engine cloud — opencode + ollama-cloud model ─────────────────
console.log('\n[/engine cloud — opencode + cloud model]');
{
  const r = engineReply(CTX(''), 'cloud');
  const reply = (r && r.reply) || '';
  ok(/opencode/.test(reply), 'set reply mentions opencode');
  ok(/ollama-cloud\/qwen3-coder-next/.test(reply),
     'cloud alias persists cloud model');
}

// ── 6: /engine opencode — local Ollama model ─────────────────────────
console.log('\n[/engine opencode — local Ollama]');
{
  const r = engineReply(CTX(''), 'opencode');
  const reply = (r && r.reply) || '';
  ok(/opencode/.test(reply), 'opencode alias works');
  ok(/ollama\/qwen3:8b/.test(reply),
     'opencode pins local ollama model by default');
}

// ── 7: /engine off — clear ───────────────────────────────────────────
console.log('\n[/engine off — clear]');
{
  const r = engineReply(CTX(''), 'off');
  ok(/cleared|no preference/i.test(r.reply || ''),
     'off → cleared', JSON.stringify(r && r.reply));
  const r2 = engineReply(CTX(''), '');
  ok(/no preference/i.test(r2.reply || ''),
     'show after off → no preference');
}

// ── 8: clear/reset/none aliases all map to off ───────────────────────
console.log('\n[clear/reset/none aliases]');
for (const tok of ['clear', 'reset', 'none']) {
  // Set first so clear has something to remove.
  engineReply(CTX(''), 'codex');
  const r = engineReply(CTX(''), tok);
  ok(/cleared|no preference/i.test(r.reply || ''),
     `/engine ${tok} clears`, JSON.stringify(r && r.reply));
}

// ── 9: Unknown alias surfaces validation error with soft prefix ──────
console.log('\n[unknown alias → soft error]');
{
  const r = engineReply(CTX(''), 'gemini');
  const reply = (r && r.reply) || '';
  ok(/⚠️|unknown engine alias/i.test(reply),
     'unknown alias surfaces diagnostic', JSON.stringify(reply));
  ok(/supported:/i.test(reply),
     'diagnostic mentions supported aliases');
}

// ── 10: Case-insensitive alias accept ────────────────────────────────
console.log('\n[case-insensitive aliases]');
for (const tok of ['CLAUDE', 'Claude', 'CoDeX']) {
  const r = engineReply(CTX(''), tok);
  const reply = (r && r.reply) || '';
  ok(!/⚠️/.test(reply),
     `/engine ${tok} accepted (case-insensitive)`,
     JSON.stringify(reply).slice(0, 80));
}

// ── 11: Audit chain receives metadata-only entries ───────────────────
console.log('\n[audit chain emits engine.pref_switched]');
{
  const chainPath = path.join(process.env.CORVIN_HOME, 'global', 'forge', 'audit.jsonl');
  const present = fs.existsSync(chainPath);
  ok(present, 'audit chain file exists', chainPath);
  if (present) {
    const lines = fs.readFileSync(chainPath, 'utf8')
      .split('\n').filter(Boolean)
      .map(l => { try { return JSON.parse(l); } catch { return null; } })
      .filter(Boolean);
    const eng = lines.filter(r => r.event_type === 'engine.pref_switched');
    ok(eng.length > 0, `engine.pref_switched in chain (${eng.length})`);
    // Metadata-only: no prompt / output / freetext fields.
    const allowed = new Set(['channel', 'chat_key', 'uid', 'action', 'engine', 'model']);
    // Infrastructure keys injected by write_event() (e.g. chain_dna from ADR-0132 LSAD).
    const infraKeys = new Set(['chain_dna']);
    let leaked = '';
    for (const rec of eng) {
      for (const k of Object.keys(rec.details || {})) {
        if (!allowed.has(k) && !infraKeys.has(k)) { leaked = k; break; }
      }
      if (leaked) break;
    }
    ok(leaked === '', 'no smuggled audit detail keys',
       leaked ? `leaked: ${leaked}` : '');
  }
}

// ── 12: Cleanup ──────────────────────────────────────────────────────
try {
  fs.rmSync(TMP, { recursive: true, force: true });
} catch { /* best-effort */ }

console.log(`\n${pass} pass / ${fail} fail`);
process.exit(fail > 0 ? 1 : 0);
