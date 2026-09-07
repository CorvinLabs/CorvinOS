// test_in_chat_commands.js — Smoke-Tests for den In-Chat-Command-Dispatcher.
//
// Run: node operator/bridges/shared/js/test_in_chat_commands.js
//
// Was getestet wed:
//   1. dispatch ignored Nicht-Commands (return null).
//   2. /help, /whoami, /skills liefern Reply-Text.
//   3. /persona <name> / /personas → retirement notice, NO settings mutation
//      (personas retired in e7e3560e — Skills replaced them).
//   4. (removed with the personas)
//      Felder stehen.
//   5. /persona <unknown> → Fehler-Hint, keine Mutation.
//   6. Nicht-Owner darf nicht binden (isOwner=false → Reply, keine Mutation).

const fs = require('fs');
const os = require('os');
const path = require('path');

const inChat = require('./in_chat_commands');

let pass = 0, fail = 0;
function ok(msg) { console.log(`PASS: ${msg}`); pass++; }
function bad(msg) { console.log(`FAIL: ${msg}`); fail++; }
function isTrue(cond, msg) { (cond ? ok : bad)(msg); }
function eq(a, b, msg) {
  if (JSON.stringify(a) === JSON.stringify(b)) ok(msg);
  else bad(`${msg} — expected ${JSON.stringify(b)}, got ${JSON.stringify(a)}`);
}
function contains(haystack, needle, msg) {
  if (typeof haystack === 'string' && haystack.includes(needle)) ok(msg);
  else bad(`${msg} — "${needle}" not found in: ${String(haystack).slice(0, 200)}`);
}

function tmpSettings() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'inchat-test-'));
  const f = path.join(dir, 'settings.json');
  fs.writeFileSync(f, JSON.stringify({}));
  return f;
}

function readJson(p) {
  return JSON.parse(fs.readFileSync(p, 'utf8'));
}

// ── 1. nicht-Command → null ──────────────────────────────────────────────
console.log('\n=== dispatch: non-commands ignored ===');
{
  const f = tmpSettings();
  eq(inChat.dispatch({ text: 'hallo', channel: 'telegram', chatKey: '1', isOwner: true, settingsFile: f }), null,
    'plain text → null');
  eq(inChat.dispatch({ text: '', channel: 'telegram', chatKey: '1', isOwner: true, settingsFile: f }), null,
    'empty → null');
  eq(inChat.dispatch({ text: '/unknown', channel: 'telegram', chatKey: '1', isOwner: true, settingsFile: f }), null,
    'unbekannter slash → null');
  // settings unchanged
  eq(readJson(f), {}, 'settings.json unchanged');
}

// ── 2. read-only commands ────────────────────────────────────────────────
console.log('\n=== dispatch: read-only commands ===');
{
  const f = tmpSettings();
  const ctx = { channel: 'telegram', chatKey: '123', isOwner: true, settingsFile: f };

  const help = inChat.dispatch({ ...ctx, text: '/help' });
  contains(help && help.reply, '/whoami', '/help mentions /whoami');
  contains(help && help.reply, '/skills', '/help mentions /skills');
  isTrue(help && !/\/personas?\b/.test(help.reply),
         '/help no longer advertises /persona or /personas (retired)');

  const hilfe = inChat.dispatch({ ...ctx, text: '/hilfe' });
  contains(hilfe && hilfe.reply, '/whoami', '/hilfe ist Alias und mentioned /whoami');

  const personas = inChat.dispatch({ ...ctx, text: '/personas' });
  eq(personas && personas.kind, 'personas_retired', '/personas → retirement notice kind');
  contains(personas && personas.reply, 'Skills', '/personas points at Skills');

  const whoami = inChat.dispatch({ ...ctx, text: '/whoami' });
  contains(whoami && whoami.reply, 'coder', '/whoami zeigt coder als Default');

  const skills = inChat.dispatch({ ...ctx, text: '/skills' });
  isTrue(skills && !/\/persona\b/.test(skills.reply), '/skills no longer lists /persona (retired)');
}

