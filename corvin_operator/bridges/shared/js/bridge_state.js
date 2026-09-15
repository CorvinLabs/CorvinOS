// bridge_state.js — runtime enable/disable toggle for bridge daemons.
//
// Source of truth: <corvin_home>/bridges/state.json
//   {
//     "channels": {
//       "discord":  {"enabled": false},
//       "whatsapp": {"enabled": true}
//     }
//   }
//
// Missing entry → channel defaults to enabled (fail-open).
//
// settings.json is bind-mounted :ro in the production container, so the
// toggle cannot live inside settings.json itself. state.json is under
// <corvin_home> which is :rw mounted. The console writes state.json via
// PUT /bridges/{channel}/enabled and additionally calls
// `supervisorctl start|stop bridge-<channel>` for immediate effect; this
// helper is the start-of-boot safety net so a disabled bridge exits early
// even when supervisorctl is unavailable.

'use strict';

const fs = require('fs');
const path = require('path');

const { corvinHome } = require('./bridge_paths');

function statePath() {
  return path.join(corvinHome(), 'bridges', 'state.json');
}

function readState() {
  const p = statePath();
  try {
    if (!fs.existsSync(p)) return { channels: {} };
    const raw = fs.readFileSync(p, 'utf-8');
    if (!raw.trim()) return { channels: {} };
    const data = JSON.parse(raw);
    if (!data || typeof data !== 'object' || Array.isArray(data)) {
      return { channels: {} };
    }
    if (!data.channels || typeof data.channels !== 'object') {
      data.channels = {};
    }
    return data;
  } catch (e) {
    // Fail-open on malformed state — better a bridge that ignores the
    // toggle than one that wedges shut on a JSON typo.
    return { channels: {} };
  }
}

function isChannelEnabled(channel) {
  const state = readState();
  const entry = state.channels[channel];
  if (!entry || typeof entry !== 'object') return true;
  return entry.enabled !== false;
}

// Convenience: call at the very top of a bridge daemon. If the channel
// is disabled, prints one log line and process.exit(0) — supervisord
// must be configured with `exitcodes=0,autorestart=unexpected` so the
// intentional exit does not loop-restart.
function exitIfDisabled(channel) {
  if (!isChannelEnabled(channel)) {
    // eslint-disable-next-line no-console
    console.log(
      `[bridge:${channel}] disabled via state.json — exiting cleanly (rc=0).`
    );
    process.exit(0);
  }
}

module.exports = {
  statePath,
  readState,
  isChannelEnabled,
  exitIfDisabled,
};
