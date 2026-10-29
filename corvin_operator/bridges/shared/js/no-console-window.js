'use strict';
// Windows: a child process started from a console-less parent (every bridge
// daemon runs hidden) gets a brand-new, VISIBLE console window unless
// windowsHide is set. This defaults windowsHide to true for every child_process
// entry point, so a new spawn/exec call site cannot forget it. An explicit
// windowsHide value is respected. Load this before any other module that
// spawns processes. No-op on non-Windows.

const cp = require('child_process');

// Returns the argument list with windowsHide set on the options object. The
// options object follows the command and the optional argv array. If there is
// none, one is inserted before the callback (or at the end).
function withWindowsHide(args) {
  const out = args.slice();
  let optIdx = -1;
  for (let i = 1; i < out.length; i++) {
    const a = out[i];
    if (typeof a === 'function') break;
    if (a !== null && typeof a === 'object' && !Array.isArray(a)) {
      optIdx = i;
      break;
    }
  }
  if (optIdx === -1) {
    const cbIdx = out.findIndex((a) => typeof a === 'function');
    out.splice(cbIdx === -1 ? out.length : cbIdx, 0, { windowsHide: true });
  } else if (out[optIdx].windowsHide === undefined) {
    out[optIdx] = { ...out[optIdx], windowsHide: true };
  }
  return out;
}

if (process.platform === 'win32' && !cp.__corvinWindowsHide) {
  for (const name of ['spawn', 'spawnSync', 'exec', 'execSync', 'execFile', 'execFileSync', 'fork']) {
    const original = cp[name];
    if (typeof original !== 'function') continue;
    cp[name] = function (...args) {
      return original.apply(this, withWindowsHide(args));
    };
  }
  cp.__corvinWindowsHide = true;
}

module.exports = { withWindowsHide };
