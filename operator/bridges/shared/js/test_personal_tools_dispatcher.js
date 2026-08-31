// E2E for /tool + /tools dispatcher (Layer 27).
//
// Runs against the real personal_tools.py CLI (subprocess) under a
// tempdir-isolated CORVIN_HOME so the user's actual ~/.corvin tree
// is never touched.

const fs   = require('fs');
const os   = require('os');
const path = require('path');
const { execSync } = require('child_process');

let pass = 0, fail = 0;
function assertEq(actual, expected, label) {
  if (actual === expected) { console.log(`  ✓ ${label}`); pass++; }
  else { console.log(`  ✗ ${label}\n    expected: ${expected}\n    actual:   ${actual}`); fail++; }
}
function assertContains(s, sub, label) {
  if (s && s.indexOf(sub) !== -1) { console.log(`  ✓ ${label}`); pass++; }
  else { console.log(`  ✗ ${label}\n    expected substring: ${sub}\n    in: ${(s||'').slice(0, 200)}`); fail++; }
}

// Sandbox CORVIN_HOME via env BEFORE require(), since the dispatcher
// shells out to a subprocess that re-reads the env.
const tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'pt-disp-'));
process.env.CORVIN_HOME = tmpRoot;
process.env.CORVIN_HOME = tmpRoot;

const inChatCmds = require('./in_chat_commands.js');
const PERSONAL_CLI = path.resolve(__dirname, '..', 'personal_tools.py');

// Helper to seed a personal tool by calling the CLI directly (bypass
// the /tool save flow which requires a session-scope source).
function seedPersonalTool(name, description) {
  const r = execSync(
    `python3 ${PERSONAL_CLI} --corvin-home ${tmpRoot} save-body ${name} --description "${description}"`,
    { input: 'def run():\n    return {}\n', encoding: 'utf8' }
  );
  return JSON.parse(r);
}

// Per-test sandbox: wipe between tests to guarantee isolation.
function resetSandbox() {
  fs.rmSync(tmpRoot, { recursive: true, force: true });
  fs.mkdirSync(tmpRoot, { recursive: true });
}

const ctx = (over) => Object.assign({
  channel: 'discord', chatKey: 'chat_X', uid: 'u_owner', isOwner: true,
  text: '', settings: {},
}, over || {});

// 1. /tools on empty home → 'no personal tools' message
console.log('1. /tools on empty home');
{
  resetSandbox();
  const r = inChatCmds.dispatch(ctx({ text: '/tools' }));
  assertEq(r.kind, 'tools', 'kind=tools');
  assertContains(r.reply, 'No personal tools yet', 'empty-state message');
}

// 2. /tools after seeding lists the tool
console.log('\n2. /tools lists seeded tools');
{
  resetSandbox();
  seedPersonalTool('alpha', 'first tool');
  seedPersonalTool('beta', 'second tool');
  const r = inChatCmds.dispatch(ctx({ text: '/tools' }));
  assertContains(r.reply, '`me.alpha`', 'lists me.alpha');
  assertContains(r.reply, '`me.beta`',  'lists me.beta');
  assertContains(r.reply, 'first tool', 'shows description');
}

// 3. /tool (no arg) → help
console.log('\n3. /tool with no subcommand → help');
{
  resetSandbox();
  const r = inChatCmds.dispatch(ctx({ text: '/tool' }));
  assertEq(r.kind, 'tool-help', 'kind=tool-help');
  assertContains(r.reply, '/tool save', 'help mentions save');
  assertContains(r.reply, '/tool rm',   'help mentions rm');
}

// 4. /tool save without args → usage hint
console.log('\n4. /tool save with no args → usage');
{
  resetSandbox();
  const r = inChatCmds.dispatch(ctx({ text: '/tool save' }));
  assertEq(r.kind, 'tool-help', 'kind=tool-help');
  assertContains(r.reply, 'Usage', 'usage line shown');
}

// 5. /tool save <missing> → not-found from CLI
console.log('\n5. /tool save <missing-source> → not-found');
{
  resetSandbox();
  const r = inChatCmds.dispatch(ctx({ text: '/tool save nonexistent_tool' }));
  assertEq(r.kind, 'tool-denied', 'kind=tool-denied');
  assertContains(r.reply, 'No tool', 'not-found message');
}

