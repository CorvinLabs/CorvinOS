// inbox_write.js — atomic inbox envelope write shared by every messenger daemon.
//
// F-B3 (adversarial review 2026-09-07): the daemons used a bare
// `fs.writeFileSync(<id>.json, …)`. The adapter polls `inbox/*.json` at 1 Hz,
// so a poll that lands between open() and the final write() of a large
// envelope (attachments carry paths + captions, /share payloads are up to
// 2000 chars) reads a truncated file, fails JSON.parse — and the OLD adapter
// unlinked it, i.e. the user's message was silently lost. Write to
// `<id>.json.tmp` (never matched by the `*.json` poll) and rename() into
// place: POSIX rename is atomic, so the adapter only ever sees complete
// envelopes. Mode 0600 — the envelope is user content.

'use strict';

const fs = require('fs');
const path = require('path');

function writeInboxAtomic(inboxDir, id, obj) {
  const finalPath = path.join(inboxDir, `${id}.json`);
  const tmpPath = `${finalPath}.tmp`;
  fs.writeFileSync(tmpPath, JSON.stringify(obj, null, 2), { mode: 0o600 });
  fs.renameSync(tmpPath, finalPath);
  return finalPath;
}

module.exports = { writeInboxAtomic };
