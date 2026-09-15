#!/usr/bin/env node
// R2-B3 regression (adversarial review round 2, 2026-09-07): the daemon's
// processed-UID memory (imap_state.json) must be bounded WITHOUT re-downloading
// old mail. The old set evicted by count alone, so >5000 unread rejected mails
// were re-fetched on every poll forever (a DoS any unauthenticated sender could
// sustain). Now: a low-water mark (`min_uid`) + a per-poll download cap, and a
// state file that is validated on load (no NaN / negative / stringly UIDs).
//
// daemon.js self-boots on require, so — as in test_inbound_auth.js — the REAL
// helper functions are extracted from the source and bound to an injected
// `fs`/state-file path. This exercises the shipped code, not a copy.
//
// Run: node operator/bridges/email/test_imap_state.js

'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');

const SRC = fs.readFileSync(path.join(__dirname, 'daemon.js'), 'utf8');

let pass = 0, fail = 0;
function t(label, ok, detail = '') {
  console.log(`  ${ok ? 'PASS' : 'FAIL'}  ${label}${detail ? ' — ' + detail : ''}`);
  if (ok) pass++; else fail++;
}

function extractFn(src, name) {
  const decl = `function ${name}(`;
  const start = src.indexOf(decl);
  if (start === -1) throw new Error(`function ${name} not found`);
  const braceOpen = src.indexOf('{', start);
  let depth = 0;
  for (let i = braceOpen; i < src.length; i++) {
    if (src[i] === '{') depth++;
    else if (src[i] === '}') { depth--; if (depth === 0) return src.slice(start, i + 1); }
  }
  throw new Error(`unbalanced braces in ${name}`);
}

const NAMES = ['normalizeUid', 'loadImapState', 'compactImapState', 'saveImapState',
  'resetImapStateIfMailboxChanged', 'isUidProcessed', 'markUidProcessed', 'selectUidsForPoll'];
const fnSrc = NAMES.map((n) => extractFn(SRC, n)).join('\n\n');

const CONST_MAX = Number((SRC.match(/const IMAP_STATE_MAX_UIDS = (\d+);/) || [])[1]);
const CONST_CAP = Number((SRC.match(/const IMAP_POLL_MAX_DOWNLOADS = (\d+);/) || [])[1]);
t('daemon declares IMAP_STATE_MAX_UIDS + IMAP_POLL_MAX_DOWNLOADS',
  Number.isInteger(CONST_MAX) && Number.isInteger(CONST_CAP) && CONST_CAP > 0 && CONST_CAP < CONST_MAX,
  `max=${CONST_MAX} cap=${CONST_CAP}`);

function makeState(stateFile, { max = CONST_MAX, cap = CONST_CAP } = {}) {
  const logs = [];
  const factory = new Function(
    'fs', 'IMAP_STATE_FILE', 'IMAP_STATE_MAX_UIDS', 'IMAP_POLL_MAX_DOWNLOADS', 'log',
    `let imapState = { uidvalidity: null, min_uid: 0, uids: [] };
     let imapStateSet = new Set();
     ${fnSrc}
     return { ${NAMES.join(', ')},
       get state() { return imapState; }, get set() { return imapStateSet; } };`,
  );
  const H = factory(fs, stateFile, max, cap, (m) => logs.push(m));
  H.logs = logs;
  return H;
}

const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'imap-state-test-'));
const STATE = path.join(dir, 'imap_state.json');

// 1. corrupted / truncated state file → empty state, no throw
console.log('\n[corrupted state file → clean start]');
{
  fs.writeFileSync(STATE, '{"uidvalidity":1,"uids":[1,2,3');
  const H = makeState(STATE);
  H.loadImapState();
  t('truncated JSON → empty set', H.set.size === 0 && H.state.uidvalidity === null);
  fs.writeFileSync(STATE, '{"uidvalidity":1,"uids":"notarray"}');
  H.loadImapState();
  t('uids not an array → ignored', H.set.size === 0);
  fs.writeFileSync(STATE, '[1,2,3]');
  H.loadImapState();
  t('non-object JSON → ignored', H.set.size === 0);
}

