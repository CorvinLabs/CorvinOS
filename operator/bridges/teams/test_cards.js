#!/usr/bin/env node
// test_cards.js — unit tests for cards.js (no external dependencies).
//
// Tests every card builder and the auto-dispatch logic. All assertions are
// structural (valid Adaptive Card JSON shape + correct card type selection).

'use strict';

const assert = require('assert');
const cards  = require('./cards');

let passed = 0;
let failed = 0;

function test(name, fn) {
  try {
    fn();
    console.log(`  ✓ ${name}`);
    passed++;
  } catch (e) {
    console.error(`  ✗ ${name}`);
    console.error(`    ${e.message}`);
    failed++;
  }
}

function assertCard(card) {
  assert.strictEqual(card.type, 'message', 'card.type must be "message"');
  assert.ok(Array.isArray(card.attachments) && card.attachments.length > 0, 'must have attachments');
  const a = card.attachments[0];
  assert.strictEqual(a.contentType, 'application/vnd.microsoft.card.adaptive');
  assert.strictEqual(a.content.type, 'AdaptiveCard');
  assert.strictEqual(a.content.version, '1.5');
  assert.ok(Array.isArray(a.content.body), 'body must be array');
  return a.content;
}

// ── plainCard ──────────────────────────────────────────────────────────────

console.log('\nplainCard');

test('returns a message activity', () => {
  const c = cards.plainCard('Hello Teams!');
  const content = assertCard(c);
  assert.strictEqual(content.body.length, 1);
  assert.strictEqual(content.body[0].type, 'TextBlock');
  assert.strictEqual(content.body[0].text, 'Hello Teams!');
  assert.strictEqual(content.body[0].wrap, true);
});

test('wraps long text', () => {
  const long = 'A'.repeat(2000);
  const c = cards.plainCard(long);
  assertCard(c);
});

test('coerces non-string to string', () => {
  const c = cards.plainCard(42);
  const content = assertCard(c);
  assert.strictEqual(content.body[0].text, '42');
});

// ── codeCard ───────────────────────────────────────────────────────────────

console.log('\ncodeCard');

test('renders fenced code block as Monospace', () => {
  const text = 'Here is code:\n```python\nprint("hello")\n```\nDone.';
  const c = cards.codeCard(text);
  const content = assertCard(c);
  const monoBlock = content.body.find((b) => b.fontType === 'Monospace');
  assert.ok(monoBlock, 'must have a Monospace TextBlock');
  assert.ok(monoBlock.text.includes('print("hello")'));
});

test('shows lang label when present', () => {
  const text = '```javascript\nconst x = 1;\n```';
  const c = cards.codeCard(text);
  const content = assertCard(c);
  const label = content.body.find((b) => b.weight === 'Bolder');
  assert.ok(label, 'must have a bold lang label');
  assert.ok(label.text.toUpperCase().includes('JAVASCRIPT'));
});

test('handles unnamed code block (no lang)', () => {
  const text = '```\nsome code\n```';
  const c = cards.codeCard(text);
  assertCard(c);
});

test('includes postscript text after code block', () => {
  const text = '```\ncode\n```\nAnd a note below.';
  const c = cards.codeCard(text);
  const content = assertCard(c);
  const lastBlock = content.body[content.body.length - 1];
  assert.ok(lastBlock.text.includes('note below'));
});

// ── sectionCard ────────────────────────────────────────────────────────────

console.log('\nsectionCard');

test('converts ## headings to bold TextBlocks', () => {
  const text = '## Overview\nSome intro text.\n## Details\nMore info.';
  const c = cards.sectionCard(text);
  const content = assertCard(c);
  const boldBlocks = content.body.filter((b) => b.weight === 'Bolder');
  assert.ok(boldBlocks.length >= 2, 'must have at least two bold heading blocks');
  assert.ok(boldBlocks.some((b) => b.text === 'Overview'));
  assert.ok(boldBlocks.some((b) => b.text === 'Details'));
});

test('includes section body text', () => {
  const text = '## Setup\nRun npm install.\nThen start.';
  const c = cards.sectionCard(text);
  const content = assertCard(c);
  const bodyBlock = content.body.find((b) => b.text && b.text.includes('npm install'));
  assert.ok(bodyBlock, 'must include section body text');
});

test('handles single section', () => {
  const text = '## Only Section\nContent here.';
  const c = cards.sectionCard(text);
  assertCard(c);
});

// ── statusCard ─────────────────────────────────────────────────────────────

console.log('\nstatusCard');

test('renders title + FactSet', () => {
  const c = cards.statusCard('Settings', [
    { key: 'Persona', val: 'coder' },
    { key: 'Quota',   val: '47/100' },
  ]);
  const content = assertCard(c);
  const factSet = content.body.find((b) => b.type === 'FactSet');
  assert.ok(factSet, 'must have FactSet');
  assert.strictEqual(factSet.facts.length, 2);
  assert.strictEqual(factSet.facts[0].title, 'Persona');
  assert.strictEqual(factSet.facts[0].value, 'coder');
});

test('renders Action.Submit buttons', () => {
  const c = cards.statusCard('Quota', [{ key: 'Used', val: '50' }], [
    { title: 'Reset Quota', value: '/quota reset' },
  ]);
  const content = assertCard(c);
  assert.ok(Array.isArray(content.actions) && content.actions.length === 1);
  assert.strictEqual(content.actions[0].type, 'Action.Submit');
  assert.strictEqual(content.actions[0].data.text, '/quota reset');
});

test('no actions when array is empty', () => {
  const c = cards.statusCard('Title', [{ key: 'k', val: 'v' }]);
  const content = assertCard(c);
  assert.ok(!content.actions || content.actions.length === 0, 'no actions expected');
});

// ── fromText (auto-dispatch) ────────────────────────────────────────────────

console.log('\nfromText (auto-dispatch)');

test('dispatches to codeCard when ``` present', () => {
  const c = cards.fromText('Look:\n```js\nlet x = 1;\n```');
  const content = assertCard(c);
  assert.ok(content.body.some((b) => b.fontType === 'Monospace'), 'must use codeCard');
});

test('dispatches to sectionCard when ## present', () => {
  const c = cards.fromText('## Heading\nsome text');
  const content = assertCard(c);
  assert.ok(content.body.some((b) => b.weight === 'Bolder'), 'must use sectionCard');
});

test('dispatches to plainCard for unstructured text', () => {
  const c = cards.fromText('Just a plain reply.');
  const content = assertCard(c);
  assert.strictEqual(content.body.length, 1);
  assert.strictEqual(content.body[0].type, 'TextBlock');
});

test('handles empty string gracefully', () => {
  const c = cards.fromText('');
  assertCard(c);
});

test('handles undefined gracefully', () => {
  const c = cards.fromText(undefined);
  assertCard(c);
});

test('code block takes priority over section headings', () => {
  const c = cards.fromText('## Section\n```py\ncode\n```');
  const content = assertCard(c);
  // codeCard dispatched first since ``` check comes before ## check
  assert.ok(content.body.some((b) => b.fontType === 'Monospace'), 'must be codeCard');
});

// ── Summary ────────────────────────────────────────────────────────────────

console.log(`\n${passed + failed} tests: ${passed} passed, ${failed} failed\n`);
if (failed > 0) process.exit(1);
