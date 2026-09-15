#!/usr/bin/env node
// test_proposal_dispatcher.js — Layer 21 proposal stack dispatcher.
//
// Covers /propose, /proposals, /proposal rm|clear via dispatch(); the
// read-only-side dispatcher dispatchReadOnlyProposal; and the /go helper
// proposalsBuildGoPayload. Backed by python3 proposal.py via spawnSync.
//
// Run: node operator/bridges/shared/js/test_proposal_dispatcher.js

'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');

const TMP = fs.mkdtempSync(path.join(os.tmpdir(), 'prop-disp-'));
process.env.CORVIN_HOME = path.join(TMP, 'corvin');

const CHANNEL = '_test_l21';
const CHAT = 'c-l21';
const OWNER = 'owner-21';
const MEMBER = 'member-21';
const STRANGER = 'stranger-21';

const BRIDGES_DIR = path.resolve(__dirname, '..', '..', CHANNEL);
const SETTINGS = path.join(BRIDGES_DIR, 'settings.json');
fs.mkdirSync(BRIDGES_DIR, { recursive: true });
fs.writeFileSync(SETTINGS, JSON.stringify({ whitelist: [OWNER] }));
process.on('exit', () => {
  try { fs.rmSync(BRIDGES_DIR, { recursive: true, force: true }); } catch {}
});

delete require.cache[require.resolve('./in_chat_commands')];
const inChat = require('./in_chat_commands');
const {
  dispatch,
  dispatchReadOnlyProposal,
  proposalsBuildGoPayload,
} = inChat;

let pass = 0, fail = 0;
function ok(cond, label, extra) {
  console.log(`  ${cond ? 'PASS' : 'FAIL'}  ${label}` + (extra ? ` — ${extra}` : ''));
  if (cond) pass++; else fail++;
}

const CTX = (text, uid, isOwner) => ({
  text, channel: CHANNEL, chatKey: CHAT, uid, isOwner,
  settingsFile: SETTINGS,
});

// ── 1: dispatch routing for /propose, /proposals, /proposal ─────────────
console.log('\n[dispatch routing]');
ok(dispatch(CTX('/propose hello', OWNER, true)) !== null,
   '/propose <text> routed');
ok(dispatch(CTX('/proposals', OWNER, true)) !== null, '/proposals routed');
ok(dispatch(CTX('/proposal rm xyz', OWNER, true)) !== null,
   '/proposal rm routed');
ok(dispatch(CTX('hello', OWNER, true)) === null, 'plain text → null');
ok(dispatch(CTX('/go', OWNER, true)) === null,
   '/go is NOT in dispatch (handled by daemon)');

// ── 2: /propose usage hint when no body ────────────────────────────────
console.log('\n[/propose usage hint]');
{
  const r = dispatch(CTX('/propose', OWNER, true));
  ok(/usage|propose|text/i.test(r.reply),
     '/propose without body returns usage hint',
     `(${r.reply.slice(0, 80)})`);
}

// ── 3: owner /propose adds entry, returns id ───────────────────────────
console.log('\n[owner /propose adds]');
{
  const r = dispatch(CTX('/propose feature X for Y', OWNER, true));
  ok(/✓|added|stack/i.test(r.reply),
     'owner /propose returns ack', `(${r.reply.slice(0, 80)})`);
  ok(r.kind === 'propose-ok', 'kind=propose-ok');
}

// ── 4: member /propose also adds (member uses dispatch path) ───────────
console.log('\n[member /propose]');
{
  // Seed member role
  const ROLES_CLI = path.resolve(__dirname, '..', 'roles.py');
  const { spawnSync } = require('child_process');
  spawnSync('python3', [ROLES_CLI, 'grant', CHANNEL, CHAT, MEMBER,
                        'member', OWNER, '1h', 'test'], { encoding: 'utf8' });

  const r = dispatch(CTX('/propose another idea', MEMBER, false));
  ok(/✓|added/i.test(r.reply),
     'member /propose succeeds', `(${r.reply.slice(0, 80)})`);
}

// ── 5: /proposals shows the stack as owner ─────────────────────────────
console.log('\n[/proposals as owner]');
{
  const r = dispatch(CTX('/proposals', OWNER, true));
  ok(/proposal stack|feature x|another idea/i.test(r.reply),
     'owner /proposals lists entries', `(${r.reply.slice(0, 80)})`);
}

// ── 6: /proposals denied for member ────────────────────────────────────
console.log('\n[/proposals denied for member]');
{
  const r = dispatch(CTX('/proposals', MEMBER, false));
  ok(/owner|admin|denied/i.test(r.reply),
     'member denied /proposals', `(${r.reply.slice(0, 80)})`);
}

