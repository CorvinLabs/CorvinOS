#!/usr/bin/env node
// test_media_fetch.js — review 2026-11-19, R3-NEW-31 / R3-NEW-33.
//
// Inbound attachments were buffered whole with no status check and no size
// limit, Slack sent its bot token to whatever URL the event carried, a
// nameless Discord attachment threw, and the Telegram voice path hung when
// the source stream failed. No network: fetch is replaced by a recorder.

'use strict';

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { PassThrough } = require('stream');
const { fetchToFile, streamToFile, hostAllowed, safeExt, SLACK_FILE_HOSTS } = require('./media_fetch');

let passed = 0, failed = 0;
async function test(name, fn) {
  try { await fn(); console.log(`  ok  ${name}`); passed++; }
  catch (e) { console.error(`  FAIL ${name}\n       ${e.message}`); failed++; }
}
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'media-fetch-'));
const dest = (n) => path.join(tmp, n);
const leftovers = () => fs.readdirSync(tmp).filter((f) => f.endsWith('.part'));

function recorder(handler) {
  const calls = [];
  const impl = async (url, init) => { calls.push({ url, headers: (init && init.headers) || {} }); return handler(url, init); };
  impl.calls = calls;
  return impl;
}

(async () => {
  console.log('== bounded attachment download ==');

  await test('happy path writes the body and returns its size', async () => {
    const f = recorder(async () => new Response('hello'));
    const n = await fetchToFile('https://files.slack.com/a/b', dest('ok.bin'), { fetchImpl: f, allowedHosts: SLACK_FILE_HOSTS });
    assert.strictEqual(n, 5);
    assert.strictEqual(fs.readFileSync(dest('ok.bin'), 'utf8'), 'hello');
  });

  await test('Slack: the bot token is never sent to a foreign host', async () => {
    const f = recorder(async () => new Response('x'));
    for (const url of ['https://evil.example/steal', 'http://files.slack.com/plain-http',
                       'https://files.slack.com.evil.example/x', 'https://user:pw@files.slack.com/x',
                       'file:///etc/passwd', 'not a url']) {
      await assert.rejects(
        fetchToFile(url, dest('no.bin'), { fetchImpl: f, allowedHosts: SLACK_FILE_HOSTS, headers: { Authorization: 'Bearer xoxb-SECRET' } }),
        /host not allowed/, url);
    }
    assert.strictEqual(f.calls.length, 0, 'no request may leave for a refused URL');
    assert.ok(!fs.existsSync(dest('no.bin')));
  });

  await test('Slack: a redirect to a foreign host is not followed', async () => {
    const f = recorder(async (url) => (url.includes('files.slack.com')
      ? new Response(null, { status: 302, headers: { location: 'https://evil.example/collect' } })
      : new Response('leak')));
    await assert.rejects(
      fetchToFile('https://files.slack.com/x', dest('redir.bin'), { fetchImpl: f, allowedHosts: SLACK_FILE_HOSTS, headers: { Authorization: 'Bearer xoxb-SECRET' } }),
      /host not allowed/);
    assert.strictEqual(f.calls.length, 1);
    assert.ok(f.calls.every((c) => c.url.startsWith('https://files.slack.com/')));
  });

  await test('a redirect inside the allowlist is followed', async () => {
    const f = recorder(async (url) => (url.endsWith('/x')
      ? new Response(null, { status: 302, headers: { location: 'https://files-edge.slack.com/y' } })
      : new Response('moved')));
    await fetchToFile('https://files.slack.com/x', dest('redir-ok.bin'), { fetchImpl: f, allowedHosts: SLACK_FILE_HOSTS });
    assert.strictEqual(fs.readFileSync(dest('redir-ok.bin'), 'utf8'), 'moved');
  });

  await test('an error page is not stored as the attachment', async () => {
    const f = recorder(async () => new Response('<html>404</html>', { status: 404 }));
    await assert.rejects(fetchToFile('https://cdn.discordapp.com/a', dest('err.png'), { fetchImpl: f }), /HTTP 404/);
    assert.ok(!fs.existsSync(dest('err.png')));
  });

  await test('declared size over the limit: refused before any request', async () => {
    const f = recorder(async () => new Response('x'));
    await assert.rejects(fetchToFile('https://cdn.discordapp.com/a', dest('big1.bin'), { fetchImpl: f, maxBytes: 10, declaredSize: 11 }), /too large/);
    assert.strictEqual(f.calls.length, 0);
  });

  await test('Content-Length over the limit: refused, nothing written', async () => {
    const f = recorder(async () => new Response('x', { headers: { 'content-length': '999999' } }));
    await assert.rejects(fetchToFile('https://cdn.discordapp.com/a', dest('big2.bin'), { fetchImpl: f, maxBytes: 10 }), /too large/);
    assert.ok(!fs.existsSync(dest('big2.bin')));
  });

  await test('a body that lies about its size is cut off at the cap', async () => {
    let pulled = 0;
    const body = new ReadableStream({
      pull(ctrl) { pulled++; if (pulled > 10000) ctrl.close(); else ctrl.enqueue(new Uint8Array(1024)); },
    });
    const f = recorder(async () => new Response(body));
    await assert.rejects(fetchToFile('https://cdn.discordapp.com/a', dest('big3.bin'), { fetchImpl: f, maxBytes: 4096 }), /too large/);
    assert.ok(pulled < 50, `stream must be cancelled early (pulled ${pulled} chunks)`);
    assert.ok(!fs.existsSync(dest('big3.bin')));
  });

  await test('no partial file is left behind by any failure', async () => {
    assert.deepStrictEqual(leftovers(), []);
  });

  console.log('== stream to file (Telegram voice) ==');

  await test('a healthy stream is written', async () => {
    const s = new PassThrough();
    const p = streamToFile(s, dest('voice.ogg'));
    s.end(Buffer.from('OggS-data'));
    assert.strictEqual(await p, 9);
    assert.strictEqual(fs.readFileSync(dest('voice.ogg'), 'utf8'), 'OggS-data');
  });

  await test('a source stream error rejects instead of hanging', async () => {
    const s = new PassThrough();
    const p = streamToFile(s, dest('voice-err.ogg'), { timeoutMs: 5000 });
    s.write(Buffer.from('partial'));
    setImmediate(() => s.destroy(new Error('ETELEGRAM: 400 file is too big')));
    await assert.rejects(p, /file is too big/);
    assert.ok(!fs.existsSync(dest('voice-err.ogg')));
  });

  await test('a stream over the cap rejects', async () => {
    const s = new PassThrough();
    const p = streamToFile(s, dest('voice-big.ogg'), { maxBytes: 100 });
    s.write(Buffer.alloc(500));
    await assert.rejects(p, /too large/);
    assert.ok(!fs.existsSync(dest('voice-big.ogg')));
  });

  await test('a stream that never ends hits the timeout', async () => {
    const s = new PassThrough();
    await assert.rejects(streamToFile(s, dest('voice-stall.ogg'), { timeoutMs: 80 }), /timed out/);
    assert.deepStrictEqual(leftovers(), []);
  });

  console.log('== helpers ==');

  await test('safeExt is null-safe and never returns a non-extension', async () => {
    assert.strictEqual(safeExt(null), '');
    assert.strictEqual(safeExt(undefined), '');
    assert.strictEqual(safeExt('noext'), '');
    assert.strictEqual(safeExt('photo.PNG'), '.png');
    assert.strictEqual(safeExt('a.tar.gz'), '.gz');
    assert.strictEqual(safeExt('.hidden'), '');
    assert.strictEqual(safeExt('trailing.'), '');
  });

  await test('hostAllowed: https only, exact host match', async () => {
    assert.ok(hostAllowed('https://files.slack.com/x', SLACK_FILE_HOSTS));
    assert.ok(hostAllowed('https://FILES.SLACK.COM/x', SLACK_FILE_HOSTS));
    assert.ok(!hostAllowed('https://slack.com/x', SLACK_FILE_HOSTS));
    assert.ok(hostAllowed('https://anything.example/x'));
    assert.ok(!hostAllowed('http://anything.example/x'));
  });

  console.log('== daemon wiring ==');
  const B = path.resolve(__dirname, '..', '..');
  const src = (rel) => fs.readFileSync(path.join(B, rel), 'utf8');

  await test('slack: download goes through fetchToFile with the Slack host allowlist and the declared size', async () => {
    const s = src('slack/daemon.js');
    assert.ok(/allowedHosts: SLACK_FILE_HOSTS/.test(s));
    assert.ok(/downloadSlackFile\(f\.url_private_download \|\| f\.url_private, dest, Number\(f\.size\)\)/.test(s));
    assert.ok(!/await fetch\(url, \{ headers: \{ Authorization/.test(s), 'the unrestricted Bearer fetch must be gone');
  });

  await test('discord: attachment intake is bounded and null-safe', async () => {
    const s = src('discord/daemon.js');
    assert.ok(/fetchToFile\(att\.url, p, \{ maxBytes: MAX_ATTACHMENT_BYTES, declaredSize: Number\(att\.size\) \}\)/.test(s));
    assert.ok(!/att\.name\.lastIndexOf/.test(s), 'att.name may be null');
    assert.ok(!/await fetch\(att\.url\)/.test(s));
  });

  await test('telegram: no unbounded buffering, no finish-only await', async () => {
    const s = src('telegram/daemon.js');
    assert.ok(!/await \(await fetch\(fileLink\)\)\.arrayBuffer\(\)/.test(s));
    assert.ok(!/out\.on\('finish', res\)/.test(s));
    assert.ok(/streamToFile\(bot\.getFileStream\(media\.file_id\)/.test(s));
    assert.ok((s.match(/fetchToFile\(await bot\.getFileLink\(media\.file_id\)/g) || []).length === 4);
  });

  fs.rmSync(tmp, { recursive: true, force: true });
  console.log(`\n${passed} passed, ${failed} failed`);
  process.exit(failed === 0 ? 0 : 1);
})().catch((e) => { console.error(e); process.exit(1); });
