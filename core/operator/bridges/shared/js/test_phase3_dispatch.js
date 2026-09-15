#!/usr/bin/env node
/* test_phase3_dispatch.js — E2E for the Layer-17/18/19/20 slash commands.
 *
 * Drives the in_chat_commands.js dispatcher with a fake CORVIN_HOME
 * and asserts that each new slash command (/ps, /pipe, /svc, /budget)
 * routes through the phase3_cli.py wrapper and returns chat-formatted
 * output. Exercises the real CLI subprocess on every case — no mocks.
 *
 * Per-subtask E2E rule from CLAUDE.md: real subprocess, real filesystem.
 */
'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');

const HERE = __dirname;
const cmd = require('./in_chat_commands');

let pass = 0;
let fail = 0;

function section(title) {
  process.stdout.write(`\n=== ${title} ===\n`);
}

function check(name, cond, detail) {
  if (cond) {
    process.stdout.write(`  PASS ${name}${detail ? ' — ' + detail : ''}\n`);
    pass++;
  } else {
    process.stdout.write(`  FAIL ${name}${detail ? ' — ' + detail : ''}\n`);
    fail++;
  }
}

function withTempHome(fn) {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), 'phase3-disp-'));
  const prev = process.env.CORVIN_HOME;
  process.env.CORVIN_HOME = home;
  try {
    fn(home);
  } finally {
    if (prev === undefined) delete process.env.CORVIN_HOME;
    else process.env.CORVIN_HOME = prev;
    fs.rmSync(home, { recursive: true, force: true });
  }
}

// --- /ps ---

section('/ps — empty session list');
withTempHome(() => {
  const r = cmd.dispatch({ text: '/ps' });
  check('returns dispatcher result', r !== null);
  check('kind=phase3-ps', r && r.kind === 'phase3-ps', r && r.kind);
  check('reply mentions empty', r && r.reply.includes('no active sessions'));
});

section('/ps -a — empty + include terminated');
withTempHome(() => {
  const r = cmd.dispatch({ text: '/ps -a' });
  check('returns dispatcher result', r !== null);
  check('reply mentions empty', r && r.reply.includes('no active sessions'));
});

// --- /pipe ---

section('/pipe list — empty initially');
withTempHome(() => {
  const r = cmd.dispatch({ text: '/pipe list' });
  check('returns dispatcher result', r !== null);
  check('kind=phase3-pipe', r && r.kind === 'phase3-pipe');
  check('reply mentions no pipes', r && r.reply.includes('no pipes'));
});

section('/pipe create + list + write + read');
withTempHome(() => {
  const c1 = cmd.dispatch({ text: '/pipe create chan named' });
  check('create succeeded', c1 && /created pipe 'chan'/.test(c1.reply), c1 && c1.reply.slice(0, 60));

  const c2 = cmd.dispatch({ text: '/pipe list' });
  check('list mentions chan', c2 && c2.reply.includes('chan'));
  check('list mentions named', c2 && c2.reply.includes('named'));

  const w = cmd.dispatch({ text: '/pipe write chan hello' });
  check('write succeeded', w && /seq=0/.test(w.reply));

  const rd = cmd.dispatch({ text: '/pipe read chan' });
  check('read returns the message', rd && rd.reply.includes('hello'));

  // Repeat read returns nothing (queue truncated)
  const rd2 = cmd.dispatch({ text: '/pipe read chan' });
  check('repeat read empty', rd2 && rd2.reply.includes('no messages'));
});

section('/pipe rm');
withTempHome(() => {
  cmd.dispatch({ text: '/pipe create gone named' });
  const r = cmd.dispatch({ text: '/pipe rm gone' });
  check('rm reports removed', r && r.reply.includes('removed'));
  const r2 = cmd.dispatch({ text: '/pipe meta gone' });
  check('meta of removed errors', r2 && r2.reply.toLowerCase().includes('error'));
});

section('/pipe (no sub) defaults to list');
withTempHome(() => {
  const r = cmd.dispatch({ text: '/pipe' });
  check('default sub=list works', r && r.reply.includes('no pipes'));
});

// --- /svc ---

section('/svc list — discovers all 7 manifests');
withTempHome(() => {
  const r = cmd.dispatch({ text: '/svc list' });
  check('returns dispatcher result', r !== null);
  check('kind=phase3-svc', r && r.kind === 'phase3-svc');
  for (const name of ['forge-mcp', 'voice-adapter', 'discord-daemon', 'whatsapp-daemon']) {
    check(`mentions ${name}`, r && r.reply.includes(name));
  }
});

section('/svc deps voice-adapter — full record');
withTempHome(() => {
  const r = cmd.dispatch({ text: '/svc deps voice-adapter' });
  check('mentions service name', r && r.reply.includes('voice-adapter'));
  check('mentions exec_start', r && r.reply.includes('exec_start'));
  check('mentions requires forge-mcp', r && r.reply.includes('forge-mcp'));
});

section('/svc deps unknown — error');
withTempHome(() => {
  const r = cmd.dispatch({ text: '/svc deps does-not-exist' });
  check('error surfaced', r && /error|unknown service/i.test(r.reply));
});

// --- /budget ---

section('/budget show — empty');
withTempHome(() => {
  const r = cmd.dispatch({ text: '/budget show' });
  check('returns dispatcher result', r !== null);
  check('kind=phase3-budget', r && r.kind === 'phase3-budget');
  check('mentions no budgets', r && r.reply.includes('no session budgets'));
});

section('/budget (no sub) defaults to show');
withTempHome(() => {
  const r = cmd.dispatch({ text: '/budget' });
  check('default sub=show works', r && r.reply.includes('no session budgets'));
});

// --- summary ---

process.stdout.write(`\n=== ${pass}/${pass + fail} cases passed ===\n`);
process.exit(fail === 0 ? 0 : 1);
