#!/usr/bin/env node
// test_intent_preflight.js — unit tests for the MessageContent preflight.
//
// Run: node operator/bridges/discord/test_intent_preflight.js
//
// Coverage:
//   1. flags with GATEWAY_MESSAGE_CONTENT (verified app)   → true
//   2. flags with ..._LIMITED (unverified app, toggle on)  → true
//   3. flags without either bit (fresh app, toggle off)    → false
//   4. non-2xx response (401 bad token, 500, CF page)      → null
//   5. fetch rejects (network down)                        → null
//   6. malformed body / missing flags                      → null
//   7. missing token / missing fetch                       → null
//   8. Authorization header carries the Bot prefix

const assert = require('assert');
const {
  messageContentAvailable,
  FLAG_GATEWAY_MESSAGE_CONTENT,
  FLAG_GATEWAY_MESSAGE_CONTENT_LIMITED,
  APP_ME_URL,
} = require('./intent_preflight');

let passed = 0;
let failed = 0;

async function ok(label, fn) {
  try {
    await fn();
    console.log(`  ok  ${label}`);
    passed += 1;
  } catch (e) {
    console.error(`  FAIL ${label}`);
    console.error(`       ${e.message}`);
    failed += 1;
  }
}

const fakeFetch = (status, body) => async () => ({
  ok: status >= 200 && status < 300,
  status,
  json: async () => body,
});

(async () => {
  console.log('== intent_preflight ==');

  await ok('verified app with toggle on → true', async () => {
    const r = await messageContentAvailable('t', fakeFetch(200, { flags: FLAG_GATEWAY_MESSAGE_CONTENT }));
    assert.strictEqual(r, true);
  });

  await ok('unverified app with toggle on (LIMITED) → true', async () => {
    const r = await messageContentAvailable('t', fakeFetch(200, { flags: FLAG_GATEWAY_MESSAGE_CONTENT_LIMITED }));
    assert.strictEqual(r, true);
  });

  await ok('fresh app, toggle off → false', async () => {
    const r = await messageContentAvailable('t', fakeFetch(200, { flags: 0 }));
    assert.strictEqual(r, false);
  });

  await ok('other flags set but not message-content → false', async () => {
    const r = await messageContentAvailable('t', fakeFetch(200, { flags: (1 << 12) | (1 << 23) }));
    assert.strictEqual(r, false);
  });

  await ok('401 (bad token) → null, login path owns the failure', async () => {
    const r = await messageContentAvailable('t', fakeFetch(401, { message: '401: Unauthorized' }));
    assert.strictEqual(r, null);
  });

  await ok('500 → null', async () => {
    const r = await messageContentAvailable('t', fakeFetch(500, {}));
    assert.strictEqual(r, null);
  });

  await ok('fetch rejects (network) → null', async () => {
    const r = await messageContentAvailable('t', async () => { throw new Error('ENOTFOUND'); });
    assert.strictEqual(r, null);
  });

  await ok('malformed body (no flags) → null', async () => {
    const r = await messageContentAvailable('t', fakeFetch(200, { id: '123' }));
    assert.strictEqual(r, null);
  });

  await ok('json() throws → null', async () => {
    const r = await messageContentAvailable('t', async () => ({ ok: true, json: async () => { throw new Error('bad json'); } }));
    assert.strictEqual(r, null);
  });

  await ok('missing token → null (no request fired)', async () => {
    let called = false;
    const r = await messageContentAvailable('', async () => { called = true; });
    assert.strictEqual(r, null);
    assert.strictEqual(called, false);
  });

  await ok('Authorization header carries Bot prefix + right URL', async () => {
    let seenUrl = null;
    let seenAuth = null;
    await messageContentAvailable('abc123', async (url, opts) => {
      seenUrl = url;
      seenAuth = opts.headers.Authorization;
      return { ok: true, json: async () => ({ flags: 0 }) };
    });
    assert.strictEqual(seenUrl, APP_ME_URL);
    assert.strictEqual(seenAuth, 'Bot abc123');
  });

  console.log(`\n${passed} passed, ${failed} failed`);
  process.exit(failed === 0 ? 0 : 1);
})();
