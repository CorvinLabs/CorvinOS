#!/usr/bin/env node
// test_disclosure_ordering.js — regression for the EU AI Act Art. 50 fix in
// daemon.js: the bot-disclosure card must be marked "seen" ONLY after it has
// actually been sent. A transient send failure must NOT persist has_seen=true
// (that would fail toward NON-disclosure — the card would never be re-shown).
//
// daemon.js self-boots on require (IMAP connect + process.exit), so it is not
// import-safe. Instead we extract the REAL disclosure block from the daemon.js
// source text and execute it against injected mocks — this exercises the
// actual shipped code, not a copy.
//
// Run: node operator/bridges/email/test_disclosure_ordering.js

'use strict';

const fs   = require('fs');
const path = require('path');

const SRC = fs.readFileSync(path.join(__dirname, 'daemon.js'), 'utf8');

let pass = 0, fail = 0;
function ok(cond, label, extra) {
  console.log(`  ${cond ? 'PASS' : 'FAIL'}  ${label}` + (extra ? ` — ${extra}` : ''));
  if (cond) pass++; else fail++;
}

// ── extract the `if (card) { … }` disclosure block verbatim from daemon.js ──
// Anchor on the comment we ship above the block and capture to its closing brace.
const anchor = SRC.indexOf('if (card) {');
ok(anchor !== -1, 'disclosure `if (card)` block present in daemon.js');

// Brace-match from the `{` of `if (card) {` to its matching `}`.
function extractBlock(src, startIdx) {
  const braceOpen = src.indexOf('{', startIdx);
  let depth = 0;
  for (let i = braceOpen; i < src.length; i++) {
    if (src[i] === '{') depth++;
    else if (src[i] === '}') { depth--; if (depth === 0) return src.slice(startIdx, i + 1); }
  }
  throw new Error('unbalanced braces');
}
const block = extractBlock(SRC, anchor);

// Structural guard: markSeen must live INSIDE a try and there must be a catch
// that does NOT mark seen.
ok(/try\s*{[\s\S]*await sendReply[\s\S]*disclosureMarkSeen[\s\S]*}\s*catch/.test(block),
   'markSeen sits after `await sendReply` inside the try');
ok(!/catch\s*\([^)]*\)\s*{[^}]*disclosureMarkSeen/.test(block),
   'catch branch does NOT call disclosureMarkSeen');

// ── behavioral: run the real block with mocks, success + failure paths ──────
async function runBlock({ sendShouldThrow }) {
  const calls = { sendReply: 0, markSeen: 0 };
  const inChatCmds = {
    disclosureMarkSeen: () => { calls.markSeen++; return { ok: true }; },
  };
  const sendReply = async () => {
    calls.sendReply++;
    if (sendShouldThrow) throw new Error('SMTP down');
  };
  const log = () => {};
  const CHANNEL = 'email';
  const fromAddr = 'alice@example.com';
  const subject = 'hi';
  const card = 'You are talking to an AI. (disclosure)';

  // Wrap the extracted block in an async fn with the same free variables the
  // daemon provides at that point in handleParsed().
  const fn = new Function(
    'inChatCmds', 'sendReply', 'log', 'CHANNEL', 'fromAddr', 'subject', 'card',
    `return (async () => { ${block} })();`,
  );
  await fn(inChatCmds, sendReply, log, CHANNEL, fromAddr, subject, card);
  return calls;
}

(async () => {
  console.log('\n[success path → mark seen]');
  {
    const c = await runBlock({ sendShouldThrow: false });
    ok(c.sendReply === 1, 'sendReply attempted');
    ok(c.markSeen === 1, 'markSeen called after successful send');
  }

  console.log('\n[send fails → do NOT mark seen (re-disclosure preserved)]');
  {
    const c = await runBlock({ sendShouldThrow: true });
    ok(c.sendReply === 1, 'sendReply attempted');
    ok(c.markSeen === 0, 'markSeen NOT called when send throws');
  }

  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail === 0 ? 0 : 1);
})();
