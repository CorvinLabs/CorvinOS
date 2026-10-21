// test_status_markers.js — the JS vocabulary IS the shipped export (T-0249, ADR-2269).
//   node shared/js/test_status_markers.js
'use strict';
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const { icon, STATUS_MARKERS } = require('./status_markers');

const shipped = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'status_markers.json'), 'utf8'));
let pass = 0;
function ok(cond, msg) { assert.ok(cond, msg); console.log(`PASS: ${msg}`); pass++; }

ok(Object.keys(shipped.icons).length === 8, 'eight names in the export');
for (const [name, glyph] of Object.entries(shipped.icons)) {
  ok(icon(name) === glyph, `icon('${name}') is the exported glyph`);
}
ok(STATUS_MARKERS.length === shipped.markers.length, 'legend has one row per marker');
ok(new Set(STATUS_MARKERS.map((m) => m[0])).size === STATUS_MARKERS.length, 'one meaning per icon');
ok(STATUS_MARKERS.every(([g]) => Object.values(shipped.icons).includes(g)), 'every legend icon is nameable');
assert.throws(() => icon('nope'), /unknown status marker/);
ok(true, 'an unknown name throws');
// The daemon's usages resolve to the vocabulary, not to a literal of their own.
ok(icon('running') === '\u23f3', 'running is the hourglass the daemon reacts with');
ok(icon('failed') === '\u274c' && icon('warning') === '\u26a0\ufe0f', 'failed / warning glyphs');
console.log(`\n${pass} pass / 0 fail`);