// 6. /tool save with a real session-scope tool present → success
console.log('\n6. /tool save with real session-scope source');
{
  resetSandbox();
  // Plant a session-scope forge tool that save_from_scope can find.
  const sessionForge = path.join(tmpRoot, 'sessions', 'chat_X', 'forge');
  fs.mkdirSync(path.join(sessionForge, 'tools'), { recursive: true });
  fs.writeFileSync(path.join(sessionForge, 'tools', 'poke_api.py'),
                   "def run(url):\n    return {'ok': True}\n");
  fs.writeFileSync(path.join(sessionForge, 'registry.json'), JSON.stringify({
    'poke_api': {
      name: 'poke_api', description: 'pokes my private API',
      runtime: 'python',
      impl_path: path.join(sessionForge, 'tools', 'poke_api.py'),
      scope: 'session', created_at: 1700000000.0, sha256: 'cafe',
    }
  }));

  const r = inChatCmds.dispatch(ctx({ text: '/tool save poke_api' }));
  assertEq(r.kind, 'tool-saved', 'kind=tool-saved');
  assertContains(r.reply, 'me.poke_api', 'saved as me.poke_api');

  // Subsequent /tools lists it.
  const r2 = inChatCmds.dispatch(ctx({ text: '/tools' }));
  assertContains(r2.reply, '`me.poke_api`', 'list shows me.poke_api');
}

// 7. /tool save <source> as <alias> uses the alias
console.log('\n7. /tool save <source> as <alias>');
{
  resetSandbox();
  const sessionForge = path.join(tmpRoot, 'sessions', 'chat_X', 'forge');
  fs.mkdirSync(path.join(sessionForge, 'tools'), { recursive: true });
  fs.writeFileSync(path.join(sessionForge, 'tools', 'foo.py'),
                   "def run(): return {}\n");
  fs.writeFileSync(path.join(sessionForge, 'registry.json'), JSON.stringify({
    'foo': { name: 'foo', description: 'd', runtime: 'python',
             impl_path: path.join(sessionForge, 'tools', 'foo.py'),
             scope: 'session', created_at: 1, sha256: 'a' },
  }));
  const r = inChatCmds.dispatch(ctx({ text: '/tool save foo as myalias' }));
  assertEq(r.kind, 'tool-saved', 'kind=tool-saved');
  assertContains(r.reply, 'me.myalias', 'aliased to me.myalias');
}

// 8. /tool save with invalid alias → invalid-name
console.log('\n8. /tool save <source> as <BAD-NAME>');
{
  resetSandbox();
  const sessionForge = path.join(tmpRoot, 'sessions', 'chat_X', 'forge');
  fs.mkdirSync(path.join(sessionForge, 'tools'), { recursive: true });
  fs.writeFileSync(path.join(sessionForge, 'tools', 'foo.py'),
                   "def run(): return {}\n");
  fs.writeFileSync(path.join(sessionForge, 'registry.json'), JSON.stringify({
    'foo': { name: 'foo', description: 'd', runtime: 'python',
             impl_path: path.join(sessionForge, 'tools', 'foo.py'),
             scope: 'session', created_at: 1, sha256: 'a' },
  }));
  const r = inChatCmds.dispatch(ctx({ text: '/tool save foo as Bad-Name' }));
  assertEq(r.kind, 'tool-denied', 'kind=tool-denied');
  assertContains(r.reply, 'Invalid name', 'invalid-name message');
}

// 9. /tool rm round-trip
console.log('\n9. /tool rm round-trip');
{
  resetSandbox();
  seedPersonalTool('removable', 'will be removed');
  const r = inChatCmds.dispatch(ctx({ text: '/tool rm me.removable' }));
  assertEq(r.kind, 'tool-removed', 'kind=tool-removed');
  // Now /tools is empty
  const r2 = inChatCmds.dispatch(ctx({ text: '/tools' }));
  assertContains(r2.reply, 'No personal tools', 'empty after rm');
}

// 10. /tool rm <missing>
console.log('\n10. /tool rm <missing>');
{
  resetSandbox();
  const r = inChatCmds.dispatch(ctx({ text: '/tool rm ghost' }));
  assertEq(r.kind, 'tool-denied', 'kind=tool-denied');
  assertContains(r.reply, 'not found', 'not-found message');
}

// 11. /tool show
console.log('\n11. /tool show');
{
  resetSandbox();
  seedPersonalTool('inspectme', 'a tool to inspect');
  const r = inChatCmds.dispatch(ctx({ text: '/tool show me.inspectme' }));
  assertEq(r.kind, 'tool-show', 'kind=tool-show');
  assertContains(r.reply, 'a tool to inspect', 'desc visible');
  assertContains(r.reply, 'me.inspectme', 'name visible');
}

// 12. /tool help
console.log('\n12. /tool help');
{
  resetSandbox();
  const r = inChatCmds.dispatch(ctx({ text: '/tool help' }));
  assertEq(r.kind, 'tool-help', 'kind=tool-help');
}

// 13. /tool unknown-sub → help
console.log('\n13. /tool <unknown> → help');
{
  resetSandbox();
  const r = inChatCmds.dispatch(ctx({ text: '/tool zoinks' }));
  assertEq(r.kind, 'tool-help', 'unknown sub returns help kind');
}

fs.rmSync(tmpRoot, { recursive: true, force: true });

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail === 0 ? 0 : 1);