// 2. weird values are sanitised (old code planted NaN and stringly UIDVALIDITY)
console.log('\n[weird values sanitised]');
{
  fs.writeFileSync(STATE, '{"uidvalidity":"1","min_uid":"3","uids":["abc",-5,1e300,"7",2,3.5,null]}');
  const H = makeState(STATE);
  H.loadImapState();
  t('only finite non-negative integers >= min_uid survive', JSON.stringify(H.state.uids) === '[7]', JSON.stringify(H.state.uids));
  t('no NaN in the set', !H.set.has(NaN));
  t('stringly uidvalidity normalised to a number', H.state.uidvalidity === 1);
  t('stringly min_uid normalised', H.state.min_uid === 3);
  H.resetImapStateIfMailboxChanged(1);
  t('numeric UIDVALIDITY 1 vs stored "1" is NOT a mailbox change', H.set.size === 1 && H.logs.length === 0, H.logs.join('|'));
  H.resetImapStateIfMailboxChanged('1');
  t('stringly UIDVALIDITY from the server is normalised too', H.set.size === 1 && H.logs.length === 0, H.logs.join('|'));
  H.resetImapStateIfMailboxChanged(2);
  t('a real UIDVALIDITY change resets set AND low-water mark',
    H.set.size === 0 && H.state.min_uid === 0 && H.state.uidvalidity === 2, JSON.stringify(H.state));
}

// 3. THE DoS: 6000 unread rejected mails must NOT be re-downloaded every poll
console.log('\n[>MAX unread mails: low-water mark stops the re-download loop]');
{
  fs.writeFileSync(STATE, '{"uidvalidity":1,"uids":[]}');
  const H = makeState(STATE, { max: 500, cap: 10000 });
  H.loadImapState();
  const unseenAll = Array.from({ length: 600 }, (_, i) => i + 1);
  const downloads = [];
  for (let poll = 1; poll <= 3; poll++) {
    const { batch } = H.selectUidsForPoll(unseenAll);
    for (const u of batch) H.markUidProcessed(u);
    downloads.push(batch.length);
  }
  t('poll 1 downloads everything once', downloads[0] === 600, JSON.stringify(downloads));
  t('polls 2+3 download NOTHING (old code: 100 re-downloads per poll forever)',
    downloads[1] === 0 && downloads[2] === 0, JSON.stringify(downloads));
  t('set bounded to MAX', H.set.size <= 500, String(H.set.size));
  t('low-water mark raised above the evicted UIDs', H.state.min_uid === 101, String(H.state.min_uid));
  t('an evicted UID still counts as processed', H.isUidProcessed(1) && H.isUidProcessed(100));
  t('a kept UID counts as processed', H.isUidProcessed(600));
  t('a future UID is pending', !H.isUidProcessed(601));
  const persisted = JSON.parse(fs.readFileSync(STATE, 'utf8'));
  t('min_uid persisted to the state file', persisted.min_uid === 101, JSON.stringify(persisted).slice(0, 80));
  t('state file is 0600', (fs.statSync(STATE).mode & 0o777) === 0o600);
  // reload from disk: the mark survives a daemon restart
  const H2 = makeState(STATE, { max: 500, cap: 10000 });
  H2.loadImapState();
  t('after reload nothing is re-downloaded', H2.selectUidsForPoll(unseenAll).batch.length === 0);
  H2.markUidProcessed(50);
  t('markUidProcessed below the mark is a no-op', !H2.set.has(50) && H2.state.uids.length === 500);
}

// 4. per-poll download cap: a flood is drained in bounded, ascending slices
console.log('\n[per-poll download cap]');
{
  fs.writeFileSync(STATE, '{"uidvalidity":1,"uids":[]}');
  const H = makeState(STATE, { max: 5000, cap: 200 });
  H.loadImapState();
  const flood = Array.from({ length: 1000 }, (_, i) => 1000 - i); // server order: descending
  const { batch, pending } = H.selectUidsForPoll(flood);
  t('batch capped at IMAP_POLL_MAX_DOWNLOADS', batch.length === 200, String(batch.length));
  t('pending reports the full backlog', pending === 1000, String(pending));
  t('smallest UIDs first (keeps the low-water mark sound)',
    batch[0] === 1 && batch[199] === 200 && batch.every((u, i) => i === 0 || u > batch[i - 1]));
  for (const u of batch) H.markUidProcessed(u);
  const second = H.selectUidsForPoll(flood).batch;
  t('next poll continues with the next slice', second[0] === 201 && second.length === 200, JSON.stringify(second.slice(0, 3)));
  t('unparsable UIDs are never selected', H.selectUidsForPoll(['x', -1, 1.5, null, 5000]).batch.join(',') === '5000');
}

fs.rmSync(dir, { recursive: true, force: true });
console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail === 0 ? 0 : 1);