// ── 2b. /welcome trigger + tts opt-in ────────────────────────────────────
console.log('\n=== dispatch: /welcome ===');
{
  const f = tmpSettings();
  const ctx = { channel: 'whatsapp', chatKey: '42', isOwner: true, settingsFile: f };

  const w = inChat.dispatch({ ...ctx, text: '/welcome' });
  if (!w) bad('/welcome → expected reply object, got null'); else ok('/welcome dispatched');
  eq(w && w.kind, 'welcome', '/welcome kind=welcome');
  eq(w && w.tts, true, '/welcome carries tts=true (signals daemon to attach voice-note)');
  contains(w && w.reply, 'Corvin', '/welcome reply mentions Corvin');
  contains(w && w.reply, 'corvin-labs.com', '/welcome reply contains the product URL');
  contains(w && w.reply, '/help', '/welcome reply mentions /help');

  // Aliases route to the same handler.
  const willkommen = inChat.dispatch({ ...ctx, text: '/willkommen' });
  eq(willkommen && willkommen.kind, 'welcome', '/willkommen alias');
  const start = inChat.dispatch({ ...ctx, text: '/start' });
  eq(start && start.kind, 'welcome', '/start alias');
  const hi = inChat.dispatch({ ...ctx, text: '/hi' });
  eq(hi && hi.kind, 'welcome', '/hi alias');

  // Language switch: ctx.lang='en' → English text.
  const en = inChat.dispatch({ ...ctx, text: '/welcome', lang: 'en' });
  contains(en && en.reply, 'Voice to Action', '/welcome lang=en → English copy (contains tagline)');
}

// ── 3.-7. /persona is RETIRED (e7e3560e): truthful notice, never a mutation ─
console.log('\n=== dispatch: /persona retired — notice, no settings mutation ===');
{
  const f = tmpSettings();
  const ctx = { channel: 'telegram', chatKey: '999', isOwner: true, settingsFile: f };

  for (const text of ['/persona research', '/persona reset', '/persona doesnotexist', '/persona']) {
    const out = inChat.dispatch({ ...ctx, text });
    eq(out && out.kind, 'persona_retired', `${text} → kind=persona_retired`);
    contains(out && out.reply, 'Skills', `${text} → reply points at Skills`);
    isTrue(out && !/✓/.test(out.reply), `${text} → no success ack (nothing was bound)`);
  }
  eq(readJson(f).chat_profiles, undefined, '/persona never writes chat_profiles');

  // A pre-existing binding is left untouched — retirement must not silently
  // rewrite operator settings either.
  const g = tmpSettings();
  fs.writeFileSync(g, JSON.stringify({
    chat_profiles: { '777': { persona: 'browser', permission_mode: 'plan' } },
  }, null, 2));
  inChat.dispatch({ channel: 'telegram', chatKey: '777', isOwner: true, settingsFile: g, text: '/persona reset' });
  eq(readJson(g).chat_profiles['777'], { persona: 'browser', permission_mode: 'plan' },
     '/persona reset leaves existing chat_profiles untouched');

  // Non-owner gets the same notice (nothing privileged left to protect).
  const h = tmpSettings();
  const out = inChat.dispatch({ channel: 'telegram', chatKey: '222', isOwner: false, settingsFile: h, text: '/persona browser' });
  eq(out && out.kind, 'persona_retired', 'non-owner → same retirement notice');
  eq(readJson(h).chat_profiles, undefined, 'non-owner → no mutation');
}

