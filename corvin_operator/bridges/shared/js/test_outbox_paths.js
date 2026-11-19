#!/usr/bin/env node
// test_outbox_paths.js — review 2026-11-19, R3-NEW-41.
//
// An outbox envelope could name ANY local file as voice/image/document/video
// attachment and the daemon uploaded it. Attachments are now accepted only
// from the outbox directory and the session workdirs; this exercises the
// guard directly and through the real shared poller.

'use strict';

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');

const tmp = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'outbox-paths-')));
const HOME = path.join(tmp, 'corvin_home');
process.env.CORVIN_HOME = HOME;
delete process.env.CORVIN_OUTBOX_ATTACHMENT_ROOTS;

const { safeAttachmentPath, sanitizeAttachmentPaths, attachmentRoots, ATTACHMENT_KEYS } = require('./outbox_paths');
const { startOutboxPoller } = require('./outbox');

let passed = 0, failed = 0;
async function test(name, fn) {
  try { await fn(); console.log(`  ok  ${name}`); passed++; }
  catch (e) { console.error(`  FAIL ${name}\n       ${e.message}`); failed++; }
}
function mk(p, content = 'x') { fs.mkdirSync(path.dirname(p), { recursive: true }); fs.writeFileSync(p, content); return p; }

const OUTBOX = path.join(tmp, 'shared', 'outbox');
fs.mkdirSync(OUTBOX, { recursive: true });
const voice = mk(path.join(OUTBOX, 'm1.ogg'));
const workFile = mk(path.join(HOME, 'tenants', '_default', 'sessions', 'voice', 'discord', '42', 'outputs', 'chart.png'));
const legacyFile = mk(path.join(HOME, 'sessions', 'tg', '7', 'outputs', 'report.pdf'));
const secretSettings = mk(path.join(HOME, 'bridges', 'discord', 'settings.json'), '{"bot_token":"T0KEN"}');
const auditChain = mk(path.join(HOME, 'tenants', '_default', 'global', 'forge', 'audit.jsonl'));
const outside = mk(path.join(tmp, 'elsewhere', 'id_rsa'), 'PRIVATE KEY');
const settingsInWorkdir = mk(path.join(HOME, 'tenants', '_default', 'sessions', 'x', 'settings.json'));
const envInOutbox = mk(path.join(OUTBOX, '.env'));

