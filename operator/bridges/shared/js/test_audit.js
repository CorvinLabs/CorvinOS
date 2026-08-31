#!/usr/bin/env node
// Phase H2 E2E: bridges/shared/js/audit.js wires real audit events
// from Node.js into the same hash-chained log the Python adapter and
// the forge plugin write to.
//
// Three scenarios:
//   1. auditEventSync — single event lands on disk with prev_hash + hash
//   2. auditEventSync — two events keep the chain linked
//   3. auditEvent (fire-and-forget) — event reaches disk after a wait
//
// Plus: unknown event_type with VOICE_AUDIT_STRICT=1 logs to stderr.

'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');

const { auditEvent, auditEventSync, KNOWN_EVENT_TYPES } = require('./audit');

let pass = 0, fail = 0;
function t(label, ok, detail = '') {
  const mark = ok ? 'PASS' : 'FAIL';
  console.log(`  ${mark}  ${label}${detail ? ' — ' + detail : ''}`);
  if (ok) pass++; else fail++;
}

function makeTmpAuditPath() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'audit-js-'));
  return path.join(dir, 'audit.jsonl');
}

function readLines(file) {
  if (!fs.existsSync(file)) return [];
  return fs.readFileSync(file, 'utf-8')
    .split('\n')
    .filter((l) => l.trim().length > 0);
}

// --- 1. single sync emit ---
console.log('\n[auditEventSync — one event lands on disk]');
{
  const audit = makeTmpAuditPath();
  process.env.VOICE_AUDIT_PATH = audit;
  const rc = auditEventSync('bridge.whitelist_deny', {
    channel: 'discord',
    user: 'hostile-id',
    details: { reason: 'not in whitelist' },
  });
  t('sync rc=0', rc === 0, `rc=${rc}`);
  const lines = readLines(audit);
  t('one record written', lines.length === 1, `got ${lines.length}`);
  if (lines.length === 1) {
    const r = JSON.parse(lines[0]);
    t('event_type matches',
      r.event_type === 'bridge.whitelist_deny');
    t('severity = WARNING',  r.severity === 'WARNING');
    t('prev_hash empty (chain start)',
      r.prev_hash === '');
    t('hash set',
      typeof r.hash === 'string' && r.hash.length > 0);
    const d = r.details || {};
    t('details.channel propagated',
      d.channel === 'discord');
    t('details.user propagated',
      d.user === 'hostile-id');
    t('details.reason propagated',
      d.reason === 'not in whitelist');
  }
  delete process.env.VOICE_AUDIT_PATH;
}

// --- 2. two sync emits → chain stays linked ---
console.log('\n[auditEventSync × 2 → chain stays linked]');
{
  const audit = makeTmpAuditPath();
  process.env.VOICE_AUDIT_PATH = audit;
  auditEventSync('bridge.login',
    { channel: 'telegram', user: 'u1' });
  auditEventSync('bridge.message_received',
    { channel: 'telegram', user: 'u1', chatKey: 'c-42' });
  const lines = readLines(audit);
  t('two records', lines.length === 2);
  if (lines.length === 2) {
    const r1 = JSON.parse(lines[0]);
    const r2 = JSON.parse(lines[1]);
    t('r2.prev_hash === r1.hash',
      r2.prev_hash === r1.hash,
      `r1=${r1.hash} r2.prev=${r2.prev_hash}`);
  }
  delete process.env.VOICE_AUDIT_PATH;
}

// --- 3. fire-and-forget reaches disk ---
console.log('\n[auditEvent fire-and-forget reaches disk after a wait]');
{
  const audit = makeTmpAuditPath();
  process.env.VOICE_AUDIT_PATH = audit;
  auditEvent('bridge.cancel', {
    channel: 'whatsapp', user: 'u-cancel',
    details: { killed: 0 },
  });
  // Wait long enough for the python child to finish (~150 ms).
  const start = Date.now();
  while (readLines(audit).length === 0 && Date.now() - start < 5000) {
    require('child_process').execSync('sleep 0.05');
  }
  const lines = readLines(audit);
  t('fire-and-forget reached disk within 5s',
    lines.length === 1, `got ${lines.length} after ${Date.now() - start}ms`);
  if (lines.length === 1) {
    const r = JSON.parse(lines[0]);
    t('event_type = bridge.cancel',
      r.event_type === 'bridge.cancel');
  }
  delete process.env.VOICE_AUDIT_PATH;
}

// --- 4. KNOWN_EVENT_TYPES exposes the contract ---
console.log('\n[KNOWN_EVENT_TYPES has the bridge.* + daemon.* set]');
{
  t('has bridge.whitelist_deny',
    KNOWN_EVENT_TYPES.has('bridge.whitelist_deny'));
  t('has daemon.started',
    KNOWN_EVENT_TYPES.has('daemon.started'));
  t('does NOT have an obviously wrong type',
    !KNOWN_EVENT_TYPES.has('not.a.real.event'));
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail === 0 ? 0 : 1);