// ── 8. /all audience toggle ───────────────────────────────────────────────
console.log('\n=== dispatch: /all audience toggle ===');
{
  const f = tmpSettings();
  const owner   = { channel: 'discord', chatKey: 'C1', isOwner: true,  settingsFile: f };
  const stranger= { channel: 'discord', chatKey: 'C1', isOwner: false, settingsFile: f };

  // status — default is owner
  let r = inChat.dispatch({ ...owner, text: '/all' });
  contains(r && r.reply, 'owner', '/all default → owner');
  eq(inChat.getAudience(f, 'C1'), 'owner', 'default audience = owner');

  // owner opens up
  r = inChat.dispatch({ ...owner, text: '/all on' });
  contains(r && r.reply, 'all', '/all on → all');
  eq(inChat.getAudience(f, 'C1'), 'all', 'audience flipped to all');
  eq(readJson(f).chat_profiles.C1.audience, 'all', 'persisted audience=all');

  // status from a non-owner is allowed (read only) and reflects the state
  r = inChat.dispatch({ ...stranger, text: '/all' });
  contains(r && r.reply, 'all', 'status visible to everyone');

  // non-owner cannot flip back
  r = inChat.dispatch({ ...stranger, text: '/all off' });
  contains(r && r.reply, 'Only the owner', 'non-owner blocked from /all off');
  eq(inChat.getAudience(f, 'C1'), 'all', 'non-owner attempt → unchanged');

  // owner closes
  r = inChat.dispatch({ ...owner, text: '/all off' });
  contains(r && r.reply, 'owner', '/all off → owner');
  eq(inChat.getAudience(f, 'C1'), 'owner', 'audience flipped back');

  // unknown arg
  r = inChat.dispatch({ ...owner, text: '/all banana' });
  contains(r && r.reply, 'Unknown', 'unknown arg surfaced');

  // per-chat scope: a different chat keeps its default
  eq(inChat.getAudience(f, 'C-other'), 'owner', 'other chat untouched');
}

// ── 9. /new /clear /reset → routed to reset handler ──────────────────────
console.log('\n=== dispatch: /new /clear /reset routed to reset handler ===');
{
  // Each test run gets a fresh CORVIN_HOME tempdir so the live audit
  // chain at <corvin_home>/global/forge/audit.jsonl never gets touched.
  // The CLI underneath is real Python — that's the contract we test.
  const sandbox = fs.mkdtempSync(path.join(os.tmpdir(), 'inchat-reset-'));
  const home = path.join(sandbox, 'home');
  const slot = path.join(sandbox, 'slot');
  process.env.CORVIN_HOME = home;
  process.env.CORVIN_PROJECT_ROOT = '';
  process.env.CORVIN_PLUGIN_SLOT_DIR = slot;
  process.env.CORVIN_FORCE_SCOPE = 'session';

  for (const trigger of ['/new', '/clear', '/reset']) {
    const f = tmpSettings();
    const ctx = { channel: 'discord', chatKey: 'reset-chat', isOwner: true, settingsFile: f };
    const out = inChat.dispatch({ ...ctx, text: trigger });
    if (!out) { bad(`${trigger} → null (expected reset reply)`); continue; }
    eq(out.kind, 'reset', `${trigger} kind=reset`);
    contains(out.reply, 'Session reset', `${trigger} reply mentions Session reset`);
    contains(out.reply, 'audit event', `${trigger} reply mentions audit event`);
  }
  // Sanity: dispatcher routes BEFORE /help, so trigger order matters.
  const f = tmpSettings();
  const ctx = { channel: 'discord', chatKey: 'reset-chat', isOwner: true, settingsFile: f };
  const out = inChat.dispatch({ ...ctx, text: '/reset' });
  eq(out && out.kind, 'reset', '/reset routes to reset handler before any other branch');

  // Tidy up env to not leak into other test runs.
  delete process.env.CORVIN_HOME;
  delete process.env.CORVIN_PROJECT_ROOT;
  delete process.env.CORVIN_PLUGIN_SLOT_DIR;
  delete process.env.CORVIN_FORCE_SCOPE;
  fs.rmSync(sandbox, { recursive: true, force: true });
}

