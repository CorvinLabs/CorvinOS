#!/usr/bin/env node
// test_pin_lockout_mock.js — review 2026-11-19, R3-NEW-12 (WhatsApp half).
//
// The WhatsApp daemon has its own authOk(); its `/auth <pin>` path had no
// throttle either. This boots the REAL daemon in --mock mode (no Baileys
// socket, no network) from a private copy in a temp dir — so its inbox /
// outbox / auth directories never land in the checkout — and drives the PIN
// path through the daemon's own HTTP endpoint on 127.0.0.1.

'use strict';

const assert = require('assert');
const fs = require('fs');
const http = require('http');
const net = require('net');
const os = require('os');
const path = require('path');
const { spawn } = require('child_process');

let passed = 0, failed = 0;
async function test(name, fn) {
  try { await fn(); console.log(`  ok  ${name}`); passed++; }
  catch (e) { console.error(`  FAIL ${name}\n       ${e.message}`); failed++; }
}

const BRIDGES = path.resolve(__dirname, '..');
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'wa-pin-'));

function copyJs(srcDir, dstDir) {
  fs.mkdirSync(dstDir, { recursive: true });
  for (const f of fs.readdirSync(srcDir)) {
    if (!/\.(js|json)$/.test(f) || f.startsWith('test_') || f === 'settings.json') continue;
    if (!fs.statSync(path.join(srcDir, f)).isFile()) continue;
    fs.copyFileSync(path.join(srcDir, f), path.join(dstDir, f));
  }
}
copyJs(path.join(BRIDGES, 'whatsapp'), path.join(tmp, 'bridges', 'whatsapp'));
copyJs(path.join(BRIDGES, 'shared', 'js'), path.join(tmp, 'bridges', 'shared', 'js'));
// status_markers.js reads its vocabulary from the generated export one level up.
fs.copyFileSync(path.join(BRIDGES, 'shared', 'status_markers.json'),
                path.join(tmp, 'bridges', 'shared', 'status_markers.json'));

const HOME = path.join(tmp, 'corvin_home');
const SETTINGS = path.join(HOME, 'bridges', 'whatsapp', 'settings.json');
fs.mkdirSync(path.dirname(SETTINGS), { recursive: true });
const OWNER = '491700000001@s.whatsapp.net';
const PIN = '428513';
fs.writeFileSync(SETTINGS, JSON.stringify({ whitelist: [OWNER], pin: PIN, rate_limit_per_hour: 1000 }));

function freePort() {
  return new Promise((resolve, reject) => {
    const s = net.createServer();
    s.once('error', reject);
    s.listen(0, '127.0.0.1', () => { const p = s.address().port; s.close(() => resolve(p)); });
  });
}

function post(port, body) {
  return new Promise((resolve, reject) => {
    const req = http.request({ host: '127.0.0.1', port, path: '/mock/inbound', method: 'POST',
                               headers: { 'Content-Type': 'application/json' } }, (res) => {
      let data = '';
      res.on('data', (c) => { data += c; });
      res.on('end', () => resolve({ status: res.statusCode, body: data }));
    });
    req.on('error', reject);
    req.end(JSON.stringify(body));
  });
}

(async () => {
  console.log('== whatsapp /auth <pin> lockout (real daemon, --mock) ==');
  const port = await freePort();
  let out = '';
  const child = spawn(process.execPath, [path.join(tmp, 'bridges', 'whatsapp', 'daemon.js'), '--mock'], {
    env: {
      PATH: process.env.PATH, NODE_PATH: process.env.NODE_PATH || '',
      HOME: path.join(tmp, 'home'), CORVIN_HOME: HOME, XDG_CONFIG_HOME: path.join(tmp, 'xdg'),
      CORVIN_LOG_FILE: path.join(tmp, 'corvin.log'), WA_HTTP_PORT: String(port),
    },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  child.stdout.on('data', (b) => { out += b; });
  child.stderr.on('data', (b) => { out += b; });
  let exited = null;
  child.on('exit', (code) => { exited = code; });

  try {
    const deadline = Date.now() + 20000;
    while (!/HTTP API listening/.test(out) && exited === null && Date.now() < deadline) {
      await new Promise((r) => setTimeout(r, 50));
    }
    await test('daemon booted in mock mode', async () => {
      assert.ok(/HTTP API listening .* mock=true/.test(out), `no listen line (exit=${exited}):\n${out.slice(-600)}`);
    });

    const attacker = '491799999999@s.whatsapp.net';
    await test('positive control: a stranger without a PIN is refused', async () => {
      const r = await post(port, { from: attacker, text: 'hello' });
      assert.strictEqual(r.status, 403, r.body);
    });

    await test('20 guesses including the correct PIN: none is accepted', async () => {
      const statuses = [];
      for (let i = 0; i < 20; i++) {
        const guess = i === 7 ? PIN : String(100000 + i);   // the 8th guess is correct
        statuses.push((await post(port, { from: attacker, text: `/auth ${guess}` })).status);
      }
      assert.ok(statuses.every((s) => s === 403), statuses.join(','));
      const wl = JSON.parse(fs.readFileSync(SETTINGS, 'utf8')).whitelist;
      assert.deepStrictEqual(wl, [OWNER], 'attacker must not be on the whitelist');
    });

    await test('the lockout is logged once, with a fingerprint instead of the JID', async () => {
      const n = (out.match(/auth: PIN lockout for /g) || []).length;
      assert.strictEqual(n, 1, `lockout lines: ${n}`);
      assert.ok(/auth: PIN lockout for [0-9a-f]{12}\b/.test(out));
    });

    await test('another sender with the correct PIN is still accepted', async () => {
      const friend = '491788888888@s.whatsapp.net';
      const r = await post(port, { from: friend, text: `/auth ${PIN}` });
      assert.notStrictEqual(r.status, 403, r.body);
      const wl = JSON.parse(fs.readFileSync(SETTINGS, 'utf8')).whitelist;
      assert.ok(wl.includes(friend), JSON.stringify(wl));
      assert.ok(!wl.includes(attacker));
    });

    await test('the PIN never appears in the daemon log', async () => {
      assert.ok(!out.includes(PIN), 'PIN leaked into the log');
    });
  } finally {
    child.kill('SIGKILL');
    await new Promise((r) => (exited !== null ? r() : child.once('exit', r)));
    fs.rmSync(tmp, { recursive: true, force: true });
  }
  console.log(`\n${passed} passed, ${failed} failed`);
  process.exit(failed === 0 ? 0 : 1);
})().catch((e) => { console.error(e); try { fs.rmSync(tmp, { recursive: true, force: true }); } catch {} process.exit(1); });