(async () => {
  console.log('== attachment path guard ==');

  await test('roots are the outbox and the session workdirs', async () => {
    const roots = attachmentRoots(OUTBOX);
    assert.ok(roots.includes(OUTBOX));
    assert.ok(roots.includes(path.join(HOME, 'tenants', '_default', 'sessions')));
    assert.ok(roots.includes(path.join(HOME, 'sessions')));
    assert.ok(!roots.includes(HOME), 'the whole runtime root must not be attachable');
  });

  await test('a voice note in the outbox and worker outputs are accepted', async () => {
    assert.strictEqual(safeAttachmentPath(voice, OUTBOX), voice);
    assert.strictEqual(safeAttachmentPath(workFile, OUTBOX), workFile);
    assert.strictEqual(safeAttachmentPath(legacyFile, OUTBOX), legacyFile);
  });

  await test('files outside the roots are refused', async () => {
    for (const p of [secretSettings, auditChain, outside, '/etc/passwd', '/etc/hostname']) {
      assert.strictEqual(safeAttachmentPath(p, OUTBOX), null, p);
    }
  });

  await test('secret-bearing names are refused even inside a root', async () => {
    assert.strictEqual(safeAttachmentPath(settingsInWorkdir, OUTBOX), null);
    assert.strictEqual(safeAttachmentPath(envInOutbox, OUTBOX), null);
  });

  await test('traversal and relative paths are refused', async () => {
    assert.strictEqual(safeAttachmentPath(path.join(OUTBOX, '..', '..', 'elsewhere', 'id_rsa'), OUTBOX), null);
    assert.strictEqual(safeAttachmentPath('m1.ogg', OUTBOX), null);
    assert.strictEqual(safeAttachmentPath('', OUTBOX), null);
    assert.strictEqual(safeAttachmentPath(null, OUTBOX), null);
    assert.strictEqual(safeAttachmentPath({ toString: () => voice }, OUTBOX), null);
    assert.strictEqual(safeAttachmentPath(OUTBOX, OUTBOX), null, 'a directory is not an attachment');
  });

  await test('a symlink inside the outbox pointing out of it is refused', async () => {
    const link = path.join(OUTBOX, 'innocent.ogg');
    try { fs.symlinkSync(secretSettings, link); }
    catch (e) { if (e.code === 'EPERM') { console.log('       (symlinks unavailable on this platform — skipped)'); return; } throw e; }
    assert.strictEqual(safeAttachmentPath(link, OUTBOX), null);
    fs.unlinkSync(link);
  });

  await test('CORVIN_OUTBOX_ATTACHMENT_ROOTS adds a root; secrets stay refused', async () => {
    const extra = path.join(tmp, 'elsewhere');
    const pic = mk(path.join(extra, 'pic.png'));
    assert.strictEqual(safeAttachmentPath(pic, OUTBOX), null);
    process.env.CORVIN_OUTBOX_ATTACHMENT_ROOTS = extra;
    try {
      assert.strictEqual(safeAttachmentPath(pic, OUTBOX), pic);
      assert.strictEqual(safeAttachmentPath(outside, OUTBOX), null, 'id_rsa stays refused');
    } finally { delete process.env.CORVIN_OUTBOX_ATTACHMENT_ROOTS; }
  });

  await test('sanitize removes refused keys, keeps the rest, logs the key only', async () => {
    const logs = [];
    const payload = { channel: 'discord', text: 'hi', voice_path: voice, document_path: secretSettings,
                      image_path: '/etc/passwd', video_path: path.join(OUTBOX, 'not-yet-written.mp4'),
                      voice_attachment_path: outside };
    const removed = sanitizeAttachmentPaths(payload, OUTBOX, { logger: (l) => logs.push(l) });
    assert.deepStrictEqual(removed.sort(), ['document_path', 'image_path', 'voice_attachment_path']);
    assert.strictEqual(payload.voice_path, voice);
    assert.strictEqual(payload.text, 'hi');
    assert.ok('video_path' in payload, 'a not-yet-existing file is left to the daemon existsSync check');
    assert.ok(!('document_path' in payload) && !('image_path' in payload) && !('voice_attachment_path' in payload));
    assert.ok(logs.length === 3 && logs.every((l) => !l.includes('/')), 'log lines carry the key, never the path');
  });

  await test('every attachment key the daemons read is covered', async () => {
    const B = path.resolve(__dirname, '..', '..');
    const used = new Set();
    for (const rel of ['discord/daemon.js', 'telegram/daemon.js', 'slack/daemon.js', 'whatsapp/daemon.js',
                       'email/daemon.js', 'teams/handler.js', 'signal/handler.js']) {
      const s = fs.readFileSync(path.join(B, rel), 'utf8');
      for (const m of s.matchAll(/payload\.((?:voice|image|document|video|audio)_(?:attachment_)?path)\b/g)) used.add(m[1]);
    }
    assert.ok(used.size >= 4, [...used].join(','));
    const missing = [...used].filter((k) => !ATTACHMENT_KEYS.includes(k));
    assert.deepStrictEqual(missing, []);
  });

  console.log('== through the shared poller ==');

  await test('poller: a planted envelope cannot exfiltrate a local file; its text is still delivered', async () => {
    fs.writeFileSync(path.join(OUTBOX, 'a_planted.json'), JSON.stringify({
      channel: 'discord', chat_id: '123', text: 'see attachment', document_path: secretSettings, voice_path: outside,
    }));
    fs.writeFileSync(path.join(OUTBOX, 'b_legit.json'), JSON.stringify({
      channel: 'discord', chat_id: '123', voice_path: voice, image_path: workFile,
    }));
    const seen = [];
    const logs = [];
    const poller = startOutboxPoller({
      outboxDir: OUTBOX, channel: 'discord', intervalMs: 20, logger: (l) => logs.push(l),
      sendFn: async (payload) => { seen.push(JSON.parse(JSON.stringify(payload))); },
    });
    const deadline = Date.now() + 5000;
    while (seen.length < 2 && Date.now() < deadline) await new Promise((r) => setTimeout(r, 25));
    if (poller && typeof poller.stop === 'function') poller.stop(); else if (poller && poller.handle) clearInterval(poller.handle);
    assert.strictEqual(seen.length, 2, `sendFn calls: ${seen.length}`);
    const planted = seen.find((p) => p.text === 'see attachment');
    assert.ok(planted, 'the text of the planted envelope is still delivered');
    assert.ok(!('document_path' in planted) && !('voice_path' in planted), JSON.stringify(planted));
    const legit = seen.find((p) => p.image_path);
    assert.strictEqual(legit.voice_path, voice);
    assert.strictEqual(legit.image_path, workFile);
    assert.ok(logs.some((l) => /refused document_path/.test(l)));
  });

  await test('whatsapp + email loops call the guard before sending', async () => {
    const B = path.resolve(__dirname, '..', '..');
    for (const rel of ['whatsapp/daemon.js', 'email/daemon.js']) {
      const s = fs.readFileSync(path.join(B, rel), 'utf8');
      assert.ok(/sanitizeAttachmentPaths\(payload, OUTBOX, \{ logger: log \}\)/.test(s), rel);
    }
    const w = fs.readFileSync(path.join(B, 'whatsapp/daemon.js'), 'utf8');
    assert.ok(w.indexOf('sanitizeAttachmentPaths(payload, OUTBOX') < w.indexOf('fs.readFileSync(payload.voice_path)'));
  });

  fs.rmSync(tmp, { recursive: true, force: true });
  console.log(`\n${passed} passed, ${failed} failed`);
  process.exit(failed === 0 ? 0 : 1);
})().catch((e) => { console.error(e); process.exit(1); });
