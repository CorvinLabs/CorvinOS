'use strict';
// python_bin.js: a fresh README install has no `python3` on PATH — the bridges
// must find the product's interpreter through CORVIN_PYTHON (set by the
// installer on every unit) or the corvinos tool venv.
const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { pythonBin } = require('./python_bin');

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'pybin-'));
const fake = path.join(tmp, 'python3');
fs.writeFileSync(fake, '#!/bin/sh\necho 3.14\n');
fs.chmodSync(fake, 0o755);

// 1. CORVIN_PYTHON wins and must be executable when it is a path
assert.strictEqual(pythonBin({ env: { CORVIN_PYTHON: fake, PATH: '/nonexistent' }, fresh: true }), fake);
// 2. a dangling CORVIN_PYTHON path is skipped, PYTHON is next
assert.strictEqual(pythonBin({ env: { CORVIN_PYTHON: path.join(tmp, 'missing'), PYTHON: fake, PATH: '/nonexistent' }, fresh: true }), fake);
// 3. no env, no python3 on PATH, no venv at HOME → the familiar literal (ENOENT surfaces)
const emptyHome = fs.mkdtempSync(path.join(os.tmpdir(), 'pybin-home-'));
assert.strictEqual(pythonBin({ env: { PATH: '/nonexistent', HOME: emptyHome, XDG_DATA_HOME: emptyHome }, fresh: true }), 'python3');
// 4. the corvinos tool venv under XDG_DATA_HOME is found
const venvBin = path.join(emptyHome, 'uv', 'tools', 'corvinos', 'bin');
fs.mkdirSync(venvBin, { recursive: true });
fs.copyFileSync(fake, path.join(venvBin, 'python3'));
fs.chmodSync(path.join(venvBin, 'python3'), 0o755);
assert.strictEqual(pythonBin({ env: { PATH: '/nonexistent', XDG_DATA_HOME: emptyHome }, fresh: true }), path.join(venvBin, 'python3'));
// 5. a bare name without a path separator is passed through (resolved by PATH at spawn)
assert.strictEqual(pythonBin({ env: { CORVIN_PYTHON: 'python3.14', PATH: '/nonexistent' }, fresh: true }), 'python3.14');
// 6. every bridge code path spawns through the resolver, never the literal
const root = path.join(__dirname, '..', '..');
const files = ['shared/js/in_chat_commands.js', 'shared/js/audit.js', 'shared/js/local-announce.js',
               'discord/daemon.js', 'whatsapp/daemon.js'];
for (const f of files) {
  const src = fs.readFileSync(path.join(root, f), 'utf8').split('\n')
    .filter((l) => !/^\s*\/\//.test(l)).join('\n');
  assert.ok(!/spawn(Sync)?\(\s*'python3'/.test(src) && !/\[\s*'python3'\s*,/.test(src) && !/\|\|\s*'python3'/.test(src),
    `${f} still spawns the literal python3`);
}
console.log('test_python_bin: ok');
