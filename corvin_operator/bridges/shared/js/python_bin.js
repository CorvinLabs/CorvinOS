'use strict';
// The Python interpreter a bridge spawns for the product's CLIs (profile, lang,
// consent, A2A, workflows, earcons, say.py …).
//
// A README install (`git clone` + `./install.sh`) on a bare Ubuntu 24.04 has NO
// `python3` on PATH: uv's Python lives in the corvinos tool venv only. Every
// bridge slash command spawned the literal `python3` and would have failed with
// ENOENT there (fresh-system review 2026-10-21, round 2). Resolution order:
//   1. CORVIN_PYTHON — baked into every unit the installer writes (the venv's
//      interpreter), also honoured by the Discord daemon's SPG check already;
//   2. PYTHON — the older name audit.js / the TTS helper used;
//   3. `python3` when it is on PATH (dev hosts, distro installs);
//   4. the corvinos tool venv's interpreter at uv's default location;
//   5. `python3` — so the error, if any, is the familiar ENOENT.
// Resolved once per process.
const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawnSync } = require('child_process');

// Windows: a Python child whose stdout is a pipe encodes it in the ANSI code
// page (cp1252), so any CLI printing non-Latin-1 text (e.g. `/lang zh-Hans` ->
// "简体中文") died with UnicodeEncodeError, and umlauts arrived as mojibake in
// the bridge, which decodes child output as UTF-8. Every Python this process
// starts inherits the env, so default the stdio encoding to UTF-8 once here.
// An operator's own value wins; POSIX is untouched.
if (process.platform === 'win32' && !process.env.PYTHONIOENCODING) {
  process.env.PYTHONIOENCODING = 'utf-8';
}

let _cached = null;

function _isExec(p) {
  try { fs.accessSync(p, fs.constants.X_OK); return fs.statSync(p).isFile(); } catch (_) { return false; }
}

function _venvCandidates(env = process.env) {
  const home = env.HOME || os.homedir();
  const data = env.XDG_DATA_HOME || path.join(home, '.local', 'share');
  const out = [path.join(data, 'uv', 'tools', 'corvinos', 'bin', 'python3'),
               path.join(data, 'uv', 'tools', 'corvinos', 'bin', 'python')];
  if (env.APPDATA) out.push(path.join(env.APPDATA, 'uv', 'tools', 'corvinos', 'Scripts', 'python.exe'));
  return out;
}

function pythonBin({ env = process.env, fresh = false } = {}) {
  if (_cached && !fresh && env === process.env) return _cached;
  let found = null;
  for (const key of ['CORVIN_PYTHON', 'PYTHON']) {
    const v = (env[key] || '').trim();
    if (v && (_isExec(v) || !v.includes(path.sep))) { found = v; break; }
  }
  if (!found) {
    const probe = spawnSync('python3', ['--version'], { stdio: 'ignore', env, timeout: 5000 });
    if (probe.status === 0) found = 'python3';
  }
  if (!found) found = _venvCandidates(env).find(_isExec) || null;
  if (!found) found = 'python3';
  if (env === process.env) _cached = found;
  return found;
}

module.exports = { pythonBin, _venvCandidates };
