'use strict';
// Test: event-loop-watchdog force-restarts a WEDGED process but spares an IDLE
// (healthy, quiet) one. The idle case is the load-bearing guard against the
// naive "no events for N minutes = unhealthy" approach, which would kill a
// perfectly healthy quiet bot. Run: node test_event_loop_watchdog.js
const { spawnSync } = require('child_process');
const path = require('path');
const MOD = JSON.stringify(path.join(__dirname, 'event-loop-watchdog.js'));

let ok = true;
const fail = (m) => { console.error('FAIL:', m); ok = false; };

// 1) A frozen main loop must be killed by a signal (not run to completion).
const frozen = `
const { startEventLoopWatchdog } = require(${MOD});
startEventLoopWatchdog({ thresholdMs: 800, checkMs: 200, beatMs: 150, graceMs: 300 });
setTimeout(() => {
  const end = Date.now() + 5000;
  while (Date.now() < end) {}          // block the event loop
  console.log('LEAK');                 // must never print
  process.exit(0);
}, 400);
`;
const r = spawnSync(process.execPath, ['-e', frozen], { encoding: 'utf8', timeout: 15000 });
if (r.signal !== 'SIGKILL' && r.signal !== 'SIGTERM' && r.status !== 137 && r.status !== 143) {
  fail(`frozen loop not killed (signal=${r.signal} status=${r.status})`);
}
if ((r.stdout || '').includes('LEAK')) fail('frozen loop resumed — watchdog too slow');

// 2) An IDLE (healthy) process — event loop turning, just no traffic — must
//    SURVIVE past the threshold. This is what makes it a freeze detector, not
//    an idle killer.
const idle = `
const { startEventLoopWatchdog } = require(${MOD});
startEventLoopWatchdog({ thresholdMs: 800, checkMs: 200, beatMs: 150, graceMs: 300 });
setTimeout(() => { console.log('IDLE_SURVIVED'); process.exit(0); }, 2600);  // idle 2.6s >> threshold
`;
const r2 = spawnSync(process.execPath, ['-e', idle], { encoding: 'utf8', timeout: 15000 });
if (r2.status !== 0 || !(r2.stdout || '').includes('IDLE_SURVIVED')) {
  fail(`idle healthy process was killed — false positive (status=${r2.status} signal=${r2.signal})`);
}

// 3) The opt-out must work (returns null, arms nothing).
const optout = `
process.env.CORVIN_BRIDGE_WATCHDOG = '0';
const { startEventLoopWatchdog } = require(${MOD});
const h = startEventLoopWatchdog({});
console.log(h === null ? 'OPTOUT_OK' : 'OPTOUT_FAIL');
process.exit(0);
`;
const r3 = spawnSync(process.execPath, ['-e', optout], { encoding: 'utf8', timeout: 10000 });
if (!(r3.stdout || '').includes('OPTOUT_OK')) fail('opt-out (CORVIN_BRIDGE_WATCHDOG=0) did not disable');

console.log(ok
  ? 'PASS: kills a frozen loop, spares an idle one, honors opt-out'
  : 'FAILED');
process.exit(ok ? 0 : 1);
