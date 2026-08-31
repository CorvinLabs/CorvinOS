#!/usr/bin/env node
// test_quota_dispatcher.js — Layer 20 quota + audit dispatcher.
//
// Covers /quota and /audit through dispatch() in in_chat_commands.js.
// Each command shells out to quota.py / audit_view.py via the
// _runQuotaCli / _runAuditViewCli helpers; the test points
// CORVIN_HOME at a tempdir and writes a fixture settings.json
// into a sentinel channel directory under bridges/_test_l20/.
//
// Run: node operator/bridges/shared/js/test_quota_dispatcher.js

'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawnSync } = require('child_process');

const TMP = fs.mkdtempSync(path.join(os.tmpdir(), 'quota-disp-'));
process.env.CORVIN_HOME = path.join(TMP, 'corvin');

const CHANNEL = '_test_l20';
const CHAT = 'c-l20';
const OWNER = 'owner-20';
const MEMBER = 'member-20';
const STRANGER = 'stranger-20';

const BRIDGES_DIR = path.resolve(__dirname, '..', '..', CHANNEL);
const SETTINGS = path.join(BRIDGES_DIR, 'settings.json');
fs.mkdirSync(BRIDGES_DIR, { recursive: true });
fs.writeFileSync(SETTINGS, JSON.stringify({ whitelist: [OWNER] }));
process.on('exit', () => {
  try { fs.rmSync(BRIDGES_DIR, { recursive: true, force: true }); } catch {}
});

delete require.cache[require.resolve('./in_chat_commands')];
const inChat = require('./in_chat_commands');
const { dispatch } = inChat;

let pass = 0, fail = 0;
function ok(cond, label, extra) {
  console.log(`  ${cond ? 'PASS' : 'FAIL'}  ${label}` + (extra ? ` — ${extra}` : ''));
  if (cond) pass++; else fail++;
}

const CTX = (text, uid, isOwner) => ({
  text, channel: CHANNEL, chatKey: CHAT, uid, isOwner,
  settingsFile: SETTINGS,
});

// Helper — grant member directly via roles.py CLI so the test scenario
// includes a non-owner caller. CORVIN_HOME passes through.
function seedMember() {
  const ROLES_CLI = path.resolve(__dirname, '..', 'roles.py');
  const r = spawnSync('python3', [ROLES_CLI, 'grant',
                                   CHANNEL, CHAT, MEMBER,
                                   'member', OWNER, '1h', 'test'],
                      { encoding: 'utf8' });
  if (r.status !== 0) throw new Error(`seedMember failed: ${r.stderr}`);
}

// ── 1: dispatch routing for /quota and /audit ───────────────────────────
console.log('\n[dispatch routing]');
ok(dispatch(CTX('/quota', OWNER, true)) !== null, '/quota routed');
ok(dispatch(CTX('/audit', OWNER, true)) !== null, '/audit routed');
ok(dispatch(CTX('hello', OWNER, true)) === null, 'plain text → null');

// ── 2: owner-bypass display (no caps in formatted reply) ────────────────
console.log('\n[owner-bypass display]');
{
  const r = dispatch(CTX('/quota', OWNER, true));
  ok(typeof r.reply === 'string' && r.reply.length > 5,
     '/quota for owner returns text', `(${r.reply.slice(0, 80)})`);
  ok(/owner|unlimited|bypass/i.test(r.reply) ||
       /messages|tokens/i.test(r.reply),
     'owner reply mentions unlimited/owner/bypass or counters',
     `(${r.reply.slice(0, 80)})`);
}

// ── 3: member self-view ─────────────────────────────────────────────────
console.log('\n[member self-view]');
{
  seedMember();
  const r = dispatch(CTX('/quota', MEMBER, false));
  ok(typeof r.reply === 'string' && r.reply.length > 5,
     'member self-quota returns text', `(${r.reply.slice(0, 80)})`);
  ok(/messages|tokens|cap|limit/i.test(r.reply),
     'member reply mentions limits/messages/tokens',
     `(${r.reply.slice(0, 80)})`);
}

// ── 4: member denied other-user quota ───────────────────────────────────
console.log('\n[member denied /quota <other>]');
{
  const r = dispatch(CTX(`/quota ${OWNER}`, MEMBER, false));
  ok(/owner|admin|denied|may.*view/i.test(r.reply),
     'member cannot view another user\'s quota',
     `(${r.reply.slice(0, 80)})`);
}