// ── 7: /proposal rm round-trip ────────────────────────────────────────
console.log('\n[/proposal rm]');
{
  // First grab an id from /proposals
  const list = dispatch(CTX('/proposals', OWNER, true));
  const m = list.reply.match(/^\s*([a-f0-9]{6})\s/m);
  if (!m) {
    ok(false, 'failed to extract proposal id from /proposals output',
       list.reply.slice(0, 200));
  } else {
    const pid = m[1];
    const rm = dispatch(CTX(`/proposal rm ${pid}`, OWNER, true));
    ok(/✓|removed/i.test(rm.reply),
       `/proposal rm ${pid} returns ack`, `(${rm.reply.slice(0, 80)})`);
    // Idempotent rm: second call should say "no proposal"
    const rm2 = dispatch(CTX(`/proposal rm ${pid}`, OWNER, true));
    ok(/no proposal|not found|already/i.test(rm2.reply),
       'second rm returns no-entry hint', `(${rm2.reply.slice(0, 80)})`);
  }
}

// ── 8: /proposal clear empties stack ───────────────────────────────────
console.log('\n[/proposal clear]');
{
  const r = dispatch(CTX('/proposal clear', OWNER, true));
  ok(/✓|cleared/i.test(r.reply),
     '/proposal clear returns ack', `(${r.reply.slice(0, 80)})`);
  const list = dispatch(CTX('/proposals', OWNER, true));
  ok(/no proposals|empty/i.test(list.reply),
     'after clear, /proposals empty', `(${list.reply.slice(0, 80)})`);
}

// ── 9: /proposal clear denied for member ───────────────────────────────
console.log('\n[/proposal clear denied for member]');
{
  const r = dispatch(CTX('/proposal clear', MEMBER, false));
  ok(/owner|admin|denied/i.test(r.reply),
     'member denied /proposal clear', `(${r.reply.slice(0, 80)})`);
}

// ── 10: dispatchReadOnlyProposal: routing ──────────────────────────────
console.log('\n[read-only /propose dispatcher]');
ok(dispatchReadOnlyProposal(CTX('hello', STRANGER, false)) === null,
   'plain text → null');
ok(dispatchReadOnlyProposal(CTX('/help', STRANGER, false)) === null,
   '/help → null');
ok(dispatchReadOnlyProposal(CTX('/proposeXfoo', STRANGER, false)) === null,
   '/proposeXfoo (no boundary) → null');

// ── 11: read-only /propose without body returns usage ──────────────────
console.log('\n[read-only /propose usage]');
{
  const r = dispatchReadOnlyProposal(CTX('/propose', STRANGER, false));
  ok(/usage|stack|text/i.test(r.reply),
     'bare /propose returns usage hint', `(${r.reply.slice(0, 80)})`);
}

// ── 12: read-only /propose <text> adds entry ───────────────────────────
console.log('\n[read-only /propose <text>]');
{
  const r = dispatchReadOnlyProposal(CTX('/propose suggestion from observer',
                                          STRANGER, false));
  ok(/✓|added/i.test(r.reply),
     'read-only /propose adds entry', `(${r.reply.slice(0, 80)})`);
  ok(r.kind === 'propose-ok', 'kind=propose-ok');
}

// ── 13: missing uid hint ───────────────────────────────────────────────
console.log('\n[missing uid hint]');
{
  const r = dispatchReadOnlyProposal(CTX('/propose hi', '', false));
  ok(/uid|daemon/i.test(r.reply),
     'missing uid returns daemon-bug hint', `(${r.reply.slice(0, 80)})`);
}

// ── 14: proposalsBuildGoPayload — empty stack + no owner text ──────────
console.log('\n[/go payload empty]');
{
  // Clear any leftover proposals
  dispatch(CTX('/proposal clear', OWNER, true));
  const p = proposalsBuildGoPayload(CTX('/go', OWNER, true), '');
  ok(p.allowed === false, 'empty /go is not allowed');
  ok(p.reason === 'empty', `reason=empty (got ${p.reason})`);
}

// ── 15: proposalsBuildGoPayload — populated stack ──────────────────────
console.log('\n[/go payload with stack]');
{
  dispatch(CTX('/propose item one', OWNER, true));
  dispatch(CTX('/propose item two', OWNER, true));
  const p = proposalsBuildGoPayload(CTX('/go', OWNER, true), '');
  ok(p.allowed === true, 'populated stack /go is allowed');
  ok(p.count === 2, `count=2 (got ${p.count})`);
  ok(/PROPOSAL STACK/.test(p.prompt),
     'prompt contains PROPOSAL STACK marker');
  ok(/item one/.test(p.prompt) && /item two/.test(p.prompt),
     'both items in prompt');
}

// ── 16: proposalsBuildGoPayload — owner steering only (empty stack) ────
console.log('\n[/go payload owner-text only]');
{
  // Stack is empty after consume in test 15
  const p = proposalsBuildGoPayload(CTX('/go', OWNER, true),
                                     'just a question');
  ok(p.allowed === true, 'steering-only /go allowed');
  ok(p.count === 0, `count=0 (got ${p.count})`);
  ok(/just a question/.test(p.prompt),
     'prompt contains owner steering text');
}

// ── 17: proposalsBuildGoPayload — member denial ────────────────────────
console.log('\n[/go denied for member]');
{
  const p = proposalsBuildGoPayload(CTX('/go', MEMBER, false), 'go');
  ok(p.allowed === false, 'member /go is not allowed');
  ok(/owner|admin|insufficient/i.test(p.reply),
     'member /go reason includes owner/admin', `(${p.reply.slice(0, 80)})`);
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail === 0 ? 0 : 1);