// ── 10. /voice-user-set learning=N round-trip ────────────────────────────
console.log('\n=== dispatch: /voice-user-set learning=N writes voice_audience_learning ===');
{
  // Sandbox the profile dir so the live ~/.config/corvin-voice/profile.json
  // stays untouched. profile.py honours XDG_CONFIG_HOME when set.
  const sandbox = fs.mkdtempSync(path.join(os.tmpdir(), 'inchat-voice-user-'));
  const prevXdg = process.env.XDG_CONFIG_HOME;
  process.env.XDG_CONFIG_HOME = sandbox;

  try {
    const f = tmpSettings();
    const ctx = { channel: 'discord', chatKey: 'V1', isOwner: true, settingsFile: f };

    // 1. learning is in the keys list (so the dispatcher accepts it)
    const help = inChat.dispatch({ ...ctx, text: '/voice-user-help' });
    contains(help && help.reply, 'learning', '/voice-user-help mentions learning key');

    // 2. /voice-user-set learning=2 round-trips into the profile file
    const out = inChat.dispatch({ ...ctx, text: '/voice-user-set learning=2' });
    if (!out) bad('/voice-user-set learning=2 → null');
    else {
      // profile_cli.py prints either an OK ack or the new state — accept both
      const reply = (out.reply || '').toLowerCase();
      if (reply.length === 0) bad('/voice-user-set learning=2 → empty reply');
      else ok('/voice-user-set learning=2 → reply produced');
    }

    // 3. Read the file back and verify the canonical key landed
    const profPath = path.join(sandbox, 'corvin-voice', 'profile.json');
    if (!fs.existsSync(profPath)) {
      bad(`profile.json not written at ${profPath}`);
    } else {
      const prof = readJson(profPath);
      eq(prof.voice_audience_learning, 2,
         'profile.json: voice_audience_learning = 2 (canonical key)');
    }

    // 4. Unknown keys still get rejected
    const bogus = inChat.dispatch({ ...ctx, text: '/voice-user-set whatever=7' });
    contains(bogus && bogus.reply, 'unknown key',
             '/voice-user-set whatever=7 → unknown-key error');

    // 5. /voice-user-show surfaces the learning value
    const show = inChat.dispatch({ ...ctx, text: '/voice-user-show' });
    contains(show && show.reply, 'learning',
             '/voice-user-show lists learning field');
  } finally {
    if (prevXdg === undefined) delete process.env.XDG_CONFIG_HOME;
    else process.env.XDG_CONFIG_HOME = prevXdg;
    fs.rmSync(sandbox, { recursive: true, force: true });
  }
}

