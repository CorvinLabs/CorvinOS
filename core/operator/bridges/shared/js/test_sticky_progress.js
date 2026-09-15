#!/usr/bin/env node
// test_sticky_progress.js — unit tests for the shared sticky-progress /
// finalize-guard primitive (shared/js/sticky_progress.js).
//
// This is the one piece of the "sticky edit-in-place + drop stale
// progress/heartbeat after finalize" mechanism that is platform-agnostic
// and therefore fully unit-testable without any live credentials — every
// per-channel daemon (Discord, Telegram, Slack, WhatsApp, Signal, Teams)
// wires its own send/edit/delete I/O around this same guard.

'use strict';

const assert = require('assert');
const { makeStickyProgress } = require('./sticky_progress');

let passed = 0;
let failed = 0;

function test(name, fn) {
  try {
    fn();
    console.log(`  ok  ${name}`);
    passed += 1;
  } catch (e) {
    console.error(`  FAIL ${name}`);
    console.error(`       ${e.message}`);
    failed += 1;
  }
}

console.log('== sticky_progress ==');

test('fresh instance: no chat has progress', () => {
  const s = makeStickyProgress();
  assert.strictEqual(s.hasProgress('chat-1'), false);
  assert.strictEqual(s.getProgress('chat-1'), undefined);
  s.stop();
});

test('setProgress/getProgress/hasProgress round-trip', () => {
  const s = makeStickyProgress();
  s.setProgress('chat-1', { messageId: 'm1' });
  assert.strictEqual(s.hasProgress('chat-1'), true);
  assert.deepStrictEqual(s.getProgress('chat-1'), { messageId: 'm1' });
  s.stop();
});

test('clearProgress removes only the given chat', () => {
  const s = makeStickyProgress();
  s.setProgress('chat-1', { messageId: 'm1' });
  s.setProgress('chat-2', { messageId: 'm2' });
  s.clearProgress('chat-1');
  assert.strictEqual(s.hasProgress('chat-1'), false);
  assert.strictEqual(s.hasProgress('chat-2'), true);
  s.stop();
});

test('setProgress overwrites an existing ref for the same chat', () => {
  const s = makeStickyProgress();
  s.setProgress('chat-1', { messageId: 'm1' });
  s.setProgress('chat-1', { messageId: 'm2' });
  assert.deepStrictEqual(s.getProgress('chat-1'), { messageId: 'm2' });
  s.stop();
});

test('isFinalized: false for unknown msg_id', () => {
  const s = makeStickyProgress();
  assert.strictEqual(s.isFinalized('msg-1'), false);
  s.stop();
});

test('isFinalized: false for falsy msg_id (undefined/null/"")', () => {
  const s = makeStickyProgress();
  assert.strictEqual(s.isFinalized(undefined), false);
  assert.strictEqual(s.isFinalized(null), false);
  assert.strictEqual(s.isFinalized(''), false);
  s.stop();
});

test('markFinalized then isFinalized: true within TTL', () => {
  const s = makeStickyProgress({ ttlMs: 60_000 });
  s.markFinalized('msg-1');
  assert.strictEqual(s.isFinalized('msg-1'), true);
  s.stop();
});

test('markFinalized: does not affect a different msg_id', () => {
  const s = makeStickyProgress({ ttlMs: 60_000 });
  s.markFinalized('msg-1');
  assert.strictEqual(s.isFinalized('msg-2'), false);
  s.stop();
});

test('isFinalized: expires after the TTL window', async () => {
  const s = makeStickyProgress({ ttlMs: 20 });
  s.markFinalized('msg-1');
  assert.strictEqual(s.isFinalized('msg-1'), true);
  await new Promise((r) => setTimeout(r, 40));
  assert.strictEqual(s.isFinalized('msg-1'), false);
  s.stop();
});

test('markFinalized: falsy msg_id is a no-op (never finalized)', () => {
  const s = makeStickyProgress();
  s.markFinalized(undefined);
  s.markFinalized(null);
  s.markFinalized('');
  // Nothing to assert against directly — just must not throw, and no
  // "" entry should report finalized.
  assert.strictEqual(s.isFinalized(''), false);
  s.stop();
});

test('progress and finalize tracking are independent namespaces', () => {
  const s = makeStickyProgress();
  // chatKey and msgId happen to collide in value — must not cross-talk.
  s.setProgress('shared-key', { messageId: 'x' });
  s.markFinalized('shared-key');
  assert.strictEqual(s.hasProgress('shared-key'), true);
  assert.strictEqual(s.isFinalized('shared-key'), true);
  s.clearProgress('shared-key');
  assert.strictEqual(s.hasProgress('shared-key'), false);
  assert.strictEqual(s.isFinalized('shared-key'), true, 'clearing progress must not clear finalized state');
  s.stop();
});

test('stop() clears the sweep interval without throwing', () => {
  const s = makeStickyProgress({ sweepMs: 5 });
  s.stop();
  // calling stop twice must be safe too
  s.stop();
});

test('typical turn lifecycle: progress edits collapse to one ref, then finalize drops stale late arrivals', () => {
  const s = makeStickyProgress();
  const chat = 'chat-42';
  const msgId = 'turn-7';

  // First _progress payload: no existing ref → caller sends a new message,
  // then records it.
  assert.strictEqual(s.hasProgress(chat), false);
  s.setProgress(chat, { messageId: 'sticky-1' });

  // Second _progress payload: existing ref → caller edits in place, ref
  // itself does not need to change (same message id).
  assert.strictEqual(s.hasProgress(chat), true);
  assert.strictEqual(s.getProgress(chat).messageId, 'sticky-1');

  // A _heartbeat that arrives while a progress sticky already exists must
  // NOT open a second slot — hasProgress staying true is what the caller
  // checks before deciding to send.
  assert.strictEqual(s.hasProgress(chat), true);

  // Real reply arrives: caller deletes the sticky, clears progress, and
  // marks the turn finalized.
  s.clearProgress(chat);
  s.markFinalized(msgId);
  assert.strictEqual(s.hasProgress(chat), false);

  // A late-arriving _progress/_heartbeat for the same msg_id (outbox
  // alphabetical-sort race) must be recognized as stale.
  assert.strictEqual(s.isFinalized(msgId), true);
});

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed > 0 ? 1 : 0);
