// test_slash_commands.js — per-subtask E2E for the Discord slash-commands
// registration module.
//
// Run: node operator/bridges/discord/test_slash_commands.js
//
// Coverage:
//   1. COMMANDS array shape — every entry has lowercase name + description,
//      and only a single STRING `args` option when present (uniform schema
//      for the interactionToText() reverse-mapper).
//   2. Discord name constraint — name matches /^[a-z0-9_-]{1,32}$/u so the
//      registration call won't reject anything at runtime.
//   3. Description length cap — Discord rejects > 100 chars per command.
//   4. /btw is registered with an optional body arg (covers the user-facing
//      bug that motivated this module).
//   5. /voice-user-set is registered with a REQUIRED args option (key=value
//      makes no sense without a body).
//   6. interactionToText round-trip:
//      a) command-only (no args) → "/cmd"
//      b) command + args         → "/cmd <args>"
//      c) empty / whitespace args → "/cmd"
//      d) missing options accessor → "/cmd" (fail-open)
//   7. registerCommands() honours DISCORD_GUILD_IDS for per-guild scope —
//      we mock the client and assert the right method is called.
//   8. registerCommands() falls back to the global scope when no guild
//      ids are set.
//   9. registerCommands() never throws even when the registration call
//      rejects — text-path stays usable.

const { COMMANDS, interactionToText, registerCommands } = require('./slash_commands');

let pass = 0, fail = 0;
function ok(msg)  { console.log(`PASS: ${msg}`); pass++; }
function bad(msg) { console.log(`FAIL: ${msg}`); fail++; }
function eq(a, b, msg) {
  if (JSON.stringify(a) === JSON.stringify(b)) ok(msg);
  else bad(`${msg} — expected ${JSON.stringify(b)}, got ${JSON.stringify(a)}`);
}
function isTrue(cond, msg) { (cond ? ok : bad).call(null, msg); }

// ── 1. COMMANDS shape ─────────────────────────────────────────────────────
console.log('\n=== COMMANDS array shape ===');
isTrue(Array.isArray(COMMANDS) && COMMANDS.length >= 20,
       `COMMANDS array has ${COMMANDS.length} entries (>= 20 expected)`);

const NAME_RE = /^[a-z0-9_-]{1,32}$/u;
let allValid = true;
for (const cmd of COMMANDS) {
  if (typeof cmd.name !== 'string' || !NAME_RE.test(cmd.name)) {
    bad(`bad command name: ${JSON.stringify(cmd.name)}`); allValid = false;
  }
  if (typeof cmd.description !== 'string' || cmd.description.length === 0) {
    bad(`missing description on ${cmd.name}`); allValid = false;
  }
  if (cmd.description && cmd.description.length > 100) {
    bad(`description too long on ${cmd.name}: ${cmd.description.length} chars`); allValid = false;
  }
  if (cmd.options) {
    if (!Array.isArray(cmd.options) || cmd.options.length !== 1) {
      bad(`${cmd.name}: options must be a single-entry array (uniform schema)`); allValid = false;
      continue;
    }
    const opt = cmd.options[0];
    if (opt.name !== 'args')      { bad(`${cmd.name}: option name must be "args"`); allValid = false; }
    if (opt.type !== 3)           { bad(`${cmd.name}: option type must be 3 (STRING)`); allValid = false; }
    if (typeof opt.description !== 'string') { bad(`${cmd.name}: option missing description`); allValid = false; }
  }
}
if (allValid) ok('every command has valid name + description + uniform options shape');

// ── 2. Specific must-have commands ────────────────────────────────────────
console.log('\n=== required slash-commands present ===');
const byName = Object.fromEntries(COMMANDS.map(c => [c.name, c]));
isTrue(!!byName.btw,           '/btw is registered (the user-facing bug)');
isTrue(byName.btw && byName.btw.options && byName.btw.options[0].required === false,
       '/btw body option is NOT required (user can send /btw alone for the empty-body ACK)');
isTrue(!!byName['voice-user-set'], '/voice-user-set is registered');
isTrue(byName['voice-user-set'] && byName['voice-user-set'].options[0].required === true,
       '/voice-user-set args is REQUIRED (key=value needed)');
isTrue(!!byName.stop && !!byName.cancel, '/stop and /cancel both registered');
isTrue(!!byName.reset && !!byName.new && !!byName.clear,
       '/reset /new /clear registered (alias trio for session reset)');
isTrue(!!byName.help, '/help registered');
isTrue(!!byName['voice-user-show'] && !!byName['voice-user-clear'],
       'voice-user-show and -clear registered');