// ── 11. /auth-up /auth-down /auth-status (Layer-16 v2 PIN-Elevation) ─────
console.log('\n=== dispatch: /auth-up /auth-down /auth-status round-trip ===');
{
  const sandbox = fs.mkdtempSync(path.join(os.tmpdir(), 'inchat-auth-'));
  const home = path.join(sandbox, 'home');
  process.env.CORVIN_HOME = home;

  try {
    // settings.json with a PIN
    const f = path.join(sandbox, 'settings.json');
    fs.writeFileSync(f, JSON.stringify({ pin: 'sekret123' }));

    const ctx = { channel: 'discord', chatKey: 'C-auth', isOwner: true,
                  settingsFile: f };

    // /auth-status before any grant
    const s0 = inChat.dispatch({ ...ctx, text: '/auth-status' });
    contains(s0 && s0.reply, 'Not elevated', '/auth-status before grant → Not elevated');

    // /auth-up without arg → usage
    const usage = inChat.dispatch({ ...ctx, text: '/auth-up' });
    contains(usage && usage.reply, 'Usage', '/auth-up without arg → usage');

    // /auth-up with WRONG pin
    const wrong = inChat.dispatch({ ...ctx, text: '/auth-up wrong-pin' });
    contains(wrong && wrong.reply, 'Wrong PIN', '/auth-up wrong → refused');
    eq(wrong && wrong.kind, 'auth-up-denied', '/auth-up wrong → kind=auth-up-denied');

    // /auth-up with CORRECT pin
    const ok1 = inChat.dispatch({ ...ctx, text: '/auth-up sekret123' });
    contains(ok1 && ok1.reply, '✓ Elevation granted', '/auth-up correct → granted');
    eq(ok1 && ok1.kind, 'auth-up', '/auth-up correct → kind=auth-up');

    // store should now have an entry under discord:C-auth
    const storePath = path.join(home, 'global', 'auth', 'elevation.json');
    eq(fs.existsSync(storePath), true, 'elevation.json exists after grant');
    const store = JSON.parse(fs.readFileSync(storePath, 'utf8'));
    contains(JSON.stringify(store), 'discord:C-auth',
             'store keyed by `<channel>:<chatKey>`');

    // /auth-status after grant
    const s1 = inChat.dispatch({ ...ctx, text: '/auth-status' });
    contains(s1 && s1.reply, 'Elevated', '/auth-status after grant → Elevated');

    // /auth-down revokes
    const dn = inChat.dispatch({ ...ctx, text: '/auth-down' });
    contains(dn && dn.reply, 'revoked', '/auth-down → revoked');

    // /auth-status after revoke
    const s2 = inChat.dispatch({ ...ctx, text: '/auth-status' });
    contains(s2 && s2.reply, 'Not elevated', '/auth-status after revoke → Not elevated');

    // non-owner blocked
    const nonOwner = { ...ctx, isOwner: false };
    const blocked = inChat.dispatch({ ...nonOwner, text: '/auth-up sekret123' });
    contains(blocked && blocked.reply, 'Only the owner', 'non-owner → blocked');

    // settings.json without pin → no-pin error
    const f2 = path.join(sandbox, 'no_pin.json');
    fs.writeFileSync(f2, JSON.stringify({}));
    const ctx2 = { ...ctx, settingsFile: f2 };
    const noPin = inChat.dispatch({ ...ctx2, text: '/auth-up anything' });
    contains(noPin && noPin.reply, 'No PIN is configured', 'no-pin-configured → helpful error');
  } finally {
    delete process.env.CORVIN_HOME;
    fs.rmSync(sandbox, { recursive: true, force: true });
  }
}

// ── SPG privilege model: guests (isOwner=false) cannot reach the owner
//    command surface (security review 2026-06-27, ADR-0166). Regression guard
//    against the daemon.js isOwner:true hardcode + missing vaultReply guard.
console.log('\n=== dispatch: SPG guest is denied the owner surface ===');
{
  const f = tmpSettings();
  const guest = { channel: 'discord', chatKey: '42', isOwner: false, settingsFile: f };

  // /vault must never expose BYOK secrets to a guest, on any subcommand.
  for (const sub of ['', 'list', 'get TOKEN', 'set X=y', 'audit']) {
    const r = inChat.dispatch({ ...guest, text: `/vault ${sub}`.trim() });
    eq(r && r.kind, 'vault-denied', `/vault ${sub || '(bare)'} guest → vault-denied`);
    if (r && r.reply) contains(r.reply, 'owner-only', `/vault ${sub || '(bare)'} guest reply mentions owner-only`);
  }

  // /objective mutations are owner-only; list stays readable.
  const addBlocked = inChat.dispatch({ ...guest, text: '/objective add high steer the bot' });
  eq(addBlocked && addBlocked.kind, 'objective-denied', '/objective add guest → objective-denied');
  const delBlocked = inChat.dispatch({ ...guest, text: '/objective delete obj_1' });
  eq(delBlocked && delBlocked.kind, 'objective-denied', '/objective delete guest → objective-denied');

  // Owner is NOT blocked by the new guards (kind must differ from *-denied).
  const ownerVault = inChat.dispatch({ channel: 'discord', chatKey: '42', isOwner: true, settingsFile: f, text: '/vault list' });
  if (ownerVault && ownerVault.kind !== 'vault-denied') ok('/vault list owner → not denied');
  else bad(`/vault list owner should not be denied — got ${ownerVault && ownerVault.kind}`);
}

console.log(`\n${pass} pass, ${fail} fail`);
process.exit(fail ? 1 : 0);
