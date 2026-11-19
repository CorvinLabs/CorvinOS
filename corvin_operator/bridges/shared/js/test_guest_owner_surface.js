#!/usr/bin/env node
// test_guest_owner_surface.js — review 2026-11-19, R3-NEW-2 / R3-NEW-12 /
// R3-NEW-14.
//
// R3-NEW-2: in a chat the owner opened with `/all on` (audience=all) every
// participant came back from authOk() as authorised AND every daemon passed
// `isOwner: true` to the command dispatcher — so a stranger reached /vault
// (BYOK secrets), /all, /debug, /invite. This file replays the reviewer's
// repro against the shared auth + dispatcher, drives the real Teams and
// Signal handlers end to end, and pins the wiring of the daemons that cannot
// be loaded without live credentials (telegram, slack, discord, email,
// whatsapp).
//
// R3-NEW-12: `/auth <pin>` had no throttle (20 guesses in 2.9 s).
// R3-NEW-14: rateAllow() without a limit never limited and its map was unbounded.
//
// No python child is spawned by the auth part: the audit emitter is replaced
// by a recorder before auth.js loads.

'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');

// ── audit recorder (must be installed before auth.js is required) ───────────
const auditLog = [];
const auditPath = require.resolve('./audit');
require.cache[auditPath] = {
  id: auditPath, filename: auditPath, loaded: true, children: [], paths: [],
  exports: {
    auditEvent: (t, o) => { auditLog.push([t, o]); },
    auditEventSync: (t, o) => { auditLog.push([t, o]); return 0; },
    KNOWN_EVENT_TYPES: new Set(),
  },
};

const authMod = require('./auth');
const { makeAuth, PIN_MAX_FAILURES, PIN_LOCKOUT_MS, MAX_TRACKED_SENDERS } = authMod;
const inChat = require('./in_chat_commands');

let pass = 0, fail = 0;
function t(label, ok, detail = '') {
  console.log(`  ${ok ? 'PASS' : 'FAIL'}  ${label}${detail ? ' — ' + detail : ''}`);
  if (ok) pass++; else fail++;
}

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'guest-surface-'));
function settingsFile(obj, name = 'settings.json') {
  const f = path.join(tmp, name);
  fs.writeFileSync(f, JSON.stringify(obj));
  return f;
}
const loader = (f) => () => JSON.parse(fs.readFileSync(f, 'utf8'));
function authFor(f, extra = {}) {
  return makeAuth({ settingsFile: f, currentSettings: loader(f), loadSettings: loader(f),
                    channel: 'telegram', lockFirstSender: true, ...extra });
}

const BRIDGES = path.resolve(__dirname, '..', '..');
const src = (rel) => fs.readFileSync(path.join(BRIDGES, rel), 'utf8');

