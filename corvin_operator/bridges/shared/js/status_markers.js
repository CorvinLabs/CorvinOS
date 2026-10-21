// status_markers.js — the chat status-icon vocabulary for the JavaScript bridges.
//
// ONE vocabulary for every chat surface (ADR-2269, CONCEPT-0114): the source is
// shared/status_markers.py; shared/status_markers.json is its generated export
// (`python3 status_markers.py --write-json`), pinned by test_status_marker_literals.py.
// Bridge code takes an icon by NAME from here — a glyph typed as a string literal in a
// chat-output module is a second copy that can drift, and the same test fails on it.
'use strict';

const MARKERS = require('../status_markers.json');

/**
 * The icon for a status name (done, failed, warning, note, investigating, running,
 * suggestion, decision). Throws on an unknown name: a typo must fail a test, not
 * render a missing icon in a chat.
 * @param {string} name
 * @returns {string}
 */
function icon(name) {
  if (!Object.prototype.hasOwnProperty.call(MARKERS.icons, name)) {
    throw new Error(`unknown status marker: ${name}`);
  }
  return MARKERS.icons[name];
}

/** [icon, meaning] in the order the model is taught them. */
const STATUS_MARKERS = MARKERS.markers.map((m) => [m[0], m[1]]);

module.exports = { icon, STATUS_MARKERS };
