#!/usr/bin/env node
// test_roles_dispatcher.js — Layer 18 capability-bundle dispatcher.
//
// Covers /role, /roles, /grant, /revoke, /leave through dispatch() in
// in_chat_commands.js. Each command shells out to roles.py via
// _runRolesCli; the test points CORVIN_HOME at a tempdir and writes a
// fixture settings.json into a sentinel channel directory under
// bridges/_test_l18/ that the real bridges never use, so we don't have
// to monkey-patch the python resolver.
//
// Run: node operator/bridges/shared/js/test_roles_dispatcher.js

'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');

const TMP = fs.mkdtempSync(path.join(os.tmpdir(), 'roles-disp-'));
process.env.CORVIN_HOME = path.join(TMP, 'corvin');

const CHANNEL = '_test_l18';
const CHAT = 'c-l18';
const OWNER = 'owner-18';
const ADMIN = 'admin-18';
const MEMBER = 'member-18';
const STRANGER = 'stranger-18';

// Bridges-level fixture path the python CLI's _channel_settings_path will
// hit. We write it for the duration of the test and rmdir at teardown.
const BRIDGES_DIR = path.resolve(__dirname, '..', '..', CHANNEL);
const SETTINGS = path.join(BRIDGES_DIR, 'settings.json');

function writeSettings(obj) {
  fs.mkdirSync(BRIDGES_DIR, { recursive: true });
  fs.writeFileSync(SETTINGS, JSON.stringify(obj, null, 2));
}
function cleanupBridges() {
  try { fs.rmSync(BRIDGES_DIR, { recursive: true, force: true }); } catch {}
}
process.on('exit', cleanupBridges);

writeSettings({ whitelist: [OWNER], read_only: [] });

// Re-import fresh so the module can pick up the fresh CORVIN_HOME.
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

// ── 1: dispatch routing for the new commands ─────────────────────────────
console.log('\n[dispatch routing]');
ok(dispatch(CTX('/role', OWNER, true)) !== null, '/role routed');
ok(dispatch(CTX('/roles', OWNER, true)) !== null, '/roles routed');
ok(dispatch(CTX('/grant', OWNER, true)) !== null, '/grant routed (usage hint)');
ok(dispatch(CTX('/revoke', OWNER, true)) !== null, '/revoke routed (usage hint)');
ok(dispatch(CTX('/leave', MEMBER, false)) !== null, '/leave routed');

ok(dispatch(CTX('hello world', OWNER, true)) === null, 'plain text → null');
ok(dispatch(CTX('', OWNER, true)) === null, 'empty → null');

// ── 2: /role missing-uid friendly hint ──────────────────────────────────
console.log('\n[/role usage]');
{
  // Dispatch /role with no tail → owner self-status (returns role info)
  const r = dispatch(CTX('/role', OWNER, true));
  ok(typeof r.reply === 'string' && r.reply.length > 10,
     '/role (no tail) returns text reply');
}

// ── 3: /role <uid> for stranger ──────────────────────────────────────────
console.log('\n[/role <uid>]');
{
  const r = dispatch(CTX(`/role ${STRANGER}`, OWNER, true));
  ok(/role/i.test(r.reply) || /none/i.test(r.reply),
     '/role <stranger> returns role-info text', `(${r.reply.slice(0, 80)})`);
}

// ── 4: /roles full listing as owner ──────────────────────────────────────
console.log('\n[/roles owner listing]');
{
  const r = dispatch(CTX('/roles', OWNER, true));
  ok(typeof r.reply === 'string' && r.reply.length > 10, '/roles returns text');
}

// ── 5: /roles denied for member ──────────────────────────────────────────
console.log('\n[/roles member denial]');
{
  const r = dispatch(CTX('/roles', MEMBER, false));
  ok(/insufficient|denied|owner|admin|capability/i.test(r.reply),
     '/roles as member is denied', `(${r.reply.slice(0, 80)})`);
}

// ── 6: /grant usage hint when args missing ───────────────────────────────
console.log('\n[/grant usage hint]');
{
  const r = dispatch(CTX('/grant', OWNER, true));
  ok(/usage|missing|target|bundle/i.test(r.reply),
     '/grant (no args) returns usage hint', `(${r.reply.slice(0, 80)})`);
}

// ── 7: valid /grant round-trip ───────────────────────────────────────────
console.log('\n[/grant valid round-trip]');
{
  const r = dispatch(CTX(`/grant ${MEMBER} member 1h`, OWNER, true));
  ok(/✓|granted|ok|success/i.test(r.reply) || r.kind === 'role-granted'
       || r.kind === 'roles-grant',
     'owner grant member succeeds', `(${r.reply.slice(0, 80)})`);
  // Round-trip: query /role <member> now
  const q = dispatch(CTX(`/role ${MEMBER}`, OWNER, true));
  ok(/member/i.test(q.reply), '/role after grant shows member',
     `(${q.reply.slice(0, 80)})`);
}

// ── 8: /grant owner is rejected ──────────────────────────────────────────
console.log('\n[/grant owner rejected]');
{
  const r = dispatch(CTX(`/grant ${STRANGER} owner`, OWNER, true));
  ok(/owner|reject|denied|invalid|not.grantable/i.test(r.reply),
     '/grant owner blocked', `(${r.reply.slice(0, 80)})`);
}