(async () => {
  // ── 1. the reviewer's repro (rep1.js) ─────────────────────────────────────
  console.log('\n[audience=all: a stranger is admitted as guest, never as owner]');
  {
    const f = settingsFile({ whitelist: ['owner1'], pin: '1234', chat_profiles: { c1: { audience: 'all' } } });
    const a = authFor(f);
    t('stranger may reach the bot in the open chat', a.authOk('stranger', '/vault list', 'c1') === true);
    t('classify(stranger, c1) === guest', a.classify('stranger', 'c1') === 'guest', a.classify('stranger', 'c1'));
    t('isOwner(stranger) === false', a.isOwner('stranger') === false);
    t('isOwner(owner1) === true', a.isOwner('owner1') === true);
    t('classify(owner1, c1) === owner', a.classify('owner1', 'c1') === 'owner');
    t('stranger is denied outside the open chat', a.authOk('stranger', 'hi', 'c2') === false);

    // What the daemons now pass to the dispatcher for that stranger.
    const guest = { channel: 'telegram', chatKey: 'c1', isOwner: a.isOwner('stranger'), settingsFile: f };
    for (const sub of ['', 'list', 'get OPENAI_API_KEY', 'set X=y']) {
      const r = inChat.dispatch({ ...guest, text: `/vault ${sub}`.trim() });
      t(`/vault ${sub || '(bare)'} → vault-denied`, !!r && r.kind === 'vault-denied', r && r.kind);
    }
    let r = inChat.dispatch({ ...guest, text: '/all off' });
    t('/all off refused', !!r && /Only the owner/.test(r.reply));
    t('audience unchanged after the refused /all off', inChat.getAudience(f, 'c1') === 'all');
    r = inChat.dispatch({ ...guest, text: '/debug on' });
    t('/debug on refused', !!r && /Only the owner/.test(r.reply));
    t('debug flag not set', inChat.getDebugEnabled(f, 'c1') === false);
    r = inChat.dispatch({ ...guest, text: '/invite @someone' });
    t('/invite refused', !!r && r.kind === 'spg-denied', r && r.kind);
    r = inChat.dispatch({ ...guest, text: '/auth-up 1234' });
    t('/auth-up refused', !!r && /Only the owner/.test(r.reply));
    // Commands whose handlers carry no owner check of their own.
    for (const cmd of ['/new', '/settings', '/memory list', '/schedule list', '/profile show',
                       '/lang de', '/kill abc', '/pipe list', '/tool rm x', '/ldd-off',
                       '/dialectic-off', '/workflow list', '/voice-user-set x', '/status', '/agents']) {
      const rr = inChat.dispatch({ ...guest, text: cmd });
      t(`${cmd} → owner-only`, !!rr && rr.kind === 'owner-only', rr && rr.kind);
    }
    r = inChat.dispatch({ ...guest, text: '/help' });
    t('/help stays available to a guest', !!r && r.kind === 'help');
    // The owner is unaffected.
    const owner = { ...guest, isOwner: a.isOwner('owner1') };
    r = inChat.dispatch({ ...owner, text: '/all off' });
    t('owner can still /all off', inChat.getAudience(f, 'c1') === 'owner', r && r.reply);
  }

  // ── 2. guest gate covers every command dispatch() knows ───────────────────
  console.log('\n[guest gate: every dispatch() head is known to the gate]');
  {
    const s = src('shared/js/in_chat_commands.js');
    const body = s.slice(s.indexOf('function dispatch(ctx) {'), s.indexOf('module.exports = {'));
    const heads = new Set();
    for (const m of body.matchAll(/head === '(\/[^']+)'/g)) heads.add(m[1]);
    t('found the command literals in dispatch()', heads.size > 60, String(heads.size));
    const unknown = [...heads].filter((h) => !inChat._internal._isKnownCommand(h));
    t('no dispatch() command escapes the guest gate', unknown.length === 0, unknown.join(' '));
    const f = settingsFile({}, 'gate.json');
    const leaks = [];
    for (const h of heads) {
      if (inChat._internal._isGuestCommand(h)) continue;
      const r = inChat.dispatch({ channel: 'x', chatKey: 'c', isOwner: false, settingsFile: f, text: h });
      if (!r || r.kind !== 'owner-only') leaks.push(h);
    }
    t('every non-guest command answers owner-only to a guest', leaks.length === 0, leaks.join(' '));
  }

  // ── 3. PIN brute force ────────────────────────────────────────────────────
  console.log('\n[/auth <pin>: lockout after repeated failures]');
  {
    const f = settingsFile({ whitelist: ['owner1'], pin: '1234' }, 'pin.json');
    let clock = 1_000_000;
    const a = authFor(f, { now: () => clock });
    auditLog.length = 0;
    let accepted = 0;
    for (let i = 0; i < 20; i++) {
      // Attempt 7 (index 6) is the CORRECT pin — the attacker is locked out by then.
      const guess = i === 6 ? '1234' : String(5000 + i);
      if (a.authOk('attacker', `/auth ${guess}`, 'c2')) accepted++;
    }
    t('20 attempts including the correct PIN: none accepted', accepted === 0, String(accepted));
    t('whitelist unchanged', JSON.stringify(loader(f)().whitelist) === '["owner1"]');
    const fails = auditLog.filter(([ty, o]) => ty === 'bridge.pin_failure' && !(o.details && o.details.reason));
    const lockouts = auditLog.filter(([ty, o]) => ty === 'bridge.pin_failure' && o.details && o.details.reason === 'lockout');
    t(`exactly ${PIN_MAX_FAILURES} failures audited, then silence`, fails.length === PIN_MAX_FAILURES, String(fails.length));
    t('lockout audited exactly once in the window', lockouts.length === 1, String(lockouts.length));
    t('no audit record carries the guessed PIN', !JSON.stringify(auditLog).includes('5003'));

    t('another sender is not locked out', a.authOk('friend', '/auth 1234', 'c2') === true);

    clock += PIN_LOCKOUT_MS + 1;
    t('after the lockout the correct PIN is accepted', a.authOk('attacker', '/auth 1234', 'c2') === true);

    // A few failures followed by success: counter is cleared.
    const f2 = settingsFile({ whitelist: ['owner1'], pin: '1234' }, 'pin2.json');
    const b = authFor(f2, { now: () => clock });
    for (let i = 0; i < PIN_MAX_FAILURES - 1; i++) b.authOk('typo', '/auth 0000', 'c');
    t('correct PIN below the threshold is accepted', b.authOk('typo', '/auth 1234', 'c') === true);

    t('safeEqual: equal / different / different length',
      authMod.safeEqual('1234', '1234') && !authMod.safeEqual('1234', '1235') && !authMod.safeEqual('1234', '12345'));
    t('the PIN comparison is constant-time (crypto.timingSafeEqual, no ===)',
      /crypto\.timingSafeEqual/.test(src('shared/js/auth.js'))
      && !/given === cs\.pin/.test(src('shared/js/auth.js'))
      && !/given === cs\.pin/.test(src('whatsapp/daemon.js')));
    t('whatsapp daemon: lockout check precedes the PIN comparison', (() => {
      const w = src('whatsapp/daemon.js');
      const i = w.indexOf('pinGuard.isLocked(sNorm'), j = w.indexOf('safeEqual(given, cs.pin)');
      return i !== -1 && j !== -1 && i < j && w.includes('pinGuard.fail(sNorm)');
    })());
  }

  // ── 4. rate limiter ───────────────────────────────────────────────────────
  console.log('\n[rateAllow: a missing limit still limits; the map is bounded]');
  {
    const f = settingsFile({ whitelist: ['owner1'] }, 'rate.json');
    const a = authFor(f);
    let allowed = 0;
    for (let i = 0; i < 500; i++) if (a.rateAllow('u1')) allowed++;
    t('rateAllow(uid) without perHour is limited (default)', allowed > 0 && allowed < 500, String(allowed));
    let n = 0;
    for (let i = 0; i < 40; i++) if (a.rateAllow('u2', 30)) n++;
    t('rateAllow(uid, 30) allows exactly 30', n === 30, String(n));
    for (let i = 0; i < MAX_TRACKED_SENDERS + 500; i++) a.rateAllow(`spam-${i}`, 30);
    t('rate map stays bounded for unknown senders', a._rateMapSize() <= MAX_TRACKED_SENDERS, String(a._rateMapSize()));
    for (const d of ['teams/daemon.js', 'signal/daemon.js']) {
      t(`${d}: no limit-less auth.rateAllow(sender) call`, !/auth\.rateAllow\(/.test(src(d)));
    }
    for (const h of ['teams/handler.js', 'signal/handler.js']) {
      t(`${h}: rate limit applied once, with a limit`, (src(h).match(/rateAllow\(\w+, currentSettings\(\)\.rate_limit_per_hour \|\| \d+\)/g) || []).length === 1);
    }
  }

  // ── 5. Teams + Signal handlers, end to end ────────────────────────────────
  console.log('\n[teams + signal handlers: guest in an open chat has no owner surface]');
  {
    const sandboxHome = fs.mkdtempSync(path.join(os.tmpdir(), 'guest-surface-home-'));
    const saved = { CORVIN_HOME: process.env.CORVIN_HOME, XDG_CONFIG_HOME: process.env.XDG_CONFIG_HOME };
    process.env.CORVIN_HOME = path.join(sandboxHome, 'corvin');
    process.env.XDG_CONFIG_HOME = path.join(sandboxHome, 'xdg');
    try {
      // Teams
      {
        const f = settingsFile({ whitelist: ['owner@corp.example'], rate_limit_per_hour: 60,
                                 chat_profiles: { 'conv-open': { audience: 'all' } } }, 'teams.json');
        const inbox = fs.mkdtempSync(path.join(tmp, 'teams-inbox-'));
        const cur = loader(f);
        const auth = makeAuth({ settingsFile: f, currentSettings: cur, loadSettings: cur, channel: 'teams' });
        const { makeHandler } = require('../../teams/handler');
        const { handleActivity } = makeHandler({ inboxDir: inbox, settingsFile: f, currentSettings: cur,
                                                 auth, logger: null, conversationRefs: new Map() });
        const send = async (upn, text) => {
          const replies = [];
          await handleActivity({
            activity: { type: 'message', text, from: { userPrincipalName: upn, aadObjectId: 'aad', id: 'id' },
                        conversation: { id: 'conv-open' } },
            sendActivity: async (m) => { replies.push(typeof m === 'string' ? m : JSON.stringify(m)); },
          });
          return replies.join('\n');
        };
        let out = await send('stranger@evil.example', '/vault list');
        t('teams: guest /vault → owner-only refusal', /owner-only/i.test(out), out.slice(0, 120));
        out = await send('stranger@evil.example', '/all off');
        t('teams: guest /all off refused, audience unchanged',
          /Only the owner/.test(out) && inChat.getAudience(f, 'conv-open') === 'all');
        out = await send('stranger@evil.example', '/off');
        t('teams: guest /off toggle denied', /Only the bot owner/.test(out), out.slice(0, 120));
        out = await send('stranger@evil.example', '/debug on');
        t('teams: guest /debug on refused', /Only the owner/.test(out) && inChat.getDebugEnabled(f, 'conv-open') === false);
        await send('owner@corp.example', '/all off');
        t('teams: owner /all off still works', inChat.getAudience(f, 'conv-open') === 'owner');
        t('teams: no command text reached the inbox',
          fs.readdirSync(inbox).filter((x) => x.endsWith('.json')).length === 0);
      }
      // Signal
      {
        const { makeHandler, normPhone } = require('../../signal/handler');
        const owner = '+491111111111', stranger = '+492222222222';
        const f = settingsFile({ whitelist: [owner], rate_limit_per_hour: 60,
                                 chat_profiles: { [stranger]: { audience: 'all' } } }, 'signal.json');
        const inbox = fs.mkdtempSync(path.join(tmp, 'signal-inbox-'));
        const cur = loader(f);
        const auth = makeAuth({ settingsFile: f, currentSettings: cur, loadSettings: cur, channel: 'signal', normalize: normPhone });
        const sent = [];
        const { handleEnvelope } = makeHandler({ inboxDir: inbox, settingsFile: f, currentSettings: cur, auth,
                                                 logger: null, sendSignal: async (to, m) => { sent.push(String(m)); } });
        const send = async (source, message) => {
          sent.length = 0;
          await handleEnvelope({ envelope: { source, sourceDevice: 1, dataMessage: { message, timestamp: Date.now() } } });
          return sent.join('\n');
        };
        let out = await send(stranger, '/vault list');
        t('signal: guest /vault → owner-only refusal', /owner-only/i.test(out), out.slice(0, 120));
        out = await send(stranger, '/all off');
        t('signal: guest /all off refused, audience unchanged',
          /Only the owner/.test(out) && inChat.getAudience(f, stranger) === 'all', out.slice(0, 120));
        out = await send(stranger, '/off');
        t('signal: guest /off toggle denied', /Only the bot owner/.test(out), out.slice(0, 120));
        out = await send(stranger, '/invite x');
        t('signal: guest /invite refused', /only owners/.test(out), out.slice(0, 120));
        t('signal: no command text reached the inbox',
          fs.readdirSync(inbox).filter((x) => x.endsWith('.json')).length === 0);
      }
      // A handler given an auth object without isOwner fails closed.
      {
        const { makeHandler } = require('../../teams/handler');
        const f = settingsFile({ whitelist: ['a@b.c'] }, 'teams2.json');
        const inbox = fs.mkdtempSync(path.join(tmp, 'teams2-inbox-'));
        const { handleActivity } = makeHandler({
          inboxDir: inbox, settingsFile: f, currentSettings: loader(f), logger: null, conversationRefs: new Map(),
          auth: { authOk: () => true, readOnlyOk: () => ({ isReadOnly: false }), rateAllow: () => true },
        });
        const replies = [];
        await handleActivity({
          activity: { type: 'message', text: '/vault list', from: { userPrincipalName: 'a@b.c', id: 'i' }, conversation: { id: 'c9' } },
          sendActivity: async (m) => { replies.push(String(m)); },
        });
        t('handler without auth.isOwner treats nobody as owner', /owner-only/i.test(replies.join('\n')));
      }
    } finally {
      for (const [k, v] of Object.entries(saved)) { if (v === undefined) delete process.env[k]; else process.env[k] = v; }
      fs.rmSync(sandboxHome, { recursive: true, force: true });
    }
  }

  // ── 6. wiring of the daemons that need live credentials to load ───────────
  console.log('\n[daemon wiring: isOwner comes from the whitelist, never a literal]');
  {
    for (const d of ['telegram/daemon.js', 'slack/daemon.js', 'discord/daemon.js', 'email/daemon.js',
                     'whatsapp/daemon.js', 'teams/handler.js', 'signal/handler.js']) {
      const s = src(d);
      t(`${d}: no "isOwner: true" literal`, !/isOwner:\s*true\s*[,}]/.test(s));
      const n = (s.match(/isOwner:\s*[A-Za-z_(]/g) || []).length;
      t(`${d}: passes a computed isOwner`, n >= 1, String(n));
    }
    for (const d of ['telegram/daemon.js', 'slack/daemon.js']) {
      const s = src(d);
      t(`${d}: owner flag is auth.isOwner(uid), computed after authOk`,
        /isOwner: isOwnerUid \} = makeAuth\(/.test(s) && /const senderIsOwner = isOwnerUid\(/.test(s)
        && s.indexOf('if (!authOk(userId') !== -1
        && s.indexOf('const senderIsOwner') > s.indexOf('if (!authOk(userId'));
      t(`${d}: toggle and dispatcher both get senderIsOwner`,
        (s.match(/isOwner: senderIsOwner/g) || []).length === 2);
    }
    t('email/daemon.js: owner = classify(addr, null) (no audience branch)',
      /isOwner: classify\(fromAddr, null\) === 'owner'/.test(src('email/daemon.js')));
    t('whatsapp/daemon.js: owner = the account itself (fromMe)',
      /isOwner: isOwnerMsg\(m, s\)/.test(src('whatsapp/daemon.js')));
    t('auth.js: the audience branch of classify() yields guest',
      /audienceFor\(settingsFile, chatKey\) === 'all'\) return 'guest';/.test(src('shared/js/auth.js')));
  }

  fs.rmSync(tmp, { recursive: true, force: true });
  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail === 0 ? 0 : 1);
})().catch((e) => { console.error(e); process.exit(1); });
