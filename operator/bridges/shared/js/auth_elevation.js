// auth_elevation.js — JS mirror of bridges/shared/auth_elevation.py.
//
// The PIN-elevation store lives at
// <corvin_home>/global/auth/elevation.json and is shared across the
// JS daemons (writers via /auth-up) and the Python pre-tool-hook
// (reader via auth_elevation_gate.py).
//
// Format must stay identical to the Python side:
//   {
//     "<chat_key>": {
//       "granted_at": <unix_seconds>,
//       "expires_at": <unix_seconds>,
//       "ttl_s":      <int>
//     }, ...
//   }
//
// Stale entries are pruned lazily on every read. No file lock — the
// risk window is tiny (a chat double-grant racing) and the result is
// always a valid grant. The pre-tool-hook is the security-critical
// reader and runs in Python.

'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');

const DEFAULT_TTL_S = 600;

function _expandVars(p) {
  // Mirrors Python's os.path.expandvars: ${VAR} / $VAR (POSIX) and
  // %VAR% (Windows). Unset vars are left untouched, same as Python.
  return p
    .replace(/\$\{([A-Za-z_][A-Za-z0-9_]*)\}/g, (m, name) => (
      process.env[name] !== undefined ? process.env[name] : m
    ))
    .replace(/\$([A-Za-z_][A-Za-z0-9_]*)/g, (m, name) => (
      process.env[name] !== undefined ? process.env[name] : m
    ))
    .replace(/%([A-Za-z_][A-Za-z0-9_]*)%/g, (m, name) => (
      process.env[name] !== undefined ? process.env[name] : m
    ));
}

function _expandUser(p) {
  // Mirrors Python's os.path.expanduser: a leading ~ or ~/ resolves
  // against the current user's home directory.
  if (p === '~') return os.homedir();
  if (p.startsWith('~/') || p.startsWith('~\\')) {
    return path.join(os.homedir(), p.slice(2));
  }
  return p;
}

function corvinHome() {
  // Mirrors shared/paths.py::corvin_home(); kept in sync with
  // shared/js/bridge_paths.js::corvinHome().
  const raw = process.env.CORVIN_HOME;
  // A whitespace-only value is not a real override — treat it the
  // same as unset. Mirrors paths.py::_resolve_env().
  const env = raw ? raw.trim() : '';
  if (env) {
    return path.resolve(_expandUser(_expandVars(env)));
  }
  // Walk up from this file looking for a plugins/ marker.
  let cur = path.resolve(__dirname);
  while (true) {
    if (fs.existsSync(path.join(cur, 'plugins'))) {
      return path.join(cur, '.corvin');
    }
    const parent = path.dirname(cur);
    if (parent === cur) break;
    cur = parent;
  }
  return path.join(os.homedir(), '.corvin');
}

function storePath() {
  return path.join(corvinHome(), 'global', 'auth', 'elevation.json');
}

function loadStore() {
  const p = storePath();
  if (!fs.existsSync(p)) return {};
  try {
    const data = JSON.parse(fs.readFileSync(p, 'utf8'));
    return (data && typeof data === 'object') ? data : {};
  } catch (e) {
    return {};
  }
}

function saveStore(data) {
  const p = storePath();
  fs.mkdirSync(path.dirname(p), { recursive: true });
  const tmp = p + '.tmp';
  fs.writeFileSync(tmp, JSON.stringify(data, null, 2) + '\n');
  fs.renameSync(tmp, p);
}

function prune(data) {
  const now = Date.now() / 1000;
  const out = {};
  for (const [k, v] of Object.entries(data)) {
    if (v && typeof v === 'object' && (v.expires_at || 0) > now) {
      out[k] = v;
    }
  }
  return out;
}

/**
 * Grant elevation for chatKey if pin matches settingsPin.
 * Returns { ok: bool, reason: string }.
 */
function grant({ chatKey, pin, settingsPin, ttlS = DEFAULT_TTL_S }) {
  if (!settingsPin) return { ok: false, reason: 'no-pin-configured' };
  if (!pin || pin !== settingsPin) return { ok: false, reason: 'wrong-pin' };
  const data = prune(loadStore());
  const now = Date.now() / 1000;
  data[chatKey] = {
    granted_at: now,
    expires_at: now + ttlS,
    ttl_s: ttlS,
  };
  saveStore(data);
  return { ok: true, reason: 'ok' };
}

function revoke(chatKey) {
  const data = prune(loadStore());
  const existed = chatKey in data;
  if (existed) {
    delete data[chatKey];
    saveStore(data);
  }
  return existed;
}

function isElevated(chatKey) {
  if (!chatKey) return false;
  const data = prune(loadStore());
  const entry = data[chatKey];
  if (!entry || typeof entry !== 'object') return false;
  return (entry.expires_at || 0) > Date.now() / 1000;
}

function remainingTtl(chatKey) {
  if (!chatKey) return 0;
  const data = prune(loadStore());
  const entry = data[chatKey];
  if (!entry || typeof entry !== 'object') return 0;
  const delta = Math.floor((entry.expires_at || 0) - Date.now() / 1000);
  return Math.max(0, delta);
}

module.exports = {
  grant,
  revoke,
  isElevated,
  remainingTtl,
  // exposed for tests
  _internal: { corvinHome, storePath, DEFAULT_TTL_S },
};
