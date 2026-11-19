#!/usr/bin/env node
// test_http_body.js — review 2026-11-19, R3-NEW-7.
//
// The Teams daemon's webhook used a hand-rolled middleware that set only
// `req.rawBody`. botbuilder's CloudAdapter.process() reads `req.body` and
// answers 400 without it, so NO activity was ever accepted — and the body was
// buffered without a limit before any JWT check.
//
// This drives the real Express app (http_app.js, what daemon.js mounts) with
// the real botbuilder CloudAdapter over real HTTP on 127.0.0.1. Bot Framework
// auth is constructed without an app id, which is botbuilder's local /
// emulator mode: no token validation, no network access.

'use strict';

const assert = require('assert');
const http = require('http');

const { CloudAdapter, ConfigurationBotFrameworkAuthentication } = require('botbuilder');
const { createApp, applyServerTimeouts, MAX_BODY_BYTES, REQUEST_TIMEOUT_MS } = require('./http_app');

let passed = 0, failed = 0;
async function test(name, fn) {
  try { await fn(); console.log(`  ok  ${name}`); passed++; }
  catch (e) { console.error(`  FAIL ${name}\n       ${e.message}`); failed++; }
}

function post(port, body, headers = {}) {
  return new Promise((resolve, reject) => {
    const req = http.request({ host: '127.0.0.1', port, path: '/api/messages', method: 'POST',
                               headers: { 'Content-Type': 'application/json', ...headers } }, (res) => {
      let data = '';
      res.on('data', (c) => { data += c; });
      res.on('end', () => resolve({ status: res.statusCode, body: data }));
    });
    req.on('error', reject);
    req.end(body);
  });
}

const activity = (text) => JSON.stringify({
  type: 'message', id: 'a1', text, channelId: 'emulator', serviceUrl: 'http://127.0.0.1:9/',
  from: { id: 'user-1', name: 'User' }, recipient: { id: 'bot-1' }, conversation: { id: 'conv-1' },
});

(async () => {
  console.log('== teams webhook body handling ==');
  const adapter = new CloudAdapter(new ConfigurationBotFrameworkAuthentication({}));
  const turns = [];
  const app = createApp({ adapter, onTurn: async (ctx) => { turns.push(ctx.activity); } });
  const server = applyServerTimeouts(app.listen(0, '127.0.0.1'));
  await new Promise((r) => server.once('listening', r));
  const port = server.address().port;

  try {
    await test('positive control: the OLD rawBody-only request shape is refused by CloudAdapter', async () => {
      // Exactly what the former middleware handed to adapter.process().
      const res = { status(c) { this.c = c; return this; }, send(b) { this.b = b; return this; },
                    end() { return this; }, header() {}, setHeader() {} };
      let called = false;
      await adapter.process({ method: 'POST', headers: {}, rawBody: activity('hi') }, res,
                            async () => { called = true; });
      assert.strictEqual(called, false, 'bot logic must not run without req.body');
      assert.strictEqual(res.c, 400);
    });

    await test('a real activity POSTed over HTTP reaches the bot logic', async () => {
      const r = await post(port, activity('hello teams'));
      assert.ok(r.status >= 200 && r.status < 300, `status ${r.status} body ${r.body}`);
      assert.strictEqual(turns.length, 1, 'onTurn must be called exactly once');
      assert.strictEqual(turns[0].text, 'hello teams');
      assert.strictEqual(turns[0].conversation.id, 'conv-1');
    });

    await test('JSON is parsed whatever the Content-Type spelling', async () => {
      const before = turns.length;
      const r = await post(port, activity('second'), { 'Content-Type': 'application/json; charset=utf-8' });
      assert.ok(r.status >= 200 && r.status < 300, `status ${r.status}`);
      assert.strictEqual(turns.length, before + 1);
    });

    await test('an oversized body is refused with 413 and never reaches the adapter', async () => {
      const before = turns.length;
      const big = JSON.stringify({ type: 'message', text: 'x'.repeat(MAX_BODY_BYTES + 1024) });
      const r = await post(port, big);
      assert.strictEqual(r.status, 413);
      assert.strictEqual(turns.length, before);
    });

    await test('a malformed body is refused with 400 and echoes nothing', async () => {
      const before = turns.length;
      const r = await post(port, '{"type": "message", "text": "SECRET-MARKER"');
      assert.strictEqual(r.status, 400);
      assert.ok(!r.body.includes('SECRET-MARKER'), 'error response must not echo the request');
      assert.strictEqual(turns.length, before);
    });

    await test('the server bounds slow requests', async () => {
      assert.strictEqual(server.requestTimeout, REQUEST_TIMEOUT_MS);
      assert.ok(server.headersTimeout > 0 && server.headersTimeout <= REQUEST_TIMEOUT_MS);
    });

    await test('daemon.js mounts http_app.js and no longer hand-rolls the body', async () => {
      const src = require('fs').readFileSync(require('path').join(__dirname, 'daemon.js'), 'utf8');
      assert.ok(/createApp\(\{\s*adapter,/.test(src), 'daemon.js must build the app through createApp');
      assert.ok(/applyServerTimeouts\(app\.listen\(/.test(src));
      assert.ok(!/req\.rawBody = data/.test(src), 'the raw-body middleware must be gone');
      assert.ok(!/data \+= chunk/.test(src));
    });
  } finally {
    await new Promise((r) => server.close(r));
  }
  console.log(`\n${passed} passed, ${failed} failed`);
  process.exit(failed === 0 ? 0 : 1);
})().catch((e) => { console.error(e); process.exit(1); });
