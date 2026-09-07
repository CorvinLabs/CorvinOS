#!/usr/bin/env node
// test_slash_task_gate.js — F-B2 regression (adversarial review 2026-09-07).
//
// The Discord `/task` slash-command used to short-circuit ABOVE the shared
// auth prelude (readOnlyOk → _isOwnerCheck/SPG → Art.50 disclosure →
// rateAllow) and write the inbox envelope straight away, so ANY Discord user
// who could see the bot could spawn a detached background worker with an
// arbitrary instruction — unauthenticated, undisclosed, unthrottled — and the
// log line leaked the first 50 chars of the instruction.
//
// daemon.js self-boots on require (discord.js login + process.exit), so it is
// not import-safe. As in email/test_inbound_auth.js we extract the REAL
// `interactionCreate` handler from the daemon.js source and execute it against
// injected mocks — this exercises the shipped code, not a copy.
//
// Run: node operator/bridges/discord/test_slash_task_gate.js

'use strict';

const fs   = require('fs');
const path = require('path');

const SRC = fs.readFileSync(path.join(__dirname, 'daemon.js'), 'utf8');

let pass = 0, fail = 0;
function t(label, ok, detail = '') {
  console.log(`  ${ok ? 'PASS' : 'FAIL'}  ${label}${detail ? ' — ' + detail : ''}`);
  if (ok) pass++; else fail++;
}

// ── extract the interactionCreate handler body ──────────────────────────────
const HEAD = "client.on('interactionCreate', async (interaction) => {";
const start = SRC.indexOf(HEAD);
t('interactionCreate handler present in daemon.js', start !== -1);
const bodyOpen = start + HEAD.length - 1;   // index of the `{`
let depth = 0, bodyEnd = -1;
for (let i = bodyOpen; i < SRC.length; i++) {
  if (SRC[i] === '{') depth++;
  else if (SRC[i] === '}') { depth--; if (depth === 0) { bodyEnd = i; break; } }
}
t('handler braces balanced', bodyEnd !== -1);
const body = SRC.slice(bodyOpen + 1, bodyEnd);

