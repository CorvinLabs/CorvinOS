// bridge_paths.js — Node mirror of paths.py bridge_runtime_dir().
//
// ADR-0008: All bridge runtime state (inbox/outbox/processed/attachments
// queues, settings.json with credentials, auth/, voice.log) lives under
// <corvin_home>/bridges/<channel>/<kind>/ so the repo tree contains zero
// user-private data. Identity-only — no FS side effects. The Phase 8.2
// migration helper is the single owner of mkdir for this tree.

'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');

const BRIDGE_CHANNEL_RE = /^[a-z][a-z0-9_-]{0,31}$/;
const BRIDGE_KINDS = new Set([
  'inbox', 'outbox', 'processed', 'attachments', 'auth', 'log',
  'settings', 'root',
]);

function validateBridgeChannel(channel) {
  if (typeof channel !== 'string') {
    throw new TypeError(`bridge channel must be string, got ${typeof channel}`);
  }
  if (!BRIDGE_CHANNEL_RE.test(channel)) {
    throw new RangeError(
      `bridge channel ${JSON.stringify(channel)} fails charset rule [a-z][a-z0-9_-]{0,31}`
    );
  }
  return channel;
}

function validateBridgeKind(kind) {
  if (!BRIDGE_KINDS.has(kind)) {
    throw new RangeError(
      `bridge kind ${JSON.stringify(kind)} not in ${JSON.stringify([...BRIDGE_KINDS].sort())}`
    );
  }
  return kind;
}

function corvinHome() {
  // Mirrors shared/paths.py::corvin_home(); kept in sync with
  // shared/js/auth_elevation.js::corvinHome().
  const env = process.env.CORVIN_HOME;
  if (env) {
    return path.resolve(env);
  }
  let cur = path.resolve(__dirname);
  while (true) {
    if (fs.existsSync(path.join(cur, '.corvin_repo')) || fs.existsSync(path.join(cur, 'plugins'))) {
      return path.join(cur, '.corvin');
    }
    const parent = path.dirname(cur);
    if (parent === cur) break;
    cur = parent;
  }
  return path.join(os.homedir(), '.corvin');
}

function bridgesHome() {
  return path.join(corvinHome(), 'bridges');
}

function bridgeChannelDir(channel) {
  return path.join(bridgesHome(), validateBridgeChannel(channel));
}

function bridgeRuntimeDir(channel, kind) {
  validateBridgeChannel(channel);
  validateBridgeKind(kind);
  const envKey = `CORVIN_BRIDGE_${channel.toUpperCase()}_${kind.toUpperCase()}`;
  const envOverride = process.env[envKey];
  if (envOverride) {
    return path.resolve(envOverride);
  }
  const rootOverride = process.env.CORVIN_BRIDGES_HOME;
  const base = rootOverride ? path.resolve(rootOverride) : bridgesHome();
  const channelDir = path.join(base, channel);
  if (kind === 'settings' || kind === 'root') {
    return channelDir;
  }
  return path.join(channelDir, kind);
}

function bridgeSettingsPath(channel) {
  return path.join(bridgeRuntimeDir(channel, 'root'), 'settings.json');
}

function bridgeLogPath(channel) {
  return path.join(bridgeRuntimeDir(channel, 'log'), 'voice.log');
}

function legacyBridgeRuntimeDir(channel, kind) {
  validateBridgeChannel(channel);
  validateBridgeKind(kind);
  let cur = path.resolve(__dirname);
  let repo = null;
  while (true) {
    if (fs.existsSync(path.join(cur, '.corvin_repo')) || fs.existsSync(path.join(cur, 'plugins'))) {
      repo = cur;
      break;
    }
    const parent = path.dirname(cur);
    if (parent === cur) break;
    cur = parent;
  }
  if (repo === null) return null;
  // Try new operator/bridges layout first, fall back to legacy plugins/ location
  const newChannelDir = path.join(repo, 'operator', 'bridges', channel);
  const channelDir = fs.existsSync(newChannelDir) ? newChannelDir : path.join(repo, 'plugins', 'voice', 'bridges', channel);
  if (kind === 'settings' || kind === 'root') return channelDir;
  return path.join(channelDir, kind);
}

// ── Where the Python CLIs live (source/vendored tree, NOT the runtime dir) ───
//
// A daemon runs from `<corvin_home>/bridges/<channel>/` on every wheel install
// (`bridge_manager.start_channel_detached` → `Popen(cwd=runtime_dir)`), and
// `_materialise_shared_js()` mirrors ONLY `shared/js/*.{js,mjs,cjs,json}` next
// to it. So `__dirname/..` — which in_chat_commands.js used for all 17 of its
// `*_CLI` constants, plus the six under `voice/scripts/` — resolved to
// `<corvin_home>/bridges/shared/`, a directory that contains inbox/, outbox/
// and js/ and not one Python file. Every slash command that shells out
// (`/new`, `/reset`, `/engine`, `/role`, `/grant`, `/quota`, `/audit`,
// `/consent`, `/join`, `/pass`, `/goal`, `/objective`, `/propose`,
// `/dialectic*`, `/ps`, `/kill`, `/lang`, `/profile`, `/settings`, `/a2a`, …)
// therefore failed with ENOENT on a pip install while working perfectly in a
// git checkout, where `__dirname/..` happens to BE the source tree.
//
// `CORVIN_BRIDGE_OPERATOR_ROOT` is exported by bridge_manager when it spawns a
// daemon; the self-locating fallback keeps a hand-started source-tree daemon
// (the dev setup, and how every existing install runs today) working unchanged.
function operatorRoot() {
  const env = process.env.CORVIN_BRIDGE_OPERATOR_ROOT;
  if (env && fs.existsSync(path.join(env, 'bridges', 'shared'))) {
    return path.resolve(env);
  }
  // __dirname = <operator>/bridges/shared/js  →  <operator>
  return path.resolve(__dirname, '..', '..', '..');
}

/** Absolute path of a Python CLI in `operator/bridges/shared/`. */
function bridgeSharedPy(name) {
  return path.join(operatorRoot(), 'bridges', 'shared', name);
}

/** Absolute path of a Python CLI in `operator/voice/scripts/`. */
function voiceScript(name) {
  return path.join(operatorRoot(), 'voice', 'scripts', name);
}

module.exports = {
  corvinHome,
  operatorRoot,
  bridgeSharedPy,
  voiceScript,
  bridgesHome,
  bridgeChannelDir,
  bridgeRuntimeDir,
  bridgeSettingsPath,
  bridgeLogPath,
  legacyBridgeRuntimeDir,
  validateBridgeChannel,
  validateBridgeKind,
};