// ── 5: owner sees other user's quota ────────────────────────────────────
console.log('\n[owner sees other user]');
{
  const r = dispatch(CTX(`/quota ${MEMBER}`, OWNER, true));
  ok(typeof r.reply === 'string' && r.reply.length > 5,
     'owner /quota <member> returns text', `(${r.reply.slice(0, 80)})`);
}

// ── 6: /quota all as owner ──────────────────────────────────────────────
console.log('\n[/quota all]');
{
  const r = dispatch(CTX('/quota all', OWNER, true));
  ok(typeof r.reply === 'string',
     'owner /quota all returns text', `(${r.reply.slice(0, 80)})`);
}

// ── 7: /quota all denied for member ─────────────────────────────────────
console.log('\n[/quota all denied for member]');
{
  const r = dispatch(CTX('/quota all', MEMBER, false));
  ok(/owner|admin|denied/i.test(r.reply),
     'member denied /quota all', `(${r.reply.slice(0, 80)})`);
}

// ── 8: /quota set + /quota usage round-trip ────────────────────────────
console.log('\n[/quota set + verify]');
{
  const set = dispatch(CTX(`/quota set ${MEMBER} 42 1000`, OWNER, true));
  ok(/✓|updated|set/i.test(set.reply) || set.kind === 'quota-set',
     '/quota set returns ack', `(${set.reply.slice(0, 80)})`);
  const view = dispatch(CTX(`/quota ${MEMBER}`, OWNER, true));
  ok(/42|1000/.test(view.reply),
     'view after set shows new caps', `(${view.reply.slice(0, 80)})`);
}

// ── 9: /quota set denied for member ─────────────────────────────────────
console.log('\n[/quota set denied for member]');
{
  const r = dispatch(CTX(`/quota set ${MEMBER} 99 9999`, MEMBER, false));
  ok(/owner|admin|denied/i.test(r.reply),
     'member denied /quota set', `(${r.reply.slice(0, 80)})`);
}

// ── 10: /quota reset round-trip ─────────────────────────────────────────
console.log('\n[/quota reset]');
{
  const r = dispatch(CTX(`/quota reset ${MEMBER}`, OWNER, true));
  ok(/✓|reset|no.quota/i.test(r.reply) ||
       r.kind === 'quota-reset',
     '/quota reset returns ack', `(${r.reply.slice(0, 80)})`);
}

// ── 11: /audit me as owner shows recent grant events ───────────────────
console.log('\n[/audit me as owner]');
{
  const r = dispatch(CTX('/audit me', OWNER, true));
  ok(typeof r.reply === 'string' && r.reply.length > 5,
     '/audit me returns text', `(${r.reply.slice(0, 80)})`);
}

// ── 12: /audit me as member shows their own events ─────────────────────
console.log('\n[/audit me as member]');
{
  const r = dispatch(CTX('/audit me', MEMBER, false));
  ok(typeof r.reply === 'string',
     'member /audit me returns text', `(${r.reply.slice(0, 80)})`);
}

// ── 13: /audit chat as owner ───────────────────────────────────────────
console.log('\n[/audit chat as owner]');
{
  const r = dispatch(CTX('/audit chat', OWNER, true));
  ok(typeof r.reply === 'string',
     'owner /audit chat returns text', `(${r.reply.slice(0, 80)})`);
}

// ── 14: /audit chat denied for member ──────────────────────────────────
console.log('\n[/audit chat denied for member]');
{
  const r = dispatch(CTX('/audit chat', MEMBER, false));
  ok(/owner|admin|denied|capability/i.test(r.reply),
     'member denied /audit chat', `(${r.reply.slice(0, 80)})`);
}

// ── 15: /audit chat with prefix filter ─────────────────────────────────
console.log('\n[/audit chat with prefix filter]');
{
  const r = dispatch(CTX('/audit chat 5 grant.', OWNER, true));
  ok(typeof r.reply === 'string',
     'prefix-filtered /audit chat returns text',
     `(${r.reply.slice(0, 80)})`);
}

// ── 16: /audit (no args) defaults to /audit me ─────────────────────────
console.log('\n[/audit no-args defaults to me]');
{
  const r = dispatch(CTX('/audit', OWNER, true));
  ok(typeof r.reply === 'string',
     '/audit no-args returns me-scope text', `(${r.reply.slice(0, 80)})`);
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail === 0 ? 0 : 1);
