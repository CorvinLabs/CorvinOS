#!/usr/bin/env node
// test_bridge_state.js — focused unit test for the bridge enable/disable
// helper. Runs in the same style as test_modules.js (no test framework).

'use strict';

const fs   = require('fs');
const os   = require('os');
const path = require('path');

const tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'corvin-bridge-state-'));
process.env.CORVIN_HOME = tmpRoot;
fs.mkdirSync(path.join(tmpRoot, 'bridges'), { recursive: true });

// Load AFTER setting env so bridge_paths picks up the temp home.
const state = require('./bridge_state');

const stateFile = path.join(tmpRoot, 'bridges', 'state.json');

let failures = 0;
function assert(cond, msg) {
  if (cond) {
    console.log('  ok  -', msg);
  } else {
    failures++;
    console.log('  FAIL -', msg);
  }
}

console.log('test_bridge_state');

// 1. Missing state.json → all channels enabled (fail-open).
assert(state.isChannelEnabled('discord') === true,
       'missing state.json → discord enabled');
assert(state.isChannelEnabled('whatsapp') === true,
       'missing state.json → whatsapp enabled');

// 2. Explicit disabled.
fs.writeFileSync(stateFile, JSON.stringify({
  channels: { discord: { enabled: false } },
}, null, 2));
assert(state.isChannelEnabled('discord') === false,
       'discord.enabled=false → disabled');
assert(state.isChannelEnabled('whatsapp') === true,
       'whatsapp not in file → enabled (default)');

// 3. Explicit enabled.
fs.writeFileSync(stateFile, JSON.stringify({
  channels: {
    discord:  { enabled: false },
    whatsapp: { enabled: true },
  },
}, null, 2));
assert(state.isChannelEnabled('discord')  === false, 'discord disabled');
assert(state.isChannelEnabled('whatsapp') === true,  'whatsapp enabled');

// 4. Malformed file → fail-open.
fs.writeFileSync(stateFile, '{ this is not JSON');
assert(state.isChannelEnabled('discord') === true,
       'malformed file → fail-open');

// 5. statePath under CORVIN_HOME.
assert(state.statePath() === stateFile,
       'statePath resolves under CORVIN_HOME');

// 6. readState always returns { channels: {...} } object.
fs.writeFileSync(stateFile, JSON.stringify({}, null, 2));
const s = state.readState();
assert(typeof s === 'object' && s !== null && typeof s.channels === 'object',
       'readState normalises missing channels key');

// Cleanup
fs.rmSync(tmpRoot, { recursive: true, force: true });

if (failures > 0) {
  console.log(`FAILED — ${failures} assertion(s) failed.`);
  process.exit(1);
}
console.log('PASSED');
