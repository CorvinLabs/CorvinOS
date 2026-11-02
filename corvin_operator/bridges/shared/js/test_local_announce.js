'use strict';
// local-announce.js: a missing announce program (notify-send / speak.sh on
// Windows, a stale python path) is reported as an ASYNC 'error' event. Without
// a listener that event crashed the whole bridge daemon on the next inbound
// message. The announcer must log it and keep the process alive.
const assert = require('assert');
const path = require('path');
const { makeAnnouncer } = require('./local-announce');

const missingRoot = path.join(__dirname, 'no-such-plugin-root-for-test');
const logs = [];
for (const mode of ['text', 'voice']) {
  const announce = makeAnnouncer({
    pluginRoot: missingRoot,
    channelLabel: 'Test',
    currentSettings: () => ({ local_announce_inbound: mode }),
    logger: (m) => logs.push(m),
  });
  announce({ text: 'hello', from: 'someone' }, 'text');
}

// python_bin.js defaults the Python stdio encoding to UTF-8 on Windows.
require('./python_bin');
if (process.platform === 'win32') {
  assert.ok(process.env.PYTHONIOENCODING, 'PYTHONIOENCODING must be set on win32');
}

setTimeout(() => {
  // Reaching this point at all is the main assertion (no unhandled 'error').
  // notify-send is absent on Windows and on many Linux hosts; when it is
  // present the spawn simply succeeds and nothing is logged for it.
  for (const m of logs) assert.match(m, /^announce failed: /);
  console.log(`local-announce: OK (${logs.length} spawn error(s) handled)`);
}, 1000);