// Structural guard: the `/task` branch must sit BELOW every prelude gate.
{
  const iTask = body.indexOf("interaction.commandName === 'task'");
  const gates = ['readOnlyOk(', '_isOwnerCheck(', 'disclosureHasSeen(', 'rateAllow('];
  t('/task branch exists', iTask !== -1);
  for (const g of gates) {
    const ig = body.indexOf(g);
    t(`/task branch sits below ${g}`, ig !== -1 && ig < iTask, `${g}@${ig} task@${iTask}`);
  }
  t('no instruction text in the /task log line', !/instr="\$\{instruction\.slice/.test(body));
  t('/task log line carries len= only', /\/task from=\$\{userId\} ch=\$\{channelId\} len=\$\{instruction\.length\}/.test(body));
}

// ── behavioural: run the real handler with mocks ────────────────────────────
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
const PARAMS = [
  'interaction', 'readOnlyOk', '_isOwnerCheck', 'inChatCmds', 'currentSettings',
  'rateAllow', 'writeInbox', 'log', 'slashCommands', 'CHANNEL', 'SETTINGS_FILE',
  'OPERATOR_NAME', 'maybeForwardAsObserver', 'READ_ONLY_ACK', 'chatToggle', 'require', '__dirname',
];
const handler = new AsyncFunction(...PARAMS, body);

function scenario({ readOnly = false, owner = true, spgAllowed = false, disclosed = true,
                    rateOk = true, instruction = 'audit every open PR' } = {}) {
  const calls = { inbox: [], logs: [], replies: [], edits: [], markSeen: 0 };
  const interaction = {
    isChatInputCommand: () => true,
    commandName: 'task',
    user: { id: 'u-1' },
    channelId: 'ch-1',
    options: { getString: (n) => (n === 'args' ? instruction : null) },
    async deferReply() {}, async editReply(m) { calls.edits.push(m); },
    // interaction.reply() takes { content, ephemeral } — normalise to the text.
    async reply(m) { calls.replies.push(typeof m === 'string' ? m : m.content); },
  };
  const fakeRequire = (m) => {
    if (m === 'path') return path;
    if (m === 'child_process') return {
      spawnSync: () => ({ status: 0, stdout: JSON.stringify({ allowed: spgAllowed }) }),
    };
    throw new Error(`unexpected require(${m})`);
  };
  const args = {
    interaction,
    readOnlyOk: () => ({ isReadOnly: readOnly, firstDrop: true }),
    _isOwnerCheck: () => owner,
    inChatCmds: {
      dispatchReadOnlyConsent: () => null,
      disclosureHasSeen: () => disclosed,
      disclosureCardText: () => 'DISCLOSURE CARD',
      disclosureMarkSeen: () => { calls.markSeen++; return { ok: true }; },
      getObserverVisibility: () => 'off',
      dispatch: () => null,
    },
    currentSettings: () => ({ rate_limit_per_hour: 30, lang: 'en' }),
    rateAllow: () => rateOk,
    writeInbox: (p) => { calls.inbox.push(p); return 'id-1'; },
    log: (l) => calls.logs.push(String(l)),
    slashCommands: { interactionToText: (i) => `/task ${i.options.getString('args')}`.trim() },
    CHANNEL: 'discord',
    SETTINGS_FILE: '/nonexistent/settings.json',
    OPERATOR_NAME: 'Owner',
    maybeForwardAsObserver: () => false,
    READ_ONLY_ACK: 'RO',
    chatToggle: { handleToggleCommand: () => null },
    require: fakeRequire,
    __dirname,
  };
  return handler(...PARAMS.map((k) => args[k])).then(() => calls);
}

(async () => {
  console.log('\n[owner, disclosed, under rate limit → inbox write]');
  {
    const c = await scenario();
    t('exactly one inbox envelope', c.inbox.length === 1);
    t('envelope text is "/task <instruction>"', c.inbox[0] && c.inbox[0].text === '/task audit every open PR');
    t('envelope routes back to the channel', c.inbox[0] && c.inbox[0].chat_id === 'ch-1' && c.inbox[0].from === 'u-1');
    t('user got the background ack', c.edits.some((m) => /background/i.test(m)));
    const taskLog = c.logs.find((l) => l.startsWith('/task from='));
    t('log line carries len= and not the instruction', !!taskLog && / len=19$/.test(taskLog) && !taskLog.includes('audit every'), taskLog);
  }

  console.log('\n[read-only sender → NO inbox write]');
  {
    const c = await scenario({ readOnly: true });
    t('no inbox envelope', c.inbox.length === 0);
    t('read-only ack sent', c.replies.includes('RO'));
  }

  console.log('\n[non-owner, no SPG invite → NO inbox write]');
  {
    const c = await scenario({ owner: false, spgAllowed: false });
    t('no inbox envelope', c.inbox.length === 0);
    t('not-authorized reply', c.replies.some((m) => /not authorized/i.test(m)));
  }

  console.log('\n[non-owner, SPG guest → task allowed as guest]');
  {
    const c = await scenario({ owner: false, spgAllowed: true });
    t('inbox envelope written for an SPG-admitted guest', c.inbox.length === 1);
  }

  console.log('\n[first contact → disclosure card first, NO inbox write]');
  {
    const c = await scenario({ disclosed: false });
    t('no inbox envelope before disclosure', c.inbox.length === 0);
    t('disclosure card delivered', c.replies.includes('DISCLOSURE CARD'));
    t('disclosure marked seen after delivery', c.markSeen === 1);
  }

  console.log('\n[rate limit reached → NO inbox write]');
  {
    const c = await scenario({ rateOk: false });
    t('no inbox envelope', c.inbox.length === 0);
    t('rate-limit reply', c.replies.some((m) => /rate limit/i.test(m)));
  }

  console.log('\n[empty /task → usage hint, no envelope with empty instruction]');
  {
    const c = await scenario({ instruction: '' });
    t('envelope text is bare "/task"', c.inbox.length === 1 && c.inbox[0].text === '/task');
    t('usage hint shown', c.edits.some((m) => /Usage/.test(m)));
  }

  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail === 0 ? 0 : 1);
})();