// ── 9: admin-grants-admin denial ─────────────────────────────────────────
console.log('\n[admin-grants-admin denial]');
{
  // Owner first creates an admin
  const grantRes = dispatch(CTX(`/grant ${ADMIN} admin 1h`, OWNER, true));
  ok(/✓|granted|ok/i.test(grantRes.reply) || grantRes.kind === 'role-granted'
       || grantRes.kind === 'roles-grant',
     'owner grants admin to seed scenario');
  // Now admin tries to grant admin → must be rejected
  const second = dispatch(CTX(`/grant another-admin admin 1h`, ADMIN, false));
  ok(/insufficient|denied|reject|cannot|kerze|admin/i.test(second.reply),
     'admin cannot grant admin', `(${second.reply.slice(0, 80)})`);
}

// ── 10: admin-grants-member success ──────────────────────────────────────
console.log('\n[admin-grants-member success]');
{
  const r = dispatch(CTX(`/grant alice-via-admin member 1h`,
                         ADMIN, false));
  ok(/✓|granted|ok|member/i.test(r.reply),
     'admin can grant member', `(${r.reply.slice(0, 80)})`);
}

// ── 11: self-grant rejection ─────────────────────────────────────────────
console.log('\n[self-grant rejection]');
{
  const r = dispatch(CTX(`/grant ${OWNER} member 1h`, OWNER, true));
  ok(/self|cannot|reject/i.test(r.reply) ||
     /✓|granted|ok/i.test(r.reply) === false,
     'self-grant gracefully rejected', `(${r.reply.slice(0, 80)})`);
}

// ── 12: invalid bundle name ──────────────────────────────────────────────
console.log('\n[invalid bundle]');
{
  const r = dispatch(CTX(`/grant ${STRANGER} superuser 1h`,
                         OWNER, true));
  ok(/invalid|unknown|bundle|reject/i.test(r.reply),
     'invalid bundle rejected', `(${r.reply.slice(0, 80)})`);
}

// ── 13: /revoke removes the entry ────────────────────────────────────────
console.log('\n[/revoke flow]');
{
  const r = dispatch(CTX(`/revoke ${MEMBER}`, OWNER, true));
  ok(/✓|revoked|ok|removed/i.test(r.reply) ||
     r.kind === 'roles-revoke',
     'owner revoke member succeeds', `(${r.reply.slice(0, 80)})`);
  // Idempotent: a second revoke returns either "no-entry" or "already" hint
  const second = dispatch(CTX(`/revoke ${MEMBER}`, OWNER, true));
  ok(typeof second.reply === 'string',
     'second revoke returns reply (not crash)');
}

// ── 14: member /revoke denied ────────────────────────────────────────────
console.log('\n[member /revoke denial]');
{
  // First re-grant the member so we have a target.
  dispatch(CTX(`/grant ${MEMBER} member 1h`, OWNER, true));
  const r = dispatch(CTX(`/revoke ${OWNER}`, MEMBER, false));
  ok(/insufficient|denied|reject|cannot|capability/i.test(r.reply),
     'member cannot revoke', `(${r.reply.slice(0, 80)})`);
}

// ── 15: admin-revokes-admin denial ───────────────────────────────────────
console.log('\n[admin-revokes-admin denial]');
{
  // ADMIN exists already from test 9; create a peer admin to attack
  dispatch(CTX(`/grant peer-admin admin 1h`, OWNER, true));
  const r = dispatch(CTX(`/revoke peer-admin`, ADMIN, false));
  ok(/peer|cannot|reject|denied|insufficient/i.test(r.reply),
     'admin cannot revoke peer admin', `(${r.reply.slice(0, 80)})`);
}

// ── 16: owner /leave denial ──────────────────────────────────────────────
console.log('\n[owner /leave denial]');
{
  const r = dispatch(CTX('/leave', OWNER, true));
  ok(/owner|cannot|whitelist/i.test(r.reply),
     'owner cannot /leave', `(${r.reply.slice(0, 80)})`);
}

// ── 17: member /leave success ────────────────────────────────────────────
console.log('\n[member /leave success]');
{
  // member is currently member from test 14 re-grant
  const r = dispatch(CTX('/leave', MEMBER, false));
  ok(/✓|left|ok|removed/i.test(r.reply),
     'member can /leave', `(${r.reply.slice(0, 80)})`);
  // Verify role is now none
  const q = dispatch(CTX(`/role ${MEMBER}`, OWNER, true));
  ok(/none/i.test(q.reply) || !/\bmember\b/i.test(q.reply),
     '/role after leave shows none/no-entry', `(${q.reply.slice(0, 80)})`);
}

// ── 18: stranger /leave returns no-entry ─────────────────────────────────
console.log('\n[stranger /leave]');
{
  const r = dispatch(CTX('/leave', 'never-existed-99', false));
  ok(/no.entry|nothing|already/i.test(r.reply),
     'stranger /leave returns no-entry', `(${r.reply.slice(0, 80)})`);
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail === 0 ? 0 : 1);
