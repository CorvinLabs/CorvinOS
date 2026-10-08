/**
 * 98 — the isolation proof (99) is only worth something if its detector can fail.
 * This spec gives it a synthetic "live chain" and shows that it flags an
 * appended suite record, a rotation, a truncation and an in-place rewrite — and
 * stays quiet on a chain that only grew with unrelated records (the positive
 * control: a detector that always says "leak" proves nothing either).
 */
import fs from 'fs';
import os from 'os';
import path from 'path';
import { compare, leaks, snapshot } from './live-chain';
import { expect, test } from './forge-fixtures';

function chain(): string {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'forge-e2e-detector-'));
  const f = path.join(dir, 'audit.jsonl');
  fs.writeFileSync(f, Array.from({ length: 400 }, (_, i) => `{"event_type":"license.loaded","n":${i}}`).join('\n') + '\n');
  return f;
}

test('quiet on a chain that only grew with unrelated records (positive control)', () => {
  const f = chain();
  const base = snapshot(f);
  fs.appendFileSync(f, '{"event_type":"os_turn.tool_called","tool_name":"Bash"}\n');
  const r = compare(base);
  expect(r.problem).toBeNull();
  expect(r.appended).toContain('os_turn.tool_called');
  expect(leaks(r.appended)).toEqual([]);
});

test('flags an appended suite record', () => {
  const f = chain();
  const base = snapshot(f);
  fs.appendFileSync(f, '{"event_type":"forge_bundle.exported","details":{"bundle_id":"nordwind-q4-ops"}}\n');
  const r = compare(base);
  expect(r.problem).toBeNull();
  expect(leaks(r.appended)).toEqual(expect.arrayContaining(['nordwind', 'forge_bundle.']));
});

test('refuses to judge a rotated chain (new inode)', () => {
  const f = chain();
  const base = snapshot(f);
  fs.renameSync(f, `${f}.1`);
  fs.writeFileSync(f, '{"event_type":"audit.rotation_link"}\n');
  expect(compare(base).problem).toMatch(/rotated/);
});

test('refuses to judge a truncated chain', () => {
  const f = chain();
  const base = snapshot(f);
  fs.truncateSync(f, 100);
  expect(compare(base).problem).toMatch(/truncated/);
});

test('refuses to judge a chain rewritten in place', () => {
  const f = chain();
  const base = snapshot(f);
  const fd = fs.openSync(f, 'r+');
  fs.writeSync(fd, 'X', base.windowOffset + 5);
  fs.closeSync(fd);
  expect(compare(base).problem).toMatch(/changed in place/);
});

test('a missing chain is "nothing to protect", but a vanished one is a failure', () => {
  expect(compare(snapshot(path.join(os.tmpdir(), 'forge-e2e-no-such-chain.jsonl'))).problem).toBeNull();
  const f = chain();
  const base = snapshot(f);
  fs.unlinkSync(f);
  expect(compare(base).problem).toMatch(/vanished/);
});