// ── 3. interactionToText round-trip ───────────────────────────────────────
console.log('\n=== interactionToText round-trip ===');

function fakeInteraction(commandName, argsValue) {
  return {
    commandName,
    options: argsValue === undefined ? null : {
      getString(name) { return name === 'args' ? argsValue : null; }
    }
  };
}

eq(interactionToText(fakeInteraction('whoami', undefined)), '/whoami',
   'no-args command → "/whoami"');
eq(interactionToText(fakeInteraction('btw', 'und auch X bitte')),
   '/btw und auch X bitte',
   '/btw with body → "/btw und auch X bitte"');
eq(interactionToText(fakeInteraction('btw', '')), '/btw',
   'empty args → "/btw" (no trailing space)');
eq(interactionToText(fakeInteraction('btw', '   ')), '/btw',
   'whitespace-only args → "/btw"');
eq(interactionToText(fakeInteraction('voice-user-set', 'learning=2')),
   '/voice-user-set learning=2',
   '/voice-user-set learning=2 round-trips');
eq(interactionToText(fakeInteraction('persona', 'browser')),
   '/persona browser',
   '/persona browser round-trips');

// Multi-line / multiline body — /btw must preserve the body verbatim
// (the [\s\S]+ regex in daemon.js picks up newlines too).
const multiline = 'line1\nline2\nline3';
eq(interactionToText(fakeInteraction('btw', multiline)),
   `/btw ${multiline}`,
   '/btw with multiline body preserves newlines');

// Fail-open: missing options accessor (e.g. some non-CHAT_INPUT
// interaction subtype) → "/<cmd>" without body, no crash.
eq(interactionToText({ commandName: 'whoami' }), '/whoami',
   'missing options accessor → "/whoami" (no crash)');
eq(interactionToText({}), '',
   'missing commandName → "" (no crash, daemon ignores)');

// ── 4. registerCommands global vs per-guild ───────────────────────────────
console.log('\n=== registerCommands routing ===');

function makeMockClient() {
  const calls = { global: 0, perGuild: [] };
  return {
    calls,
    application: {
      commands: { async set(cmds) { calls.global++; calls.lastCmds = cmds; } }
    },
    guilds: {
      async fetch(gid) {
        return {
          commands: { async set(cmds) { calls.perGuild.push({ gid, count: cmds.length }); } }
        };
      }
    }
  };
}

(async () => {
  // (a) no DISCORD_GUILD_IDS → global scope
  const prev = process.env.DISCORD_GUILD_IDS;
  delete process.env.DISCORD_GUILD_IDS;
  let mock = makeMockClient();
  let logs = [];
  await registerCommands(mock, m => logs.push(m));
  eq(mock.calls.global, 1, 'no guild ids → 1 global registration call');
  eq(mock.calls.perGuild.length, 0, 'no per-guild calls when global is used');
  isTrue(logs.some(l => /global/i.test(l)), 'log line mentions global registration');
  isTrue(mock.calls.lastCmds === COMMANDS, 'global registration uses the COMMANDS array verbatim');

  // (b) DISCORD_GUILD_IDS=g1,g2 → per-guild scope
  process.env.DISCORD_GUILD_IDS = 'g1,g2';
  mock = makeMockClient();
  logs = [];
  await registerCommands(mock, m => logs.push(m));
  eq(mock.calls.global, 0, 'guild ids set → no global registration');
  eq(mock.calls.perGuild.length, 2, 'two guilds → two per-guild calls');
  eq(mock.calls.perGuild[0].gid, 'g1', 'first guild = g1');
  eq(mock.calls.perGuild[1].gid, 'g2', 'second guild = g2');
  eq(mock.calls.perGuild[0].count, COMMANDS.length, 'g1 got the full COMMANDS array');

  // (c) Failing registration must NOT throw (text-path fallback stays usable)
  process.env.DISCORD_GUILD_IDS = '';
  const brokenClient = {
    application: { commands: { async set() { throw new Error('boom'); } } },
    guilds:      { async fetch() { throw new Error('also boom'); } },
  };
  logs = [];
  try {
    await registerCommands(brokenClient, m => logs.push(m));
    ok('registerCommands swallows registration errors (text-path stays alive)');
  } catch (e) {
    bad(`registerCommands threw on registration error: ${e.message}`);
  }
  isTrue(logs.some(l => /failed/i.test(l)),
         'failure is logged so the operator can diagnose');

  // Restore env
  if (prev === undefined) delete process.env.DISCORD_GUILD_IDS;
  else process.env.DISCORD_GUILD_IDS = prev;

  console.log(`\n${pass} pass, ${fail} fail`);
  process.exit(fail ? 1 : 0);
})();
